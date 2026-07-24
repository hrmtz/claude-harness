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
0. arm once   python3 scripts/magi_autorun.py arm <doc>
              -> binds the campaign to this Codex thread; no user acknowledgement is required
1. fan-out    scripts/magi_fanout_codex.sh <doc> <round> <state-dir>
                [--persona-set magi] [--prior <prior-synthesis.json|->]
              -> three `codex exec` processes, read-only, schema-constrained output
2. synthesize read the three round_<N>_<persona>.json; write round_<N>_codex.json with
              reviewer=SYNTHESIS, exact source_artifacts digests, and one disposition for every
              source finding
2b. converge  python3 scripts/magi_design_convergence_gate.py evaluate <doc>
              -> bounded next action; stop on REDESIGN, SCOPE_SPLIT, or BLOCKED
3. cross-family (MANDATORY before any plateau claim)
              scripts/magi_xfamily.sh --reviewer claude|grok <doc> <round> <prior.json|-> <state-dir>/round_<N+1>_xfamily
4. gate       scripts/magi_plateau_gate.sh <doc> <state-dir>/round_<N+1>_xfamily
                --reviewer-family claude|grok
              -> writes .dual-magi/PLATEAU.<doc-id>.<artifact-sha-prefix> ONLY if G1..G9 pass
5. revise the doc with the findings; re-invoke for the next round
```

After every phase, including cross-family, create a synthesis envelope before the next round.
Round 1 uses `--prior -`. Every later fan-out and cross-family round requires the immediately
preceding synthesis JSON. It must be schema-valid, live in the active state directory, identify the
same canonical document, and carry `round == current_round - 1`. Do not silently start a fresh
broad review in the middle of a campaign.

A synthesis may deduplicate or resolve findings, but it must not silently omit them. For every
`<source-file>#<finding_id>`, add one disposition: `carried`, `duplicate`, `resolved`, or `deferred`.
Carried/duplicate entries must name a real `synthesis_finding_id`. The validator discovers every
preceding-round JSON source in the active state directory and verifies exact path/digest coverage.

State lives in `${doc_dir}/.dual-magi/` (already gitignored via `docs/**/.dual-magi/`).

Arming is mandatory for this skill. On its intact path, the plugin Stop hook refuses a mid-campaign stop and injects
the next-turn continuation automatically. It ends only on an exact-revision plateau marker, fixed
fuse exhaustion, an explicit terminal command, or two continued turns with no durable progress.
Never replace this with a user acknowledgement prompt.
Hook-internal parse or I/O failure is deliberately fail-open to avoid an unrecoverable Stop loop;
the separate campaign guard remains fail-closed for provider spend.

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

## Campaign convergence guard

After each successful fan-out or cross-family phase, run the report-only design
convergence evaluator. It stops mechanically on a repeated HIGH+ root,
recurring new HIGH+ roots in one subsystem, non-decreasing blocker mass across
three revisions, two logical correction cycles, or an unaffordable transition
that must preserve the final cross-family launch.

`PLATEAU_CANDIDATE` means only that the current exact revision has zero HIGH+
roots and a verified cross-family artifact. It never creates a marker and never
authorizes implementation. Only `magi_plateau_gate.sh` may establish plateau
after G1-G9.

Plateau safety and autonomous-loop safety are separate. Before launching any reviewer, both
adapters claim from a canonical document-scoped ledger through `scripts/magi_campaign_guard.py`.
The default autonomous ceiling is 16 weighted model launches: fan-out costs 3, implementation-only
incremental targeted review costs 1, and cross-family costs 1. Fan-out and targeted review both
reserve the following cross-family unit. Retries consume
budget; repeating round 1 or changing state directory cannot reset it. Above it, scripts exit `4` with
`CAMPAIGN BUDGET EXHAUSTED — NOT PLATEAU` before a model starts.

Exit `4` does not waive unresolved findings and does not authorize implementation. Stop document
mutation for the exhausted campaign, choose an in-scope correction/scope reduction/primitive
replacement autonomously, then invoke round 1 again. If the document or review-protocol SHA
changed, the guard automatically rolls over without user acknowledgement. Every revision campaign
shares one fixed global allowance of 16 weighted model launches. At global exhaustion, emit a definitive blocked
result; never pause for an acknowledgement and never reset history through a fresh state directory.

If requirements change while a claim is live, do not start another adapter, edit the document, or
abandon the ledger entry implicitly. First run:

```bash
python3 scripts/magi_campaign_guard.py cancel-revision "$DOC" \
  --expected-artifact-sha "$(sha256sum "$DOC" | cut -d' ' -f1)" \
  --reason "requirements changed: <brief reason>"
```

Cancellation is exact-artifact and fail-closed: the charged launch becomes
`superseded-by-requirement-revision` only after the verified adapter process tree is gone and the
canonical review lock is available. A cleanup-blocked result is terminal until the same command
can finish cleanup. Change the document content before invoking replacement round 1; a protocol
change alone cannot restart a superseded revision.

For every finding, `dup_flag` is schema-bounded to `new`, `duplicate`, `regression`,
`readiness-gap`, or `scope-expansion`. After round 2, freeze committed scope. Missing evidence
explicitly scheduled for later is a readiness gap; an optional stronger guarantee or new subsystem
is scope expansion. Neither may be HIGH-or-worse. If existing committed behavior is unsafe or
unimplementable, classify it as `new` or `regression` instead. If readiness gaps and scope
expansions are the only findings, use `GO-WITH-REVISE`, not `REVISE` or `REJECT`.

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
(recursion, or a concurrent review of the same doc) · `4` = autonomous campaign budget exhausted,
autonomous pivot or definitive blocked result required · `64` = invalid invocation or
ceiling arguments.

Env: `MAGI_MAX_AUTONOMOUS_MODEL_LAUNCHES` may tighten the default ceiling of 16 but cannot extend it.
There is no acknowledgement or authorization path that extends the fuse.
`MAGI_FANOUT_TIMEOUT_S` may tighten the fan-out deadline from its default/maximum of 900 seconds.

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
