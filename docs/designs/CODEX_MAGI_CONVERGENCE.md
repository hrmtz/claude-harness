# CODEX_MAGI_CONVERGENCE — bounded autonomous review campaigns

status: implementation design
author: codex-cinder-raven
date: 2026-07-19
scope: `plugins/harness-magi-codex/`

## FAMILY_ROUTING

```text
preferred: Claude design plateau -> Codex implementation -> Claude design-intent review -> Codex fixes/tests
actual: user authorized Codex work; Codex produced a reversible local spike, then Codex Magi review;
        Claude exact-revision cross-family review remains mandatory before acceptance
missing: Claude pre-spike planning plateau (the spike is not accepted implementation)
reason: user-directed routing exception for an immediate reversible Codex spike; Claude availability
        was not the reason and no implementation is accepted before its exact-revision review
degraded_until: Claude cross-family gate passes the exact design revision and Codex applies findings/tests
```

## 1. Problem

The Codex-orchestrated dual-magi loop has a strong plateau safety gate but no autonomous
campaign stop. Those are different controls:

- The plateau gate answers: “may this exact revision be called reviewed?”
- A campaign guard must answer: “may another autonomous review round be started?”

The current skill says that a new HIGH-or-worse finding requires another round. The fan-out
reviewers do not receive prior findings, `dup_flag` is unconstrained text, and the plateau gate
only examines the latest cross-family artifact. A reviewer can therefore widen the design on each
pass, then report the consequences of that widening as new blockers. Nothing requires the number
of new blockers to decline.

The observed deja-code Phase 2 campaign reached same-family round 23 with a REJECT verdict after
repeated document revisions. Round 23 again found a CRITICAL admission-accounting problem and
several HIGH findings. The correct response is neither “plateau” nor an unbounded round 25. The
orchestrator must make a bounded in-scope correction or primitive/scope reduction itself; if the
fixed global fuse is then exhausted, it emits a terminal blocked result without waiting for an ack.

## 2. Non-goals

- Do not weaken G1–G9 or permit unresolved HIGH findings at plateau.
- Do not infer semantic equivalence between findings with string similarity.
- Do not add an acknowledgement or authorization path that extends the fixed global fuse.
- Do not make a campaign marker forgery-resistant.
- Do not automatically edit the reviewed document.
- Do not treat a review-budget stop as permission to implement or ship.
- Do not add heartbeat, paging, host scheduling, or resource-reservation subsystems. A bounded
  provider deadline and descendant cleanup are in scope; broader monitoring remains follow-up.

## 3. Invariants

### C1 — bounded autonomous execution

Fan-out and cross-family adapters must share a canonical document-scoped ledger and refuse more
than 16 model launches by default. Fan-out costs 3 and cross-family costs 1, so without retries
this permits four pairs:

```text
fan-out 1 -> cross-family 2
fan-out 3 -> cross-family 4
fan-out 5 -> cross-family 6
fan-out 7 -> cross-family 8
```

Retries consume launch budget. A caller cannot reset the budget with a fresh state directory or by
repeating round 1. Exit 4 means campaign-budget exhaustion. It is not an adapter failure and not
plateau.

### C2 — acknowledgement-free, bounded rollover

The orchestrator must not pause for user acknowledgement between review campaigns. When a round-1
fan-out would otherwise be denied, the guard automatically starts a new campaign only when the
document SHA or review-protocol SHA changed since the last claim.

- at most 16 weighted model launches across the entire canonical ledger;
- retries remain counted and a fresh state directory never resets either total.

There is no separate campaign-count ceiling: every new campaign begins with a weight-3 fan-out, so
the fixed global fuse itself bounds campaign count. This avoids a second stop condition that could
strand usable launch budget while still preventing spend expansion.

At global exhaustion the orchestrator emits a definitive blocked result; it does not ask for an
acknowledgement and does not silently reset history.

### C3 — prior state is mandatory after round 1

Every round after round 1 must receive a schema-valid prior synthesis artifact from the same state
directory, canonical document identity, and immediately preceding round. `-` is valid only for
round 1. `{}`, another document's artifact, and a skipped-round artifact fail before model launch.

A changed document/protocol may roll into the next bounded campaign automatically. A new state
directory alone never resets canonical history.

The same-family prompt must receive prior findings just as the cross-family prompt does. This
does not remove reviewer independence: siblings receive the same immutable prior artifact and are
still started before any sibling output is read.

### C4 — finding relationship is schema-bounded

`dup_flag` becomes an enum:

- `new`: a newly discovered defect in the committed scope;
- `duplicate`: substantively covered by a prior finding;
- `regression`: introduced by applying a prior fix;
- `readiness-gap`: evidence or operational preparation intentionally scheduled later;
- `scope-expansion`: optional new capability or stronger guarantee outside committed scope.

`readiness-gap` and `scope-expansion` findings may not be `REJECT`, `CRITICAL`, or `HIGH`.
If the existing committed scope is actually unsafe or unimplementable, the finding is `new` or
`regression`; it is not optional expansion.

If readiness gaps and scope expansions are the only findings, the headline verdict is
`GO-WITH-REVISE`, not `REVISE` or `REJECT`. Otherwise G7 would correctly deny plateau despite the
finding classification, recreating the loop through a contradictory headline.

### C5 — scope freeze after the first pair

After round 2, reviewers prioritize:

1. resolution of prior blockers;
2. regressions introduced by those resolutions;
3. contradictions or unsafe behavior inside the already committed scope.

Reviewers may record readiness gaps and optional expansion, but must not use them to perpetuate a
blocking loop. They must not demand a new subsystem merely because it would be more robust than
the documented contract.

### C6 — plateau remains independent

Campaign accounting never writes a `PLATEAU.*` marker. Passing the campaign guard merely
allows a reviewer process to start. The existing plateau gate remains the sole plateau marker
author and continues to require G1–G9.

### C7 — no silent compatibility fallback

A round above the fixed global budget must fail before launching any model.
A later round without a prior artifact must fail before launching any model. Neither condition
may silently degrade to a fresh broad review.

### C8 — the session cannot stop mid-campaign

At skill entry, `magi_autorun.py arm <doc>` binds an opt-in campaign to the current
`CODEX_THREAD_ID`. The plugin Stop hook reads that registry. While the campaign is active it blocks
the stop and gives Codex a continuation reason to inspect durable state and run the next legal
phase; it never asks the user to type “continue” or approve another round.

The hook completes automatically when it sees the exact-revision plateau marker. It emits a
terminal blocked result when the fixed fuse cannot fund the next phase. Two consecutive continued
turns with no document or ledger progress also become terminal blocked, preventing an internal
no-op loop. The provider adapters remain the long-running phase executors; the Stop hook is the
session-lifecycle controller, not a background provider process.

## 4. Components

### 4.1 `magi_campaign_guard.py`

Commands:

```text
magi_campaign_guard.py claim <doc> <round> fanout|xfamily <state-dir>
magi_campaign_guard.py finish <doc> <claim-id> success|failed
magi_campaign_guard.py new-campaign <doc> --operator <label> --reason <text>
```

`claim` atomically appends a weighted provider-launch record to a canonical
`.dual-magi/CAMPAIGN.<doc-id>.json` ledger under a document lock. It enforces alternating legal
transitions and allows at most two failed attempts for the same round/phase. A successful phase
cannot be retried. `finish` closes the claim as success or failure; a process that dies after claim
remains conservatively charged and is marked abandoned by the next lock owner. `new-campaign` is
an environment-gated deterministic-test boundary (`MAGI_TEST_ALLOW_NEW_CAMPAIGN=1`) that preserves
history and cannot extend the global fuse. Normal
orchestration uses automatic rollover and never requires this command or a user acknowledgement.

The global ceiling comes from `MAGI_MAX_AUTONOMOUS_MODEL_LAUNCHES`, default 16. The environment may
only tighten it; values above 16 are rejected and there is no extension artifact or command.

### 4.2 Fan-out integration

`magi_fanout_codex.sh` gains `--prior <path|->`, validates prior identity/round/state, checks the
Codex capability, and acquires a canonical document execution lock before claiming from the guard.
It then starts Codex processes. Round 1 defaults to `-`; later rounds require a file.

Each provider is wrapped by `timeout` with `MAGI_FANOUT_TIMEOUT_S` (default and maximum 900
seconds). Provider and scrubber descendants close the inherited document-lock fd. Parent signal and
exit cleanup terminates/reaps reviewer shells, clears owned temporary artifacts, finishes the claim
as failed, and leaves a bounded retry path.

The prompt receives a convergence contract and the scrubbed prior artifact. Since the script
remains the sole prompt author, all three siblings receive identical campaign context.

### 4.3 Cross-family integration

`magi_xfamily.sh` validates and uses the same canonical document execution lock before it checks
the provider and claims through the guard. It rejects `-` as prior for rounds greater than 1. The cross-family prompt receives the same finding
classification and scope-freeze contract.

### 4.4 Schema and post-output validation

The finding schema constrains `dup_flag` and requires canonical `artifact_id` plus reviewed
`artifact_sha`. Prior validation requires the same artifact identity, immediately preceding round,
and active state directory. The Codex response-format schema subset rejects JSON
Schema `allOf`/`if` conditions, so `scripts/magi_validate_findings.py` performs the cross-field
checks after constrained decoding and before an artifact is accepted. Both adapters fail the round
if a readiness/scope observation has blocking severity or if an observation-only response uses a
blocking headline. This remains structural enforcement without claiming semantic honesty.

### 4.5 Stop-hook controller

`magi_autorun.py` stores a session-bound registry under the user state directory and mirrors a
document-scoped `AUTORUN.<doc-id>.json`. Its `arm`, `complete`, and `blocked` commands are
non-interactive. `hooks/magi_autorun_hook.sh` invokes `--hook` on Codex Stop events. Campaigns are
opt-in, so an unrelated Codex session or an unarmed document is never trapped by the hook.

## 5. Failure semantics

| condition | exit | meaning |
|---|---:|---|
| legal launch within base budget | 0 | reviewer may start; launch is durably counted |
| legal launch after automatic revision rollover | 0 | reviewer may start; prior history remains counted |
| fixed global budget exceeded | 4 | emit terminal blocked result; no ack prompt |
| unreadable or inconsistent canonical ledger | 2 | fail closed as state corruption; not budget exhaustion |
| later round has no prior artifact | 64 | invocation contract error |
| illegal phase order or exhausted same-phase retry | 64 | caller transition error; not budget exhaustion |
| invalid ceiling override | 64 | no claim is written |

Exit 4 must be documented in README and SKILL.md. It must never be interpreted as plateau or as a
waiver of unresolved findings.

## 6. Test matrix

### Campaign guard

- four legal pairs consume 16 weighted model launches under defaults;
- the next fan-out would consume 19 and fails with exit 4;
- retries consume budget and a third same-round attempt fails;
- a fresh state directory cannot reset the canonical campaign;
- a changed document/protocol can roll over automatically but retains the global spend;
- no file or command can extend the fixed global fuse;
- an environment launch ceiling below 16 is honored globally;
- an environment launch ceiling above 16 is rejected;
- legacy unweighted fan-out claims migrate as weight 3, not weight 1;
- an inconsistent stored phase weight fails closed;
- missing provider capability and a held execution lock fail before claim.
- a hung fan-out reaches its deadline, releases the document lock, closes the failed claim, and can
  use its one bounded retry;
- an armed Stop hook continues without ack, completes on exact-revision plateau, and terminates
  after two no-progress continuations rather than looping internally.

### Invocation contract

- fan-out round 1 accepts no prior artifact;
- fan-out later round rejects `-` before launching Codex;
- fan-out later round includes scrubbed prior content in every prompt;
- cross-family later round rejects `-` before launching the provider;
- both adapters stop above budget before launching a provider.

### Schema

- every classification enum value validates;
- unknown `dup_flag` fails;
- `scope-expansion` HIGH fails;
- `readiness-gap` CRITICAL fails;
- `new` HIGH and `regression` CRITICAL remain valid.

### Existing safety

- G1–G9 plateau tests remain unchanged in meaning;
- provider provenance tests remain green;
- secret scrub and read-only tests remain green;
- plugin and skill validation pass.

## 7. Cut line and measurement

The 16-call value is a provisional safety fuse, not a claim that 16 is the ROI-optimal number. The
only available historical baseline is the user-reported deja-code campaign reaching 17 Magi
iterations / same-family round 23; the repository contains no trustworthy token-cost, elapsed-time,
or per-round finding-yield record for it. This patch therefore does not invent dollar savings.

For the next real campaigns, retain weighted launches, phase timestamps, attempts, terminal state,
and the count of carried `new`/`regression` HIGH-or-worse findings per pair. Success means the fuse
stops post-pair-four spend without losing a later-confirmed committed-scope blocker. Revisit 16 only
from that evidence; an environment override may tighten but never expand it.

The strongest smaller alternative was only a canonical pre-launch counter. It would stop spend but
would not prevent scope-widening blockers, arbitrary-prior resets, successful-phase reruns, or a
Codex turn stopping to request acknowledgement. The convergence schema, synthesis envelope, and
Stop hook are retained because they close those observed paths; heartbeat, paging, resource floors,
and a standalone scheduler are outside the cut line.

## 8. Autonomous workflow

The entire canonical document history stops after 16 weighted model launches. If unresolved
blockers remain and no safe in-scope pivot fits the remaining budget, report:

```text
CAMPAIGN BUDGET EXHAUSTED — NOT PLATEAU
unresolved blockers: ...
terminal reason: unresolved blockers remain after the fixed autonomous review fuse
```

The skill arms autorun first. Before exhaustion, the orchestrator applies an in-scope correction or architecture pivot itself,
then invokes round 1 again.
If the document or protocol SHA changed, the guard automatically rolls into the next bounded
campaign. If the global ceiling is exhausted, report the blocker as final rather than pausing for
an acknowledgement.

## 9. Residual risks

- A model can misclassify a scope expansion as `new`; schema cannot determine semantics.
- A same-UID process can edit the ledger; integrity against a malicious local peer remains outside
  the T1 threat model, while malformed or inconsistent accounting fails closed.
- Absolute round numbers assume the documented alternating protocol. Starting at an arbitrary
  high round is intentionally rejected rather than guessed.
- Prior synthesis remains model-authored. A later change may add deterministic correlation or
  stable semantic fingerprints, but string hashes would create false certainty today.
- A bounded campaign may stop with real unresolved defects. That is intended: return a definitive
  blocked result, not implementation, false plateau, or an acknowledgement prompt.
- The controller guarantees session continuation, not unattended semantic document editing. Codex
  still chooses and applies fixes inside each continued turn; two no-progress turns terminate
  blocked rather than trapping the session forever.
