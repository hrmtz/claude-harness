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
  I5: Output is ALWAYS scanned — never fail-open by hard-skipping on size (#39).
      The candidate-run prefilter + a bounded global HMAC budget (MAX_SCAN_WINDOWS)
      + a wall-clock soft-deadline (MAX_SCAN_SECONDS) keep cost finite. Delimited
      (sub-cap) runs are scanned first (best-effort: this ORDERING means ordinary
      creds are reached before giant blobs, but it is NOT a guarantee — if pass 1
      itself exhausts the budget/deadline, later sub-cap runs are skipped too);
      rare giant delimiter-free blobs are scanned within the remaining budget, else
      best-effort head+tail. Whenever full coverage is not achieved the scan is
      reported INCOMPLETE (scan_complete=False) and the caller warns honestly — it
      NEVER claims auto-handling for regions that were not checked, and an incomplete
      scan that DID redact still appends a manual-review caveat.
  I6: All emit_context output is GENERIC (no key names, no precise lengths).
"""

from __future__ import annotations
import os, sys, json, re, datetime, time, hmac as _hmac, hashlib, traceback, subprocess, shutil
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
STATE_DIR = Path.home() / ".claude" / "state" / "credential_scrub"
MANIFEST_DIR = STATE_DIR / "manifest"
SALT_FILE = STATE_DIR / "salt.bin"
HOOK_LOG = Path.home() / ".claude" / "state" / "hook_logs" / "hooks.log"

# NOTE (#39): MAX_SCAN_BYTES is NO LONGER a hard skip. It is retained only as the
# "this output is very large" threshold used to phrase the incomplete-scan warning.
# Scanning is now always attempted (bounded by MAX_SCAN_WINDOWS), so an oversized
# log / JSON / base64 blob no longer fails OPEN (warn-but-don't-redact).
MAX_SCAN_BYTES = 256_000
# Per-candidate-run length boundary. scan_output is O(run_len × distinct_lengths)
# sliding-window HMAC; a single multi-hundred-KB delimiter-free run (e.g. a SQL BYTEA
# dump returned by an MCP tool) is the pathological case. The build validates every
# manifest byte_length to 12..4096 (load rejects >4096), so the LARGEST possible known
# secret is 4096 bytes. Runs at/under this length are treated as ordinary "delimited"
# windows and scanned FULLY first (pass 1). Runs longer than this are giant blobs
# scanned in pass 2: fully if the remaining global budget covers them, otherwise
# best-effort head+tail windows of this size. The tail window start is backed off by
# (longest known secret − 1) bytes so a max-length secret whose last byte lands at the
# very start of the tail region — and therefore begins BEFORE run_len−MAX_CANDIDATE_RUN
# — is still hashed (boundary-straddle fix). A pass-2 partial scan is flagged
# INCOMPLETE. This replaces the old hard-skip-the-whole-run behavior (#39), which
# fail-OPENED exactly on the blobs most likely to carry a leak.
MAX_CANDIDATE_RUN = 4096
# Global HMAC budget for one main() invocation. At ~0.6M stdlib HMACs/s this bounds
# worst-case scan walltime to a few seconds so the hook never hits its timeout →
# SIGKILL → silent fail-open. Sub-cap (delimited) runs are scanned BEFORE any giant
# blob, so an ordinary API key is *reached first*. This ordering is best-effort, NOT a
# guarantee: if the budget (or the wall-clock deadline) is exhausted while still in
# pass 1, later sub-cap runs are left unscanned and scan_complete becomes False — the
# caller then warns honestly instead of implying full coverage. Tunable.
MAX_SCAN_WINDOWS = 2_000_000
# Wall-clock soft-deadline (seconds) for one scan_output call. Independent of the
# window budget: MAX_SCAN_WINDOWS bounds COUNT, this bounds TIME so a slow box /
# blake3 import / GC pause can't push the scan past the hook timeout → SIGKILL →
# silent fail-open. When the deadline trips mid-scan the scan stops and reports
# scan_complete=False (best-effort coverage), never unbounded work.
MAX_SCAN_SECONDS = 8.0
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
def _run_windows(run_len: int, lengths: list[int]) -> int:
    """Number of sliding-window start positions summed over all known lengths
    that fit in a run of run_len bytes. Used to decide whether a giant run can be
    scanned fully within the remaining budget. `lengths` must be ascending."""
    total = 0
    for L in lengths:
        if L > run_len:
            break
        total += run_len - L + 1
    return total


def scan_output(output_bytes: bytes,
                by_length: dict[int, dict[str, list[str]]],
                salt: bytes,
                algorithm: str) -> tuple[list[tuple[bytes, list[str]]], bool]:
    """Bounded, no-fail-open scan (#39).

    Returns (hits, scan_complete) where:
      hits          = list of (matched_literal_bytes, [key_names]) without duplicates.
      scan_complete = True iff every candidate region was scanned for every known
                      length. False means the global HMAC budget was exhausted or a
                      giant delimiter-free run only got best-effort head+tail coverage;
                      the caller MUST warn honestly rather than imply auto-handling.

    Assumes algorithm_available(algorithm) was checked by caller — see main().
    Preserves the existing HMAC known-secret matching logic (sliding byte windows
    sized by manifest byte_length, compared via compute_hmac); only the scheduling
    around it changed so all lengths/sizes are scanned within a finite budget."""
    hits: dict[bytes, list[str]] = {}
    if not by_length or not output_bytes:
        return [], True
    lengths = sorted(by_length.keys())
    max_len = lengths[-1]         # longest known secret (always <= MAX_CANDIDATE_RUN)
    budget = [MAX_SCAN_WINDOWS]   # boxed so the nested scanner can mutate it
    deadline = time.monotonic() + MAX_SCAN_SECONDS
    complete = True

    def scan_span(run: bytes, start: int, stop: int) -> bool:
        """Slide every known length over run, hashing windows whose START index is
        in [start, stop). Returns False if the global window budget OR the wall-clock
        soft-deadline ran out (so the caller can flag the scan incomplete). The
        per-window HMAC match logic is unchanged; budget/deadline accounting is charged
        for EVERY window iteration (before the duplicate-hit short-circuit) so a run of
        repeated already-matched windows can't bypass both bounds and run unbounded —
        slicing + the `in hits` lookup are themselves the cost in that pathological case
        (codex re-review MED: dedup `continue` previously skipped budget+deadline)."""
        rl = len(run)
        for L in lengths:
            if L > rl:
                break
            hash_lookup = by_length[L]
            last = min(stop, rl - L + 1)
            i = start if start > 0 else 0
            while i < last:
                if budget[0] <= 0:
                    return False
                budget[0] -= 1
                # Wall-clock guard: sample time every 8192 iterations (amortizes the
                # monotonic() call) so a slow runtime can't push past the hook timeout.
                if budget[0] % 8192 == 0 and time.monotonic() > deadline:
                    return False
                window = run[i:i+L]
                i += 1
                if window in hits:
                    continue
                h = compute_hmac(window, salt, algorithm)
                keys = hash_lookup.get(h)
                if keys:
                    hits[window] = list(keys)
        return True

    # Pass 1 — scan all sub-cap (delimited) runs first. These are the cheap,
    # high-signal windows where ordinary credentials live (quoted/comma-delimited
    # tokens in JSON, logs, env dumps). Scanning them BEFORE any giant blob means an
    # ordinary API key is *reached first*; this is best-effort ordering, NOT a
    # guarantee — if pass 1 itself exhausts the budget or trips the deadline, the
    # remaining sub-cap runs are skipped and complete becomes False (signalled to the
    # caller via scan_complete).
    oversized: list[bytes] = []
    for m in CANDIDATE_RUN.finditer(output_bytes):
        run = m.group()
        if len(run) > MAX_CANDIDATE_RUN:
            oversized.append(run)
            continue
        if not scan_span(run, 0, len(run)):
            complete = False

    # Pass 2 — giant contiguous in-class runs (rare: a SQL BYTEA / PEM body / base64
    # blob with no delimiters). Scan fully when the remaining budget covers it; else
    # best-effort head+tail windows and mark INCOMPLETE so the caller warns honestly
    # instead of failing OPEN silently (the #39 hole).
    for run in oversized:
        run_len = len(run)
        if budget[0] <= 0:
            complete = False
            break
        if _run_windows(run_len, lengths) <= budget[0]:
            if not scan_span(run, 0, run_len):
                complete = False
        else:
            complete = False
            head_stop = MAX_CANDIDATE_RUN
            # Back the tail window START off by (max_len - 1) bytes: a max-length
            # secret whose LAST byte lands at the first byte of the tail region begins
            # at (run_len - MAX_CANDIDATE_RUN) - (max_len - 1), i.e. BEFORE
            # run_len - MAX_CANDIDATE_RUN. Starting the tail slide there ensures any
            # secret with at least one byte in the last MAX_CANDIDATE_RUN bytes is
            # hashed (boundary-straddle fix). Clamp to head_stop so a barely-oversized
            # run does not re-scan / underflow.
            tail_start = run_len - MAX_CANDIDATE_RUN - (max_len - 1)
            if tail_start < head_stop:
                tail_start = head_stop
            scan_span(run, 0, head_stop)
            scan_span(run, tail_start, run_len)

    if not complete:
        # Count-only (no values): surfaces the bounded-coverage event in the log so
        # an oversized-output partial scan is observable rather than silent.
        hook_log(f"scan_incomplete budget_remaining={budget[0]} oversized_runs={len(oversized)}")
    return list(hits.items()), complete


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


def resume_context(replaced: int, scan_complete: bool = True) -> str:
    """Step 3: terse 'keep working' context. The matched leak is already sanitized +
    queued for incident logging.

    When scan_complete is True the whole output was checked, so Claude can resume with
    no manual steps. When scan_complete is False the redaction is real but coverage was
    PARTIAL (oversized output, budget/deadline exhausted) — the message must NOT imply
    the output was fully auto-handled, so it drops the "no manual steps needed" wording
    and asks for manual review (MED finding: an incomplete-but-redacted scan previously
    routed through the all-clear wording, contradicting the manual-review caveat)."""
    ref = ""
    try:
        last = (STATE_DIR / "last_issue").read_text().strip()
        if last:
            ref = f" (tracked in claude-harness#{last})"
    except OSError:
        pass
    base = (
        f"⚠️  credential leak auto-handled: transcript sanitized ({replaced} replacement(s)) "
        f"+ incident logged to the claude-harness `credential-leak` issue{ref}. "
    )
    if scan_complete:
        return base + (
            "No manual steps needed — continue your current task. "
            "Rotation for the affected credential is tracked in that issue."
        )
    return base + (
        "NOTE: the output was very large and the scan was INCOMPLETE — the matched "
        "credential(s) were redacted, but some regions were NOT checked. Manual review "
        "IS needed: inspect the tool output for other secrets before continuing. "
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

    # I5 (#39): NO hard skip on size. Always scan; the candidate-run prefilter and
    # the MAX_SCAN_WINDOWS budget keep cost finite, so a large output no longer
    # fails OPEN (warn-but-don't-redact).
    output_bytes = tool_output.encode("utf-8", errors="surrogateescape")

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
        hits, scan_complete = scan_output(output_bytes, by_length, salt, algorithm)
        if not hits:
            if not scan_complete:
                # #39: oversized output, budget exhausted before full coverage. Nothing
                # matched the SCANNED regions, but unscanned regions were not checked.
                # Warn honestly — do NOT imply auto-handling (no redaction occurred).
                hook_log(f"scan_incomplete no_match bytes={len(output_bytes)}")
                emit_generic_context(
                    "⚠️  credential_scrub: tool output was very large; the scan hit its "
                    "compute budget and was INCOMPLETE. No known credential matched the "
                    "scanned regions, but some regions were NOT checked. No redaction was "
                    "performed. Review manually + consider rotation if sensitive data may "
                    "be present."
                )
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
    # Step 3: resume context — keep working, the matched leak is already neutralized +
    # logged. Auto-handling ("no manual steps needed") is claimed ONLY when the scan was
    # complete; an incomplete scan that still redacted gets the manual-review wording
    # baked into resume_context itself, so the all-clear phrasing never contradicts the
    # caveat (#39 MED).
    emit_generic_context(resume_context(replaced, scan_complete))
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
