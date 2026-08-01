# Issue #276 — briefing-to-warmup handoff

## Outcome

Move preparation to the cheapest safe point:

- irreversible or destructive work keeps the existing design/review gates;
- reversible work starts once its first bounded step and rollback are known;
- a background job expected to run at least 30 minutes prompts reconnaissance of the next step.

## Invariant

No new path may cross an irreversible boundary without the existing exact-revision and
implementation gates. The lighter path authorizes only local, bounded, reversible work and never
authorizes deployment, canonical mutation, or a plateau claim.

## Ready-to-drive checkpoint

Reversible execution may start when all four facts are explicit:

1. the task invariant is stated;
2. the first executable step is known and bounded;
3. rollback or disposal of that step is known;
4. no known CRITICAL/HIGH finding invalidates that step.

Open implementation-detail questions move to executable checks and in-flight reconnaissance. They
do not cause another prose-only design round. A finding that changes the invariant or makes the
step irreversible returns the task to the heavy briefing track.

## Changes

1. Extend `self_check_reminder.sh` with a conservative `>=30 min` reconnaissance class. Preserve
   the existing early self-check. The prompt tells the agent to inspect code branches/constants,
   doc-referenced objects, naming assumptions, and test lockstep; drift is recorded for the next
   phase, not fixed opportunistically.
2. State the reversible/heavy split in `AGENTS.md` and all three ultramagi surfaces. Exact plateau
   remains required before irreversible boundaries, but not before a reversible warmup step.
3. Add hook tests for foreground/small-background silence, monitoring-only output, reconnaissance
   output, cross-CLI payloads, and self-check recursion suppression.

## Acceptance / rollback

- Hook tests prove reconnaissance appears only for a background command in the conservative
  long-wait class, including path/module-shaped ingest/embed/export jobs; shell `export NAME=value`
  and monitor-only classes do not trigger reconnaissance.
- Magi doc-drift and skill-install tests remain green.
- Revert the hook, test, and instruction changes together; no persistent state or schema changes.

## Family routing

```text
preferred: Claude design -> Codex implementation -> Claude review -> Codex fixes/tests
actual: Codex concise design + reversible implementation -> Claude CLI unavailable -> Grok adversarial review -> Codex fixes/tests
missing: pre-code design plateau intentionally omitted because this local reversible change alters that policy; Claude CLI produced no review after two bounded attempts
degraded_until: Grok findings are fixed and executable tests pass; no irreversible action is in scope
```
