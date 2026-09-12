# Magi cause-first repair: local validation

Date: 2026-09-11. Base: `e9a7cf6da18834f6febd80d1d675e43bb944fa15`.
Scope and limits: [design](../../designs/MAGI_REVISE_RECOVERY.md).

## Cause and correction

The continuation hook previously requested generic fixes and the next phase without requiring a
causal hypothesis, observations, or a regression tied to the reported symptom. The same omission
appeared in the family-specific repair instructions. The shared contract now requires those steps
before investigation, editing, and re-review respectively; repeated roots require correcting the
previous causal explanation. It is an orchestration contract, not a hard edit/admission gate.

The existing no-progress fingerprint also ignored repair notes. A bounded regular
`REPAIR.<document_id>.md` file now contributes its content digest to progress, without exposing or
certifying its contents. Merely touching the file does not count as progress. Campaign spending,
terminal states, and plateau authority remain separate.

## Before/after evidence

The same final autorun test file was applied to the old autorun implementation and the candidate:
test SHA-256 `5440b108fd09bdd9816c2730647b4e9b5d6b6bc14d2ecd5c93340d15262ca7cb`.

- Old implementation: 3 selected tests failed with 29 assertions about missing instructions,
  unrecognized repair progress, and unsafe record handling; no setup errors.
- Candidate: the same 3 tests pass; all 25 autorun tests pass.
- Profile mirrors: missing cause-first contracts fail on the baseline; all 8 mirror/installer
  checks pass after the change.
- Campaign guard: both tests selected by `-k protocol` pass.
- Independent static review of the final diff found no additional concrete defects.

Candidate autorun SHA-256: `fd342cb368d20e31b0db91b5c4145f8565892243ebaba1b83fd4621fd5e8480e`.
Protocol SHA-256: `9bbd9d2d178c0f5e6b112dfbbef32a9ebb31dbdbea184c8e1eb2d753a8915928`.

Commands:

```sh
python3 plugins/harness-magi-codex/tests/test_autorun.py
python3 plugins/harness-magi-codex/tests/test_profile_mirrors.py
python3 -m unittest plugins/harness-magi-codex/tests/test_campaign_guard.py -k protocol
```

The first candidate failed the directory-record case: `fdopen` ran before the regular-file check.
The correction checks `fstat` first and closes the descriptor on every exit. The test fixture and
expected behavior were unchanged. Intermediate failures and final results are retained locally
alongside this report and in the persistent backup archive.

## Known limits

`test_docs_match_scripts.py` reports the same two undocumented preflight variables on the base and
candidate: `MAGI_PREFLIGHT_MODEL` and `MAGI_PREFLIGHT_REASONING_EFFORT`. This pre-existing drift is
recorded separately; it is not reported as a passing check.

No provider was launched for this repair's causal analysis. No installed plugin, past review
artifact, claim, or campaign budget was rewritten. This change does not repair the separate G8
review-provenance failure, certify any existing campaign, or grant shipping authority.
