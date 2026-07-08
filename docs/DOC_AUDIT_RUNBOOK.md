# Doc-audit runbook — the generative half of anti-drift

The deterministic tests (e.g. `plugins/harness-formation/tests/test_docs_match_dispatch.py`)
catch *contradictions* — a documented contract the code no longer matches. They do
**not** catch *thinness* — a real feature no doc names. That is a generative problem
a human runs on a cadence. This runbook is that procedure. Run it before a MINOR or
MAJOR release (the `versioning` skill names it as a step), or ad hoc when a
subsystem has grown.

Design rationale + why not "auto-write docs on every commit":
`docs/designs/ANTI_DOC_DRIFT.md`.

## Procedure (proven 3× on 2026-07-08)

1. **Isolate.** Create a read-only worktree pinned at the branch head so a long
   sweep isn't disturbed by parallel commits:
   `git worktree add --detach ../<repo>-sweep <branch>`.

2. **Find the baseline.** The docs' staleness = the commit where the core docs were
   last touched. `git log -1 --format=%ad --date=short -- docs/ README.md`; take the
   oldest load-bearing doc as the baseline `$BASE`.

3. **Scope the diff.** `git diff --stat $BASE HEAD -- <src dirs>` and
   `git diff --name-status $BASE HEAD | grep '^A'` — the added modules/files are the
   prime suspects for undocumented features.

4. **Sweep in parallel.** One reader per subsystem (Task/Agent subagents,
   non-overlapping). Each returns, per feature: what it does · code location · is it
   in the overview docs / a subsystem doc / nowhere · verdict
   **TRACKED / DOC-ONLY / UNTRACKED / DOC-BUG** (doc contradicts code). Ground every
   claim in a real file; a feature named only in prose without its identifier reads
   as more-untracked than it is — spot-check.

5. **Rank by operator risk.** DOC-BUG first (a wrong doc misleads worse than a
   missing one), then guards/mutators that surprise, then thin docs.

6. **Fix, human-gated.** Edit the affected docs (delegate non-overlapping files to
   parallel subagents for large sweeps; mirror `.ja` siblings). Never auto-commit —
   read the diff.

7. **Verify.** Confirm changed-file set is only the intended docs; run the
   deterministic tests; check links resolve and translated siblings have no leftover
   source-language paragraphs.

8. **Merge + clean up.** ff-merge to dev, remove the worktree, push.

## Distinctions that keep it honest

- **Fix a DOC-BUG in the doc; file an UNTRACKED as a doc gap, don't force a gate on
  it.** Padding a flagship README with an exhaustive list nobody asked for, just to
  satisfy a mis-scoped check, is churn — that is exactly why the deterministic layer
  only checks *public-contract enumerations*, not thinness.
- **Point at the SoT instead of copying it.** A prose "N hooks" numeral is drift-bait;
  prefer "the full wired set is in `hooks.json` (authoritative)" over a copied count
  no test guards.
- **design-history / point-in-time records are not a current-state surface** — leave
  them; they describe what was true when written.
