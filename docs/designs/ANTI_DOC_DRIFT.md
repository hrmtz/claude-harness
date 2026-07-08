# Anti-doc-drift — design (v2, post cross-family REJECT)

_ultramagi. Design **PLATEAU**: Claude ×3 (GO-WITH-REVISE) → codex REJECT (2 CRITICAL)
→ v2 → codex confirm (GO-WITH-REVISE, "plateau permitted; both CRITICALs closed, no
new CRITICAL"). The loop turned a 3-part "detector framework" into one test + a
runbook — the over-build was caught at the design gate. The count test was dropped
on codex's final scope call (a bare numeral is unchecked drift-bait; removed instead
of guarded). Shipped as of this commit._

## Problem (unchanged, grounded)

The dangerous drift class is "doc **contradicts** code" (not "feature undocumented").
Five real 2026-07-08 cases: "17 hooks" (18 real); formation SKILL "splits the
window" (default new-window); kimi README "pre-exec block impossible" (ships one);
rails "T3 progress-stall" (unimplemented); formation usage() "claude=normal default"
(bypass). Deterministic → checkable. Thin-docs is a *generative* problem → the human
audit's job, not a check.

## The two corrections that reshaped the design

**Round 1 (Claude):** the "one rule" is *public-contract enumeration mismatch*, not
artifact-existence; the plugin-skills and per-hook-name invariants were thin-docs
false positives (dropped); the count invariant must not free-grep.

**Cross-family (codex) — the load-bearing REJECT:** the whole idea of a branded
"detector A with its own runner" is over-built and dishonest, because **the runner
does not exist and cannot exist as designed**:
- no CI, no `core.hooksPath`, no installed pre-push; `sync_hooks_to_live.py` only
  *prints* "run … to confirm" — there is no jog to join.
- this checkout is a **git worktree**; `git rev-parse --git-dir` →
  `…/claude-harness/.git/worktrees/harness-antidrift`, which has no `hooks/`. A
  "repo-local `git/hooks/`" plan is simply wrong.
- a pre-push advisory is bypassable (`--no-verify`), absent on fresh clones, and by
  definition can't block — neither enforcement nor durable green-history.

And the sentinel count check "silently checks nothing": with opt-in sentinels that
no README carries, it only validates `hooks.json` against itself.

## v2 decision: not a detector — release-time TESTS + a wired runbook

There is no "system A." There are three concrete, honest deliverables, each wired
to a runner that already fires:

### 1. Public-contract tests, beside the code they check

Written as ordinary harness tests (the repo's `plugins/*/tests/test_*.py`
convention — they already run standalone `python3 test_x.py`), so the runner is
**the existing test discipline**, not a phantom hook.

- `plugins/harness-formation/tests/test_docs_match_dispatch.py` — every
  `(\w+)\) cmd_\1` verb in `bin/formation`'s dispatch is named in SKILL.md
  (word-boundary; a **backreference** so `spawn) cmd_wrong` is caught). If the
  dispatch can't be parsed at all → the test **ERRORS** (raises), it does not
  report drift — checker-blindness must not masquerade as doc-drift.
- **hook-count test — DROPPED** (codex final scope call). The count has two valid
  answers (18 distinct scripts / 19 event registrations) and is *descriptive
  inventory*, not a durable behavioral contract; guarding it needs sentinel
  machinery for a weak self-consistency check. The honest move is not to duplicate a
  count into prose at all: the bare "18 hooks" numerals were **removed** from
  `hooks.json`, both READMEs, so there is no unchecked numeral to drift. `hooks.json`
  is pointed to as the authoritative set instead.

The dispatch test is `PASS | DRIFT(exit 1) | ERROR(raise)` — a parse failure raises
rather than string-searching a detail field, so checker-blindness can't read as
doc-drift.

### 2. Doc-audit runbook, wired into the release flow

`docs/DOC_AUDIT_RUNBOOK.md` captures the generative arc proven 3× this session
(worktree → code-diff-since-baseline → parallel subagent sweep → human-gated edits
→ verify → merge). It is invoked as an explicit **step in the `versioning` skill**
(`plugins/harness-rails/skills/versioning/SKILL.md`) — before a MINOR/MAJOR tag,
"run the doc-audit runbook" — because release already has a human and a cadence.
Not a free-floating weekly skill (which rots, like the un-wired precedent).

### 3. CI is the only real enforcement — offered, not assumed

The tests above run in the local test discipline (advisory). The **only** durable,
non-bypassable enforcement is a required CI check. This design does **not** invent a
fake runner to pretend otherwise. If the operator wants enforcement, the upgrade is
a small GitHub Actions workflow (triggered on changes to `hooks.json`, `bin/formation`,
the checked docs, or the tests) made a **required** branch-protection check — a
one-file addition, but an explicit operator decision, not a v0 default. Absent that,
the honest ceiling is "these are release-time assertions in the test suite."

## Explicitly dropped

- The standalone `doc_drift_check.py` "detector" + its `@invariant` framework
  branding + the pre-push/sync-jog "runner" (all three CRITICAL/HIGH-rooted). The
  prototype is deleted or demoted to nothing; the assertions live in the two test
  files.
- Invariants #3 (plugin-skills) and #4 (per-hook-name) — thin-docs false positives.
- The commit-time nudge C — double-nags `code_review_suggest.sh`; basename→doc grep
  is noise. Reconsider only as one line in that existing hook gated on a real test
  failure, never in v0.
- The "report-only → blocking promotion" pipeline — no runner accumulates its signal;
  it was aspirational cover. Enforcement = CI-or-nothing, stated plainly.

## Baseline & scope honesty

- After adding the two tests + sentinels, both tests **pass** today (18 == 18; all 8
  formation verbs documented). Green baseline is real, not by luck.
- The magi/rails skill thinness codex/round-1 noted is **not** touched here — it's
  the runbook's (B's) backlog, not a test, because it's thinness not contradiction.
- Total surface: 2 test files (~60 lines each), 3 one-line sentinel additions, 1
  runbook, 1 versioning-skill step. No framework, no new hook, no phantom runner.

## Resolved (confirm round)

The confirm round answered the open question: **honest v0 = the formation-dispatch
test + the runbook, count test dropped, numerals removed.** The formation test checks
a genuine public contract with zero ceremony; the count test's ceremony wasn't worth
its weak signal. CI as a required check remains the only durable *enforcement* and is
an explicit later operator decision — not faked with a phantom pre-push/sync runner.

## Shipped (this commit)

- `plugins/harness-formation/tests/test_docs_match_dispatch.py` (green; bug-hunted
  against a planted drift, an unparseable dispatch, and a mismatched handler).
- `docs/DOC_AUDIT_RUNBOOK.md` — the generative sweep procedure.
- `versioning` skill step 4b — runs the test (require exit 0) + the runbook before a
  MINOR/MAJOR tag. That is the real runner: a human-in-the-loop cadence that already
  fires, not an advisory hook that rots.
- Removed the drift-bait "18 hooks" numerals; `hooks.json` is the authoritative set.
