---
name: bug-hunt
version: 0.1.0
description: |
  Adversarial post-change review. Spawn three parallel Task agents focused on
  distinct bug categories — race / concurrency, edge-case / null-empty, and
  error-swallow / silent-failure — to surface defects that linear self-review
  systematically misses. The siblings of magi (pre-flight) and code-review
  (style): bug-hunt is the *adversarial* slot, looking for ways the change
  breaks under hostile inputs and concurrency.

  USE WHEN a non-trivial diff just landed: after commit / before push, after
  a deploy, after a multi-file refactor, or anytime the user says "bug-hunt"
  / "adversarial review" / "find what I missed." SKIP for doc-only diffs,
  single-line config changes, or diffs already reviewed by the same protocol.

  Cardinal rule: the hunters are independent. NEVER let one agent's findings
  contaminate another's prompt — that defeats divergence.
allowed-tools:
  - Task
  - Read
  - Grep
  - Bash
  - Write
---

# bug-hunt — adversarial post-change review

Distilled from a 2026-05-11 8-fix bug-hunt round on PRS-LLM-dev (= 8 real
defects found post-deploy on a clean-looking diff). Linear self-review missed
all 8; three adversarial perspectives caught them in parallel.

## Why it works

Linear self-review (one agent reading the diff top to bottom) drifts toward
the diff's own framing. Bug-hunters get **persona-anchored prompts** that
force them to look in directions the change author *didn't* think about:

- **HORNET** thinks in races, lock order, ordering assumptions, concurrent
  iterators, TOCTOU, retry-without-idempotency, deadlocks.
- **GNAT** thinks in null / empty / boundary inputs, off-by-ones, unicode,
  encoding, large inputs, empty collections, missing fields, type coercion.
- **WASP** thinks in error swallows, silent failures, fallback corrupting
  the happy path, logs that hide problems, exceptions caught too broadly.

Each gets the same diff; their **independent** lists are aggregated.
Convergent flags (2+ hunters on the same line) are highest-priority. Each
hunter must propose a concrete fix per finding — "is wrong" without "do X"
gets discarded.

## When to invoke

Trigger on:

- Non-trivial diff just landed (> 50 changed lines or > 3 files)
- Production deploy completed → before next deploy
- User says "bug-hunt" / "adversarial" / "find what I missed"
- Magi pre-flight verdict was PROCEED-WITH-CAUTION → confirm assumptions
  on the actual implementation
- Post-refactor verification (refactors silently change behavior in
  surprising ways)

**Skip for**:
- Doc-only diffs
- Single-line config edits
- Diffs already bug-hunted (cite the prior hunt)
- WIP commits known to be incomplete

## Protocol

### 1. Pin the diff

```bash
DIFF=$(git diff HEAD~1 HEAD)         # or HEAD vs main, or staged
echo "$DIFF" | wc -l                 # sanity: not empty, not 100k+ lines
```

If the diff is > 5000 lines, **split**: bug-hunt per logical chunk (one file
or one feature). Hunters lose signal in mega-diffs.

### 2. Round 1 — three Task agents in parallel

Spawn three `Task` calls **in a single assistant turn**. Each agent gets:

1. The diff (verbatim)
2. Its persona prompt template (under `templates/`)
3. Read-only access to surrounding code (so it can verify call sites)

Persona templates in this skill:

- `templates/hornet_prompt.md` — race / concurrency
- `templates/gnat_prompt.md` — edge-case / null-empty
- `templates/wasp_prompt.md` — error-swallow / silent failure

Each returns a list of findings in the format:

```
FINDING N: <one-line summary>
  file:line — <code excerpt>
  why broken: <one paragraph>
  fix: <concrete patch, code or instruction>
  severity: HIGH | MEDIUM | LOW
```

### 3. Aggregate

Merge all findings. Deduplicate by (file, line, theme). Mark **convergent**
(2+ hunters flagged) and **single-hunter** (one only). Convergent findings
have ~5× the true-positive rate of single-hunter findings — handle them
first.

### 4. Triage + fix

Per finding, decide:

- **Fix now** — HIGH severity, fix in same session, re-deploy after batch
- **gh issue** — MEDIUM, capture as P2/P3, fix in next pass
- **Dismiss with reason** — false positive (write down *why* the hunter was
  wrong; that protects against repeat false positives)

Avoid "fix later, mental note" — that's how findings get lost. Either fix or
issue.

### 5. Re-verify

After fixing, re-run the relevant smoke / curl / unit tests. If the change
touched user-visible behavior, manual test the happy path before declaring
the hunt complete.

## Output format

```
# Bug-hunt: <diff scope>

## Diff summary
- <N files changed, M lines>
- <one-line theme>

## Findings

### HIGH (n)
1. **<convergent | hornet | gnat | wasp>** file:line — <summary>
   <details> · fix: <action>

### MEDIUM (n)
...

### LOW (n)
...

### Dismissed (false positives)
- <finding> — <why not actually broken>

## Actions
- Fixed in this session: <list>
- Filed as gh issue: <list with #s>
- Re-verified with: <smoke / test command>
```

## Anti-patterns

- **One agent doing all three perspectives** — defeats divergence. Independent
  prompts are load-bearing.
- **Letting hunters see each other's findings** — biases the second/third
  agent toward "I already saw that, skip" instead of independently surfacing
  it (which would be a convergent flag = HIGH-priority signal).
- **Mega-diff hunts** — > 5000 lines drowns signal. Split by file or feature.
- **"Looks fine to me" dismissal** — every dismissed finding gets a written
  reason so the next hunt doesn't repeat the same false positive.
- **No re-verification** — fixing the finding without re-running the smoke
  reintroduces "we *think* we fixed it" optimism that bug-hunt is meant to
  remove.

## Related

- `magi` — *pre-change* three-perspective review (planning slot)
- `bug-hunt` (this skill) — *post-change* adversarial review (verification
  slot)
- `code-review` / `pr-review-toolkit` — *style + cleanliness* review
  (orthogonal axis)

The three together form a triangle around a change:
- magi: should we do this?
- code-review: is this *clean*?
- bug-hunt: does this *break*?
