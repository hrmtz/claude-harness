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
import argparse
import json
import re
import sys
from pathlib import Path

REDACTED = "«REDACTED»"

PATTERNS = [
    # DSN userinfo: scheme://user:secret@host (including an empty user) -> keep shape.
    (re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<user>[^:/@\s]*):(?P<pw>[^@/\s]+)@"),
     lambda m: f"{m.group('scheme')}{m.group('user')}:{REDACTED}@"),
    # KEY=VALUE secrets. Case-insensitive, and the name may end in _KEY: AWS_SECRET_ACCESS_KEY=,
    # ANTHROPIC_API_KEY=, and libpq's lowercase conninfo `password=` all have to be caught.
    # Anchor on a name that ENDS in a secret word so `--json-schema key=` style flags are unaffected.
    (re.compile(r"(?i)\b(?P<k>[a-z0-9_]*(?:password|passwd|token|secret|api_?key|access_key|_key))"
                r"\s*=\s*(?:"
                r"\\(?P<eq>['\"])(?:(?!\\(?P=eq))[^\r\n])*(?:\\(?P=eq)|(?=\r|\n|$))"
                r"|(?P<q>['\"])(?:\\[^\r\n]|(?!(?P=q))[^\\\r\n])*(?:(?P=q)|(?=\r|\n|$))"
                r"|(?P<v>[^\s'\"]+))"),
     lambda m: f"{m.group('k')}={REDACTED}"),
    # Bearer tokens
    (re.compile(r"\b[Bb]earer\s+[A-Za-z0-9._\-]{12,}"), lambda m: f"Bearer {REDACTED}"),
    # Common vendor key shapes
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), lambda m: REDACTED),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}"), lambda m: REDACTED),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), lambda m: REDACTED),
]


def scrub_text(s: str) -> str:
    return scrub_text_with_count(s)[0]


def scrub_text_with_count(s: str) -> tuple[str, int]:
    count = 0
    for pat, repl in PATTERNS:
        s, replacements = pat.subn(repl, s)
        count += replacements
    return s, count


def scrub(obj, redactions: list[int] | None = None):
    if isinstance(obj, str):
        value, count = scrub_text_with_count(obj)
        if redactions is not None:
            redactions[0] += count
        return value
    if isinstance(obj, list):
        return [scrub(x, redactions) for x in obj]
    if isinstance(obj, dict):
        # Scrub KEYS too. The schema-validated path has fixed keys, but the fallback parse and
        # the prior-findings re-scrub accept arbitrary JSON, where a credential can appear as a
        # key and would otherwise be re-transmitted to the other vendor on the next round.
        result = {}
        for key, value in obj.items():
            if isinstance(key, str):
                safe_key, count = scrub_text_with_count(key)
                if redactions is not None:
                    redactions[0] += count
            else:
                safe_key = key
            result[safe_key] = scrub(value, redactions)
        return result
    return obj


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", action="store_true")
    parser.add_argument("--meta")
    args = parser.parse_args()
    raw = sys.stdin.read()
    input_bytes = len(raw.encode("utf-8"))
    parsed_json = False
    redactions = [0]
    if args.text:
        output, redactions[0] = scrub_text_with_count(raw)
    else:
        try:
            doc = json.loads(raw)
            parsed_json = True
        except json.JSONDecodeError:
            # Never fail open: if it is not JSON, scrub it as text rather than passing it through.
            output, redactions[0] = scrub_text_with_count(raw)
        else:
            output = json.dumps(
                scrub(doc, redactions), indent=2, ensure_ascii=False
            ) + "\n"
    sys.stdout.write(output)
    if args.meta:
        Path(args.meta).write_text(
            json.dumps(
                {
                    "input_bytes": input_bytes,
                    "output_bytes": len(output.encode("utf-8")),
                    "parsed_json": parsed_json,
                    "redactions": redactions[0],
                },
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
