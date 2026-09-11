# Cross-family publication validation repair

Base: `f0800cd0bf2ff9cff773c2c5e313e6d6a4584642` (dev).
Incident: Shinfutsu PR53 `348924558534d9dde410772c4abf2f714bec4519`, artifact
`b989d65e2fb307642175ac386022ec1f312ea7ad7b7bb3bc2b6603bc2bb69df7`.
Root: cross-family success publication omits downstream semantic validation.

## Hypothesis and observations before editing

Hypothesis: publication validates less than G8/synthesis, recording invalid output as a
successful launch and preventing its bounded technical retry. Refutation would be rejection
by the publication verifier of the same invalid carried reference or reviewer identity.
Scope: the common Claude/Grok adapter, durable-pair verifier, G8 carried-reference check,
synthesis identity check, and existing retry/accounting behavior.

Both provider routes use `magi_xfamily.sh`; the Claude entry point merely execs it.
The common adapter validates staged findings/meta before moving the pair to canonical names
and finishing success. Contrary to a literal reading of the historical note, publication
ordering already contains a validation boundary: its *coverage* is insufficient.
`magi_verify_xfamily_artifacts.verify` validates schema, artifact identity, metadata digests,
and transcript identity, but never compares carried source_ref/synthesis_finding_id pairs.
`magi_plateau_gate.sh:carried_prior_blockers` does that comparison later at G8.
`magi_synthesize.load_sources` separately enforces four case-insensitive reviewer names per
provider; the durable-pair verifier omits this check too.
The synthesis committed-pair path reuses the durable verifier. Campaign finish itself
records status, rather than repeating artifact validation.

Existing provider tests primarily produce empty dispositions with accepted identities;
the gate regression covers prior severity retention only at the later gate. They do not
assert rejection before success publication for invalid carried pairs or identity.

## Planned correction

Move the existing G8 carried-reference function to `magi_validate_findings`, passing the
canonical document explicitly. Reuse it from G8 and the staged/durable-pair verifier.
Share the existing synthesis reviewer-name predicate with that verifier. Preserve allowed
names, prior validation, original severity, charged failures and retry ceilings. A valid
REVISE or HIGH finding remains a successful review; validation is not plateau acceptance.
Add explicit prompt instructions to copy carried pairs from prior dispositions and use
an accepted reviewer name. No output normalization or schema weakening.

## Validation evidence

The offline adapter test is `plugins/harness-magi-codex/tests/test_xfamily_publication.py`.
Its `--plugin` argument selects the runtime, preserving identical fixtures and assertions.
Base and candidate both pass valid carried pairs and valid product REVISE/HIGH cases.
The initial retry-exhaustion assertion expected budget-denial exit 4; both runtimes actually
return transition-denial exit 64. This was a test expectation error, not a runtime defect.
Initial logs are retained. The final test corrects that assertion without changing the guard.

Focused checks passed: Claude provider 19, Grok provider 13, G8 gate 41, synthesis,
campaign guard 57 plus 111 subtests, docs/design-convergence 25 plus 2 subtests.
An AST comparison confirms the extracted G8 function is unchanged except for explicit
`doc`/`__file__` binding. No new dependencies or retry/accounting machinery were introduced.
`magi_protocol.py write-external` was run after staging runtime inputs; its external-input
snapshot is unchanged because none of those external inputs changed.

Evidence directory: `/home/hrmtz/sanada_backup_persistent/g8-repair_20260911_110923/`.
Initial regression SHA256: `6d74a609bcd967226ac40b50643c0932a239b1df9577ae5c97ad90b9a73e5849`.
Initial identical-test results: base exit 1 with eight failures (83.485 s); candidate exit 0,
all four tests / sixteen subcases passed (95.578 s). Every base failure is the claimed
symptom: invalid output returned exit 0, recorded success, and published canonical files.
The valid-pair, product REVISE and transport/retry-ceiling cases also pass on base.
Logs: `publication-base-final.log` and `publication-candidate-final.log` in the evidence
directory; the final fixture and initial failed expectation attempts are retained there.
Independent review and CI results are recorded with the PR.
No plateau or historical campaign recovery is inferred from these local checks.

## Historical recovery limits

This prospective repair does not change old successful claims to failed, refund launches,
reset cycles, alter provider output, or authorize recovery of the blocked Shinfutsu campaign.
The old campaign, archive, installed plugin and pinned runtime remain read-only. Historical
admission must be evaluated separately under its preserved history; no recovery run here.


## Independent review correction: GNAT-R1-001

The first cause account omitted another identity check: synthesis rejects duplicate
finding_id values within one source, but shared validation accepts the same payload.
The reviewer supplied this counterexample; a local two-entry payload with identical IDs
and different titles confirmed acceptance by shared validation before this correction.
The old fixture used unique finding IDs, so its otherwise correct before/after evidence
could not detect this condition. This is the same publication-validation gap, not a new
budget, gate, or product-severity rule.

Move the existing length-versus-set uniqueness check into shared `validate`; synthesis
already calls that function. This also keeps same-family and prior-envelope validation
consistent with the existing downstream requirement. Add the duplicate-ID fixture to the
same before/after adapter matrix. Preserve the first round and all charged review history.

Final test SHA256: `6b7542bee764a22f121a7567cd63193bdee2b5e3839fa0b6bdf4f2a7c907a95f`.
The same final fixture produces base RED (exit 1, ten target failures, 87.654 s) and
candidate GREEN (exit 0, four tests / eighteen subcases, 105.986 s). The duplicate-ID
cases use empty dispositions to isolate identity uniqueness from carried-reference checks.
Both provider cases verify charged failure, canonical absence and the unchanged two-attempt
ceiling. Logs: `publication-base-final-v2.log`, `publication-candidate-final-v2.log`; exact
fixture: `test_xfamily_publication.final-v2.py`. Earlier evidence remains preserved.

## Family routing and delivery

Preferred: Claude design intent -> Codex implementation -> Claude independent review ->
Codex final fixes/tests. Actual: Codex performed this bounded cause-first repair and offline
regression; three independent Codex bug-hunt reviewers examined aa46cb0. Their MED duplicate-ID
finding prompted the correction above; no HIGH+ finding was reported. Claude cross-family
review of the final revision is pending; exact results and artifact identities belong in
the PR handoff. No design plateau, installed distribution, or historical recovery is claimed.
