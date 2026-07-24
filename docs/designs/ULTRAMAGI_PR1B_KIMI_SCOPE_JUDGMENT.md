# PR 1b scope judgment — independent Formation artifact

> **This document is NOT part of the dual-Magi exact-revision campaign for
> `ULTRAMAGI_IMPLEMENTATION_CONVERGENCE.md`.** It is an independent scope judgment produced by
> Formation worker `issue107-kimi-scope` at the request of `issue107-codex`. It does not add a
> review round, does not change campaign state, does not touch `.dual-magi/`, the ledger, autorun
> registry, or plateau markers, and does not modify the PR 1b design or any code. It classifies
> the existing round 6/7/8 findings and proposes the smallest safe PR 1b boundary.

- issue: `hrmtz/claude-harness#107`
- date: 2026-07-24
- recommendation: **REPORT_ONLY**

## 1. Evidence of inputs read

Read in full, with SHA-256 captured at read time:

| input | sha256 |
|---|---|
| `docs/designs/ULTRAMAGI_IMPLEMENTATION_CONVERGENCE.md` | `f44642fe7f055a1fefc2f48a67a676a8309dc3642b7023ad63e66ef3337e03f5` |
| `.dual-magi/impl-convergence/round_6_xfamily.json` | `63e23df947002c03c5b5a5c142721f7b053d1ec6b1ce6016a454f203fdccaaf9` |
| `.dual-magi/impl-convergence/round_6_xfamily_synthesis.json` | `998c0bb0a7dd40feb47800b2f193633fd6ee083a215771ae34adfa079dac94b5` |
| `.dual-magi/impl-convergence/round_7_balthasar.json` | `a21c8cb5a6afaf8224450eb56fd1e98300b136d372d48080d2d502f562721e74` |
| `.dual-magi/impl-convergence/round_7_caspar.json` | `1cb860eef59976ea35491e8b1d6980f810cc276c53195c5dd8af307152a774af` |
| `.dual-magi/impl-convergence/round_7_magi_synthesis.json` | `7a899dc0fc68e01a84d5ecee0f1176bd82f8009b9ac10ccc394fa2602b331818` |
| `.dual-magi/impl-convergence/round_7_melchior.json` | `b484b9bfa451fd1160b38ae01921a761f3617d75723f144ef8927fded2fdddb2` |
| `.dual-magi/impl-convergence/round_8_xfamily.json` | `105c83ece25c9563f5999b5ace48f1baca1654fbdbd7e59530dee651e1f7960e` |
| `.dual-magi/impl-convergence/round_8_xfamily_synthesis.json` | `70bdc3c16fc973dbbd5b26d5345af653465d5b7fd9f564c5ba074a1b9c23447f` |

Live surfaces inspected only to confirm scope ownership: directory listings of
`plugins/harness-magi-codex/{scripts,schemas,tests,skills}` (confirming `magi_verify_round.py`,
`magi_convergence_gate.py`, `magi_provider_sandbox.py`, and the convergence schemas do not yet
exist; `finding.schema.json`, `magi_campaign_guard.py`, `magi_fanout_codex.sh`, `magi_xfamily.sh`,
`magi_plateau_gate.sh` do). No code was read for new-finding hunting; none was modified.

Round-7 Melchior returned GO with zero findings while citing non-existent scripts
(`magi_execution_guard.py`, `dual_magi_plateau.py`) — noted by GROK-R8-003 as plateau-bias
evidence. It contributes no finding to classify.

## 2. Classification table

Every distinct round 6/7/8 finding, canonical root first, aliases indented. Round-5 source refs
are listed for lineage only (outside the requested window).

| # | Canonical finding | Severity | Aliases / lineage | Bucket |
|---|---|---|---|---|
| A | `GROK-R6-001` (= `SYN-R6-001`) — provider reads not structurally confined to the detached snapshot (cwd/read-only masks nothing) | CRITICAL | carried from `SYN-R5-001`, `MEL-PR1B-R5-001` | **DEFER_PR2** |
| B | `GROK-R6-002` (= `SYN-R6-002`) — xfamily + lost CLAIM_ID lack shared publication reconciliation | HIGH | carried from `SYN-R5-002`, `BALTHASAR-R5-1` | **DEFER_PR2** |
| C | `GROK-R8-003` (= `SYN-R8-003`) — §13 disposition launders unresolved CRITICALs as addressed | MED | earlier form: `GROK-R6-003` (HIGH; R6 synthesis folded it into `SYN-R6-001` as duplicate) | **DEFER_PR2** |
| D | `GROK-R8-001` (= `SYN-R8-001`) — claim adoption permits uncharged provider re-execution through the started-before-receipt window (no provider-started nonce) | CRITICAL | `BALTHASAR-R7-1`, `SYN-R7-001` | **DEFER_PR2** |
| E | `GROK-R8-002` (= `SYN-R8-002`) — PASS-capable bwrap profiles mount provider auth material into the model-tool namespace | CRITICAL | `CASPAR-R7-001`, `SYN-R7-002` | **DEFER_PR2** |

No finding is classified `PR1b_REQUIRED` or `SCOPE_EXPANSION`; no finding is classified twice.
Rationale for the two empty buckets is in §3.

## 3. Rationale (tied to Issue #107 acceptance, not generic hardening)

Issue #107's minimal safe convergence improvement is: replace the unbounded "fix findings;
re-run until clean" loop with a deterministic, budget-aware verdict over implementation-review
history (CONTINUE / FINAL_REVIEW_REQUIRED / BLOCKED / REDESIGN, stall and regression detection),
while the existing `magi_plateau_gate.sh` G1–G9 remains the sole PASS authority.

The decisive observation: **every open round 6/7/8 finding is conditioned on the enforcement
machinery, not on the evaluation logic.**

- Root A (confinement), Root D (uncharged relaunch), and Root E (credential exposure) exist only
  because the blocked design has PR 1b *launching and confining providers* (bwrap profiles,
  claim-or-adopt, receipts). A report-only evaluator launches nothing, adopts nothing, and mounts
  nothing; all three defects have no surface to live on.
- Root B (reconciliation) is recovery machinery for crash windows that only exist when the
  orchestrator itself stages/publishes provider output — again enforcement-only.
- Root C (§13 overclaim) is requirement-revision cleanup whose only hazard is authorizing
  enforcement under false pretenses; a REPORT_ONLY recommendation removes the hazard, and the
  text fix rides with the PR 2 design revision.

Why DEFER_PR2 rather than SCOPE_EXPANSION for A/D/E: per the decision boundary, ambiguous cases
take the safer bucket. These three are not "optional stronger guarantees" — they are hard
prerequisites for any future enforcement. Calling a fuse-integrity hole (D) or a
credential-confidentiality hole (E) "optional" would misclassify them and invite exactly the
§13-style laundering Root C warns about. PR 2 already owns "process ownership, cancellation,
recovery" per the campaign's own cut lines (design §9/§12), which is where provider sandboxing,
claim adoption, and receipt reconciliation belong.

Why nothing is PR1b_REQUIRED: no reviewer found a defect in the deterministic decision table,
the severity-mass/stall rules, the ledger-to-artifact history walk, or the read-only G1–G6/G9
verifier factorization — the components that constitute a minimal report-only deliverable. The
briefing also forbids promoting MED/LOW items (Root C is MED) into mandatory PR 1b scope.

## 4. Minimal PR 1b boundary

### Deliverable

A **report-only deterministic evaluator** plus the read-only verifier factorization:

```text
python3 scripts/magi_convergence_gate.py evaluate <implementation-manifest.json>
```

- Reads an operator-authored manifest (descriptor-validated), the existing campaign ledger, and
  existing review artifacts; verifies `base..target` diff coverage against the live repository
  read-only at evaluation time.
- Emits exactly: `CONTINUE(initial-full|full-target-fix)`, `FINAL_REVIEW_REQUIRED(final-full)`,
  `BLOCKED(reason)`, `REDESIGN(reason)`. **PASS and PASS_WITH_RESIDUALS are not emitted in this
  mode.** Ship authorization stays where it is today: human judgment plus the existing
  `magi_plateau_gate.sh` G1–G9 marker. This keeps the design's own invariant ("the evaluator
  never writes a plateau marker") trivially true and makes Roots A/B/D/E unreachable.
- Decision logic, precedence, severity mass, stall/regression rules, and budget/affordability
  checks come from design §8 as written, reusing shared guard helpers (no fuse-arithmetic
  duplication). Well-formed decisions exit 0; unsafe manifest open exits 2; usage errors exit 64.
- History walk = ledger-to-receipt-to-artifact completeness over **existing** campaigns (design
  §5.4 minus the convergence-receipt requirements, which presuppose enforcement receipts that do
  not exist yet).

### Included surfaces (PR 1b)

- `plugins/harness-magi-codex/scripts/magi_convergence_gate.py` — `evaluate` + shared history
  helpers only. No `start`, `prepare`, `evidence`, registry, snapshots, or receipts.
- `plugins/harness-magi-codex/scripts/magi_verify_round.py` — factorized read-only G1–G6/G9
  checks; `magi_plateau_gate.sh` calls it and keeps G7/G8; marker bytes and behavior unchanged.
- `plugins/harness-magi-codex/schemas/implementation-convergence.schema.json` — manifest
  contract; `trusted_tests`/`result_directory` optional in v1 report-only (their absence is
  recorded in the decision reason, never silently treated as green).
- `plugins/harness-magi-codex/schemas/finding.schema.json` + `magi_validate_findings.py` —
  additive optional fields only (subsystem, root_cause_id, affected_invariant,
  changes_design_invariant, relation_to_prior, residual, closures). Legacy payloads stay valid;
  no provider is required to emit the new fields in v1.
- `plugins/harness-magi-codex/tests/test_convergence_gate.py` (+ shared-verifier tests).
- Docs: `skills/ultramagi/SKILL.md` (both plugins) gain "run the evaluator after each
  implementation-review phase; output is advisory; plateau gate + human remain ship authority";
  README note.

### Excluded surfaces (explicit non-goals for PR 1b)

- `magi_campaign_guard.py` changes (`claim-or-adopt`, `finish-or-confirm`, prepared receipts,
  `--expected-ledger-sha256`) — Root B/D machinery → PR 2.
- `magi_provider_sandbox.py`, bwrap profiles, sentinel/credential probes — Root A/E machinery →
  PR 2.
- `magi_convergence_test_result.py`, evidence index, detached worktree snapshots, XDG registry,
  `start`/`prepare`, manifest archiving — process-execution and immutability machinery → PR 2.
- `--convergence-manifest` adapter flags and convergence-mode prompt contracts in
  `magi_fanout_codex.sh` / `magi_xfamily.sh` → PR 2.
- Diff-scoped review, weight-1 optimization, diff-context envelope → PR 3 (as already cut).
- Any change to ledger schema, phase weights, plateau marker format, or PR 1a reserve.

### Acceptance tests for this boundary

- Evaluator unit/fixture tests over synthetic ledger histories: no successful fanout →
  `CONTINUE(initial-full)`; fanout without xfamily → `FINAL_REVIEW_REQUIRED(final-full)`; two
  consecutive blocker-mass non-decreases across three target revisions → `BLOCKED`; regression in
  two consecutive revisions / same-subsystem new roots / `changes_design_invariant=true` →
  `REDESIGN`; budget exhaustion or unaffordable next transition → `BLOCKED`; gate denial after a
  blocker review is well-formed non-PASS, not corruption.
- Completeness: an early HIGH dropped from later synthesis still counts; failed/abandoned claims
  charge budget but contribute no evidence; malformed/absent/stale artifacts → `BLOCKED`.
- Mode guarantee: no input produces PASS-family output; every well-formed decision exits 0;
  malformed manifest exits 2; usage error exits 64.
- Read-only guarantee: repository, ledger, and `.dual-magi/` trees are byte-identical
  (sha256 + `git status`) before/after any evaluator run; evaluator writes nothing but stdout
  and its own output file.
- Schema: legacy finding payload validates; extended payload validates; unknown additional
  properties still rejected.
- Verifier factorization: existing `test_plateau_gate.sh` and guard/autorun/adapter/lock/scrub
  suites stay green; plateau markers on fixtures are byte-identical pre/post refactor.
- Manifest validation: wrong repository, wrong target SHA, or a diff path omitted from
  `changed_paths` fails closed.

## 5. Recommendation: REPORT_ONLY

Enforcement of PASS-family decisions is **not safe** on the current design bytes: Roots D and E
are open CRITICALs on the exact mechanisms (claim adoption, provider sandbox) that enforcement
would depend on, and the mechanical plateau gate already denied the enforcement design at G7/G8.
Abandoning PR 1b is unnecessary: the evaluation core — the piece that actually fixes Issue
#107's unbounded-loop problem — is unattacked, small, and safe as a read-only diagnostic.
REPORT_ONLY is materially smaller than the blocked design (drops sandbox, registry,
reconciliation, receipts, test producer, and adapter flags — roughly the §12 registry +
snapshot + confinement + evidence packages, ~10–22 person-hours of the 50-hour cap).

## 6. Residual risks and handoffs

Residual risks accepted under REPORT_ONLY:

- Advisory output is evaluated against live-repo state at run time (TOCTOU staleness). Acceptable
  because nothing is authorized by it; recorded in the decision reason.
- Humans may over-trust advisory verdicts. Mitigated by output wording ("advisory; plateau gate
  G1–G9 + human remain ship authority") and SKILL.md text.
- Roots D and E remain open holes on the enforcement path. They must not be forgotten: this
  judgment carries them as named PR 2 prerequisites, and any future enforcement design revision
  must resolve them *in normative text* before re-review (Root C).

Named handoffs:

- **PR 2 (enforcement, only if ever pursued)** — prerequisites, in order:
  1. Root E: tool-inaccessible authentication (short-lived env/broker/fd) with credential-sentinel
     probes per provider; no secret-bearing mounts in the tool namespace.
  2. Root D: fsynced provider-started nonce before spawn; adopt-launches-only-if-absent;
     used-15 recovers complete staged evidence only; full crash-injection matrix.
  3. Root B: one shared claim-or-adopt/finish-or-confirm reconciliation for fanout and xfamily.
  4. Root A: audited per-provider confinement profiles with live/snapshot conflicting sentinels.
  5. Root C: rewrite §13 to list only verifiably-resolved items; extend §12 abort triggers to
     confinement, credential-isolation, and reconciliation failure.
  6. Registry/snapshot/test-producer/evidence-index machinery and adapter flags.
- **PR 3** — diff-scoped review, weight-1 optimization, diff-context envelope (unchanged from the
  existing cut line).

## 7. Campaign-state attestation

This judgment read campaign artifacts and wrote exactly one new file (this document). No
`.dual-magi/` file, ledger, autorun registry entry, plateau marker, design document, or source
file was created, modified, or deleted; no review was launched; no git/GitHub state was touched.
