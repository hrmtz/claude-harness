#!/usr/bin/env python3
"""Doc-vs-code contract test: every formation subcommand the bin dispatches must be
documented in SKILL.md.

This is the deterministic half of the anti-doc-drift design (docs/designs/
ANTI_DOC_DRIFT.md) — it checks a PUBLIC CONTRACT (the CLI dispatch table), not
thin docs. It exists because the 2026-07-08 audit found SKILL.md had drifted from
the bin (a flag default, a placement default). A verb the code dispatches but no
doc names is a contradiction a machine can catch.

Discipline (from the dual-magi review that produced this):
- Parse the dispatch as `<verb>) cmd_<verb>` with a BACKREFERENCE, so a mismatched
  `spawn) cmd_wrong` is not silently accepted as a documented verb.
- If the dispatch cannot be parsed at all (a bin refactor to another shape), RAISE
  — that is checker-blindness, and must NOT be reported as doc-drift.
- "Documented" = the verb appears in SKILL.md as `formation <verb>` (word-boundary)
  or as a backticked/space-bounded token, so a doc *style* change doesn't false-fail.

Run: python3 plugins/harness-formation/tests/test_docs_match_dispatch.py
Exit 0 = all dispatched verbs documented · 1 = drift · raises = checker cannot see
the dispatch (fix the parser, do not treat as drift).
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(HERE, "..", "bin", "formation")
SKILL = os.path.join(HERE, "..", "skills", "formation", "SKILL.md")


def dispatched_verbs(bin_text: str) -> set[str]:
    """Verbs from the dispatch table: `spawn) cmd_spawn ;;`. The backreference
    (\\w+)\\)\\s+cmd_\\1 requires the handler name to match the case label, so a
    typo'd handler is not counted as a real verb."""
    verbs = set(re.findall(r"^\s*([a-z][a-z0-9_-]*)\)\s+cmd_\1\b", bin_text, re.M))
    return verbs


def is_documented(verb: str, skill_text: str) -> bool:
    # imperative usage `formation <verb>`, or the verb as a standalone/backticked token
    if re.search(rf"\bformation {re.escape(verb)}\b", skill_text):
        return True
    if re.search(rf"`{re.escape(verb)}`", skill_text):
        return True
    return False


def main() -> int:
    with open(BIN, encoding="utf-8") as fh:
        bin_text = fh.read()
    with open(SKILL, encoding="utf-8") as fh:
        skill_text = fh.read()

    verbs = dispatched_verbs(bin_text)
    if not verbs:
        # Checker-blindness, not doc-drift: the dispatch shape changed. Fail loudly
        # so someone fixes the parser rather than silently passing or false-flagging.
        raise RuntimeError(
            "no `<verb>) cmd_<verb>` dispatch found in bin/formation — the parser "
            "can no longer see the dispatch table. Fix this test's regex; do NOT "
            "treat this as documentation drift."
        )

    undocumented = sorted(v for v in verbs if not is_documented(v, skill_text))
    if undocumented:
        print(f"DRIFT: bin/formation dispatches {sorted(verbs)}; "
              f"SKILL.md does not document {undocumented}")
        return 1
    print(f"OK: all {len(verbs)} dispatched verbs documented in SKILL.md "
          f"({', '.join(sorted(verbs))})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
