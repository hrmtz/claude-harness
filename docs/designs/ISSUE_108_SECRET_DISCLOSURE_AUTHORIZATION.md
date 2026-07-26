# Issue #108 Epic — Secret disclosure authorization

Status: active; Slice 1 merged, Slice 2A implemented
Parent issue: <https://github.com/hrmtz/claude-harness/issues/108>
Invariant: a credential read authorization alone must never authorize disclosure.

## Scope

Issue #108 contains three independently mergeable and rollback-capable outcomes. It is therefore
an Epic under the Ultramagi admission contract, not one implementation task.

### Slice 1 — Legacy output separation

Design: `docs/designs/ISSUE_108/01-LEGACY_OUTPUT_SEPARATION.md`

- retire `HARNESS_ACK_CRED_READ=1` as a Bash plaintext-output bypass;
- make the generic Read marker insufficient to reveal a credential file;
- preserve safe non-value alternatives;
- add the exact incident regression: deny -> read ACK -> output still denied.

Completion evidence:

- [ ] design plateau marker for the exact slice revision (not achieved; maintainer human waiver
      recorded after the bounded 16/16 campaign);
- [x] implementation bug-hunt and cross-family findings applied under the same waiver;
- [x] direct harness-core and cross-CLI tests;
- [x] merged PR #146 linked from Issue #108 at `9e2bcd1`.

Rollback boundary: the new denial can be reverted independently, although rollback must not restore
an undocumented plaintext bypass.

### Slice 2 — Destination-bound delivery

Coordination: `docs/designs/ISSUE_108/02-DESTINATION_BOUND_DELIVERY.md`

The maintainer approved a minimal explicit-delivery scope. It adds a value-free classifier mode,
a 120-second one-use receipt, and one fixed `scp` wrapper. Remote temp/rename, recovery,
supervision, scheduling, telemetry, and destination history are not required for this slice.

Completion evidence:

- [x] 2A implementation and synthetic tests;
- [x] Claude, Codex, Grok, and Kimi hook selection;
- [ ] merged PR linked from Issue #108.

Dependency: Slice 1.

Rollback boundary: remove the authorizer/wrapper while Slice 1 continues to deny legacy plaintext
output.

### Slice 3 — Recurrence escalation

- record value-free disclosure events;
- refuse a second plaintext-chat disclosure in the same session or local calendar day;
- retain safe file transfer, rotation, and non-disclosing use paths;
- add audit failure and concurrency tests.

Completion evidence:

- [ ] child design and plateau;
- [ ] same-session/day recurrence tests;
- [ ] implementation plateau;
- [ ] merged PR linked from Issue #108.

Dependency: Slice 2.

Rollback boundary: remove recurrence state without changing the receipt or legacy-output contracts.

## Shared invariants

- receipts, audit, logs, tests, errors, and GitHub text contain no credential value;
- SOPS remains restricted to `sops edit` and `sops exec-env`;
- no general shell parser, taint engine, memory service, or password-manager integration is added;
- missing/malformed authorization state denies disclosure but does not break benign tools;
- a malicious same-UID process is outside the forgery-resistance claim;
- PostToolUse transcript redaction is defense in depth, not pre-disclosure prevention;

## Sequencing

Slice 1 is merged and Slice 2A is implemented. Recent-destination UX remains optional follow-up
work. Slice 3 remains dependent on the explicit-delivery path being merged.

## Epic completion

- [x] Slice 1 merged and evidenced via PR #146 and maintainer human waiver
- [ ] Slice 2 merged and evidenced
- [ ] Slice 3 merged and evidenced
- [ ] Issue #108 acceptance criteria traced to shipped tests
- [ ] public docs describe the final capability boundaries
- [ ] no unreviewed credential-output bypass remains
