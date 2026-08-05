---
name: calibrated-verification
version: 0.1.0
description: |
  Every verification script, test, scanner, or matcher must ship with a
  calibration pair — a known positive it provably catches and a negative
  control it provably passes — and must print its denominator (scanned N /
  matched M / dropped K). "All clean" from an uncalibrated checker is
  indistinguishable from a broken checker.

  USE WHEN writing or running any detector-shaped artifact: a doc-vs-source
  matcher, an audit script, a migration verifier, a scrubber, a grep-based
  sweep — and ESPECIALLY before asserting a negative ("no matches", "no
  references", "all clean", "0 findings") from its output.
  SKIP when the tool's output is directly the deliverable (not a judgment
  about presence/absence).
allowed-tools:
  - Bash
  - Read
  - Grep
---

# calibrated-verification — the detector is a suspect too

## Placement rationale (decided 2026-08-05, #281)

A separate skill, not a section in `skill-tdd`: skill-tdd fires when you
author a SKILL.md; this rule must fire when you author or trust **any
checker**, which happens far more often and in different contexts. A rule
filed under the wrong trigger never loads. Per skill-tdd's own
structural-first gate this stays a skill (not a hook): "does this test seed
a known positive" is a judgment call with no machine-checkable predicate.
The machine-checkable half of #281 — negative claims in docs without a
denominator — IS a hook: `harness-core/hooks/negative_claim_advisor.sh`.

## Iron Law

```
A CHECKER THAT HAS NEVER CAUGHT A PLANTED DEFECT HAS NEVER BEEN SEEN WORKING.
"ALL CLEAN" IS ALSO WHAT A BROKEN DETECTOR PRINTS.
```

## The three requirements

1. **Known positive** — seed at least one input the checker MUST flag, and
   assert that it does. If the positive stops firing, the run is invalid —
   report "checker broken", not "all clean".
2. **Negative control** — at least one input the checker MUST pass, and
   assert that it does. A detector that flags everything is as useless as
   one that flags nothing, and over-firing gets detectors disabled.
3. **Denominator, always printed** — scanned N / matched M / skipped or
   unresolved K. Silent drops are forbidden: anything the tool could not
   resolve is COUNTED and shown, never discarded. A conclusion drawn from
   the output quotes these numbers ("0 hits in 44,625 resolved; 44,092
   unresolved" — which is NOT "0 hits").

## Incidents this rule is distilled from (both 2026-08-05)

- **nakamura-fdr#46** — the static analyzer silently dropped 49.7% of
  accesses (resolved 44,625 / dropped 44,092). Its R=0 became "readers
  zero — must not be used" in a doc; four workers concurred; other docs
  cited it as precedent. Ground truth: 3 writes / 6 reads. Headcount is no
  cross-check when everyone reads the same broken tool.
- **Same day, same repo** — a doc-vs-ROM matcher had a mask-width bug and
  returned "no match" for every entry. The one seeded known positive
  exposed it immediately; without it the report would have read "all
  entries clean". Reference implementation:
  `tests/test_doc_listings_match_rom.py::test_calibration_detector_catches_known_positives`.

Prior art in this repo: gh #363 (a gate that raised before ever querying
had "never once run green" — `check_psycopg_placeholders.sh` header), and
`plugins/harness-core/tests/test_negative_claim_advisor.sh` (ADVISE cases +
SILENT controls + logged firings as the denominator).

## Rationalization table

| Excuse | Reality |
|---|---|
| "The checker is 10 lines, it can't be wrong" | The mask-width bug was one constant. Size caps neither blast radius nor plausibility. |
| "All clean — done" | All clean is the checker's failure mode too. Prove it can fail before believing it passed. |
| "N reviewers agreed with the result" | Reviewers of the same tool's output share its blind spot. Review is only independent when the *instrument* differs. |
| "I'll add the calibration case later" | Later = after the negative claim shipped. The claim is being made NOW. |
| "Printing counts is noise" | The denominator is the difference between "0 found" and "0 found in the half we could see". |
