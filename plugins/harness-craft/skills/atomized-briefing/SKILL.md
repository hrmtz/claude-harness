---
name: atomized-briefing
version: 0.1.0
description: |
  Write an implementation plan / worker briefing as atomized 2-5 minute
  steps with exact file paths, explicit Consumes/Produces interfaces, and
  zero placeholders — so a context-free executor (formation worker, spawned
  subagent, or future you) can run it without re-deriving intent.

  USE WHEN briefing a formation worker, dispatching subagents over a
  multi-task plan, or writing any plan another session will execute.
  SKIP for single-task work you will execute yourself in this session.
allowed-tools:
  - Read
  - Write
  - Grep
---

# atomized-briefing — plans a context-free executor can run

Distilled from [obra/superpowers](https://github.com/obra/superpowers)
`writing-plans` (MIT, Jesse Vincent). Reframed for harness-formation worker
briefings and subagent dispatch.

## Audience assumption

Write for a skilled engineer who knows **nothing** about this codebase, this
toolset, or the discussion that produced the plan. A formation worker's pane
and a spawned subagent both start context-free — the briefing is their entire
world.

## Task boundaries

A task is the smallest unit that carries its own test cycle and is worth a
reviewer's gate. Fold setup/scaffolding/docs into the task whose deliverable
needs them; split only where a reviewer could reject one task while approving
its neighbor. Each task ends with an independently testable deliverable.

Steps inside a task are one action each (2-5 minutes): write the failing
test → run it, expect FAIL → minimal implementation → run it, expect PASS →
commit.

## Required blocks per task

```markdown
### Task N: <component>

**Files:**
- Create: exact/path/to/file.py
- Modify: exact/path/to/existing.py:123-145
- Test:   tests/exact/path/test_file.py

**Interfaces:**
- Consumes: <exact signatures this task uses from earlier tasks>
- Produces: <exact names/types later tasks rely on>
```

The Interfaces block is load-bearing: an executor sees only their own task —
this block is how they learn what neighboring tasks call things. A function
named `clearLayers()` in Task 3 but `clearFullLayers()` in Task 7 is a plan
bug; catch it in self-review.

Plan header carries **Goal** (one sentence), **Architecture** (2-3
sentences), and **Global Constraints** (version floors, naming rules — exact
values copied verbatim from the spec; every task implicitly includes them).

## No placeholders — these are plan failures

- "TBD" / "TODO" / "implement later"
- "Add appropriate error handling" / "handle edge cases"
- "Write tests for the above" (without the actual test code)
- "Similar to Task N" (repeat the code — tasks may run out of order)
- Steps that say what without showing how (code steps require code blocks)
- References to names/types not defined in any task

## Self-review (run yourself, before dispatch)

1. **Spec coverage** — every spec requirement maps to a task; list gaps.
2. **Placeholder scan** — grep the plan for the failure patterns above.
3. **Interface consistency** — names/signatures match across tasks.

Fix inline and move on; no re-review round needed.

## Harness wiring

- **formation**: paste per-task briefings into the worker's spawn prompt;
  keep the plan file as the shared SoT the mailbox refers to by task number.
- **subagent dispatch**: one fresh agent per task; review between tasks
  (spec compliance first, code quality second).
- Plans other sessions will execute live in the repo (e.g. `docs/plans/`),
  not in scratch space.

---
*Upstream: obra/superpowers `writing-plans` — MIT © 2025 Jesse Vincent.
Distilled and adapted for claude-harness (formation/subagent framing).*
