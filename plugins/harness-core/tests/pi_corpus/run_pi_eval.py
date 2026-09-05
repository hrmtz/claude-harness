#!/usr/bin/env python3
"""Standing prompt-injection regression harness for prompt-rendering code.

Motivation: an occasional red-team pass finds a pile of holes at once, and the
findings then decay because nothing re-checks them. This freezes each finding as
a corpus row so every commit re-runs it — the same shape `fp_corpus` uses for the
credential guards.

  python3 run_pi_eval.py --adapter path/to/pi_adapter_project.py
  python3 run_pi_eval.py --adapter ... --json
  python3 run_pi_eval.py --adapter ... --write-baseline

GATE: exits non-zero on any NEW failure (a ``(case, sink, invariant)`` triple not
in the baseline), on any config error, or when the sabotage check cannot prove the
battery still detects. Fixed failures are reported but never offset a new one.

WHAT THIS DOES NOT TEST: this is a *structural* check on the rendered string. A
green run says delimiters held and single-line fields stayed single-line. It says
nothing about whether an LLM is persuaded by the content inside a correctly formed
fence — semantic resistance is a different question and needs different evidence.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys
import traceback
from collections import Counter
from typing import Any

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))

import pi_adapter as C  # noqa: E402

# outcome vocabulary
PASS, FAIL, ERROR, SKIPPED, UNMEASURED = "PASS", "FAIL", "ERROR", "SKIPPED", "UNMEASURED"
#: Outcomes that mean "we did not learn anything here". Never silently a pass.
NON_VERDICT = (ERROR, SKIPPED, UNMEASURED)

LINE_BREAKS = ("\n", "\r", "\v", "\f", "", " ", " ")


#: Fixed nonce handed to sinks that accept one, so a ``{CLOSE}`` payload carries
#: the delimiter the fence ACTUALLY uses. Without it the runner probes delimiters
#: on one render and then attacks a second render that rolled a fresh nonce --
#: which quietly downgrades every I1 case into the far weaker "guess the nonce"
#: test. Pinning it states the worst case plainly: assume the attacker knows the
#: nonce. Whether production rolls a fresh one is I4's separate question, and I4
#: deliberately renders WITHOUT this pin.
PINNED_NONCE = "b7f3c1a90e42"


def render(sink, payload):
    """Render through a sink, pinning the nonce when the sink accepts one."""
    if sink.accepts_nonce:
        return sink.render(payload, nonce=PINNED_NONCE)
    return sink.render(payload)


# ── loading ────────────────────────────────────────────────────────────────
def load_adapter(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location("pi_adapter_project", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot import adapter: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_corpus() -> list[dict]:
    with open(HERE / "corpus.jsonl", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ── config validation (each of these otherwise degrades to a silent pass) ──
def config_errors(sinks, sabotages, corpus) -> list[str]:
    errs: list[str] = []
    if not sinks:
        errs.append("empty_adapter: adapter exposes no SINKS")
        return errs
    seen: set[str] = set()
    for s in sinks:
        if not s.name:
            errs.append("empty_sink_name: a sink has no name")
        if s.name in seen:
            errs.append(f"duplicate_sink_name: {s.name!r}")
        seen.add(s.name)
        if s.accepts_nonce and s.nonce is None:
            errs.append(f"accepts_nonce_without_hook: {s.name!r} claims nonce support "
                        "but supplies no extractor, so I4 could never be verified")
    ids = {c["id"] for c in corpus}
    invs = set(C.INVARIANTS)
    for sab in sabotages:
        if not sab.expected:
            errs.append(f"sabotage_without_expected: {sab.sink!r} lists no expected "
                        "violations, so the check would pass vacuously")
        if sab.sink not in seen:
            errs.append(f"sabotage_unknown_target: sink {sab.sink!r} not registered")
        for cid, inv in sab.expected:
            if cid not in ids:
                errs.append(f"sabotage_unknown_target: case {cid!r}")
            if inv not in invs:
                errs.append(f"sabotage_unknown_target: invariant {inv!r}")
    return errs


# ── payload materialisation ────────────────────────────────────────────────
def probe_delims(sink) -> tuple[str, str] | None:
    """Ask the sink for its real delimiters using a benign render."""
    if sink.delims is None:
        return None
    try:
        return sink.delims(render(sink, "benign"))
    except Exception:
        return None


def resolve(payload, sink, notation) -> tuple[Any, str | None]:
    """Substitute placeholders. Returns (payload, unresolvable-reason)."""
    if payload is None:
        return None, None
    if payload == "{MISSING}":
        return C.MISSING, None
    if not isinstance(payload, str):
        return payload, None

    if "{NOTATION}" in payload:
        payload = payload.replace("{NOTATION}", " ".join(notation))
    if "{HUGE}" in payload:
        payload = payload.replace("{HUGE}", "あ" * 1_000_000)

    needs_delims = any(t in payload for t in
                       ("{OPEN}", "{CLOSE}", "{CLOSE_TRUNC}", "{CLOSE_UPPER}",
                        "{CLOSE_FULLWIDTH}", "{CLOSE_SPLIT}"))
    if needs_delims:
        d = probe_delims(sink)
        if not d:
            return None, ("unresolvable_placeholder: case needs delimiters but sink "
                          f"{sink.name!r} exposes no delims hook")
        op, cl = d
        mid = len(cl) // 2
        payload = (payload
                   .replace("{OPEN}", op)
                   .replace("{CLOSE_TRUNC}", cl[:-3])
                   .replace("{CLOSE_UPPER}", cl.upper())
                   .replace("{CLOSE_FULLWIDTH}", cl.replace("<", "＜").replace(">", "＞"))
                   .replace("{CLOSE_SPLIT}", cl[:mid] + "\n" + cl[mid:])
                   .replace("{CLOSE}", cl))
    return payload, None


def bracket(payload, invariant):
    """Wrap in canaries — only for the positional invariants."""
    if invariant not in C.CANARY_INVARIANTS or not isinstance(payload, str):
        return payload
    return C.CANARY_OPEN + payload + C.CANARY_CLOSE


def spans(fragment: str) -> list[tuple[int, int]]:
    """Every bracketed occurrence of the field, not just the first.

    A builder that renders the same value twice (a body line and a summary line,
    say) gets both measured. Looking only at the first occurrence leaves the
    second one silently untested, which is the shape of blind spot this corpus
    exists to close.
    """
    out: list[tuple[int, int]] = []
    i = 0
    while True:
        a = fragment.find(C.CANARY_OPEN, i)
        if a < 0:
            return out
        b = fragment.find(C.CANARY_CLOSE, a + len(C.CANARY_OPEN))
        if b < 0:
            return out
        out.append((a + len(C.CANARY_OPEN), b))
        i = b + len(C.CANARY_CLOSE)


def span(fragment: str) -> tuple[int, int] | None:
    sp = spans(fragment)
    return sp[0] if sp else None


# ── invariant checks ───────────────────────────────────────────────────────
def check_i1(fragment, sink, delims):
    """Containment: the field's span must sit strictly inside one delimiter pair."""
    if delims is None:
        return UNMEASURED, "sink exposes no delims hook"
    op, cl = delims
    sp = span(fragment)
    if sp is None:
        return ERROR, "canary markers missing (truncated or rewritten) — not measurable"
    start, end = sp
    opens = [i for i in range(len(fragment)) if fragment.startswith(op, i)]
    closes = [i for i in range(len(fragment)) if fragment.startswith(cl, i)]
    if not opens or not closes:
        return FAIL, "no delimiter pair in output"
    # the pair that should contain the field
    o = max([i for i in opens if i < start], default=None)
    if o is None:
        return FAIL, "field starts before any opening delimiter"
    c = min([i for i in closes if i >= end], default=None)
    if c is None:
        return FAIL, "field is not followed by a closing delimiter"
    inner = fragment[start:end]
    if op in inner or cl in inner:
        return FAIL, "raw delimiter survived inside the field span"
    between = fragment[o + len(op):c]
    if between.count(cl) > 0:
        return FAIL, "an extra closing delimiter appears before the pair closes"
    # An unbalanced count means the payload minted a delimiter somewhere even if
    # the pair around the field happens to look intact -- two opens against one
    # close still leaves a frame the model has to guess the end of.
    if len(opens) != len(closes):
        return FAIL, (f"delimiters unbalanced: {len(opens)} open vs "
                      f"{len(closes)} close")
    return PASS, ""


def check_i1b(fragment, sink, tokens):
    """Neutralisation: structural tokens must not survive verbatim in the span."""
    sp = spans(fragment)
    if not sp:
        return ERROR, "canary markers missing — not measurable"
    for n, (a, b) in enumerate(sp, 1):
        hit = [t for t in tokens if t in fragment[a:b]]
        if hit:
            where = f" (occurrence {n} of {len(sp)})" if len(sp) > 1 else ""
            return FAIL, f"structural token survived verbatim{where}: {hit}"
    return PASS, ""


def check_i2(fragment, sink, baseline_fragment):
    """No new structure: a single-line sink must not gain line breaks in-field.

    Compared against a benign render of the same sink so the template's own
    newlines are never counted against the payload.
    """
    if sink.kind != "single_line":
        return SKIPPED, "not a single_line sink"
    sp = spans(fragment)
    if not sp:
        return ERROR, "canary markers missing — not measurable"
    base_sp = spans(baseline_fragment)
    inner = "".join(fragment[a:b] for a, b in sp)
    base_inner = "".join(baseline_fragment[a:b] for a, b in base_sp)
    got = sum(inner.count(b) for b in LINE_BREAKS)
    ref = sum(base_inner.count(b) for b in LINE_BREAKS)
    if ref:
        # The sink puts line breaks inside the field's own span even for benign
        # input, so "did the payload add one" cannot be answered by counting.
        # Say so rather than comparing totals -- equal counts in different places
        # is exactly the case a total would wave through.
        return UNMEASURED, (f"the benign render already contains {ref} line break(s) "
                            "inside the field span; a count cannot separate the "
                            "payload's breaks from the sink's own")
    if got:
        kinds = [repr(b) for b in LINE_BREAKS if b in inner]
        return FAIL, f"{got} line break(s) grew inside a single-line field: {kinds}"
    return PASS, ""


def check_i3(fragment, sink, notation):
    """Notation survives -- INSIDE THE FIELD, not merely somewhere on the page.

    Matching against the whole fragment is the bug this replaces: a template
    that mentions a unit itself would report PASS for a sink that destroyed the
    field. The span is located with the canaries, same as I1/I2.
    """
    sp = spans(fragment)
    if not sp:
        return ERROR, "canary markers missing -- not measurable"
    inner = "".join(fragment[a:b] for a, b in sp)
    missing = [n for n in notation if n not in inner]
    if missing:
        return FAIL, f"notation mangled or dropped from the field: {missing}"
    return PASS, ""


def check_i4(sink):
    """Freshness AND passthrough.

    Freshness alone is not the whole invariant: a sink that silently ignores the
    nonce it is handed still rolls a fresh one each render and would sail past a
    freshness-only check -- while every I1 case that pins the nonce is quietly
    attacking a delimiter the fence never used. Both halves are checked here.
    """
    if sink.nonce is None:
        return SKIPPED, "sink exposes no nonce extractor"
    # NOT pinned: freshness asks whether the production path rolls a new one.
    try:
        a = sink.nonce(sink.render("benign"))
        b = sink.nonce(sink.render("benign"))
    except Exception as exc:
        return ERROR, f"nonce extraction raised {type(exc).__name__}: {exc}"
    if a is None or b is None:
        return UNMEASURED, "nonce extractor returned None"
    if a == b:
        return FAIL, f"nonce reused across renders ({a!r})"
    if sink.accepts_nonce:
        try:
            got = sink.nonce(sink.render("benign", nonce=PINNED_NONCE))
        except Exception as exc:
            return ERROR, f"pinned render raised {type(exc).__name__}: {exc}"
        if got != PINNED_NONCE:
            return FAIL, (f"sink claims accepts_nonce but ignored it "
                          f"(passed {PINNED_NONCE!r}, fence used {got!r}); every "
                          "pinned I1 case was attacking the wrong delimiter")
    return PASS, ""


# ── execution ──────────────────────────────────────────────────────────────
def run(adapter, corpus, only_sinks=None) -> list[dict]:
    notation = tuple(getattr(adapter, "NOTATION", None) or C.DEFAULT_NOTATION)
    tokens = tuple(getattr(adapter, "STRUCTURAL_TOKENS", None) or C.DEFAULT_STRUCTURAL_TOKENS)
    results: list[dict] = []

    for sink in adapter.SINKS:
        if only_sinks and sink.name not in only_sinks:
            continue
        delims = probe_delims(sink)
        try:
            benign = render(sink, bracket("benign", "I2"))
        except Exception as exc:
            results.append(dict(case="(benign-render)", sink=sink.name, invariant="-",
                                outcome=ERROR, detail=f"{type(exc).__name__}: {exc}"))
            continue

        # I4 is a property of the sink, not of any single case
        out, detail = check_i4(sink)
        results.append(dict(case="(sink)", sink=sink.name, invariant="I4",
                            outcome=out, detail=detail))

        for case in corpus:
            inv, kind = case["invariant"], case["kind"]
            if kind != "any" and kind != sink.kind:
                continue
            payload, bad = resolve(case["payload"], sink, notation)
            if bad:
                results.append(dict(case=case["id"], sink=sink.name, invariant=inv,
                                    outcome=ERROR, detail=bad))
                continue
            try:
                frag = render(sink, bracket(payload, inv))
            except Exception as exc:
                outcome = FAIL if inv == "I5" else ERROR
                results.append(dict(case=case["id"], sink=sink.name, invariant=inv,
                                    outcome=outcome,
                                    detail=f"{type(exc).__name__}: {exc}"))
                continue

            if inv == "I1":
                out, detail = check_i1(frag, sink, delims)
            elif inv == "I1b":
                out, detail = check_i1b(frag, sink, tokens)
            elif inv == "I2":
                out, detail = check_i2(frag, sink, benign)
            elif inv == "I3":
                out, detail = check_i3(frag, sink, notation)
            elif inv == "I5":
                out, detail = PASS, ""
            else:
                out, detail = UNMEASURED, f"no predicate for {inv}"
            results.append(dict(case=case["id"], sink=sink.name, invariant=inv,
                                outcome=out, detail=detail))
    return results


def sabotage_check(adapter, corpus) -> tuple[bool, list[str]]:
    """Prove the battery still detects. Matched per (case, invariant), never by count."""
    sabs = list(getattr(adapter, "SABOTAGE", ()) or ())
    if not sabs:
        # Not "skipped" -- unproven. Without a negative control a green run says
        # only that the battery ran, and that is the failure mode this whole
        # corpus exists to prevent.
        return False, ["no SABOTAGE declared -- the battery cannot show it still "
                       "detects anything. Declare at least one."]
    notes: list[str] = []
    ok = True
    for sab in sabs:
        broken = sab.build()
        shim = type("A", (), {"SINKS": [broken],
                              "NOTATION": getattr(adapter, "NOTATION", None),
                              "STRUCTURAL_TOKENS": getattr(adapter, "STRUCTURAL_TOKENS", None)})
        res = run(shim, corpus)
        got = {(r["case"], r["invariant"]) for r in res if r["outcome"] == FAIL}
        for cid, inv in sab.expected:
            if (cid, inv) in got:
                notes.append(f"  detected {cid}/{inv} on sabotaged {sab.sink}")
            else:
                actual = [r["outcome"] for r in res
                          if r["case"] == cid and r["invariant"] == inv] or ["(not run)"]
                notes.append(f"  MISSED {cid}/{inv} on sabotaged {sab.sink} "
                             f"(got {actual[0]}) — an ERROR does not count as detection")
                ok = False
    return ok, notes


# ── reporting ──────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--baseline", default=None)
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--sink", action="append", dest="sinks")
    args = ap.parse_args()

    adapter = load_adapter(pathlib.Path(args.adapter).resolve())
    corpus = load_corpus()
    sinks = list(getattr(adapter, "SINKS", ()) or ())
    sabs = list(getattr(adapter, "SABOTAGE", ()) or ())

    errs = config_errors(sinks, sabs, corpus)
    if errs:
        for e in errs:
            print(f"CONFIG ERROR  {e}", file=sys.stderr)
        return 2

    results = run(adapter, corpus, set(args.sinks) if args.sinks else None)
    # Synthetic rows -- the per-sink I4 probe and the benign-render error -- are
    # not corpus cases. Counting them would let a run where every real case blew
    # up still look populated.
    corpus_rows = [r for r in results if not r["case"].startswith("(")]
    if not corpus_rows:
        print("CONFIG ERROR  zero_cases_executed: no corpus case matched any sink",
              file=sys.stderr)
        return 2
    verdicts = [r for r in corpus_rows if r["outcome"] in (PASS, FAIL)]
    if not verdicts:
        print("CONFIG ERROR  zero_cases_executed: every corpus case ended in "
              "ERROR/SKIPPED/UNMEASURED -- nothing was measured", file=sys.stderr)
        for r in corpus_rows[:10]:
            print(f"  {r['outcome']:<11} {r['sink']}/{r['case']}: {r['detail']}",
                  file=sys.stderr)
        return 2

    bl_path = pathlib.Path(args.baseline) if args.baseline else \
        pathlib.Path(args.adapter).resolve().parent / "pi_baseline.json"
    baseline = set()
    if bl_path.exists():
        baseline = {tuple(x) for x in json.loads(bl_path.read_text())["failures"]}

    failures = {(r["case"], r["sink"], r["invariant"]) for r in results if r["outcome"] == FAIL}
    passed = {(r["case"], r["sink"], r["invariant"]) for r in results if r["outcome"] == PASS}
    new = sorted(failures - baseline)
    # Only a baseline entry now observed PASSING counts as fixed. One that ERRORed
    # simply vanished from `failures`, and calling that a fix reports progress for
    # a case nobody measured.
    fixed = sorted(baseline & passed)
    unmeasured_baseline = sorted(baseline - failures - passed)

    if args.write_baseline:
        bl_path.write_text(json.dumps(
            {"failures": sorted(list(failures)),
             "note": "Accepted, tracked gaps. New failures fail the gate; fixed ones "
                     "never offset a new one. Shrink this file, never grow it silently."},
            indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {bl_path} ({len(failures)} accepted failures)")
        return 0

    sab_ok, sab_notes = sabotage_check(adapter, corpus)

    if args.json:
        print(json.dumps({"results": results, "new": new, "fixed": fixed,
                          "sabotage_ok": sab_ok}, ensure_ascii=False, indent=2))
    else:
        counts = Counter(r["outcome"] for r in results)
        print("### coverage")
        for s in sinks:
            mark = "" if s.covered else "  [UNCOVERED — declared undefended]"
            print(f"  {s.name:<28} kind={s.kind:<12} delims={'y' if s.delims else 'n'} "
                  f"nonce={'y' if s.nonce else 'n'}{mark}")
        print("\n### outcomes")
        for k in (PASS, FAIL, ERROR, SKIPPED, UNMEASURED):
            if counts.get(k):
                print(f"  {k:<12} {counts[k]}")
        if counts.get(ERROR) or counts.get(UNMEASURED):
            print("\n### not measured (never treat as pass)")
            for r in results:
                if r["outcome"] in (ERROR, UNMEASURED):
                    print(f"  {r['outcome']:<11} {r['sink']}/{r['case']}/{r['invariant']}: {r['detail']}")
        if new:
            print("\n### NEW failures (gate)")
            for c, s, i in new:
                d = next(r["detail"] for r in results
                         if (r["case"], r["sink"], r["invariant"]) == (c, s, i))
                print(f"  {s}/{c}/{i}: {d}")
        if fixed:
            print("\n### fixed since baseline (report only — does not offset a new failure)")
            for c, s, i in fixed:
                print(f"  {s}/{c}/{i}")
        if unmeasured_baseline:
            print("\n### baseline entries NOT re-measured this run (not fixed)")
            for c, s, i in unmeasured_baseline:
                print(f"  {s}/{c}/{i}")
        print("\n### battery self-check (sabotage)")
        for n in sab_notes:
            print(n)
        print("\n" + "=" * 72)
        print("NOTE: structural check only. A green run does not establish that an LLM "
              "resists persuasive content inside a well-formed fence.")

    if new:
        return 1
    if not sab_ok:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
