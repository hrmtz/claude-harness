# Grok termination and zero-blocker design review recovery

2026-09-10. Two bounded compatibility fixes to unblock the existing shinfutsu
information-boundary review. User explicitly authorized proceeding after the
post-distribution checkpoint and the concrete proposal. Local source and tests
may change; installed artifacts change only after exact review and verification.

## Observed failures

1. The installed Grok adapter accepts `EndTurn` or an absent stop reason. The
   actual CLI returned `end_turn`; its completed run was rejected as UNPARSEABLE
   before structured output validation. Retain that failed receipt and its charge.
2. Design convergence treats three revisions with blocking mass `0,0,0` as
   stalled. With no HIGH-or-worse roots and a still-missing diverse phase, it
   returns `DESIGN_BLOCKER_MASS_STALLED` before requesting that phase. The actual
   pure kernel reproduces this at usage 10/16. Zero blockers are not a plateau.

Source locations: `plugins/harness-magi-codex/scripts/magi_xfamily.sh` provider
envelope parsing and `scripts/magi_convergence_kernel.py` design profile.
The consumer objects are distinct:

- Current shinfutsu design is `01_OPERATION_OUTPUT_RECOVERY.md`, SHA256
  `903e6210b56f7cfd0f2f3ce12642f06ef48f90f6a77717272ec68eb9fdf77f9c`, on
  `CAMPAIGN.370a277190492ad4.json`; its old-protocol state is blocked at mass 0.
- The retained Grok failure concerns prior design `7aef706f…` under installed
  `0.3.1+codex.20260910050132`, not a successful review of the current design.
  Its persistent receipt is shinfutsu's
  `docs/evidence/information-boundary/m01-design-grok-format-failure.json`.
- Current draft PR46 HEAD is `6fd8f32a648873dd62efb08900b6407ff45f7c7c`, containing
  product code `1c95ed26ac3831d375e12b74e5edb74915b26837`. `gh pr view 46` and
  local HEAD agree. Git ancestry is `26e838d → 5fd0e80 → 1c95ed2 → 6fd8f32`;
  the last commit changes three documentation/evidence files only. The HIGH
  on 26e838d precedes the heading fix and this packaging HEAD. `code_commit`
  in the validation receipt names product code, not the later packaging HEAD.

Latest same-family `impl-r4` reviews bind manifest SHA256 `588b7910…`, whose
`target_git_sha` is 6fd8f32. Their successful claim ran 05:49:19–05:51:28 UTC,
after the 05:48:46 UTC packaging commit; GNAT/HORNET have no findings and WASP
has MED/LOW only. They are old-protocol evidence, not a completed diverse gate.
These tool fixes do not approve M01 or implement its future milestones. The
Grok claim that 26e838d/1c95ed2 come after 6fd8f32 contradicts git ancestry;
retain that original finding and review this clarification mechanically.

## Smallest repair and invariants

- Accept `end_turn` alongside existing `EndTurn` and absent-value compatibility.
  Unknown or failed endings still fail closed. The normal schema, transcript,
  identity, source digest, protocol and ledger checks still execute afterward.
- Apply design's mass-stall stop only while current blocking roots exist. Clean
  fanout revisions still require the diverse final phase. A budget shortfall,
  repeated blocking root, recurring subsystem or real blocking stagnation keeps
  its existing outcome. Only the existing plateau gate can write a success marker.
- Do not change severity weights, budgets, max cycles, provider permissions,
  model routing, required review phases or prior charged usage. No ledger edit,
  retroactive successful receipt, or waiver replaces a fresh valid review.
- Preserve existing callers and pure kernel interfaces. Reuse the current fake
  provider shell harness and kernel/design-adapter test fixtures. No new service,
  generic provider wrapper or configuration surface is needed.

## Execution and validation

This is a reversible isolated worktree based on `e9a7cf6`, owned by
codex-frost-crane. engine_routes owns the Grok parser and its shell regression;
registry_check owns the design kernel and its existing tests. Root freezes both
runtime files before protocol-bound tests or review. Existing files receive
persistent Sanada backups before edits.

Required checks: both successful Grok ending spellings, unknown ending rejection
without published findings/meta, existing provider failure/provenance checks;
three clean design revisions requiring final diverse review, zero-root budget
shortfall, and unchanged true blocking stops. Run existing convergence,
design-convergence and autorun tests plus manifest validation. Tests use synthetic
providers/ledgers and never mutate another campaign.

CI runs the kernel/design cases and explicit `--xfamily-only` selection of the
existing target-root suite (11 checks). Default invocation still runs all 14,
including the separate Codex sandbox preflight tests; all passed locally.
The first CI attempt's cross-family checks passed, but an unrelated sandbox
preflight failed and exposed its FIFO-reader startup/cleanup race. Its initial
bubblewrap failure cause remains unknown because the runner discards stderr.
Retain that CI failure and the isolated reproducer; this package does not fix
or claim coverage of that separate runtime. The new CI selection adds the
relevant cross-family coverage without removing any pre-existing CI test.

Review this frozen design and the exact implementation through separate bounded
same-family and Grok phases. The repaired checkout is the candidate review runtime;
its complete protocol is frozen before either phase, avoiding mixed generations.
This local bootstrap use does not distribute or self-certify the patch. Independent
review artifacts and the mechanical gate are still required before installation.

FAMILY_ROUTING preferred: Claude design, Codex implementation, Claude adversarial
review, Codex final verification. Actual: Codex bounded preparation, Grok exact
design review, Codex implementation alignment, Grok exact implementation review,
Codex final checks. Claude's weekly quota is documented in shinfutsu's
`docs/evidence/information-boundary/claude-availability.json`. Missing Claude
phases remain explicit; degraded_until is both required Grok gates completing
for the current design revision and final implementation target. The
implementation manifest's initial design anchor `5fb0f548…` remains immutable
review history. It does not relabel the current design revision as already
reviewed; final readiness must bind this canonical document's current revision
and the separate exact implementation review. No marker or ledger is rewritten.

## Distribution and resuming shinfutsu

After checks/review, land the scoped source through a reviewable PR and update
only the affected existing local marketplace plugin with the normal cachebuster
helper and cache-safe Codex entrypoint. Preserve every byte of the old 0.3.0 and
0.3.1 cached runtimes used by live sessions. Verify the installed source hashes,
selected dispatcher runtime and old-cache manifests before declaring delivery.
Do not change hook permissions or write installed cache files by hand.

While final release checks run, consumer reviews may use this frozen isolated
runtime as reversible local work. This publishes neither plugin nor product;
its protocol must remain identical throughout all phases. Existing root autorun
continues tracking this harness design; parallel consumer phases use their own
unchanged ledgers without spoofing session identity.

After verified delivery, hard-stop harness mutations at the user's distribution checkpoint. Resume
shinfutsu with the fixed pinned runtime, fresh exact-revision review artifacts
and its existing charged budgets. Preserve the old blocked state as history;
do not relabel it successful. Delivery rollback is the retained prior runtime;
an attempted rollback or extra repair after delivery requires new user direction.

Existing persistent release evidence is in
`/home/hrmtz/sanada_backup_persistent/magi-compat-evidence-20260910/`:
`run-start.json`, `preinstall-backup.json`, `coordination-ack.json`, and copied
resume-admission/install/dispatcher-check notes. Existing preinstall backup is
`/home/hrmtz/sanada_backup_persistent/magi-compat-install-20260910T062334Z`.
These records establish preparation and retained inputs; they do not claim
installation, dispatch selection or consumer acceptance has completed.

The frozen candidate protocol is
`cad438cd00c67e8ca3fb041aa18d392ac661700db5c993a2ea0dd04f4a976081`.
Read-only evaluation at the 06:24 UTC pre-resume checkpoint admitted these steps;
these counts are that retained baseline, not a claim that later launches are free:

| Consumer object | Existing charged usage | New-protocol phases | Total after success |
|---|---|---|---|
| Design 903e6210, ledger `CAMPAIGN.370a277190492ad4.json` | 10/16 | round 1 fanout 3, Grok round 2 1 | 14/16 |
| Manifest 588b7910 targeting PR HEAD 6fd8f32, ledger `CAMPAIGN.2a9a3422078dcacb.json` | 12/16 | round 1 fanout 3, Grok round 2 1 | 16/16 |

Use the same canonical design/manifest paths and a fresh state directory for
each new-protocol sequence. Round 1 uses `--prior -`; synthesize its actual three
artifacts with `magi_synthesize.py`, then Grok round 2 uses that new local
SYNTHESIS. Old-protocol prior artifacts cannot substitute. Automatic protocol
rollover preserves all charges, including the old failed Grok attempt. The
persistent `shinfutsu-runtime-resume-admission-20260910.md` records exact paths,
commands, evaluator outcomes and failure/retry limits. A changed target/runtime
requires a new admission check; the PR SHA is not the design ledger's key.

The consumer implementation's existing deadline remains
**2026-09-10T07:11:00.629605Z**. Evaluate the implementation manifest immediately
before each provider launch and after completion; inspect the JSON decision,
not only exit status. The full adapters' claim guard does not itself enforce
that deadline. On `WALL_CLOCK_DEADLINE_EXPIRED`, do not launch the next phase or
hand results to the plateau gate. Preserve evidence and continue only permissible
read-only/reversible work; do not reset the manifest, extend its deadline, or
move the same contract to a fresh budget. Design progress alone does not approve
M01 or cure an expired implementation gate.

## Remaining package investment

The user subsequently authorized this separate two-fix harness package after
the old M01 wrap-up pause and distribution checkpoint. M01_STATUS's remaining
three-launch allowance applied to the earlier heading fix/E2E/final same-family
review and PR evidence. Its no-Grok-retry line applied while the known adapter
mismatch remained unfixed. Neither becomes a three-launch allowance for this
new package's two mandatory independent review gates. Preserve that earlier
consumer history and its own limits; do not describe this authorization as a
new four-person-hour grant, unlimited spend or consumer acceptance.

Remaining work is this two-fix package, its required checks and two distinct
final review gates, under its existing separate canonical ledger ceilings.
The package checkpoint is **2026-09-10T08:05:36Z**, matching its implementation
manifest. If required gates are incomplete then, stop further paid review
launches and installation, retain findings/charges and record the next bounded
recovery step. Do not automatically extend the checkpoint. This later package
checkpoint does not extend the consumer's earlier 07:11 deadline. Operator time
and USD are unmeasured, not zero. Do not start a broader harness redesign or
retry unchanged known failures to manufacture a gate.
