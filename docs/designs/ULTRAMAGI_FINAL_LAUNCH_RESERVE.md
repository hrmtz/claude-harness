# Ultramagi final-launch reserve

status: pre-implementation design
issue: `hrmtz/claude-harness#107`
slice: PR 1a
date: 2026-07-24

## FAMILY_ROUTING

```text
preferred: Claude design -> Codex implementation -> Claude implementation-intent review
           -> Codex final fixes/tests
actual: Codex drafted this narrow design; isolated same-family service reviewers inspect it;
        Grok performs the exact-revision design gate; Codex implements; Claude performs the
        implementation-intent review; Codex applies final fixes/tests
missing: preferred Claude ownership of the initial design draft
reason: assigned Formation worker is Codex and open SEV-1 #110 prohibits local child Codex
        processes that could refresh shared plugin cache
degraded_until: exact-revision Grok design plateau and Claude implementation-intent review exist
```

## Decision

Preserve one weighted launch for the mandatory cross-family phase when claiming a fan-out.

The existing campaign guard remains the only launch-accounting authority. Existing phase weights,
transition graph, ledger schema, global fuse, retry accounting, rollover behavior, adapters,
finding schema, plateau gate, and G1–G9 remain unchanged.

Before accepting a `fanout` claim of weight 3:

```text
total_used + fanout_weight + xfamily_reserve <= effective_ceiling
total_used + 3 + 1 <= effective_ceiling
```

Before an `xfamily` claim, retain the existing check:

```text
total_used + 1 <= effective_ceiling
```

The reserve is scheduling capacity, not authorization. The xfamily claim must still satisfy the
existing transition, retry, artifact/protocol identity, provenance, and plateau rules.

## Invariants

### R1 — no ceiling extension

The effective ceiling remains:

```text
min(GLOBAL_MAX_MODEL_LAUNCHES, MAGI_MAX_AUTONOMOUS_MODEL_LAUNCHES)
```

The environment may tighten the default 16 and cannot raise it. No acknowledgement, approval
file, state directory, revision rollover, or new campaign extends the global history.

### R2 — all claimed attempts remain charged

`model_launches()` continues counting successful, failed, abandoned, and running claims across
every revision campaign. A failed fan-out consumes 3. A retry requires another 3 plus the reserved
1 and is denied when unaffordable.

### R3 — reserve applies only to fan-out

The guard adds one unit only to the fan-out admission calculation. It does not write a synthetic
claim, decrement the ledger, or double-charge the later xfamily claim.

### R4 — existing transition graph remains exact

The legal sequence remains:

```text
round 1 fanout -> round 2 xfamily -> round 3 fanout -> round 4 xfamily -> ...
```

The reserve cannot make an illegal xfamily transition legal. If a fan-out fails and its retry is
unaffordable, the campaign terminates budget-blocked; it cannot skip to xfamily.

Transition validation precedes affordability. An illegal round/phase is exit 64 even when budget
is exhausted. Only a transition-valid but unaffordable claim returns exit 4.

### R5 — budget denial is non-authorizing

Reserve denial uses existing exit 4:

```text
CAMPAIGN BUDGET EXHAUSTED — NOT PLATEAU
```

The diagnostic states that the fan-out would consume capacity reserved for its mandatory
cross-family successor. No marker is written and no provider starts.

### R6 — no process or artifact migration

This slice changes no running-claim cleanup, process ownership, locks, ledger schema, finding
schema, provider prompt, or historical artifact. Rollback is an ordinary code/docs revert.

## Admission examples

With ceiling 16:

| used before claim | requested phase | calculation | result |
|---:|---|---|---|
| 12 | fanout | `12 + 3 + 1 = 16` | allow |
| 15 | xfamily | `15 + 1 = 16` | allow if transition-valid |
| 13 | fanout | `13 + 3 + 1 = 17` | deny before provider |
| 15 after failed fanout | fanout retry | `15 + 3 + 1 = 19` | deny |

With tightened ceiling 4:

| used before claim | requested phase | calculation | result |
|---:|---|---|---|
| 0 | fanout | `0 + 3 + 1 = 4` | allow |
| 3 | xfamily | `3 + 1 = 4` | allow if transition-valid |
| 3 after failed fanout | fanout retry | `3 + 3 + 1 = 7` | deny |

## Implementation

Add a named constant:

```text
FINAL_XFAMILY_RESERVE = PHASE_WEIGHT["xfamily"]
```

The ordered `claim()` control flow under its single existing document lock is:

1. load canonical ledger and account any stale running launch using existing semantics;
2. run `validate_transition`;
3. on transition error, either raise exit 64 or plan an in-memory `may_rollover` campaign;
4. call the pure admission decision against global usage and the effective ceiling;
5. on denial, raise exit 4 without persisting the planned rollover or any launch;
6. on admission, append the real launch weight and atomically write the ledger.

The pure admission calculation is:

```text
required = PHASE_WEIGHT[phase]
if phase == "fanout":
    required += FINAL_XFAMILY_RESERVE
```

Deny when `total_used + required > global_ceiling`. The ledger still records only the requested
phase's real weight after admission.

Keep the calculation in `magi_campaign_guard.py`; callers and docs must not maintain a second
counter. Refactor two layers:

1. a pure, no-I/O/no-lock admission function taking used weight, ceiling, and requested/inferred
   phase and returning required weight, reserve, affordability, and reason;
2. locked loaders for callers that do not already hold the document lock.

`claim()` calls only the pure function while holding its existing lock; nested lock acquisition is
forbidden. Autorun/status acquires the document lock and derives only the active campaign's next
transition: retry the last failed/abandoned phase when its retry budget remains; otherwise use the
transition-valid successor; use round 1 fanout only when the active campaign has no launches. It
does not infer a caller's future explicit rollover request from artifact identity. `may_rollover`
remains solely inside `claim()` when an explicit otherwise-illegal round 1 fanout is requested.
Autorun calls the pure function with its inferred active phase, releases the lock, and persists
terminal `blocked` on reserve denial. It emits no continuation/retry and mutates no ledger.

The locked status loader returns a discriminated state: affordable candidate,
reserve/fuse-blocked, retry-exhausted/transition-blocked, or running/retry candidate under the
existing cleanup contract. Retry exhaustion terminalizes autorun immediately without calling the
admission function. Status inspection never converts or mutates a running claim.

## Tests

Extend `test_campaign_guard.py`:

- ceiling 16: fan-out at used 12 succeeds, following xfamily reaches 16;
- ceiling 16: fan-out at used 13 is exit 4 and creates no claim;
- ceiling 16: charged failed fan-out at used 12 makes retry exit 4;
- ceiling 4: initial fan-out succeeds and following xfamily succeeds;
- ceiling 4: charged failed initial fan-out makes retry exit 4;
- xfamily reserve is not double-charged;
- illegal fanout/xfamily transitions remain exit 64 at both affordable and exhausted usage;
- autorun at transition-valid fanout with used 13 persists blocked immediately, creates no claim,
  and emits no continuation;
- retries, revision rollover, and fresh state directory cannot reset usage;
- denial writes no plateau marker and starts no provider.

Extend `test_autorun.py` with the used=13 hook fixture: status becomes blocked with the
reserve/fixed-fuse reason, no continuation decision is emitted, and no new claim exists.
Also assert: a prior successful fanout at used 13 infers xfamily and is not reserve-blocked;
separately, a guard-level explicit round 1 fanout with changed identity at used 13 returns exit 4
and writes no rollover campaign.
Two failed fanout attempts and two failed xfamily attempts each terminalize on the first hook with
a retry-exhausted reason and no claim/provider/continuation; a running-claim fixture preserves the
existing non-mutating recovery behavior.

Run the existing campaign guard, autorun, adapter, plateau, lock, scrub, docs, and plugin-manifest
suites.

## Documentation

Update both Ultramagi skill surfaces and the Codex README:

```text
fan-out admission preserves one weighted launch for the immediately following mandatory
cross-family review; denial is BLOCKED, never permission to ship
```

Remove no existing G1–G9 or cross-family requirement. Update plugin version/cachebuster because
the installed normative skill text changes.

## Effort and abort threshold

Planning estimate:

- shared guard/autorun admission helper and tests: 1–2 person-hours;
- docs/version/full verification: 1–2 person-hours.

The default cap is 4 person-hours. Abort PR 1a and reassess at 150% of either package estimate, if
an excluded schema/adapter/process change becomes necessary, or if unrelated suite repair is
required. Success is the named 16-unit/4-unit boundary fixtures plus all existing relevant suites
remaining green. If the cap is exceeded, prioritize open cache-reliability incident #110 instead.

## Scope exclusions

- convergence evaluator or four-state manifest;
- historical blocker envelope or closure schema;
- requirement-revision cancellation;
- process/lock ownership;
- diff-scoped prompts;
- targeted or weight-1 incremental reviewer.

Those remain separate #107 slices. Implementation begins only after an exact-revision
cross-family review finds no unresolved HIGH-or-worse issue in this design.
