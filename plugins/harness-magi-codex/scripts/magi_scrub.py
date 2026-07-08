#!/usr/bin/env python3
"""magi_scrub.py — redact credential-shaped substrings before anything is written to disk.

Design: docs/designs/CODEX_MAGI_MIRROR.md §4.2 (INV-5).

A dual-magi round found the leak path this exists to close: a reviewer runs
`psql 'postgres://user:PASSWORD@host/db'`, the command string lands verbatim in
verify_commands_executed, that JSON is fed into the next round's prompt, and the
credential is re-transmitted to another vendor's API. The default allowlist now has
no DB tools at all, but doc text and reviewer quotes remain a path, so scrub anyway.

Usage:
    magi_scrub.py < in.json > out.json     # scrub a JSON document (structure preserved)
    magi_scrub.py --text < in.txt > out.txt
"""
import json
import re
import sys

REDACTED = "«REDACTED»"

PATTERNS = [
    # DSN userinfo: scheme://user:secret@host  -> keep scheme/user/host shape
    (re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<user>[^:/@\s]+):(?P<pw>[^@/\s]+)@"),
     lambda m: f"{m.group('scheme')}{m.group('user')}:{REDACTED}@"),
    # KEY=VALUE secrets. Case-insensitive, and the name may end in _KEY: AWS_SECRET_ACCESS_KEY=,
    # ANTHROPIC_API_KEY=, and libpq's lowercase conninfo `password=` all have to be caught.
    # Anchor on a name that ENDS in a secret word so `--json-schema key=` style flags are unaffected.
    (re.compile(r"(?i)\b(?P<k>[a-z0-9_]*(?:password|passwd|token|secret|api_?key|access_key|_key))"
                r"\s*=\s*(?P<v>[^\s'\"]+)"),
     lambda m: f"{m.group('k')}={REDACTED}"),
    # Bearer tokens
    (re.compile(r"\b[Bb]earer\s+[A-Za-z0-9._\-]{12,}"), lambda m: f"Bearer {REDACTED}"),
    # Common vendor key shapes
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), lambda m: REDACTED),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}"), lambda m: REDACTED),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), lambda m: REDACTED),
]


def scrub_text(s: str) -> str:
    for pat, repl in PATTERNS:
        s = pat.sub(repl, s)
    return s


def scrub(obj):
    if isinstance(obj, str):
        return scrub_text(obj)
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    if isinstance(obj, dict):
        # Scrub KEYS too. The schema-validated path has fixed keys, but the fallback parse and
        # the prior-findings re-scrub accept arbitrary JSON, where a credential can appear as a
        # key and would otherwise be re-transmitted to the other vendor on the next round.
        return {(scrub_text(k) if isinstance(k, str) else k): scrub(v) for k, v in obj.items()}
    return obj


def main() -> int:
    raw = sys.stdin.read()
    if "--text" in sys.argv[1:]:
        sys.stdout.write(scrub_text(raw))
        return 0
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        # Never fail open: if it is not JSON, scrub it as text rather than passing it through.
        sys.stdout.write(scrub_text(raw))
        return 0
    json.dump(scrub(doc), sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
