---
name: root-cause-debugging
version: 0.1.0
description: |
  Four-phase debugging protocol with one Iron Law: no fix without root-cause
  investigation first. Includes the 3-failed-fixes circuit breaker that
  escalates from "try another patch" to "question the architecture".

  USE WHEN hitting any bug, test failure, or unexpected behavior — and
  ESPECIALLY under time pressure, when a "quick fix" seems obvious, or when
  previous fixes didn't stick.
  SKIP for none — simple bugs have root causes too.
allowed-tools:
  - Bash
  - Read
  - Grep
---

# root-cause-debugging — aim before firing

Distilled from [obra/superpowers](https://github.com/obra/superpowers)
`systematic-debugging` (MIT, Jesse Vincent). Harness wiring: this skill is
the **aim** that the 仗助 loop (instant kill → fix → rerun) needs — speed
without root cause is thrashing. Escalation at 3 failed fixes routes to
dual-magi-review, and escalation is NOT retreat (松岡-compatible).

## Iron Law

```
NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST
```

Systematic is faster than guess-and-check: upstream measured 15-30 min vs
2-3 h thrashing, ~95% vs ~40% first-fix rate.

## Phase 1 — root cause

1. **Read the error completely** — full stack trace, line numbers, codes.
2. **Reproduce reliably** — if you can't, gather data; don't guess.
3. **Check recent changes** — git diff, new deps, config, environment.
4. **Multi-component systems: instrument the boundaries before proposing
   anything.** Log what enters/exits each layer, run once, and let the
   evidence show WHERE it breaks — then investigate that component only.
5. **Trace bad values backward** to their origin; fix at the source, not
   where the symptom surfaced.

## Phase 2 — pattern

Find working code similar to the broken path (same codebase or reference
implementation — read it completely, don't skim). List every difference,
however small; don't assume "that can't matter".

## Phase 3 — hypothesis

One specific hypothesis ("X is the root cause because Y"), tested by the
smallest possible change, one variable at a time. Didn't confirm? New
hypothesis — do NOT stack more changes on top. If you don't understand
something, say so and investigate; don't pretend.

## Phase 4 — fix

1. Failing test case first (automated if possible, one-off script if not).
2. One fix, addressing the root cause. No "while I'm here" refactoring.
3. Verify: target test passes, nothing else broke.

## Circuit breaker: 3 failed fixes → question the architecture

If each fix reveals a new problem somewhere else, or needs "massive
refactoring" to land — that is not a failed hypothesis, that is a wrong
architecture. STOP patching. Run **dual-magi-review** on the design (or at
minimum surface the architecture question to the user) before fix #4.

This escalation is a report-with-alternatives, not a retreat.

## Red flags — any of these means: return to Phase 1

- "Quick fix for now, investigate later"
- "Just try changing X and see"
- Multiple changes in one run
- "It's probably X" without evidence
- Proposing fixes before tracing data flow
- "One more fix attempt" when 2+ already failed

| Excuse | Reality |
|---|---|
| "Too simple to need process" | Simple bugs have root causes; the process is fast on them |
| "Emergency, no time" | Systematic is faster than thrashing — measured |
| "I'll test after the fix works" | Untested fixes don't stick |
| "Multiple fixes at once saves time" | Can't isolate what worked; breeds new bugs |
| "I see the problem" | Seeing a symptom ≠ understanding the cause |

If investigation truly ends at "environmental / timing / external": document
what you ruled out, add handling (retry, timeout, message) and monitoring.
But ~95% of "no root cause" is incomplete investigation.

---
*Upstream: obra/superpowers `systematic-debugging` — MIT © 2025 Jesse
Vincent. Distilled and adapted for claude-harness (magi escalation wiring).*
