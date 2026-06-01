#!/usr/bin/env python3
"""
credential_scrub.py — unified PostToolUse scrubber (v2)

Revision rationale (= dual-magi round 1 findings consolidated):
  - Codex-1: byte-replace on raw JSONL fails when credential contains JSON-escape chars.
              FIX: parse JSONL records, replace inside string fields, serialize back.
  - Codex-2: build script captures ambient env. Build-side fix (this file unaffected).
  - Codex-3: broad sidecar glob = audit tamper surface.
              FIX: manifests load only from ~/.claude/state/credential_scrub/manifest/.
  - Codex-4: key-name → injection vector via marker + additionalContext.
              FIX: strict identifier regex on load; generic <REDACTED> marker.
  - Codex-5: char vs byte length mismatch → deterministic FN for non-ASCII.
              FIX: manifest stores byte_length; scan slices bytes by byte_length.
  - Codex-6: scan-time ImportError propagated past P4.
              FIX: try/except wraps scan AND redact phases.
  - Codex-10: additionalContext leaks key names + precise lengths.
              FIX: emit count only ("N credentials matched, see hook log").
  - Codex-11: settings matcher coverage regression.
              FIX: ship recommended matcher = Bash|Read|Edit|Task; documented.
  - Melchior-1: corpus mismatch (tool_response vs jsonl).
              FIX: detect on tool_output; redact on jsonl by JSON-aware traversal.
              The literal must exist in BOTH the decoded payload AND the decoded
              jsonl string field — JSON-aware traversal verifies presence in jsonl
              before claiming success.
  - Melchior-2: perf. FIX: candidate-run pre-filter (regex finds base64-ish runs ≥16
              chars first; only those are sliding-hashed). Plus MAX_SCAN_BYTES cap.
  - Melchior-3: lib.sh ignored. FIX: bash wrapper sources lib.sh; py reads
              env-passed transcript_path + tool_output.
  - Melchior-4: blake3 fallback silent. FIX: hard preflight; manifest declares
              algorithm; mismatch → fail-safe + hook_log; never silent downgrade.
  - Melchior-8: os.replace race. FIX: write to sibling tempfile in same dir,
              os.replace; document that Claude's append fd may temporarily orphan
              (rare, recoverable on next write since Claude opens by path).
  - Melchior-10: hash collision discards keys. FIX: dict[hmac] -> list[key_name].
  - Balthasar-1: P3 (millisecond literal) framing wrong. Doc-side fix.
  - Balthasar-4: stderr bypass. FIX: parse_tool_output (via lib.sh) emits all 4 fields.
  - Caspar-2: log path drift. FIX: write to ~/.claude/state/hook_logs/hooks.log
              via "credential_scrub" tag.
  - Caspar-17: kill switch. FIX: bash wrapper checks .disabled flag.

Invariants:
  I1: Hook process loads ONLY hmac digests + byte_length + key_name from manifest.
      Plaintext credential bytes touched only when (a) extracted from tool_output as
      candidate window, (b) verified by hmac match, (c) used as needle for jsonl
      string-field replace. Lifetime: bounded by this main() invocation.
  I2: Manifest files outside ~/.claude/state/credential_scrub/manifest/ are NOT
      loaded. No discovery glob. Trust boundary == local state dir owned by user.
  I3: format_version mismatch → fail-safe + hook_log; never silent ignore.
  I4: Any uncaught exception in scan / redact → fail-safe + hook_log; exit 0.
  I5: Output size > MAX_SCAN_BYTES → scan SKIPPED with explicit warning context
      (= visible to Claude so the user sees the gap).
  I6: All emit_context output is GENERIC (no key names, no precise lengths).
"""

from __future__ import annotations
import os, sys, json, re, datetime, hmac as _hmac, hashlib, traceback, subprocess, shutil
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
STATE_DIR = Path.home() / ".claude" / "state" / "credential_scrub"
MANIFEST_DIR = STATE_DIR / "manifest"
SALT_FILE = STATE_DIR / "salt.bin"
HOOK_LOG = Path.home() / ".claude" / "state" / "hook_logs" / "hooks.log"

MAX_SCAN_BYTES = 256_000          # hard cap; above this scan is SKIPPED + warned
# Per-candidate-run length cap (perf). scan_output is O(run_len × distinct_lengths)
# sliding-window HMAC; a single multi-hundred-KB base64 run (e.g. a SQL BYTEA dump
# returned by an MCP tool) would otherwise mint ~10M HMACs and blow the hook timeout
# → SIGKILL → fail-OPEN (leak not redacted). The build validates every manifest
# byte_length to 12..4096 (credential_scrub_build: load rejects >4096), so the
# LARGEST possible known secret is 4096 bytes; a single contiguous in-class run
# longer than that cannot itself be a known secret. Runs over this cap are skipped
# (logged, count-only) — a bounded tradeoff: a secret *embedded inside* a >4KB
# delimiter-free blob is missed. The rolling-hash prefilter (follow-up) removes the
# tradeoff; this cap is the do-now fix that keeps the widened sensor from fail-open.
MAX_CANDIDATE_RUN = 4096
MAX_MANIFEST_ENTRIES = 500        # cap loaded entries
MAX_MANIFEST_FILES = 64           # cap manifest file count
SUPPORTED_FORMAT_VERSION = 1
KEY_NAME_REGEX = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
# Default algorithm = sha256-hmac (= stdlib, always available).
# blake3-keyed is optional perf upgrade (~3-5x faster); user installs separately.
DEFAULT_ALGORITHM = "sha256-hmac"

# Candidate-run pre-filter: any contiguous run of ≥16 of these chars is a candidate
# windowable region. Broad enough to cover base64 + base64url + hex + URL-encoded
# + percent-escapes + common password punctuation. Credentials outside this
# alphabet (= raw multibyte unicode, special symbols) won't pre-filter; for those
# the build script flags them at generation time so the user knows the gap.
CANDIDATE_RUN = re.compile(rb"[A-Za-z0-9._\-+/=:@%!#$&*()|~?\[\]]{16,}")

GENERIC_REDACT_MARKER = "<REDACTED>"

# ----------------------------------------------------------------------------
# Logging — generic; never logs values or hashes
# ----------------------------------------------------------------------------
def hook_log(msg: str) -> None:
    # H5+H6 (round 2): use os.open with explicit 0o600 mode so the log file is
    # owner-readable only. mkdir parent with 0o700 if we own it. Idempotent +
    # safe for shared use by other hooks (= chmod is best-effort; other hooks
    # using `open(..., 'a')` continue to work).
    try:
        HOOK_LOG.parent.mkdir(parents=True, exist_ok=True)
        try:
            HOOK_LOG.parent.chmod(0o700)
        except OSError:
            pass
        ts = datetime.datetime.now().strftime("%F_%T")
        fd = os.open(str(HOOK_LOG),
                     os.O_WRONLY | os.O_APPEND | os.O_CREAT,
                     0o600)
        try:
            os.write(fd, f"[{ts}] [credential_scrub] {msg}\n".encode("utf-8", errors="replace"))
        finally:
            os.close(fd)
    except OSError:
        pass


# ----------------------------------------------------------------------------
# HMAC helpers
# ----------------------------------------------------------------------------
def load_salt() -> bytes | None:
    """Load 32-byte salt or return None (= disable scrub if no salt yet)."""
    try:
        data = SALT_FILE.read_bytes()
        if len(data) != 32:
            hook_log(f"salt corrupt: expected 32 bytes, got {len(data)}; scrub disabled")
            return None
        return data
    except FileNotFoundError:
        return None
    except OSError as e:
        hook_log(f"salt read error: {type(e).__name__}; scrub disabled")
        return None


def algorithm_available(algorithm: str) -> bool:
    """H7: preflight whether the algorithm can be computed in this process."""
    if algorithm == "sha256-hmac":
        return True  # stdlib, always
    if algorithm == "blake3-keyed":
        try:
            import blake3  # noqa: F401
            return True
        except ImportError:
            return False
    return False


def compute_hmac(data: bytes, salt: bytes, algorithm: str) -> str:
    if algorithm == "blake3-keyed":
        import blake3  # caller is expected to have preflighted via algorithm_available
        return blake3.blake3(data, key=salt).hexdigest()
    if algorithm == "sha256-hmac":
        return _hmac.new(salt, data, hashlib.sha256).hexdigest()
    raise ValueError(f"unsupported algorithm: {algorithm}")


# ----------------------------------------------------------------------------
# Manifest loading — strict provenance, strict validation
# ----------------------------------------------------------------------------
def load_manifests() -> tuple[dict[int, dict[str, list[str]]], str | None]:
    """
    Returns:
      by_byte_length: dict[int, dict[hmac_hex, list[key_name]]]
      algorithm: declared algorithm or None if no usable manifest
    """
    import yaml
    by_length: dict[int, dict[str, list[str]]] = {}
    algorithms: set[str] = set()

    if not MANIFEST_DIR.is_dir():
        return by_length, None

    manifest_files = sorted(MANIFEST_DIR.glob("*.scrub.json"))
    if not manifest_files:
        manifest_files = sorted(MANIFEST_DIR.glob("*.scrub.yaml"))

    if len(manifest_files) > MAX_MANIFEST_FILES:
        hook_log(f"too many manifests ({len(manifest_files)} > {MAX_MANIFEST_FILES}); refuse to load")
        return {}, None

    total_entries = 0
    for fp in manifest_files:
        # Reject if file is not regular file or not user-owned
        try:
            st = fp.lstat()
        except OSError:
            continue
        if not os.path.isfile(fp) or st.st_uid != os.getuid():
            hook_log(f"manifest provenance reject: {fp.name}")
            continue

        try:
            if fp.suffix == ".json":
                doc = json.loads(fp.read_text())
            else:
                doc = yaml.safe_load(fp.read_text())
        except Exception as e:
            hook_log(f"manifest parse error {fp.name}: {type(e).__name__}")
            continue

        if not isinstance(doc, dict):
            continue
        if doc.get("format_version") != SUPPORTED_FORMAT_VERSION:
            hook_log(f"manifest version mismatch {fp.name}: {doc.get('format_version')!r}")
            continue
        algo = doc.get("algorithm")
        if algo not in ("blake3-keyed", "sha256-hmac"):
            hook_log(f"manifest algorithm reject {fp.name}: {algo!r}")
            continue
        algorithms.add(algo)

        for entry in doc.get("entries", []) or []:
            if total_entries >= MAX_MANIFEST_ENTRIES:
                hook_log(f"manifest entry cap reached ({MAX_MANIFEST_ENTRIES}); rest dropped")
                break
            try:
                key = str(entry["key"])
                byte_length = int(entry["byte_length"])
                hmac_hex = str(entry["hmac"]).lower()
            except (KeyError, ValueError, TypeError):
                continue
            # Key-name validation: strict identifier, no injection chars
            if not KEY_NAME_REGEX.match(key):
                continue
            if byte_length < 12 or byte_length > 4096:
                continue
            if not re.match(r"^[0-9a-f]{32,128}$", hmac_hex):
                continue
            by_length.setdefault(byte_length, {}).setdefault(hmac_hex, []).append(key)
            total_entries += 1

    if len(algorithms) > 1:
        hook_log(f"mixed algorithms in manifests: {algorithms}; scrub disabled")
        return {}, None
    return by_length, (next(iter(algorithms)) if algorithms else None)


# ----------------------------------------------------------------------------
# Scan — candidate-run pre-filter + sliding-byte-window HMAC match
# ----------------------------------------------------------------------------
def scan_output(output_bytes: bytes,
                by_length: dict[int, dict[str, list[str]]],
                salt: bytes,
                algorithm: str) -> list[tuple[bytes, list[str]]]:
    """Return list of (matched_literal_bytes, [key_names]) without duplicates.
    Assumes algorithm_available(algorithm) was checked by caller — see main()."""
    hits: dict[bytes, list[str]] = {}
    if not by_length or not output_bytes:
        return []
    lengths = sorted(by_length.keys())
    skipped_runs = 0
    for m in CANDIDATE_RUN.finditer(output_bytes):
        run = m.group()
        run_len = len(run)
        # Perf guard: a single contiguous run longer than the largest possible known
        # secret (MAX_CANDIDATE_RUN) is a dump/digest column, not a credential window.
        # Skip it to bound the quadratic sliding scan — see MAX_CANDIDATE_RUN rationale.
        if run_len > MAX_CANDIDATE_RUN:
            skipped_runs += 1
            continue
        for L in lengths:
            if L > run_len:
                break
            hash_lookup = by_length[L]
            for i in range(run_len - L + 1):
                window = run[i:i+L]
                if window in hits:
                    continue
                h = compute_hmac(window, salt, algorithm)
                keys = hash_lookup.get(h)
                if keys:
                    hits[window] = list(keys)
    if skipped_runs:
        # Count-only (no values): surfaces the bounded-coverage tradeoff in the log
        # so an oversized-blob FN is observable rather than silent.
        hook_log(f"oversize_runs_skipped={skipped_runs} (len>{MAX_CANDIDATE_RUN})")
    return list(hits.items())


# ----------------------------------------------------------------------------
# JSON-aware transcript redaction
# ----------------------------------------------------------------------------
def redact_string(s: str, literals: list[bytes]) -> tuple[str, int]:
    """Replace each literal (decoded from bytes via utf-8) in s with GENERIC_REDACT_MARKER.
    Returns (new_string, n_replacements)."""
    n = 0
    for lit in literals:
        try:
            needle = lit.decode("utf-8")
        except UnicodeDecodeError:
            # fall back: try latin-1 (round-trips arbitrary bytes); rare for credentials
            needle = lit.decode("latin-1", errors="replace")
        if needle and needle in s:
            n += s.count(needle)
            s = s.replace(needle, GENERIC_REDACT_MARKER)
    return s, n


def walk_and_redact(node: Any, literals: list[bytes]) -> tuple[Any, int]:
    """Recursively walk JSON value, redacting in every string field."""
    if isinstance(node, str):
        new, n = redact_string(node, literals)
        return new, n
    if isinstance(node, list):
        total = 0
        out = []
        for item in node:
            new, n = walk_and_redact(item, literals)
            out.append(new)
            total += n
        return out, total
    if isinstance(node, dict):
        total = 0
        out = {}
        for k, v in node.items():
            new_v, n = walk_and_redact(v, literals)
            out[k] = new_v
            total += n
        return out, total
    return node, 0


def redact_jsonl(transcript_path: Path, literals: list[bytes]) -> int:
    """
    Parse jsonl line-by-line as JSON, walk-redact, serialize back atomically.
    Returns total replacement count.
    """
    if not literals:
        return 0
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="surrogateescape") as f:
            raw_lines = f.readlines()
    except OSError as e:
        hook_log(f"transcript read error: {type(e).__name__}")
        return 0

    out_lines: list[str] = []
    total_replaced = 0
    for line in raw_lines:
        stripped = line.rstrip("\n")
        if not stripped:
            out_lines.append(line)
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            # non-JSON line (rare); leave alone
            out_lines.append(line)
            continue
        new_obj, n = walk_and_redact(obj, literals)
        if n > 0:
            new_line = json.dumps(new_obj, ensure_ascii=False, separators=(",", ":"))
            out_lines.append(new_line + "\n")
            total_replaced += n
        else:
            out_lines.append(line)

    if total_replaced == 0:
        return 0

    tmp_path = transcript_path.with_suffix(transcript_path.suffix + ".scrub.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8", errors="surrogateescape") as f:
            f.writelines(out_lines)
        os.replace(tmp_path, transcript_path)
    except OSError as e:
        hook_log(f"transcript rewrite error: {type(e).__name__}")
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return 0
    return total_replaced


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def emit_generic_context(message: str) -> None:
    out = {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": message}}
    sys.stdout.write(json.dumps(out))


def session_id_from_raw(raw_input: str) -> str:
    try:
        return str(json.loads(raw_input).get("session_id") or "unknown")
    except Exception:
        return "unknown"


def spawn_leak_followup(key_names: list[str], replaced: int, transcript: str, session_id: str) -> None:
    """Step 2 of the post-leak chain: fire credential_leak_followup.sh DETACHED so
    gh latency never blocks this hook. Fail-safe: any error is swallowed (the
    transcript is already sanitized regardless). Passes ONLY key names + counts —
    never a credential value."""
    try:
        followup = Path(__file__).resolve().parent / "credential_leak_followup.sh"
        if not followup.is_file():
            return
        bash = shutil.which("bash") or "/bin/bash"
        # De-dup + cap the key-name list (already non-sensitive identifiers).
        detail = ", ".join(sorted(set(key_names))[:20]) or "(hash-manifest match)"
        env = dict(os.environ)
        env.update({
            "LEAK_SOURCE": "hash_scrub",
            "LEAK_DETAIL": detail,
            "LEAK_REPLACED": str(replaced),
            "LEAK_TRANSCRIPT": transcript,
            "LEAK_SESSION_ID": session_id,
        })
        subprocess.Popen(
            [bash, str(followup)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,   # detach from this hook's process group
            env=env,
        )
    except Exception:
        hook_log("leak_followup_spawn_failed; transcript already sanitized")


def resume_context(replaced: int) -> str:
    """Step 3: terse 'keep working' context. The leak is already sanitized +
    queued for incident logging, so Claude should resume rather than stop for
    manual cleanup."""
    ref = ""
    try:
        last = (STATE_DIR / "last_issue").read_text().strip()
        if last:
            ref = f" (tracked in claude-harness#{last})"
    except OSError:
        pass
    return (
        f"⚠️  credential leak auto-handled: transcript sanitized ({replaced} replacement(s)) "
        f"+ incident logged to the claude-harness `credential-leak` issue{ref}. "
        "No manual steps needed — continue your current task. "
        "Rotation for the affected credential is tracked in that issue."
    )


def _collect_text_leaves(node: Any, out: list[str]) -> None:
    """H8: recursively walk a JSON structure and collect all string leaves.
    Used to extract inner .text fields from tool_response.content arrays,
    which lib.sh's parse_tool_output emits as raw JSON when content is an array."""
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, list):
        for item in node:
            _collect_text_leaves(item, out)
    elif isinstance(node, dict):
        for v in node.values():
            _collect_text_leaves(v, out)


def extract_scan_corpus_from_raw_input(raw_input: str) -> str:
    """H8: parse HOOK_INPUT JSON and recursively collect every string leaf under
    tool_response. This catches credentials in content arrays (= multi-part
    assistant messages) that lib.sh's flat jq extraction emits as JSON-escaped
    blobs. Falls back to empty string if parse fails (= caller will use the
    lib.sh-extracted output as the only corpus)."""
    try:
        payload = json.loads(raw_input)
    except json.JSONDecodeError:
        return ""
    tr = payload.get("tool_response")
    if tr is None:
        return ""
    leaves: list[str] = []
    _collect_text_leaves(tr, leaves)
    return "\n".join(leaves)


def main() -> int:
    tool_output = os.environ.get("CREDENTIAL_SCRUB_TOOL_OUTPUT", "")
    transcript = os.environ.get("CREDENTIAL_SCRUB_TRANSCRIPT", "")
    raw_input = os.environ.get("CREDENTIAL_SCRUB_RAW_INPUT", "")

    # H8 (round 2): union of (a) lib.sh's flat extraction (fast path for typical
    # stdout/stderr/output payloads) and (b) recursive text-leaf walk of the raw
    # HOOK_INPUT (catches content-array shapes where (a) emits JSON-escaped form).
    augmented = ""
    if raw_input:
        augmented = extract_scan_corpus_from_raw_input(raw_input)
    if augmented and augmented != tool_output:
        # Concat with separator so candidate-run regex doesn't bridge fields
        tool_output = (tool_output + "\n" + augmented) if tool_output else augmented

    if not tool_output or not transcript:
        return 0
    transcript_path = Path(transcript)
    if not transcript_path.is_file():
        return 0

    # I5: output size cap with explicit warning
    output_bytes = tool_output.encode("utf-8", errors="surrogateescape")
    if len(output_bytes) > MAX_SCAN_BYTES:
        hook_log(f"output_oversize bytes={len(output_bytes)}; scan SKIPPED")
        emit_generic_context(
            "⚠️  credential_scrub: tool output exceeded scan size cap "
            f"({len(output_bytes)} > {MAX_SCAN_BYTES} bytes). Scan was SKIPPED. "
            "If sensitive data may be in this output, review manually + consider rotation."
        )
        return 0

    salt = load_salt()
    if salt is None:
        # No salt installed yet — scrub is disabled. Surface once per session via context.
        return 0

    # I4: scan and redact wrapped in fail-safe
    try:
        by_length, algorithm = load_manifests()
        if not by_length or not algorithm:
            return 0
        # H7 (round 2): preflight algorithm availability AFTER load and BEFORE scan.
        # If manifest declares blake3-keyed but blake3 isn't importable, surface
        # explicitly — used to be a silent FN inside the scan loop (P4 violation).
        if not algorithm_available(algorithm):
            hook_log(f"algorithm_unavailable {algorithm}; scan SKIPPED")
            emit_generic_context(
                f"⚠️  credential_scrub: manifest declares '{algorithm}' but runtime cannot compute it. "
                "Scan SKIPPED for this tool call. Install the algorithm's deps or rebuild manifests with "
                "--algorithm sha256-hmac. See ~/.claude/state/hook_logs/hooks.log."
            )
            return 0
        hits = scan_output(output_bytes, by_length, salt, algorithm)
        if not hits:
            return 0
        literals = [w for w, _ in hits]
        replaced = redact_jsonl(transcript_path, literals)
    except Exception as e:
        # I4: any uncaught exception → fail-safe + hook_log (no value / hash leak)
        hook_log(f"scan/redact exception {type(e).__name__}: trace suppressed")
        # Best-effort: also avoid emitting tb to stderr (default behavior would dump locals on -v)
        return 0

    if replaced == 0:
        # Detected in tool_output but not present in jsonl text form (e.g., already
        # redacted by earlier hook pass, or JSON-encoding boundary). Log + warn.
        hook_log(f"detected_but_not_in_jsonl matches={len(hits)} replaced=0")
        emit_generic_context(
            "⚠️  credential_scrub: candidate credential matches found in tool output but "
            "did not appear in transcript record fields. Review tool call and plan rotation."
        )
        return 0

    # I6: generic context — count only, no key names, no precise lengths
    hook_log(f"scrubbed matches={len(hits)} replaced={replaced}")
    # Step 2: file/append the incident issue (detached, fail-safe). key_names are
    # non-sensitive identifiers from the manifest (e.g. ANTHROPIC_API_KEY), never values.
    key_names = sorted({k for _, names in hits for k in names})
    spawn_leak_followup(key_names, replaced, str(transcript_path),
                        session_id_from_raw(raw_input))
    # Step 3: resume context — keep working, the leak is already neutralized + logged.
    emit_generic_context(resume_context(replaced))
    return 0


if __name__ == "__main__":
    # I4 catch-all — never propagate to parent shell with non-zero.
    # Exclude SystemExit / KeyboardInterrupt from suppression (= they are control flow,
    # not bugs; SystemExit(0) from main() must propagate cleanly).
    try:
        sys.exit(main())
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException:
        try:
            hook_log("top_level_exception suppressed")
        except Exception:
            pass
        sys.exit(0)
