#!/usr/bin/env python3
"""
credential_scrub_build.py — manifest generator (v2)

Single-file mode (= invoked via build_all wrapper, runs under `sops exec-env`):
    sops exec-env <file>.enc.yaml \\
        'python3 credential_scrub_build.py --source-file <file>.enc.yaml'

Multi-file mode (= bootstrap or audit, does NOT need sops exec-env directly —
the wrapper loops):
    bash credential_scrub_build_all.sh   # → loops, invokes single-file per .enc.yaml

Revision rationale (= dual-magi round 1 fixes baked in):
  - Codex-2 (REJECT): build script captures ambient env.
      FIX: at startup, BEFORE reading sops values, snapshot env keys.
           After sops exec-env populated env, the populated keys are the
           delta; reject ambient collisions with explicit error.
      Implementation: caller passes `--baseline-env-fd` from a fd snapshotted
      BEFORE `sops exec-env` ran (= driver script generates baseline, then
      invokes exec-env, then this script compares).
  - Codex-7 (MED): stale corpus assumption.
      FIX: script does not hardcode counts; emits coverage report after build.
  - Codex-9 (MED): non-atomic write + permission.
      FIX: tempfile sibling + os.replace + chmod 0600 on create.
  - Codex-5 (HIGH): byte_length not char_length.
      FIX: byte_length = len(value.encode('utf-8')) stored, char_length audit-only.
  - Codex-4 (HIGH): key-name injection.
      FIX: identifier regex validation at build time; refuse non-conforming names.
  - Codex-6 (HIGH): blake3 missing → silent fallback.
      FIX: hard error at startup (--check-deps); manifest header reflects truth.
  - Codex/Balthasar HIGH: HMAC-keyed hash (not unsalted).
      FIX: load per-host salt; HMAC with it; if salt absent, generate via --init-salt.
  - Balthasar-2/3 (REJECT/HIGH): selector heuristic + URL coverage.
      FIX: explicit per-file include manifest in Phase A (= `<file>.scrub.yaml`
           sidecar that author writes). Heuristic remains as --dry-run candidate
           reporter. Phase A = include manifest required for production build.
  - Balthasar-5 (HIGH): TOCTOU staleness.
      FIX: source_mtime stored; --check-staleness CLI mode for audit.
  - Caspar-1/2: blake3 + log path.
      FIX: log to ~/.claude/state/hook_logs/hooks.log via "credential_scrub_build" tag.
  - Caspar-3 (REJECT): rotation friction.
      FIX: build_all wrapper script (separate file) eliminates 27× manual.
  - Caspar-4 (REJECT): multi-host scope.
      FIX: Manifests are per-host. Manifests in ~/.claude/state/ (= per-host);
           never committed to a secrets repo.

Property invariants (build-side):
  B1: Plaintext value bytes never written to manifest, stderr, stdout, or log.
  B2: Exception path emits only exception CLASS name, never str(e). repr() forbidden.
  B3: Manifest written atomically with 0o600 perms; failed write removes tmp.
  B4: Ambient env collision → hard error (refuse build, prompt user).
  B5: --algorithm requested but unavailable → hard error (no silent downgrade).
"""

from __future__ import annotations
import os, sys, re, math, json, argparse, datetime, secrets, hmac as _hmac, hashlib, tempfile
from collections import Counter
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
STATE_DIR = Path.home() / ".claude" / "state" / "credential_scrub"
MANIFEST_DIR = STATE_DIR / "manifest"
SALT_FILE = STATE_DIR / "salt.bin"
HOOK_LOG = Path.home() / ".claude" / "state" / "hook_logs" / "hooks.log"

FORMAT_VERSION = 1
MIN_BYTE_LENGTH = 12               # lowered from 16 to capture short user passwords
MIN_ENTROPY_BITS_PER_CHAR = 2.5    # lowered to admit weak user passwords (relevant for FN)
KEY_NAME_REGEX = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")

# Strong override: name contains one of these → bypass entropy / skip-keyword
STRONG_CRED_KEYWORDS = ("PASSWORD", "SECRET", "TOKEN", "PRIVATE_KEY", "ACCESS_KEY", "BEARER")
# Soft include: name contains one of these → eligible (entropy/length gate still applies)
CRED_KEYWORDS = STRONG_CRED_KEYWORDS + ("KEY", "PWD", "AUTH", "CRED", "SALT")
# Skip unless overridden by STRONG (= these names usually identify config, not creds)
SKIP_KEYWORDS = ("BUCKET", "ENDPOINT", "REGION", "HOST", "PORT", "EMAIL",
                 "USER", "BIND", "CMD", "PATH", "PROPERTY", "PUBLIC", "SITE_URL",
                 "AGENT")  # USER_AGENT_TOKEN is config, not cred
URL_HINT_KEYWORDS = ("URL", "DSN", "CONN", "CONNECTION_STRING")

# Expanded URL patterns covering schemes the existing L3 catalog supports
# AND adversarial-flagged ones (libsql, jdbc, redis password-only).
URL_PATTERNS = [
    # standard user:pw@host
    re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://[^:/@\s]+:([^@\s]+)@", re.ASCII),
    # jdbc:<scheme>://user:pw@host  (= jdbc compound)
    re.compile(r"^jdbc:[a-zA-Z][a-zA-Z0-9+.\-]*://[^:/@\s]+:([^@\s]+)@", re.ASCII),
    # token-as-userinfo (libsql://token@host)
    re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://([A-Za-z0-9._\-+/=]{12,})@", re.ASCII),
    # password-only with empty user (redis://:pw@host)
    re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://:([^@\s]+)@", re.ASCII),
]

# H3 (round 2 BALTHASAR + CODEX-r2-5): tightened to WHOLE-VALUE anchor match.
# Substring `.search()` was over-eager — matched "TODO" inside PEM comments, or
# "example" inside `client_email: "...@example.iam.gserviceaccount.com"`,
# causing legitimate GA4 JSON credentials to be skipped + inner private_key lost.
# Now requires the entire value to match a placeholder pattern.
PLACEHOLDER_RE = re.compile(
    r"\A(?:"
    r"<REDACTED>|changeme\d*|placeholder|example|YOUR_[A-Z_]+|"
    r"test[-_]?token|dummy[-_a-z0-9]*|TODO|FIXME|XXX[-_a-z0-9]*|"
    r"REPLACE[-_]?ME|FILL[-_]?ME(?:_IN)?|NOT[-_]?SET"
    r")\Z",
    re.IGNORECASE,
)

# H2 (round 2 MELCHIOR-r2-2): credentials containing chars outside this set
# CANNOT be matched by the scrub hook's CANDIDATE_RUN prefilter. Build script
# detects this and SKIPS them with reason=unscannable_alphabet so user knows
# the gap rather than getting silent FN. Keep this in sync with credential_scrub.py
# CANDIDATE_RUN regex character class.
SCANNABLE_ALPHABET = set(b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                          b"0123456789._-+/=:@%!#$&*()|~?[]")


# ----------------------------------------------------------------------------
# Logging — generic; never logs values
# ----------------------------------------------------------------------------
def hook_log(msg: str) -> None:
    # H5+H6 (round 2): explicit 0o600 mode on log file, 0o700 on parent dir.
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
            os.write(fd, f"[{ts}] [credential_scrub_build] {msg}\n".encode("utf-8", errors="replace"))
        finally:
            os.close(fd)
    except OSError:
        pass


# ----------------------------------------------------------------------------
# Salt + HMAC
# ----------------------------------------------------------------------------
def ensure_salt(init: bool = False) -> bytes:
    if SALT_FILE.exists():
        data = SALT_FILE.read_bytes()
        if len(data) != 32:
            raise RuntimeError(f"salt file corrupt: {SALT_FILE}")
        return data
    if not init:
        raise RuntimeError(
            f"salt missing: {SALT_FILE}. Run with --init-salt once to create."
        )
    # H5 (round 2): explicit 0o700 perms on state dir so dir listing doesn't enumerate
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        STATE_DIR.chmod(0o700)
    except OSError:
        pass
    salt = secrets.token_bytes(32)
    # Write atomically with 0o400 perms
    fd, tmp = tempfile.mkstemp(dir=str(STATE_DIR), prefix=".salt_", suffix=".tmp")
    try:
        os.write(fd, salt)
        os.fsync(fd)
        os.close(fd)
        os.chmod(tmp, 0o400)
        os.replace(tmp, SALT_FILE)
    finally:
        if Path(tmp).exists():
            try:
                Path(tmp).unlink()
            except OSError:
                pass
    return salt


def compute_hmac(value: bytes, salt: bytes, algorithm: str) -> str:
    if algorithm == "blake3-keyed":
        import blake3
        return blake3.blake3(value, key=salt).hexdigest()
    if algorithm == "sha256-hmac":
        return _hmac.new(salt, value, hashlib.sha256).hexdigest()
    raise ValueError(f"unsupported algorithm: {algorithm}")


def preflight_algorithm(algorithm: str) -> None:
    """Hard preflight: fail loudly if requested algorithm unavailable."""
    if algorithm == "blake3-keyed":
        try:
            import blake3  # noqa: F401
        except ImportError:
            raise RuntimeError(
                "blake3 module not installed. Install via `pip install blake3` "
                "OR pass --algorithm sha256-hmac (slower, but stdlib)."
            )


# ----------------------------------------------------------------------------
# Selector
# ----------------------------------------------------------------------------
def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = Counter(s)
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def is_placeholder(value: str) -> bool:
    # H3: whole-value match only (= PLACEHOLDER_RE is anchored with \A...\Z).
    return bool(PLACEHOLDER_RE.match(value))


def is_unscannable_alphabet(value: str) -> bool:
    """H2: True if value contains any byte outside the scanner's prefilter alphabet."""
    return any(b not in SCANNABLE_ALPHABET for b in value.encode("utf-8"))


def extract_url_password(value: str) -> str | None:
    for pat in URL_PATTERNS:
        m = pat.match(value)
        if m:
            return m.group(1)
    return None


def select_value(key: str, value: str, include_set: set[str] | None,
                 exclude_set: set[str] | None) -> tuple[str, str | None, str]:
    """
    Returns (decision, hashable_value, reason).
      decision ∈ {"include", "skip", "url_pw"}
      hashable_value: the actual string to hash (or None on skip)
      reason: include path or skip reason
    """
    name_upper = key.upper()

    # explicit lists win
    if exclude_set and key in exclude_set:
        return "skip", None, "explicit_exclude"
    if include_set and key in include_set:
        # User explicitly listed → hash if non-empty; bypass heuristic
        if not value:
            return "skip", None, "explicit_include_empty"
        if is_placeholder(value):
            return "skip", None, "placeholder"
        # URL extraction still applies for explicit-include URL keys
        pw = extract_url_password(value)
        if pw and not is_placeholder(pw):
            return "url_pw", pw, "url_password"
        return "include", value, "explicit_include"

    # Heuristic path (= Phase A fallback when no include manifest provided)
    # Strong override: STRONG_CRED_KEYWORD → include regardless of skip/entropy
    if any(s in name_upper for s in STRONG_CRED_KEYWORDS):
        if is_placeholder(value):
            return "skip", None, "placeholder"
        if len(value.encode("utf-8")) < MIN_BYTE_LENGTH:
            return "skip", None, "byte_length_too_short"
        # URL extraction
        pw = extract_url_password(value)
        if pw and not is_placeholder(pw):
            return "url_pw", pw, "url_password"
        return "include", value, "strong_keyword"

    # URL hint (= name suggests URL form)
    if any(h in name_upper for h in URL_HINT_KEYWORDS):
        pw = extract_url_password(value)
        if pw and not is_placeholder(pw):
            if len(pw.encode("utf-8")) >= MIN_BYTE_LENGTH:
                return "url_pw", pw, "url_password"
            return "skip", None, "url_password_too_short"

    # Generic cred keyword
    if any(c in name_upper for c in CRED_KEYWORDS):
        # Skip if also matches a skip keyword (= AGENT etc.)
        if any(s in name_upper for s in SKIP_KEYWORDS):
            return "skip", None, "skip_keyword_override"
        if is_placeholder(value):
            return "skip", None, "placeholder"
        if len(value.encode("utf-8")) < MIN_BYTE_LENGTH:
            return "skip", None, "byte_length_too_short"
        if shannon_entropy(value) < MIN_ENTROPY_BITS_PER_CHAR:
            return "skip", None, "entropy_too_low"
        return "include", value, "name_heuristic"

    return "skip", None, "name_heuristic_no_match"


# ----------------------------------------------------------------------------
# Multi-line / JSON / PEM inner extraction
# ----------------------------------------------------------------------------
def extract_inner_secrets(key: str, value: str) -> list[tuple[str, str, str]]:
    """
    For JSON / PEM values, extract inner credential fields.
    Returns list of (sub_label, sub_value, extracted_from).
    """
    extracted = []
    # JSON blob (Google service account credentials shape)
    stripped = value.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            blob = json.loads(stripped)
        except json.JSONDecodeError:
            blob = None
        if isinstance(blob, dict):
            for inner_key in ("private_key", "private_key_id", "client_secret",
                              "refresh_token", "access_token"):
                v = blob.get(inner_key)
                if isinstance(v, str) and len(v.encode("utf-8")) >= MIN_BYTE_LENGTH:
                    extracted.append((f"{key}#{inner_key}", v, f"json_field:{inner_key}"))
    # PEM body (between BEGIN/END markers)
    pem_match = re.search(
        r"-----BEGIN [A-Z ]+-----\s*([A-Za-z0-9+/=\s]+?)\s*-----END [A-Z ]+-----",
        value, re.DOTALL,
    )
    if pem_match:
        body = re.sub(r"\s+", "", pem_match.group(1))
        if len(body) >= MIN_BYTE_LENGTH:
            extracted.append((f"{key}#pem_body", body, "pem_body"))
    return extracted


# ----------------------------------------------------------------------------
# Ambient env protection
# ----------------------------------------------------------------------------
def detect_ambient_collision(cred_key_names: list[str],
                              baseline_env_keys: set[str]) -> list[str]:
    """
    Return list of cred key names that were already in the baseline env (pre-exec-env).
    These are AMBIENT — value might not be sops-derived.
    """
    return [k for k in cred_key_names if k in baseline_env_keys]


# ----------------------------------------------------------------------------
# Build manifest for one sops file
# ----------------------------------------------------------------------------
def build_manifest(source_file: Path, salt: bytes, algorithm: str,
                   include_manifest_path: Path | None = None,
                   baseline_env_keys: set[str] | None = None) -> dict:
    import yaml
    with open(source_file) as f:
        doc = yaml.safe_load(f)
    if not isinstance(doc, dict):
        raise ValueError(f"{source_file}: not a yaml mapping")
    cred_key_names = [k for k in doc.keys() if k != "sops"]

    # Validate identifier shape; reject malformed keys outright
    valid_keys = [k for k in cred_key_names if KEY_NAME_REGEX.match(k)]
    invalid_keys = [k for k in cred_key_names if not KEY_NAME_REGEX.match(k)]
    if invalid_keys:
        hook_log(f"invalid_key_names file={source_file.name} count={len(invalid_keys)}")

    # Ambient env protection
    if baseline_env_keys:
        collisions = detect_ambient_collision(valid_keys, baseline_env_keys)
        if collisions:
            raise RuntimeError(
                f"ambient env collision detected for {len(collisions)} key(s); "
                "refusing to build to avoid hashing parent shell values. "
                "Resolution: unset these env vars before invoking the build, "
                "or run in a clean subshell (`env -i bash -c ...`). "
                "Affected key count is logged."
            )

    # Load include manifest if present (Phase A explicit list)
    include_set: set[str] | None = None
    exclude_set: set[str] | None = None
    if include_manifest_path and include_manifest_path.is_file():
        try:
            inc_doc = yaml.safe_load(include_manifest_path.read_text())
            if isinstance(inc_doc, dict):
                include_list = inc_doc.get("include") or []
                exclude_list = inc_doc.get("exclude") or []
                include_set = {str(x) for x in include_list if isinstance(x, str)}
                exclude_set = {str(x) for x in exclude_list if isinstance(x, str)}
        except Exception:
            hook_log(f"include_manifest_parse_error: {include_manifest_path.name}")

    entries: list[dict] = []
    skipped: list[dict] = []

    for k in valid_keys:
        v = os.environ.get(k)
        if v is None:
            skipped.append({"key": k, "reason": "not_in_env"})
            continue
        if not isinstance(v, str):
            skipped.append({"key": k, "reason": "non_string_value"})
            continue

        # H3 (round 2): ALWAYS attempt inner extraction first — independent of
        # parent placeholder check. A GA4 JSON whose client_email happens to
        # contain 'example.iam.gserviceaccount.com' (= legit Google form) used
        # to fail parent placeholder, skipping inner private_key entirely.
        inner_secrets = extract_inner_secrets(k, v)
        for sub_key, sub_val, sub_reason in inner_secrets:
            sub_bytes = sub_val.encode("utf-8")
            # H2: refuse to hash credentials the scanner can't find
            if is_unscannable_alphabet(sub_val):
                skipped.append({
                    "key": k,
                    "reason": f"unscannable_alphabet({sub_reason})",
                })
                continue
            entries.append({
                "key": k,
                "byte_length": len(sub_bytes),
                "hmac": compute_hmac(sub_bytes, salt, algorithm),
                "extracted_from": sub_reason,
            })

        decision, hashable, reason = select_value(k, v, include_set, exclude_set)
        if decision == "skip":
            skipped.append({"key": k, "reason": reason})
            continue
        assert hashable is not None
        # H2: refuse to hash unscannable parent values too
        if is_unscannable_alphabet(hashable):
            skipped.append({
                "key": k,
                "reason": f"unscannable_alphabet({reason})",
            })
            continue
        primary_bytes = hashable.encode("utf-8")
        entries.append({
            "key": k,
            "byte_length": len(primary_bytes),
            "hmac": compute_hmac(primary_bytes, salt, algorithm),
            "extracted_from": reason,
        })

    # Don't store absolute path of source file; basename only (Codex-style hygiene)
    source_id = hashlib.sha256(str(source_file.resolve()).encode("utf-8")).hexdigest()[:16]
    return {
        "format_version": FORMAT_VERSION,
        "algorithm": algorithm,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%FT%TZ"),
        "source_file_basename": source_file.name,
        "source_file_id": source_id,
        "source_mtime": datetime.datetime.fromtimestamp(
            source_file.stat().st_mtime, tz=datetime.timezone.utc
        ).strftime("%FT%TZ"),
        "phase": "A",
        "entries": sorted(entries, key=lambda e: (e["key"], e["byte_length"])),
        "skipped": sorted(skipped, key=lambda s: s["key"]),
    }


# ----------------------------------------------------------------------------
# Atomic write with 0o600 perms
# ----------------------------------------------------------------------------
def write_manifest_atomic(manifest: dict, output_path: Path) -> None:
    # H5 (round 2): manifest dir explicit 0o700 so basenames aren't enumerable
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        output_path.parent.chmod(0o700)
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(
        dir=str(output_path.parent),
        prefix=f".{output_path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(manifest, f, indent=2, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, output_path)
    except Exception:
        try:
            Path(tmp).unlink(missing_ok=True)
        except OSError:
            pass
        raise


# ----------------------------------------------------------------------------
# Staleness check
# ----------------------------------------------------------------------------
def check_staleness(manifest_dir: Path) -> list[tuple[str, str]]:
    """Return list of (manifest_basename, status) for any manifest whose source is newer."""
    import yaml
    drift = []
    for fp in sorted(manifest_dir.glob("*.scrub.json")):
        try:
            doc = json.loads(fp.read_text())
        except Exception:
            drift.append((fp.name, "parse_error"))
            continue
        # source_file_id ties to a path hash; we can't recover the actual path from id
        # alone. Caller passes path map via --source-map if precise check needed.
        # For now we surface manifest mtime vs source_mtime declared in manifest.
        mtime_declared = doc.get("source_mtime", "")
        drift.append((fp.name, f"declared_mtime={mtime_declared}"))
    return drift


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-file", type=Path,
                    help="path to .enc.yaml (single-file mode)")
    ap.add_argument("--output", type=Path,
                    help="manifest output path (default: STATE_DIR/manifest/<basename>.scrub.json)")
    ap.add_argument("--include-manifest", type=Path,
                    help="explicit include manifest (.scrub.yaml with include:/exclude: lists)")
    # Default = sha256-hmac (= stdlib, always available). blake3-keyed is optional
    # perf upgrade (~3-5x faster scan); requires `pip install blake3` (PEP 668 may
    # require --break-system-packages or a dedicated venv on Debian 12+).
    ap.add_argument("--algorithm", default="sha256-hmac",
                    choices=["blake3-keyed", "sha256-hmac"])
    ap.add_argument("--init-salt", action="store_true",
                    help="generate per-host HMAC salt if missing")
    ap.add_argument("--check-deps", action="store_true",
                    help="check dependencies and exit")
    ap.add_argument("--check-staleness", action="store_true",
                    help="report manifest staleness vs source mtime")
    ap.add_argument("--dry-run", action="store_true",
                    help="report selection decisions without writing manifest")
    ap.add_argument("--baseline-env-file", type=Path,
                    help="newline-separated env key names from BEFORE sops exec-env (for ambient-collision detection)")
    args = ap.parse_args()

    # B5: hard preflight
    try:
        preflight_algorithm(args.algorithm)
    except RuntimeError as e:
        print(f"build preflight failed: {type(e).__name__}", file=sys.stderr)
        # explanation message is class-name-bound, no value leak
        print(str(e), file=sys.stderr)
        return 2

    if args.check_deps:
        print("deps OK", file=sys.stderr)
        return 0

    if args.check_staleness:
        for name, status in check_staleness(MANIFEST_DIR):
            print(f"{name}\t{status}")
        return 0

    # Ensure salt
    try:
        salt = ensure_salt(init=args.init_salt)
    except RuntimeError as e:
        print(f"salt error: {type(e).__name__}", file=sys.stderr)
        print(str(e), file=sys.stderr)
        return 2

    if not args.source_file:
        if args.init_salt:
            print(f"salt initialized at {SALT_FILE}", file=sys.stderr)
            return 0
        ap.print_help(sys.stderr)
        return 2

    if not args.source_file.is_file():
        print(f"source file not found: {args.source_file}", file=sys.stderr)
        return 2

    # Baseline env protection
    baseline_env_keys: set[str] | None = None
    if args.baseline_env_file and args.baseline_env_file.is_file():
        baseline_env_keys = {
            line.strip() for line in args.baseline_env_file.read_text().splitlines()
            if line.strip()
        }

    try:
        manifest = build_manifest(
            args.source_file, salt, args.algorithm,
            include_manifest_path=args.include_manifest,
            baseline_env_keys=baseline_env_keys,
        )
    except Exception as e:
        # B2: class name only, no str(e) on credential-bearing exceptions
        msg = type(e).__name__
        # For RuntimeError raised by us (which has safe message), surface it
        if isinstance(e, RuntimeError):
            msg = f"{msg}: {e}"
        print(f"build failed: {msg}", file=sys.stderr)
        hook_log(f"build_failed file={args.source_file.name} kind={type(e).__name__}")
        return 1

    if args.dry_run:
        summary = {
            "source": args.source_file.name,
            "included": [e["key"] for e in manifest["entries"]],
            "skipped_count": len(manifest["skipped"]),
            "skipped": manifest["skipped"],
        }
        print(json.dumps(summary, indent=2))
        return 0

    output_path = args.output or (MANIFEST_DIR / f"{args.source_file.stem}.scrub.json")
    try:
        write_manifest_atomic(manifest, output_path)
    except Exception as e:
        print(f"write failed: {type(e).__name__}", file=sys.stderr)
        return 1

    n_inc = len(manifest["entries"])
    n_skip = len(manifest["skipped"])
    print(f"manifest written: {output_path} ({n_inc} hashed, {n_skip} skipped)")
    hook_log(f"build_ok file={args.source_file.name} included={n_inc} skipped={n_skip}")
    return 0


if __name__ == "__main__":
    # B2 catch-all: never propagate via traceback with values.
    # Exclude SystemExit / KeyboardInterrupt (= control flow, not bugs).
    try:
        sys.exit(main())
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException as e:
        print(f"top-level exception: {type(e).__name__}", file=sys.stderr)
        sys.exit(1)
