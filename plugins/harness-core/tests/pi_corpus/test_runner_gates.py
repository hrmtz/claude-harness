#!/usr/bin/env python3
"""Negative controls for the runner itself.

The battery checks other people's code; this checks the battery. Every case here
is a way the runner could report green while measuring nothing — which is the
failure this whole corpus exists to prevent, so it deserves the same discipline
the corpus demands of its subjects.

Each was a real defect found by cross-family review on 2026-09-05, reproduced
here so a future edit cannot quietly reintroduce it.

  python3 test_runner_gates.py
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).parent
RUNNER = HERE / "run_pi_eval.py"

# Building an adapter as source text (rather than importing one) is deliberate:
# the gates under test live in main(), and exit codes are the contract.
PREAMBLE = f"""
import sys
sys.path.insert(0, {str(HERE)!r})
from pi_adapter import Sink, Sabotage
"""


def run_adapter(source: str) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "adapter_under_test.py"
        p.write_text(PREAMBLE + source, encoding="utf-8")
        r = subprocess.run([sys.executable, str(RUNNER), "--adapter", str(p),
                            "--baseline", str(pathlib.Path(td) / "none.json")],
                           capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr


# A sink that renders correctly, so any failure below comes from the gate under
# test and not from the fixture being broken.
GOOD_SINK = '''
def _render(payload, *, nonce=None):
    n = nonce or __import__("secrets").token_hex(6)
    body = "" if payload is None or not isinstance(payload, str) else payload
    body = " ".join(body.replace("<<<", "[x").split())
    return f"<<<E_{n}>>>\\nsafe\\n<<<END_E_{n}>>>\\n    Source: {body}\\n"

def _delims(f):
    import re
    m = re.search(r"<<<E_([0-9a-f]+)>>>", f)
    return (f"<<<E_{m.group(1)}>>>", f"<<<END_E_{m.group(1)}>>>") if m else None

def _nonce(f):
    import re
    m = re.search(r"<<<E_([0-9a-f]+)>>>", f)
    return m.group(1) if m else None
'''

CASES: list[tuple[str, str, str]] = []


def case(name: str, expect: str):
    def deco(fn):
        CASES.append((name, expect, fn()))
        return fn
    return deco


@case("every render raises -> must not exit 0", "zero_cases_executed")
def _all_render_error():
    return '''
def _boom(payload, *, nonce=None):
    raise RuntimeError("renderer is broken")
SINKS = [Sink("boom", _boom, "single_line")]
SABOTAGE = [Sabotage("boom", lambda: Sink("boom", _boom, "single_line"),
                     expected=[("i2-heading", "I2")])]
'''


@case("no SABOTAGE declared -> unproven, not green", "SABOTAGE")
def _no_sabotage():
    return GOOD_SINK + '''
SINKS = [Sink("s", _render, "single_line", delims=_delims, nonce=_nonce,
              accepts_nonce=True)]
'''


@case("sink ignores the nonce it is handed -> I4 fails", "ignored it")
def _nonce_ignored():
    return '''
def _render(payload, *, nonce=None):
    n = __import__("secrets").token_hex(6)          # nonce argument discarded
    body = payload if isinstance(payload, str) else ""
    body = " ".join(body.replace("<<<", "[x").split())
    return f"<<<E_{n}>>>\\nsafe\\n<<<END_E_{n}>>>\\n    Source: {body}\\n"

def _delims(f):
    import re
    m = re.search(r"<<<E_([0-9a-f]+)>>>", f)
    return (f"<<<E_{m.group(1)}>>>", f"<<<END_E_{m.group(1)}>>>") if m else None

def _nonce(f):
    import re
    m = re.search(r"<<<E_([0-9a-f]+)>>>", f)
    return m.group(1) if m else None

SINKS = [Sink("s", _render, "single_line", delims=_delims, nonce=_nonce,
              accepts_nonce=True)]
SABOTAGE = [Sabotage("s", lambda: Sink("s", _render, "single_line",
                                       delims=_delims, nonce=_nonce),
                     expected=[("i2-heading", "I2")])]
'''


@case("notation present in template but destroyed in field -> I3 fails",
      "notation mangled or dropped from the field")
def _notation_template_only():
    return '''
def _render(payload, *, nonce=None):
    n = nonce or "aaaaaaaaaaaa"
    body = payload if isinstance(payload, str) else ""
    body = " ".join(body.replace("<<<", "[x").split())
    body = body.replace("\\u2265", "").replace("\\u00b5g", "ug")   # mangle the field
    # ...while the surrounding template mentions the same notation itself
    return (f"<<<E_{n}>>>\\nsafe\\n<<<END_E_{n}>>>\\n"
            f"    note: units such as \\u226510\\u2076 and \\u00b5g are preserved\\n"
            f"    Source: {body}\\n")

def _delims(f):
    import re
    m = re.search(r"<<<E_([0-9a-f]+)>>>", f)
    return (f"<<<E_{m.group(1)}>>>", f"<<<END_E_{m.group(1)}>>>") if m else None


def _nonce(f):
    import re
    m = re.search(r"<<<E_([0-9a-f]+)>>>", f)
    return m.group(1) if m else None

SINKS = [Sink("s", _render, "single_line", delims=_delims, nonce=_nonce,
              accepts_nonce=True)]
SABOTAGE = [Sabotage("s", lambda: Sink("s", _render, "single_line", delims=_delims),
                     expected=[("i3-notation", "I3")])]
'''


@case("second occurrence of the field is measured too", "occurrence 2 of 2")
def _second_occurrence():
    return '''
def _render(payload, *, nonce=None):
    n = nonce or "aaaaaaaaaaaa"
    body = payload if isinstance(payload, str) else ""
    safe = body
    for _t in ("<<<", "[INST]", "[/INST]", "<<SYS>>", "<</SYS>>",
               "<|im_start|>", "<|im_end|>"):
        safe = safe.replace(_t, "[x]")
    safe = " ".join(safe.split())
    raw = " ".join(body.split())          # second copy: NOT neutralised
    return (f"<<<E_{n}>>>\\n{safe}\\n<<<END_E_{n}>>>\\n"
            f"<<<E_{n}>>>\\n{raw}\\n<<<END_E_{n}>>>\\n")

def _delims(f):
    import re
    m = re.search(r"<<<E_([0-9a-f]+)>>>", f)
    return (f"<<<E_{m.group(1)}>>>", f"<<<END_E_{m.group(1)}>>>") if m else None


def _nonce(f):
    import re
    m = re.search(r"<<<E_([0-9a-f]+)>>>", f)
    return m.group(1) if m else None

SINKS = [Sink("s", _render, "fenced", delims=_delims, nonce=_nonce,
              accepts_nonce=True)]
SABOTAGE = [Sabotage("s", lambda: Sink("s", _render, "fenced", delims=_delims),
                     expected=[("i1-inst", "I1b")])]
'''


def main() -> int:
    failures = 0
    for name, expect, src in CASES:
        code, out = run_adapter(src)
        ok = code != 0 and expect in out
        print(f"{'ok  ' if ok else 'FAIL'}  {name}")
        if not ok:
            failures += 1
            print(f"      expected a non-zero exit mentioning {expect!r}; "
                  f"got exit={code}")
            print("      " + "\n      ".join(out.strip().splitlines()[-12:]))
    print(f"\n{len(CASES) - failures}/{len(CASES)} runner gates hold")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
