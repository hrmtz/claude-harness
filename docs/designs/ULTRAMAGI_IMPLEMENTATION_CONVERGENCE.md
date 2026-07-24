# Ultramagi implementation convergence evaluator

status: pre-implementation design
issue: `hrmtz/claude-harness#107`
scope: PR 1b — implementation review convergence only
date: 2026-07-24

> [!IMPORTANT]
> **PR 1b normative override:** the shipped PR 1b evaluator is the report-only slice defined by
> [`ULTRAMAGI_PR1B_REPORT_ONLY_DECISION.md`](./ULTRAMAGI_PR1B_REPORT_ONLY_DECISION.md), the
> harness-magi-codex README, and the shipped Ultramagi skills. Its complete decision alphabet is
> `CONTINUE | FINAL_REVIEW_REQUIRED | BLOCKED | REDESIGN`; it never emits `PASS` or
> `PASS_WITH_RESIDUALS`. The PASS-family return table and enforcement flow below are retained as
> pre-implementation design for later slices and are not the PR 1b operator contract.

## FAMILY_ROUTING

```text
preferred: Claude design -> Codex implementation -> Claude implementation-intent review ->
           Codex final fixes/tests
actual: Codex writes this narrow design; isolated same-family service reviewers inspect the
        exact revision; Grok is the mandatory cross-family design reviewer because open incident
        #110 forbids local child Codex processes that can refresh the shared plugin cache; Codex
        implements; Claude reviews implementation intent, with recorded Grok fallback only if
        Claude is unavailable
missing: preferred Claude ownership of the initial design
degraded_until: a non-Codex exact-revision plateau marker exists for this document
```

## 1. Decision and cut line

Add a report-only deterministic evaluator for Ultramagi implementation bug-hunt campaigns:

```text
python3 scripts/magi_convergence_gate.py evaluate <implementation-manifest.json>
```

It returns:

```text
CONTINUE
FINAL_REVIEW_REQUIRED
PASS
PASS_WITH_RESIDUALS
BLOCKED
REDESIGN
```

The final four are terminal. `CONTINUE` names `initial-full` or `full-target-fix`;
`FINAL_REVIEW_REQUIRED` names `final-full`.

PR 1b changes implementation-review orchestration only. It does not evaluate design campaigns,
change the campaign ledger schema, change phase weights, add a provider phase, cancel providers,
or introduce diff-scoped review. PR 1a remains the sole budget/reserve implementation.

The evaluator never launches providers, edits a manifest, edits review output, or writes a plateau
marker. Existing `magi_plateau_gate.sh` remains the cross-family G1–G9 PASS authority. A
nonterminal completed xfamily review does not need a marker; a PASS-family result does.

Replace “Fix findings; re-run until clean” with:

```text
Run the convergence evaluator after every implementation-review phase. Follow only its bounded
next mode. final-full means: prepare evidence, run xfamily, conditionally run the existing plateau
gate, then evaluate. Ship only on PASS/PASS_WITH_RESIDUALS under risk policy. BLOCKED and REDESIGN
are non-authorizing terminal states.
```

## 2. Reviewed object, control manifest, and target snapshot

The reviewed object is a committed Git revision. Runtime control state is outside the reviewed
repository. Start a run once:

```text
python3 scripts/magi_convergence_gate.py start \
  --repository <root> --base <sha> --scope-id <stable issue/task reference>
```

`start` owns an XDG-state registry locked with `flock`. The unique key is canonical repository
identity plus base SHA plus scope ID. It creates one `implementation_campaign_id` and one stable
control root:

```text
$XDG_STATE_HOME/harness-magi-codex/convergence/<implementation_campaign_id>/
  manifest.json
  .dual-magi/
  snapshots/
  revisions/
  reviews/
  results/
```

An active registry key cannot be started again. The canonical `manifest.json` pathname is immutable
for the entire run. A copied/renamed manifest is rejected because its realpath does not match the
registry. New target revisions atomically archive the old bytes and replace bytes only at that
same pathname. This preserves the guard's path-keyed ledger, fixed global usage, and complete
history. The manifest is outside `repository_root` and never part of `target_git_sha`, avoiding a
self-referential commit SHA.

```text
scripts/magi_fanout_codex.sh --convergence-manifest <manifest> \
  <manifest> 1 <state-dir> --persona-set bug-hunt --prior -

scripts/magi_xfamily.sh --reviewer claude --convergence-manifest <manifest> \
  <manifest> 2 <prior-synthesis> <state-dir>/round_2_xfamily
```

`--convergence-manifest` must resolve to the positional document. The flag opts into the extended
prompt, receipt, and validation contracts. Omitting it preserves the legacy interface.

Before the first claim of every physical guard campaign, add:

```text
python3 scripts/magi_convergence_gate.py prepare <manifest> <state-dir>
```

`prepare`:

1. descriptor-validates the manifest;
2. verifies the named repository and commit;
3. creates or verifies a detached clean Git worktree for exactly `target_git_sha` below the
   external control root's `snapshots/<target_git_sha>/`;
4. rejects submodules and Git-LFS pointer files in changed paths for v1;
5. atomically archives immutable manifest bytes under
   `revisions/<target-sha>/<convergence-protocol-sha>/manifest.json`;
6. creates a unique review state directory
   `reviews/<target-sha>/<convergence-protocol-sha>/<physical-attempt>/`;
7. writes a pre-claim receipt with campaign ID pending, manifest SHA, target/base SHA, snapshot
   path, Git tree SHA, changed-path digest, convergence protocol SHA, and creation time;
8. after the first claim returns, atomically finalizes that receipt with the guard campaign ID.

The repository snapshot, not the live worktree, is the read surface for every provider and
required test. Adapters resample snapshot HEAD, tree, tracked cleanliness, and manifest/receipt
identity before and after provider execution and record both samples. Any drift fails closed.

Add `magi_provider_sandbox.py`. In convergence mode it launches the complete provider CLI inside a
Linux `bwrap` empty-root mount namespace:

```text
/review   detached target snapshot, read-only, provider cwd
/inputs   prompt, output schema, historical envelope, evidence index, read-only
/outputs  only the adapter-owned FIFOs/files required for structured output
/provider-state isolated writable transcript/session directory
```

Only audited read-only system runtime paths and the provider-specific authentication files needed
by the CLI are mounted. Provider state uses a run-private home; after completion, the adapter
persists only the bound transcript/session artifact needed by G6. The live repository root, caller
cwd, other worktrees, user documents, and unrelated provider history are not mounted. Network
remains available for the provider API. `/proc` and `/dev` are minimal. The convergence prompt
names `/review` as the only target root; although the control manifest records the canonical live
repository identity for validation, that path is not visible in the namespace.

Codex, Claude, and Grok each have an audited mount profile and a live opt-in confinement probe.
Conflicting sentinels at the live absolute repository path and `/review` prove every enabled
Read/Grep/Glob/read_file route can observe only snapshot bytes. A provider whose CLI/auth/session
contract cannot run in this namespace is unavailable for convergence; it cannot emit a
PASS-capable receipt. If preferred Claude and the recorded Grok fallback cannot both satisfy their
profiles, enforcement remains report-only under §12.

Required tests receive the snapshot read-only and a private temporary directory. Live untracked
bytes and control evidence are not visible except for explicitly injected bounded inputs.

The manifest includes the stable implementation campaign ID, scope ID, and canonical control path.
`prepare`, both adapters, evidence, and evaluate verify them against the locked registry.

One distinct target Git SHA is one logical convergence revision. Existing guard campaigns remain
physical launch containers. Protocol-only rollover or repeated campaigns with the same target SHA
are coalesced on campaign ID, base/target SHA, manifest identity, and snapshot tree. Different
protocol epochs are allowed under one logical target. All attempts are charged; historical
findings from every valid epoch remain blockers, while only successful evidence under the current
protocol epoch may satisfy the current phase. Conflicting target/snapshot bindings BLOCK. Logical
revisions are ordered by first ledger claim and form an ancestry chain from base to target. A
repeated target never counts as a new revision for regression/stall rules.

If code changes, the orchestrator commits the fix, archives and atomically replaces the same
canonical manifest with the new target, and explicitly starts round 1 in a new prepared state
directory. Guard rollover detects changed bytes. A different manifest path is never a continuation
and cannot access the active registry key.

Retries keep existing round/phase and do not form another logical revision. Only successful claims
are evidence; all claim statuses remain charged through existing guard accounting.

## 3. Manifest contract

Add `schemas/implementation-convergence.schema.json`:

```text
schema: magi-implementation-convergence/v1
implementation_campaign_id: registry-issued UUID
scope_id: stable non-empty issue/task reference
canonical_control_path: exact registry manifest realpath
risk_class: standard | canonical-migration | data-loss | security | irreversible
repository_root: absolute canonical path
target_git_sha: 40 lowercase hex
base_git_sha: 40 lowercase hex
changed_paths: sorted unique repository-relative paths
affected_invariants: sorted unique non-empty identifiers
trusted_tests: non-empty bounded array
result_directory: absolute path below the external control root
wall_clock_deadline: RFC3339 UTC
```

Each `trusted_tests[]` object contains:

```text
test_id: stable identifier
argv: non-empty string array; never a shell string
cwd: repository-relative directory
timeout_seconds: 1..900
invariants: non-empty subset of affected_invariants
result_path: absolute regular-file path below result_directory, named <test_id>.json
```

The union of every declared test's invariants must equal `affected_invariants`; partial or extra
coverage is invalid. PASS independently rejects an empty evidence index or uncovered invariant.

These argv arrays are trusted committed repository test entry points, not an untrusted-code
sandbox contract. PR 1b provides T1 evidence capture and bounded waiting. Hostile child-process
ownership, descendant cancellation, and aggregate resource containment are PR 2.

Limits:

- manifest: 1 MiB;
- at most 64 changed paths, 64 invariants, and 64 tests;
- each review JSON: 4 MiB;
- each result: 1 MiB;
- historical blocker envelope: 4 MiB / 512 blocking references;
- evidence index: 1 MiB;
- cumulative referenced bytes: 32 MiB.

Manifest, receipt, snapshot root, results, ledger, state directories, and review files reject
symlinks. Reads use descriptor-first no-follow checks, regular-file and size checks, and pre/post
`device,inode,size,mtime_ns` stability. FIFO, socket, device, sparse oversize, replacement race,
malformed JSON, limit overflow, or path escape is `BLOCKED`.

The evaluator requires:

- snapshot `HEAD == target_git_sha`;
- clean snapshot tracked state;
- every changed path differs in `base_git_sha..target_git_sha`;
- no tracked diff path is omitted;
- current manifest/receipt/snapshot/final artifacts remain identical on final resample.

## 4. Findings, historical dispositions, and residuals

Extend `finding.schema.json` under `additionalProperties: false`. New fields are optional so
historical payloads remain valid. Convergence-mode adapter validation requires them where stated.

Each finding may add:

```text
subsystem
root_cause_id
affected_invariant
changes_design_invariant: boolean
relation_to_prior: optional historical source_ref
residual:
  owner
  risk
  defer_reason
  tracking_ref
```

The root object may add:

```text
closures:
  - source_ref
    disposition: carried | resolved
    test_result_sha256: required only for resolved
```

In convergence mode:

- every current REJECT/CRITICAL/HIGH or `dup_flag=regression` finding includes subsystem,
  root cause, affected invariant, and design-invariant flag;
- duplicate/regression includes `relation_to_prior`;
- final xfamily output contains exactly one closure for each historical blocker in the injected
  envelope;
- nonterminal reviews may mark a source `carried`; PASS-family evaluation requires every source
  `resolved`;
- a closure's invariant is never provider-authored; the evaluator derives it byte-for-byte from
  the canonical historical source record named by `source_ref`;
- resolved closures carry an authoritative result digest only, not command/evidence prose;
- residualized MED/LOW/nit carries every residual field;
- normalized orchestration values never supply or override provider-authored semantics.

The historical envelope includes blockers from prior successful claims and the current fanout.
A current xfamily blocker is present in raw current findings; it enters the next revision's
historical envelope. Honest REVISE/REJECT output is schema-valid and finishes its claim even when
closures are carried.

Fanout and xfamily prompts request these fields only in convergence mode. Legacy calls remain
unchanged.

## 5. Claim-bound receipts and ledger-complete discovery

Add shared history functions in `magi_convergence_gate.py`. Adapters invoke the same helper; they
do not duplicate history logic.

### 5.1 Shared claim adoption and publication reconciliation

Every prepared physical attempt has a random `prepared_receipt_id` persisted before claim. Extend
the guard's optional convergence path without changing legacy claim behavior:

```text
claim-or-adopt <doc> <round> <phase> <state-dir>
  --prepared-receipt-id <id>
  --expected-ledger-sha256 <sha>

finish-or-confirm <doc> <claim-id> success
```

Under the document lock, `claim-or-adopt` first finds a unique running launch with the same
prepared receipt ID, canonical state directory, round, and phase. If found, it returns the existing
claim/campaign IDs without another charge. If not found, it performs normal transition/admission,
stores the prepared receipt ID and convergence protocol digest in the new launch, atomically
writes the ledger, and returns them. A changed expected ledger digest rejects before mutation.
Ambiguous/conflicting matches fail closed. The next invocation can therefore adopt a claim whose
ledger write succeeded even when stdout/CLAIM_ID delivery was lost.

`finish-or-confirm success` changes a matching running claim to success or accepts an already
successful claim; every other terminal status fails closed. Convergence adapters do not
automatically abandon a matching prepared running claim. Known provider failure with no valid
staged receipt finishes failed; SIGKILL or uncertain handoff leaves running state for adoption.

Both phases use one reconciliation protocol under the review lock:

1. adopt/create claim;
2. write provider bytes only to claim-scoped staging;
3. validate all bytes and atomically fsync a claim-scoped phase receipt;
4. `finish-or-confirm success`;
5. atomically promote canonical outputs;
6. on restart, adopt/inspect before sibling refusal or any new claim;
7. valid running receipt -> finish and promote; successful staged receipt -> promote;
8. invalid partial bytes belonging to failed/abandoned claim -> quarantine before the one bounded
   retry; unexplained sibling bytes retain INV-3 refusal.

The same state machine covers fanout and xfamily. The plateau gate runs only after xfamily
reconciliation has a successful claim and canonical findings/meta/receipt.

### 5.2 Fanout receipt

Every convergence fanout uses claim-scoped staging and writes
`round_<N>_fanout.<claim-id>.receipt.json` containing:

```text
claim_id
campaign_id
manifest_archive_sha256
target_git_sha
snapshot_head/tree/path identity
persona_set: bug-hunt
personas: [hornet, gnat, wasp]
prompt_contract_sha256
adapter_protocol_sha256
output path/SHA for all three provider files
pre/post snapshot samples
```

The helper requires exact HORNET/GNAT/WASP reviewer identities, three unique outputs, and receipt
digests. Default Magi, flagless bug-hunt, legacy-inferred success, or an unbound output cannot count
as convergence evidence.

Crash injection covers every output move, receipt fsync/rename, promotion, and pre/post finish
boundary. Existing INV-3 still rejects unexplained siblings.

### 5.3 Xfamily receipt

Xfamily writes findings, meta, and a phase receipt to claim-scoped staging and uses the shared
reconciliation protocol. Its receipt binds transcript, envelope, index, and every canonical output
digest. The existing xfamily meta is extended in convergence mode with:

```text
claim_id
campaign_id
manifest_archive_sha256
target_git_sha
snapshot samples
historical_envelope_sha256
historical_source_count
evidence_index_sha256
prompt_contract_sha256
```

At the used-15/16 boundary, a crash after any valid staged byte, finish, or promotion step recovers
the same claim without another model launch. No partial/stale output can satisfy the receipt.

### 5.4 Complete historical walk

Under the existing document lock, the helper:

1. loads the canonical ledger and all archived campaign receipts in ledger order;
2. validates each physical campaign-to-target binding;
3. counts every attempt through existing `model_launches`;
4. validates every successful fanout receipt and output;
5. validates every successful xfamily output/meta/receipt;
6. unions raw REJECT/CRITICAL/HIGH findings from every successful provider output;
7. groups matching target SHAs into logical revisions without dropping findings.

Source identity:

```text
<claim_id>/<review-file-basename>#<finding_id>
```

Duplicate IDs in one file, missing/ambiguous expected files, invalid schema, successful claim
without a valid receipt, conflicting target receipt, or ancestry failure is `BLOCKED`.

Completeness is ledger-to-receipt-to-artifact. The manifest has no operator-curated cycle list.
Failed/abandoned attempts affect budget only.

For final xfamily, the adapter already holds the review lock. It calls the helper to create an
envelope and records the exact canonical ledger-file digest sampled under the document lock. The
guard claim accepts `--expected-ledger-sha256`; under its own existing lock it rejects a changed
ledger before transition/accounting. The adapter rebuilds once on mismatch and otherwise fails
closed. Review lock alone is not claimed to serialize direct guard callers. After success, the
evaluator rebuilds history only up to the final claim and requires the canonical envelope
digest/count and pre-claim ledger digest recorded in meta.

Define `convergence_protocol_sha256` over every load-bearing convergence schema, evaluator/history
helper, test-result producer, shared round verifier, guard, adapter, prompt, and receipt contract.
The existing guard `protocol_sha()` includes these paths so a change selects a new physical
campaign before claim. The same digest appears in manifest archives, receipts, meta, envelope, and
evidence index. Component mutation tests prove rollover in the same path-keyed ledger.

## 6. Trusted test results and evidence index

Add:

```text
python3 scripts/magi_convergence_test_result.py run <manifest> <test-id>
python3 scripts/magi_convergence_gate.py evidence <manifest> <state-dir>
```

The producer selects predeclared argv by ID and never parses a shell string. It runs from the
detached snapshot with a cleared allowlisted environment, private TMPDIR, new session, timeout,
TERM/KILL best-effort cleanup, and streamed 512 KiB stdout/stderr caps. It does not claim hostile
descendant containment. A command that survives cleanup or prevents bounded completion yields no
successful result and PR 2 owns stronger cancellation.

The producer atomically writes `magi-convergence-test-result/v1`:

```text
test_id, argv, cwd, invariants, target_git_sha
started_at, finished_at, exit_code, timed_out
stdout_sha256, stderr_sha256, producer_protocol_sha256
snapshot head/tree/path identity
```

`evidence` validates every current-target result and writes a canonical index:

```text
manifest_sha256
target_git_sha
tests:
  - test_id
    invariants
    status: missing
  - test_id
    invariants
    status: present
    result_sha256
    exit_code
    timed_out
```

The array has exactly one entry per manifest test in manifest order. `missing` forbids result
fields. `present` requires them. Omission, duplicates, null/sentinel hashes, reordering, or unknown
test IDs are corrupt. A present nonzero/timeout entry and a missing entry are well-formed
non-PASS. Trusted adapter code computes all file digests. The xfamily prompt injects the index
beside the historical envelope, so read-only reviewers copy authoritative digests rather than
calculate or invent them. Meta binds the index digest. The evaluator rebuilds and compares it.

For nonterminal decisions, a well-formed failed test or missing successful result is evidence of
non-PASS, not state corruption. It does not by itself BLOCK or prevent
`CONTINUE(full-target-fix)`. PASS-family evaluation requires every declared result to exist,
exit 0, not time out, match manifest/snapshot/protocol identity, and cover each resolved closure's
affected invariant.

## 7. Round evidence and final-full ordering

Factor the current read-only G1–G6/G9 checks into
`magi_verify_round.py`. `magi_plateau_gate.sh` invokes the shared verifier plus G7/G8 before it
writes a marker. The evaluator invokes the same verifier without G7/G8 to accept a grounded,
provenance-valid nonterminal xfamily review. Marker contents and G1–G9 behavior do not change.

`final-full` is one ordered orchestration mode:

1. run every trusted test for the current target;
2. build the canonical evidence index and historical envelope;
3. run convergence-mode xfamily with both injected;
4. if verdict/findings are G7/G8-eligible, run `magi_plateau_gate.sh` on that exact output;
5. run the convergence evaluator.

Gate denial for a well-formed blocker review is a normal non-PASS input, not missing evidence.
Adapter/provider/meta failure is `BLOCKED`.

For PASS association, the evaluator requires:

- exact current `PLATEAU.<doc-id>.<manifest-sha-prefix>` marker;
- marker artifact SHA, verdict, model, family, session, grounding, G1–G9 list;
- final meta artifact/output/transcript identity;
- final claim/receipt, manifest archive, snapshot, envelope, and evidence-index digests.

PASS requires final verdict GO, zero current findings, all historical closures resolved, every
test green, non-empty evidence, exact union coverage of all affected invariants, and every
identity/history/budget check.

PASS_WITH_RESIDUALS requires the same except final verdict GO-WITH-REVISE and only MED/LOW/nit
findings with full residual metadata. It is allowed only for `standard` risk. Residuals in
canonical-migration, data-loss, security, or irreversible campaigns return `BLOCKED`.

Current REJECT/CRITICAL/HIGH denies PASS regardless of closures. A marker is never required for
CONTINUE, FINAL_REVIEW_REQUIRED, BLOCKED, or REDESIGN.

## 8. Deterministic decisions

Output JSON contains decision, next_mode, stable reason_code, usage, ceiling, target SHA, blocker
mass, and historical source count.

Precedence:

1. malformed/unstable identity, receipt, history, schema, or deadline -> `BLOCKED`;
2. current provider finding with `changes_design_invariant=true` -> `REDESIGN`;
3. regression in two consecutive logical target revisions, or new blocking roots in the same
   subsystem in two consecutive revisions -> `REDESIGN`;
4. valid current marker/final review and PASS predicates -> PASS-family result;
5. exhausted budget or unaffordable exact next transition from shared guard state -> `BLOCKED`;
6. no successful current fanout -> `CONTINUE(initial-full)`;
7. successful fanout without successful xfamily -> `FINAL_REVIEW_REQUIRED(final-full)`;
8. two consecutive non-decreases of unresolved blocker mass across three distinct target
   revisions -> `BLOCKED(BLOCKER_MASS_STALLED)`;
9. otherwise -> `CONTINUE(full-target-fix)`.

Test exit failure, carried closures, absent marker after a blocker review, or current blockers are
well-formed non-PASS evidence evaluated at rules 2–9, not rule-1 corruption.

Severity mass:

```text
REJECT=16 CRITICAL=8 HIGH=4 MED=2 LOW=1 nit=0
```

Only current unresolved REJECT/CRITICAL/HIGH contributes blocker mass. Regression/new-root
overrides numeric decrease. MED/LOW/nit neither count as blocking progress nor force an unbounded
loop; they are fixed or validly residualized at the next final review.

Usage, ceiling, next phase, reserve, and affordability come from shared guard helpers. The
evaluator does not duplicate fuse arithmetic. Budget exhaustion never authorizes PASS.

Every well-formed decision exits 0, including BLOCKED/REDESIGN. Unsafe manifest open exits 2;
usage errors exit 64.

## 9. Compatibility, rollout, and rollback

Rollout is additive and opt-in:

1. install optional schema fields, shared round verifier, evaluator, receipts, producer, flags,
   tests, and docs;
2. legacy calls without `--convergence-manifest` stay unchanged;
3. run synthetic/repository-local replay fixtures;
4. use convergence only for new untracked implementation manifests.

No existing ledger/finding is migrated. Existing active campaigns keep the legacy workflow. A
successful launch without a convergence receipt cannot be retroactively treated as convergence
evidence.

Rollback removes opt-in orchestration/code. Persistent snapshots, receipts, and review artifacts
remain inert. Detached worktree cleanup is an explicit bounded maintenance command after terminal
state; it is not provider cancellation.

Threat boundary is T1 accidental omission/staleness/corruption. Same-UID forgery and hostile
committed test code are out of scope. Robust descendant ownership and requirement-revision
cancellation are PR 2.

## 10. Required tests

- legacy finding payload remains valid; convergence payload validates;
- convergence mode rejects missing semantic fields on blockers/regressions;
- nonterminal xfamily can carry blockers, finish success, and return full-target-fix without a
  marker;
- PASS requires resolved closures, green results, and exact marker;
- detached snapshot excludes live untracked shadow/config/data/executable bytes;
- wrong repository, wrong target, dirty/mutated snapshot, and revert-before-evaluate fail;
- manifest archive and campaign receipts reconstruct target revisions;
- one registry-backed canonical manifest path retains ledger usage/history across targets;
- copied/renamed manifest and duplicate active scope registry entry are rejected;
- every physical campaign gets a unique state directory and finalized receipt;
- same-target campaigns coalesce; conflicting receipts and non-ancestry BLOCK;
- protocol epochs coalesce by target while only current-protocol evidence satisfies current phase;
- every convergence component mutation rolls over in the same ledger;
- exact fanout receipt binds claim, bug-hunt personas, prompt, snapshot, and three outputs;
- lost claim stdout adopts the unique durable running launch without another charge;
- round-1 and later fanout receipts use unique claim/round names;
- fanout and xfamily share claim-scoped reconciliation; crash recovery never accepts stale output;
- used-15 xfamily recovers valid staged/finished evidence without another launch;
- default Magi or flagless bug-hunt cannot count as convergence evidence;
- ledger history includes an early HIGH omitted from later synthesis;
- a manifest cannot omit a successful launch or HIGH;
- failed/abandoned claims count usage but not evidence;
- missing/ambiguous/stale/wrong-round/wrong-document/digest-mismatched artifact BLOCKS;
- captured fanout prompt requires semantic fields;
- captured xfamily prompt contains every source_ref and evidence-index digest;
- provider bwrap profiles expose snapshot/input/output/provider-state only and mask live worktrees;
- conflicting live/snapshot sentinels prove Codex/Claude/Grok tools read only `/review`;
- provider profile/auth/session failure cannot produce a PASS-capable receipt;
- xfamily meta binds claim, snapshot, manifest, envelope, and evidence index;
- closure coverage missing/extra/duplicate/fabricated BLOCKS at PASS;
- closure source invariant is derived; mismatch/unrelated green test cannot close it;
- carried closure is allowed only on nonterminal paths;
- producer rejects unknown test IDs and shell strings;
- evidence index has canonical missing/present entries and exact manifest order;
- producer runs trusted argv against snapshot, clears non-allowlisted env, caps output, and times
  out boundedly without claiming hostile descendant containment;
- failed/missing test with current HIGH returns full-target-fix;
- empty trusted tests, empty evidence, or partial invariant coverage denies PASS;
- stale-target/wrong-invariant/wrong-protocol result denies PASS;
- dirty live worktree does not affect snapshot review; dirty snapshot blocks;
- shared round verifier preserves G1–G6/G9 for nonterminal and G1–G9 for plateau;
- marker/final meta/session/model/envelope/index mismatch denies PASS;
- current HIGH denies PASS-family;
- complete standard residuals PASS_WITH_RESIDUALS; high-risk residuals BLOCK;
- design-invariant change/repeated regression/same-subsystem blockers REDESIGN;
- decreasing mass continues; two non-decreases BLOCK;
- no fanout -> initial-full; fanout without xfamily -> final-full;
- final-full used-15 xfamily runs gate then PASS at 16/16;
- shared reserve: fanout at used13 BLOCKS, xfamily at used15 remains affordable, failed fanout
  retry cannot consume reserve;
- swift-fox synthetic history terminates; slate-lantern preserves final xfamily unit;
- existing guard, autorun, adapters, plateau, lock, scrub, docs, and manifest tests remain green.

## 11. Changed surfaces

```text
plugins/harness-magi-codex/schemas/finding.schema.json
plugins/harness-magi-codex/schemas/implementation-convergence.schema.json
plugins/harness-magi-codex/schemas/convergence-test-result.schema.json
plugins/harness-magi-codex/scripts/magi_validate_findings.py
plugins/harness-magi-codex/scripts/magi_verify_round.py
plugins/harness-magi-codex/scripts/magi_convergence_gate.py
plugins/harness-magi-codex/scripts/magi_convergence_test_result.py
plugins/harness-magi-codex/scripts/magi_campaign_guard.py
plugins/harness-magi-codex/scripts/magi_provider_sandbox.py
plugins/harness-magi-codex/scripts/magi_fanout_codex.sh
plugins/harness-magi-codex/scripts/magi_xfamily.sh
plugins/harness-magi-codex/scripts/magi_plateau_gate.sh
plugins/harness-magi-codex/tests/test_convergence_gate.py
plugins/harness-magi-codex/tests/test_convergence_test_result.py
plugins/harness-magi-codex/tests/provider prompt/receipt fixtures
plugins/harness-magi-codex/skills/ultramagi/SKILL.md
plugins/harness-magi/skills/ultramagi/SKILL.md
plugins/harness-magi-codex/README.md
plugins/harness-magi-codex/.codex-plugin/plugin.json
protocol/doc-drift tests
```

Implementation starts only after the exact-revision cross-family plateau gate passes for this
design.

## 12. Effort and abort threshold

Engineering ranges:

- schemas, receipts, history, evaluator: 6–10 person-hours;
- registry, canonical-path lifecycle, reconciliation: 3–6 person-hours;
- detached snapshot and adapter transport: 3–6 person-hours;
- provider confinement profiles and live probes: 3–6 person-hours;
- trusted test evidence/index: 2–4 person-hours;
- shared verifier and plateau integration: 2–4 person-hours;
- fixtures/regression suites: 6–10 person-hours;
- docs/version/review fixes: 2–4 person-hours.

Default cap: 50 person-hours. At 150% of a package range, if any supported provider cannot be
path-confined to the detached snapshot, if fanout/xfamily or lost-CLAIM publication cannot be
reconciled without another launch, if claim-bound receipts require ledger v2, or if trusted tests
require hostile descendant ownership, stop PASS enforcement and retain report-only history
diagnostics. Process ownership belongs to PR 2; diff-scoped/weight-1 review belongs to PR 3.

## 13. Review disposition

Round-1/cross-family blockers are addressed:

- nonterminal reviews allow carried closures, need G1–G6/G9 but not a marker, and treat failed
  tests as non-PASS evidence;
- final-full explicitly prepares evidence, runs xfamily, conditionally gates, then evaluates;
- provider/test reads use one detached target snapshot; provider mount profiles mask live
  repository/worktree paths and are gated by live confinement probes;
- archived manifests plus receipts durably bind logical target revisions;
- one registry-backed external canonical path preserves the path-keyed fuse across revisions;
- protocol epochs roll over before claim and coalesce without conflicting target identity;
- fanout receipts bind convergence mode and bug-hunt personas to claim/output bytes;
- one claim-or-adopt/finish-or-confirm state machine covers lost claim output, fanout, xfamily, and
  the used-15/16 receipt/finish/promotion crash windows;
- trusted code computes and injects the evidence digest index for read-only reviewers;
- missing/present evidence is a closed tagged union; tests are non-empty and cover every invariant;
- closure invariants are derived from their historical sources;
- hostile descendant containment is removed from PR 1b and assigned to PR 2.

Final findings from the earlier blocked `ULTRAMAGI_CONVERGENCE_SLICE1.md` remain resolved by:

- ledger-to-artifact completeness rather than manifest-curated cycle history;
- explicit convergence CLI/receipt/meta transport;
- implementation-only phase;
- historical envelope in scope while diff-context envelope remains PR 3;
- explicit test/index fields;
- closure digests as the only authoritative execution evidence.

Ceiling sidecars, ceiling migration, and plateau marker-format changes remain deleted. PR 1a
already supplies the final-launch reserve.
