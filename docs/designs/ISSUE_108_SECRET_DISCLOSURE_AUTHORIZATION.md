# Issue #108 Epic — Secret disclosure authorization

Status: active
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

- [ ] design plateau marker for the exact slice revision;
- [ ] implementation bug-hunt and cross-family plateau;
- [ ] direct harness-core and cross-CLI tests;
- [ ] merged PR linked from Issue #108.

Rollback boundary: the new denial can be reverted independently, although rollback must not restore
an undocumented plaintext bypass.

### Slice 2 — Destination-bound delivery

- add a user-prompt-derived one-use authorization receipt;
- bind scope, destination, representation, nonce, and expiry;
- support a file-preserving SSH transfer through one repo-owned wrapper;
- record value-free recent successful destinations;
- never infer chat plaintext from “send it”.

Completion evidence:

- [ ] child design and plateau;
- [ ] wrapper injection/failure tests;
- [ ] value-free history tests;
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
- Claude is unavailable for this Epic because the official capacity signal reported headroom 0.02;
  Grok is the explicit cross-family fallback.

## Sequencing

Only Slice 1 is admitted now. Later slices must not be implemented or pulled into review findings
for Slice 1. Security defects that show Slice 1 itself is unsafe remain in scope; richer delivery or
history features are follow-up scope.

## Epic completion

- [ ] Slice 1 merged and evidenced
- [ ] Slice 2 merged and evidenced
- [ ] Slice 3 merged and evidenced
- [ ] Issue #108 acceptance criteria traced to shipped tests
- [ ] public docs describe the final capability boundaries
- [ ] no unreviewed credential-output bypass remains
