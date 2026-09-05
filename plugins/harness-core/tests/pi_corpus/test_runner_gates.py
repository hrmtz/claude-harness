#!/usr/bin/env python3
"""Negative controls for the runner itself.

The battery checks other people's code; this checks the battery. Every case here
is a way the runner could report green while measuring nothing — the failure this
whole corpus exists to prevent, so it deserves the discipline the corpus demands
of its subjects.

Three things each row-level case must establish, because any one alone is too
weak:

  1. **Positive control** — the same fixture with the defect removed does NOT
     produce the row. Otherwise a test can pass because something unrelated is
     broken.
  2. **Exact row** — the expected ``(case, invariant, outcome)`` is present in
     the results. Matching on "did it exit non-zero and does the output mention
     X" is what let an earlier version of this file go green while the predicate
     under test returned PASS and an unrelated case failed the gate.
  3. **Mutation** — neuter the predicate that is supposed to catch it, and the
     expectation must stop holding. A test that still passes with the checker
     switched off was never testing the checker.

Every case below is a live defect found by cross-family review on 2026-09-05.

  python3 test_runner_gates.py
"""
from __future__ import annotations

import importlib.util
import io
import pathlib
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout

HERE = pathlib.Path(__file__).parent
RUNNER = HERE / "run_pi_eval.py"
sys.path.insert(0, str(HERE))

import run_pi_eval as R  # noqa: E402

PREAMBLE = f"""
import sys
sys.path.insert(0, {str(HERE)!r})
from pi_adapter import Sink, Sabotage
"""

DELIMS = '''
def _delims(f):
    import re
    m = re.search(r"<<<E_([0-9a-f]+)>>>", f)
    return (f"<<<E_{m.group(1)}>>>", f"<<<END_E_{m.group(1)}>>>") if m else None

def _nonce(f):
    import re
    m = re.search(r"<<<E_([0-9a-f]+)>>>", f)
    return m.group(1) if m else None

def _clean(body):
    for _t in ("<<<", ">>>", "[INST]", "[/INST]", "<<SYS>>", "<</SYS>>",
               "<|im_start|>", "<|im_end|>", "<|system|>", "<|user|>",
               "<|assistant|>", "<|endoftext|>"):
        body = body.replace(_t, "[x]")
    return " ".join(body.split())
'''


def load(source: str, td: pathlib.Path):
    p = td / "adapter_under_test.py"
    p.write_text(PREAMBLE + DELIMS + source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("adapter_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, p


def rows_for(mod, corpus):
    with redirect_stdout(io.StringIO()):
        return R.run(mod, corpus)


def has_row(rows, case_id, invariant, outcome) -> bool:
    return any(r["case"] == case_id and r["invariant"] == invariant
               and r["outcome"] == outcome for r in rows)


def exit_code(source: str, td: pathlib.Path) -> tuple[int, str]:
    _, p = load(source, td)
    r = subprocess.run([sys.executable, str(RUNNER), "--adapter", str(p),
                        "--baseline", str(td / "none.json")],
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


# ── fixtures ───────────────────────────────────────────────────────────────
# Defective and clean fixtures differ by one edit, so the positive control
# isolates that defect rather than merely being "some other adapter".

CLEAN_SINGLE_LINE = '''
def _render(payload, *, nonce=None):
    n = nonce or __import__("secrets").token_hex(6)
    body = _clean(payload if isinstance(payload, str) else "")
    return f"<<<E_{n}>>>\\nsafe\\n<<<END_E_{n}>>>\\n    Source: {body}\\n"

SINKS = [Sink("s", _render, "single_line", delims=_delims, nonce=_nonce,
              accepts_nonce=True)]

def _unfolded():
    def r(payload, *, nonce=None):
        n = nonce or "aaaaaaaaaaaa"
        b = payload if isinstance(payload, str) else ""
        return f"<<<E_{n}>>>\\nsafe\\n<<<END_E_{n}>>>\\n    Source: {b}\\n"
    return Sink("s", r, "single_line", delims=_delims)

SABOTAGE = [Sabotage("s", _unfolded, expected=[("i2-heading", "I2"),
                                               ("i2-lone-cr", "I2")])]
'''

CLEAN_FENCED = '''
def _render(payload, *, nonce=None):
    n = nonce or __import__("secrets").token_hex(6)
    body = _clean(payload if isinstance(payload, str) else "")
    return f"<<<E_{n}>>>\\n{body}\\n<<<END_E_{n}>>>\\n"

SINKS = [Sink("s", _render, "fenced", delims=_delims, nonce=_nonce,
              accepts_nonce=True)]

def _raw():
    def r(payload, *, nonce=None):
        n = nonce or "aaaaaaaaaaaa"
        b = payload if isinstance(payload, str) else ""
        return f"<<<E_{n}>>>\\n{b}\\n<<<END_E_{n}>>>\\n"
    return Sink("s", r, "fenced", delims=_delims)

SABOTAGE = [Sabotage("s", _raw, expected=[("i1-close-verbatim", "I1"),
                                          ("i1-inst", "I1b")])]
'''

_SL_BODY = '    body = _clean(payload if isinstance(payload, str) else "")'
_SL_RET = ('    return f"<<<E_{n}>>>\\nsafe\\n<<<END_E_{n}>>>\\n'
           '    Source: {body}\\n"')
_FEN_RET = '    return f"<<<E_{n}>>>\\n{body}\\n<<<END_E_{n}>>>\\n"'

# (label, case_id, invariant, outcome, defective_source, clean_source, predicate)
ROW_CASES: list[tuple[str, str, str, str, str, str, str]] = [
    # A sink that accepts a nonce and silently discards it. Freshness alone
    # cannot see this -- it still rolls a new nonce on every render, while every
    # pinned I1 case is attacking a delimiter the fence never used.
    ("sink accepts a nonce then discards it",
     "(sink)", "I4", R.FAIL,
     CLEAN_SINGLE_LINE.replace(
         'n = nonce or __import__("secrets").token_hex(6)',
         'n = __import__("secrets").token_hex(6)  # nonce argument discarded'),
     CLEAN_SINGLE_LINE, "check_i4"),

    # The field is mangled while the surrounding template mentions the same
    # notation, so a whole-fragment substring match reports PASS.
    ("notation destroyed in field, present in template",
     "i3-notation", "I3", R.FAIL,
     CLEAN_SINGLE_LINE.replace(_SL_RET,
         '    body = body.replace("\\u2265", "").replace("\\u00b5g", "ug")\n'
         '    return (f"<<<E_{n}>>>\\nsafe\\n<<<END_E_{n}>>>\\n"\n'
         '            f"    note: \\u226510\\u2076 and \\u00b5g are preserved\\n"\n'
         '            f"    Source: {body}\\n")'),
     CLEAN_SINGLE_LINE, "check_i3"),

    # The partial version: notation kept in the first copy of the field and
    # destroyed in the second. Concatenating the spans hides it.
    ("notation kept in copy 1, destroyed in copy 2",
     "i3-notation", "I3", R.FAIL,
     CLEAN_FENCED.replace(_FEN_RET,
         '    lost = body\n'
         '    for _a, _b in (("\\u2265", ">="), ("H\\u2082O", "WATER"),\n'
         '                   ("\\u00b5g", "ug"), ("10\\u2076", "10^6"),\n'
         '                   ("CO\\u2082", "CO2")):\n'
         '        lost = lost.replace(_a, _b)\n'
         '    return (f"<<<E_{n}>>>\\n{body}\\n<<<END_E_{n}>>>\\n"\n'
         '            f"<<<E_{n}>>>\\n{lost}\\n<<<END_E_{n}>>>\\n")'),
     CLEAN_FENCED, "check_i3"),

    # The first copy sits in the fence, the second is left bare outside it.
    ("copy 1 fenced, copy 2 bare",
     "i1-close-verbatim", "I1", R.FAIL,
     CLEAN_FENCED.replace(_FEN_RET,
         '    return (f"<<<E_{n}>>>\\n{body}\\n<<<END_E_{n}>>>\\n"\n'
         '            f"    also: {body}\\n")'),
     CLEAN_FENCED, "check_i1"),

    # One copy neutralised, the other not.
    ("copy 1 neutralised, copy 2 raw",
     "i1-inst", "I1b", R.FAIL,
     CLEAN_FENCED.replace(_FEN_RET,
         '    raw = " ".join((payload if isinstance(payload, str) else "").split())\n'
         '    return (f"<<<E_{n}>>>\\n{body}\\n<<<END_E_{n}>>>\\n"\n'
         '            f"<<<E_{n}>>>\\n{raw}\\n<<<END_E_{n}>>>\\n")'),
     CLEAN_FENCED, "check_i1b"),

    # A canary eaten on one occurrence must be ERROR: "the renderer ate my
    # marker" and "the field is fine" are different events.
    ("canary eaten on one occurrence -> ERROR",
     "i1-inst", "I1b", R.ERROR,
     CLEAN_FENCED.replace(_FEN_RET,
         '    two = body.replace("ZQCANARYZ7", "")\n'
         '    return (f"<<<E_{n}>>>\\n{body}\\n<<<END_E_{n}>>>\\n"\n'
         '            f"<<<E_{n}>>>\\n{two}\\n<<<END_E_{n}>>>\\n")'),
     CLEAN_FENCED, "check_i1b"),
]

# (label, source, substring that must appear in a non-zero-exit run)
GATE_CASES: list[tuple[str, str, str]] = [
    ("every render raises", '''
def _boom(payload, *, nonce=None):
    raise RuntimeError("renderer is broken")
SINKS = [Sink("boom", _boom, "single_line")]
SABOTAGE = [Sabotage("boom", lambda: Sink("boom", _boom, "single_line"),
                     expected=[("i2-heading", "I2")])]
''', "zero_cases_executed"),

    ("no SABOTAGE declared",
     CLEAN_SINGLE_LINE.split("def _unfolded")[0], "SABOTAGE"),

    # The partial version: most cases pass, a few cannot be measured. A guard
    # that only fires when NOTHING was judged walks straight past this.
    ("only some cases unmeasurable",
     CLEAN_SINGLE_LINE.replace(_SL_BODY,
         '    body = payload if isinstance(payload, str) else ""\n'
         '    if "\\u2265" in body:\n'
         '        body = body.replace("ZQCANARYA7", "")\n'
         '    body = _clean(body)'),
     "i3-notation"),
]


def main() -> int:
    failures = 0
    corpus = R.load_corpus()

    with tempfile.TemporaryDirectory() as _td:
        td = pathlib.Path(_td)

        # A correct adapter must be clean. Without this the whole file could be
        # passing because the runner rejects everything put in front of it.
        code, out = exit_code(CLEAN_SINGLE_LINE, td)
        ok = code == 0
        print(f"{'ok  ' if ok else 'FAIL'}  positive control: a correct sink exits 0")
        if not ok:
            failures += 1
            print("      " + "\n      ".join(out.strip().splitlines()[-14:]))

        for label, cid, inv, outcome, bad_src, good_src, predicate in ROW_CASES:
            bad, _ = load(bad_src, td)
            good, _ = load(good_src, td)

            caught = has_row(rows_for(bad, corpus), cid, inv, outcome)
            clean = not has_row(rows_for(good, corpus), cid, inv, outcome)

            # Mutation: switch the predicate off and the expectation must die.
            original = getattr(R, predicate)
            setattr(R, predicate, lambda *a, **k: (R.PASS, ""))
            try:
                survives = has_row(rows_for(bad, corpus), cid, inv, outcome)
            finally:
                setattr(R, predicate, original)

            ok = caught and clean and not survives
            print(f"{'ok  ' if ok else 'FAIL'}  {label}  "
                  f"[{cid}/{inv}->{outcome}, guarded by {predicate}]")
            if not ok:
                failures += 1
                if not caught:
                    print("      the defective fixture did not produce that row")
                if not clean:
                    print("      the CLEAN fixture produced it too — "
                          "the test is not isolating the defect")
                if survives:
                    print(f"      still produced with {predicate} neutered — "
                          "the test does not exercise it")

        for label, src, expect in GATE_CASES:
            code, out = exit_code(src, td)
            ok = code != 0 and expect in out
            print(f"{'ok  ' if ok else 'FAIL'}  gate: {label}  [mentions {expect!r}]")
            if not ok:
                failures += 1
                print(f"      expected non-zero exit mentioning {expect!r}, "
                      f"got exit={code}")
                print("      " + "\n      ".join(out.strip().splitlines()[-14:]))

    total = 1 + len(ROW_CASES) + len(GATE_CASES)
    print(f"\n{total - failures}/{total} runner gates hold")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
