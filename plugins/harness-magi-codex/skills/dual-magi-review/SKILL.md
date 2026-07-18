---
name: dual-magi-review
description: Independent multi-perspective peer review of a large design doc, orchestrated from Codex. Runs three same-family reviewers as parallel `codex exec` processes, then a MANDATORY cross-family round via headless Claude or explicit Grok fallback to subtract shared training bias. A plateau ("this design is done") cannot be declared by the model — only a gate script may, and only when it mechanically confirms the selected cross-family provider reviewed this exact revision. Use for design docs of at least 500 lines, production-critical changes, or anything you wrote yourself and are now reviewing. Not for small diffs or docs under roughly 200 lines.
---

# dual-magi-review (Codex orchestrator, Claude/Grok cross-family)

A "Magi" is a panel of independent, perspective-orthogonal reviewers. **dual-magi** pairs
same-family reviewers with a **cross-family** reviewer — a different model family — because
same-family reviewers share training-data blind spots and will confidently agree with each other.

This is the mirror of the Claude-orchestrated plugin (`plugins/harness-magi/`): here **Codex is
the orchestrator; Claude is the preferred cross-family reviewer and Grok is an explicit fallback**.

## Family routing policy

For design docs that will lead to implementation, reviewers should evaluate whether the design
respects the default ultramagi routing:

```text
Claude: planning / design plateau
Codex: implementation
Claude: adversarial design-intent review
Codex: final fixes + tests
```

This is not a substitute for cross-family review; it is a role contract. A design that asks the
same family to design, implement, and approve its own interpretation should justify that choice or
be marked for revision.

Fallback when a family is unavailable:

- If Claude is unavailable because of quota, capacity, auth, or CLI failure, select Grok
  explicitly. Record preferred/actual routing in an operator `FAMILY_ROUTING` note; the adapter
  meta mechanically records the actual provider only. A verified Grok round is cross-family
  relative to Codex and may satisfy the plateau gate.
- If Codex is unavailable, implementation should be limited to reversible scaffolding/tests until
  Codex can perform the coding or final executable review.
- If either cross-family adapter is unavailable, write a `FAMILY_ROUTING` note documenting
  preferred routing, actual routing, missing family/phase/reason, and what must run before ship.

## Why cross-family is mandatory, not optional

Field data from this repo's own review of the design that produced this skill:

| round | family | verdict | findings |
|---|---|---|---|
| 1 | Claude ×3 | REVISE | 25 (6 CRITICAL) |
| 2 | **codex** | **REJECT** | **5 NEW CRITICAL — none touched by the 3 Claude reviewers** |
| 3 | codex | REJECT | 3 new |
| 4 | codex | REJECT | 0 new (1 self-contradiction) |
| 5 | codex | GO | 0 |

Round 2's five criticals included two that were unimplementable-as-written (a `@file` flag form
the CLI rejects; a hash field the transcript does not contain) and one live credential-leak path.
Same-family consensus at round 1 was *not* evidence of correctness. This mirrors gh #195, where
four Claude CONFIRM rounds were overturned by one Codex round.

## The loop (one invocation = one round)

All `scripts/...` and `schemas/...` references below are relative to the
installed `harness-magi-codex` plugin root (two directories above this
`SKILL.md`), never the user's project root. Resolve that absolute plugin root
before invoking a bundled script.

```
1. fan-out    scripts/magi_fanout_codex.sh <doc> <round> <state-dir> [--persona-set magi]
              -> three `codex exec` processes, read-only, schema-constrained output
2. synthesize read the three round_<N>_<persona>.json; write round_<N>_codex.json
3. cross-family (MANDATORY before any plateau claim)
              scripts/magi_xfamily.sh --reviewer claude|grok <doc> <round> <prior.json|-> <state-dir>/round_<N+1>_xfamily
4. gate       scripts/magi_plateau_gate.sh <doc> <state-dir>/round_<N+1>_xfamily
                --reviewer-family claude|grok
              -> writes .dual-magi/PLATEAU.<doc-id>.<artifact-sha-prefix> ONLY if G1..G9 pass
5. revise the doc with the findings; re-invoke for the next round
```

State lives in `${doc_dir}/.dual-magi/` (already gitignored via `docs/**/.dual-magi/`).

## You may not declare plateau

**The model does not decide when review is finished.** `magi_plateau_gate.sh` does, and it
refuses unless it can confirm all of:

| assert | what it blocks |
|---|---|
| G1 | cross-family round missing, or its verdict is outside the schema enum |
| G2 | a same-family round or provider-label mismatch masquerading as cross-family |
| G3 | a **stale** round — reviewed a different revision of the doc (`artifact_sha` mismatch) |
| G4 | findings swapped after the adapter wrote them (`output_sha` mismatch) |
| G5 | `num_turns < 1`, or `num_turns <= 1` while operations are reported |
| G6 | a `session_id` that resolves to no selected-provider transcript, or transcript/model mismatch |
| G7 | a `REJECT` or `REVISE` verdict |
| G8 | any unresolved `REJECT`/`CRITICAL`/`HIGH` finding, whatever the headline verdict says |
| G9 | ungrounded rounds: `schema_grounding_verdict: FAIL`, an **empty** `verify_commands_executed`, or commands claimed while the transcript shows no tool use |

If the gate exits non-zero, the design is **not** at plateau. Do not say it is. Do not proceed to
the irreversible step. This is a structural rail precisely because gh #195's root cause was an AI
forgetting a behavioral one.

## Plateau definition

The marker means only that G1-G9 passed: the selected cross-family provider reviewed the current
revision, returned a non-blocking verdict without unresolved HIGH-or-worse findings, and met the gate's minimal
grounding checks. A new-vs-prior findings ratio under roughly 20% remains useful operator judgment,
but the gate does not enforce it. Same-family agreement is **never** plateau.

A round that surfaces new HIGH-or-worse findings — even at `GO-WITH-REVISE` — is not plateau. Keep going.
Conversely, refusing to ever converge is its own failure mode: when discovery has stopped and the
doc is honest about its limits, ship it.

## Schema grounding

Every reviewer must verify claims by **running commands** (`rg`, `grep`, reading real files,
`--help` output) and report them in `verify_commands_executed`. Doc-vs-reality drift is a CRITICAL
finding. A round whose reviewers only read prose is **degraded** regardless of its stated verdict.

Note the limit, and state it honestly: schema conformance guarantees nothing about content truth.
Both CLIs use constrained decoding and will **fabricate** a required field to satisfy a schema
(measured). G9 checks only that the reported operation list is non-empty and that the provider
transcript contains some tool use; it does not prove those lists correspond. A reviewer that runs
one read tool and invents its findings will pass. That residual risk is real.

## Threat model — read before trusting the gate

The gate protects against **T1: accidental skip** — a forgotten flag, a buggy script, a stale
artifact reused as if fresh. It does **not** protect against **T2: an adversarial process running
as the same OS user**, which can write any of these files, including the transcript. Nothing here
is forgery-resistant. Do not describe it as such.

## Args

```
dual-magi-review <doc-path> [--rounds N] [--persona-set magi|bug-hunt]
                            [--no-cross-family <reason>]
```

`--no-cross-family` is an audited, explicit opt-out — not a silent skip. The gate still refuses to
write a marker, so an opted-out review **cannot claim plateau**.

Env: `MAGI_XFAMILY_CLAUDE_MODEL` (legacy fallback `MAGI_XFAMILY_MODEL`, default
`claude-fable-5`), `MAGI_XFAMILY_GROK_MODEL` (default `grok-4.5`), and
`MAGI_XFAMILY_TIMEOUT_S` (default `900`).

Adapter exit codes: `0` = round complete · `2` = fail-closed, no usable result · `3` = lock held
(recursion, or a concurrent review of the same doc).

## Fail-closed

If the cross-family round fails (CLI missing, timeout, unparseable output), the adapter writes
`*_xfamily.FAILED.json` — deliberately **not** at the success path's filename — and exits 2.
It does not fall back to same-family-only. The cross-family round is a *necessary condition* for
plateau; continuing without it is the exact failure this skill exists to prevent.

## Constraints

- **DB-grounded docs are out of scope for v1.** The reviewer allowlist contains no database tools.
  A reviewer running `psql 'postgres://user:PASSWORD@host/db'` would persist that credential into
  `verify_commands_executed`, which is then fed into the next round's prompt and re-transmitted to
  another vendor's API. A credential-safe wrapper is future work.
- The doc is sent in full to another model vendor. Ensure it contains no secrets.
- Reviewer independence is structural: the fan-out script is the **sole author** of reviewer
  prompts and starts all three processes before reading any output. Do not hand-compose prompts.

## Not for

Small code diffs (use `/simplify`), single-function checks, docs under ~200 lines (overhead
exceeds value), or a time-critical hotfix (this takes hours).
