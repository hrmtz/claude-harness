#!/usr/bin/env python3
"""Doc-vs-code contract test for harness-magi-codex.

The deterministic half of the anti-doc-drift design (docs/designs/ANTI_DOC_DRIFT.md).
It checks PUBLIC CONTRACTS the scripts actually implement — exit codes, env vars, and the
plateau gate's assert set — against what the docs promise. A plugin whose docs claim an
exit code the script never returns is a contradiction a machine can catch.

This plugin exists because a cross-family review found the design specifying two mutually
exclusive lock protocols at once. That class of defect is what this test blocks.

Discipline (inherited from harness-formation/tests/test_docs_match_dispatch.py):
- If a contract cannot be parsed out of the source at all, RAISE. A refactor that hides
  the contract is checker-blindness, not doc-drift, and must not be reported as PASS.
- "Documented" is matched loosely enough that a doc style change does not false-fail.

Run: python3 plugins/harness-magi-codex/tests/test_docs_match_scripts.py
Exit 0 = contracts agree · 1 = drift · raises = checker cannot see the contract.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(HERE, "..")
ADAPTER = os.path.join(PLUGIN, "scripts", "magi_xfamily.sh")
FANOUT = os.path.join(PLUGIN, "scripts", "magi_fanout_codex.sh")
GATE = os.path.join(PLUGIN, "scripts", "magi_plateau_gate.sh")
GUARD = os.path.join(PLUGIN, "scripts", "magi_campaign_guard.py")
README = os.path.join(PLUGIN, "README.md")
DESIGN = os.path.join(PLUGIN, "..", "..", "docs", "designs", "CODEX_MAGI_MIRROR.md")
DUAL_SKILL = os.path.join(PLUGIN, "skills", "dual-magi-review", "SKILL.md")
ULTRA_SKILL = os.path.join(PLUGIN, "skills", "ultramagi", "SKILL.md")


def read(p: str) -> str:
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def adapter_exit_codes(src: str) -> set[str]:
    """Literal `exit N` in the adapter. Excludes 0 (success) and 64 (usage)."""
    codes = set(re.findall(r"^\s*exit (\d+)", src, re.M))
    codes |= set(re.findall(r"\bexit (\d+)\b", src))
    meaningful = {c for c in codes if c not in {"0", "64", "130", "143"}}
    if not meaningful:
        raise RuntimeError(f"cannot parse any exit codes from {ADAPTER} — checker is blind")
    return meaningful


def gate_asserts(src: str) -> set[str]:
    """The G-numbered asserts the gate actually calls fail() with."""
    tags = set(re.findall(r'fail\("(G\d)"', src))
    if not tags:
        raise RuntimeError(f"cannot parse any G-asserts from {GATE} — checker is blind")
    return tags


def env_vars(src: str) -> set[str]:
    names = set(re.findall(r"\$\{(MAGI_[A-Z_]+):-", src))
    if not names:
        raise RuntimeError(f"cannot parse any MAGI_* env vars from {ADAPTER} — checker is blind")
    return names


def guard_exit_codes(src: str) -> set[str]:
    codes = set(re.findall(r"^\s*return (\d+)$", src, re.M))
    meaningful = {code for code in codes if code != "0"}
    if not meaningful:
        raise RuntimeError(f"cannot parse campaign guard exit codes from {GUARD} — checker is blind")
    return meaningful


def guard_env_vars(src: str) -> set[str]:
    names = set(re.findall(r'os\.environ\.get\(\s*"(MAGI_[A-Z_]+)"', src))
    if not names:
        raise RuntimeError(f"cannot parse campaign guard env vars from {GUARD} — checker is blind")
    return names


def main() -> int:
    adapter, fanout, gate, guard = read(ADAPTER), read(FANOUT), read(GATE), read(GUARD)
    skill_paths = (DUAL_SKILL, ULTRA_SKILL)
    if not all(os.path.isfile(path) for path in skill_paths):
        raise RuntimeError("cannot read every shipped SKILL.md — checker is blind")
    docs = read(README) + read(DESIGN) + "".join(read(path) for path in skill_paths)
    drift = []

    for code in sorted(adapter_exit_codes(adapter)):
        # The doc must explain what this exit code means, e.g. "exit 3" / "3 = lock" / "exit 2".
        if not re.search(rf"\b{code}\s*=|\bexit(?:s)? {code}\b|`{code}`", docs):
            drift.append(f"adapter can `exit {code}` but no doc explains it")

    for code in sorted(guard_exit_codes(guard)):
        if not re.search(rf"\b{code}\s*=|\bexit(?:s)? {code}\b|`{code}`", docs):
            drift.append(f"campaign guard can exit {code} but no doc explains it")

    for tag in sorted(gate_asserts(gate)):
        if tag not in docs:
            drift.append(f"plateau gate asserts {tag} but no doc mentions it")

    # Conversely: a doc promising a G-assert the gate never makes is equally a contradiction.
    documented = set(re.findall(r"\bG[1-9]\b", docs))
    implemented = gate_asserts(gate)
    for tag in sorted(documented - implemented):
        drift.append(f"docs promise assert {tag} but the gate never checks it")

    for var in sorted(env_vars(adapter) | env_vars(fanout) | guard_env_vars(guard)):
        if var not in docs:
            drift.append(f"adapter reads ${var} but no doc mentions it")

    # The design's central honesty claim must not be silently dropped from the README.
    if "forgery" in read(README).lower() and "not" not in read(README).lower():
        drift.append("README uses 'forgery' without disclaiming it")

    if drift:
        print("DRIFT:", *drift, sep="\n  - ", file=sys.stderr)
        return 1
    print(f"PASS: adapter/guard exit codes, {len(implemented)} G-asserts, and env vars documented")
    return 0


if __name__ == "__main__":
    sys.exit(main())
