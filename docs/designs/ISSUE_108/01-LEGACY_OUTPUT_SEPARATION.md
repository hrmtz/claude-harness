# Issue #108 Slice 1 — Legacy output separation

Status: design candidate for Ultramagi review
Parent: `docs/designs/ISSUE_108_SECRET_DISCLOSURE_AUTHORIZATION.md`
Issue: <https://github.com/hrmtz/claude-harness/issues/108>

## 1. Slice outcome

After this slice, legacy credential-read acknowledgements cannot expose a credential through Bash,
Read, or supported MCP local-file readers.

The following sequence must fail closed:

1. a credential file Read is denied;
2. the agent creates the documented legacy read marker;
3. the agent retries Read;
4. the file remains denied and no file content reaches tool output.

Likewise, a Bash command prefixed with `HARNESS_ACK_CRED_READ=1` remains denied when it matches a
credential-output pattern.

This slice intentionally does not add an authorized disclosure path. Destination-bound delivery is
Slice 2. Until then, operators use existing non-disclosing consumers or perform an external manual
delivery outside agent tool output.

## 2. Family routing

```text
FAMILY_ROUTING
preferred: Claude planning/design plateau -> Codex implementation -> Claude implementation
           design-intent review -> Codex final fixes/tests
actual: Codex planning/design -> Grok exact-revision design plateau -> Codex implementation ->
        Codex bug-hunt -> Grok exact-revision implementation design-intent review ->
        Codex final fixes/tests
missing_family: Claude
missing_phases: planning/design plateau; implementation design-intent review
reason: official Claude headroom 0.02; Formation admission defect tracked by capacity-oracle-mcp#2
degraded_until: the exact current design receives a mechanical Grok plateau marker before code,
                then the exact implementation receives the required Codex bug-hunt, Grok
                cross-family plateau marker, Codex-applied fixes, and green deterministic tests
ship_obligation: Grok is the documented complete cross-family fallback for this slice; no later
                 Claude review debt remains after degraded_until is satisfied
checkable_predicate: before each cross-family claim, record preferred=Claude, actual=Grok,
                     missing phase, and reason; accept the phase only when the plateau marker for
                     the exact artifact records reviewer_family=grok and its meta records the
                     actual Grok model/session. Never inherit actual=Grok into another slice
                     without a fresh official-capacity check and routing record.
```

No accepted implementation begins before the Grok design plateau gate. A local test-only spike
would remain reversible and unaccepted, but this plan does not need one. No Claude process is
permitted for this slice.

## 3. Scope

### 3.1 In scope

- `plugins/harness-core/hooks/bash_command_guard.sh`;
- `plugins/harness-core/hooks/credential_file_read_guard.sh`;
- their direct deterministic tests;
- authoritative hook/public-contract documentation where behavior changes;
- existing Claude/Codex/Kimi/Grok hook payload compatibility.

### 3.2 Out of scope

- a new disclosure ACK or receipt;
- SSH/file delivery;
- recent-destination lookup;
- same-day/session recurrence state;
- new persistent state;
- general shell parsing or taint correlation;
- expanding the credential source catalog;
- credential rotation;
- reading any real credential file.

Out-of-scope recommendations must be recorded against Slice 2 or 3. They cannot expand this diff.

## 4. Grounded current behavior

### 4.1 Bash

`bash_command_guard.sh` uses `PATTERNS_REASONS` entries whose optional `ack` flag identifies
patterns that may be bypassed.

It sets `ACK_BYPASS=1` when the raw command begins with:

```text
HARNESS_ACK_CRED_READ=1
```

For each matching pattern carrying `ack`, it logs a bypass and continues without adding a denial.

Current tests require that prefix to allow at least:

- a direct environment-value print;
- direct reading of `.env`;
- other CLI credential-dump commands marked `ack`.

The prefix is command-local, but it is not a read-only capability. It directly changes whether
stdout-producing commands run.

### 4.2 Read and MCP local-file readers

`credential_file_read_guard.sh` classifies known credential paths. For a blocked path it checks:

```text
~/.claude/state/cred_read_ack
```

If the marker exists and is at most 120 seconds old, the guard atomically renames and removes it,
then exits success. The Read tool consequently returns file content.

The marker is one-use but not bound to a path, tool, session, destination, or representation.

The current terminal denial path is also asymmetric: it prints prose to stderr and exits 2. It
does not call `emit_deny`. The shared helper documents that Claude-compatible CLIs consume
`hookSpecificOutput.permissionDecision=deny`, while Grok requires top-level `decision=deny`.
Slice 1 must replace this terminal path rather than assuming parity already exists.

### 4.3 PostToolUse scrubbers

Credential scrubbers run after output exists. Editing an active transcript cannot establish that
the value was never sent to model context, a renderer, hook input, or a remote service.

This slice preserves PostToolUse scrubbers as defense in depth but removes all documentation that
describes them as making an intentional plaintext read safe.

## 5. Threat model

The protected actor is a fallible agent that interprets ambiguous wording as permission to print a
secret and follows a guard's bypass instructions.

Concrete failure:

- guard correctly denies a credential source;
- denial text tells the agent how to bypass;
- agent supplies the legacy ACK;
- guard permits raw output;
- scrubber reacts only after disclosure.

Secondary failures:

- hidden tests or docs continue advertising the bypass;
- one CLI uses a different payload shape and retains the Read bypass;
- a pattern loses `ack` metadata but prose still recommends the prefix;
- a test passes because it checks process exit rather than emitted permission decision;
- rollback reintroduces the legacy bypass silently.

A malicious same-UID process may edit hooks or state and is outside scope.

## 6. Invariants

### S1 — Bash ACK cannot authorize output

`HARNESS_ACK_CRED_READ=1` never suppresses a matching credential-output denial.

### S2 — Read marker cannot authorize output

`cred_read_ack` never makes a classified credential file readable by Read or MCP local-file tools.

### S3 — No replacement bypass

Slice 1 introduces no alternate flag, marker, environment variable, receipt, helper, or command
that reveals a credential.

### S4 — Safe alternatives remain

Benign metadata operations, key-name-only operations, count-only checks, and SOPS environment
injection into a non-output consumer retain their existing behavior.

### S5 — Classification coverage is unchanged

The existing credential-path and command-pattern catalogs are not narrowed. Template exemptions
remain unchanged.

### S6 — Cross-CLI decisions remain enforceable

Claude/Codex/Kimi continue receiving the Claude-compatible deny shape; Grok continues receiving its
top-level deny shape through `emit_deny`. This is an explicit migration from the Read guard's
current stderr plus exit-2 path.

### S7 — Value-free observability

Denial messages and logs name the safety action and pattern class without file content or
environment values.

## 7. Bash design

### 7.1 Remove bypass semantics

Delete:

- `ACK_BYPASS` detection;
- the per-pattern branch that logs `BYPASS` and skips denial.

Retain `ack` in the catalog only during the same patch if needed to produce a focused compatibility
diagnostic. Preferred final state removes the flag from catalog entries and updates the catalog
comment because dead authorization metadata is misleading.

The guard continues matching both literal and de-obfuscated commands.

### 7.2 Legacy prefix handling

A command such as:

```text
HARNESS_ACK_CRED_READ=1 <credential-output-command>
```

is evaluated normally. Matching credential-output patterns deny it.

The prefix need not receive a new standalone denial when the underlying command is benign. The
slice is about preventing output bypass, not banning an inert environment assignment everywhere.

If the underlying command matches multiple patterns, the normal combined denial remains.

### 7.3 Denial guidance

Every reason string that currently says “use `HARNESS_ACK_CRED_READ=1` when the value is needed” is
rewritten to one of:

- list key names only;
- check set/unset or a count;
- use `sops exec-env` with a repo-baked consumer that does not print the value;
- wait for the destination-bound delivery slice;
- perform manual operator handling outside agent tool output.

The guard must not teach an alternative plaintext-output trick.

### 7.4 Why not retain ACK for “internal” Bash

PreToolUse sees a command string, not a proved data-flow graph. It cannot establish that an
arbitrary command will keep credential bytes out of stdout, stderr, files, subprocess arguments,
network egress, or later tools.

Therefore Slice 1 does not attempt to distinguish safe from unsafe arbitrary ACK-prefixed shell
programs. Supported safe use is expressed as existing explicit non-output patterns.

## 8. Read-tool design

### 8.1 Remove marker bypass

Delete the branch that claims `cred_read_ack` and exits success.

For every classified credential file, the guard denies regardless of marker presence.

### 8.2 Marker treatment

The guard may remove an encountered stale or fresh legacy marker only if doing so is race-safe and
cannot affect unrelated state. The safer minimal implementation is:

- ignore the marker;
- do not consume or mutate it;
- emit a migration diagnostic saying it no longer authorizes tool output.

This avoids a state mutation and lets operators remove old markers deliberately. The marker is
not secret.

### 8.3 Denial text

Remove the “Archeology bypass” instruction.

Retain:

- key count;
- key names;
- `sops exec-env` into a consumer;
- Edit guidance that does not require a full Read.

Add a stable phrase:

```text
READ_ACK_DOES_NOT_AUTHORIZE_OUTPUT
```

Build one value-free reason string, then call `emit_deny "$reason"` as the terminal operation.
Expected behavior:

- Claude/Codex/Kimi: exit 0 with one JSON document whose
  `hookSpecificOutput.hookEventName` is `PreToolUse`, whose
  `permissionDecision` is `deny`, and whose reason contains the stable phrase;
- Grok: exit 0 with one JSON document whose top-level `decision` is `deny` and whose reason contains
  the stable phrase.

Tests parse the decision object and stable reason code, not exit 2 or the full prose. No second JSON
document or extra stdout is permitted. Human guidance remains inside the reason string; debug
details, if any, go to value-free hook logs.

The end-to-end fixture table is exact:

| Environment/payload | Required stdout envelope |
|---|---|
| default Claude/Codex snake-case path | `.hookSpecificOutput.permissionDecision == "deny"` |
| Kimi Claude-compatible snake-case path | `.hookSpecificOutput.permissionDecision == "deny"` |
| `GROK_SESSION_ID` plus camel-case path | `.decision == "deny"` |

Every row also asserts the corresponding reason field contains
`READ_ACK_DOES_NOT_AUTHORIZE_OUTPUT`, exit status is zero, and stdout parses as exactly one JSON
document. Existing `test_lib_grok_compat.sh` remains the shared-helper compatibility test; the new
credential-guard rows prove the guard actually calls that helper. This does not add MCP surfaces or
new hooks.

### 8.4 MCP parity

`hooks.json` already routes supported MCP local-file readers to the same guard. No manifest change
is required for Slice 1.

File URI decoding, remote-scheme exclusion, and template exemptions remain unchanged.

## 9. Failure behavior

For classified sources:

- missing `jq` or unparsable hook input preserves current empty-path behavior; changing global hook
  fail-open policy is out of scope;
- a valid classified path always emits the CLI-correct deny object;
- marker filesystem errors cannot produce an allow because the marker is not consulted;
- logging failure cannot produce an allow because `emit_deny` is independent of logging;
- no source file is opened by the guard.

For benign/unclassified sources, behavior remains unchanged.

## 10. Test plan

All tests use synthetic paths and placeholder content. They do not create values matching the
credential scrub catalog.

The shell harness becomes fail-closed before security assertions:

- use strict error handling appropriate to the script;
- create one test-owned temporary root with checked `mktemp -d`;
- verify it is a writable directory;
- install a trap that removes only that exact root;
- abort nonzero before assertions if setup fails;
- never convert setup failure into an expected guard denial.

A harness self-test invokes the fixture setup with an intentionally unusable temporary parent and
requires a nonzero suite result. This prevents the observed false green where every temporary HOME
creation failed but the shell suite reported all assertions passed.

### 10.1 Bash regression

Change existing allow assertions to deny assertions:

- legacy prefix plus environment-value print => deny;
- legacy prefix plus `.env` reader => deny;
- legacy prefix plus CLI credential dump => deny.

Retain:

- prefix does not bypass `sops -d`;
- benign command with an inert prefix remains allowed if it matches no credential pattern;
- safe metadata/template operations remain allowed;
- de-obfuscated credential paths remain denied.

Add an assertion that emitted denial guidance does not contain instructions to enable the legacy
prefix.

### 10.2 Read regression

For a temporary HOME:

- classified path without marker => deny;
- fresh marker plus classified path => deny;
- marker remains non-authoritative;
- second attempt also denies;
- template path remains allowed;
- unclassified path remains allowed;
- local `file://` URI for a classified path denies;
- remote URI remains outside this local-file guard.

The exact Issue #108 regression is a named test:

```text
hook denial -> create read marker -> retry -> deny, with no fixture content in output
```

Every deny assertion parses the emitted JSON and checks the family-specific decision field plus
`READ_ACK_DOES_NOT_AUTHORIZE_OUTPUT`. Process exit status alone is not a denial oracle.

### 10.3 Cross-CLI shapes

Use existing shared helpers and add fixtures where coverage is absent:

- snake-case `tool_input.file_path`;
- camel-case `toolInput.path`;
- top-level MCP path variants;
- Grok environment selects top-level deny JSON;
- default selects Claude-compatible `hookSpecificOutput`.

These tests invoke `credential_file_read_guard.sh` end to end. Testing `emit_deny` in isolation is
insufficient because the defect is the Read guard's failure to call it.

### 10.4 Public-contract drift

Search tracked source/docs/tests for:

```text
HARNESS_ACK_CRED_READ
cred_read_ack
Archeology bypass
intentional read bypasses
```

Every remaining occurrence must be:

- historical release documentation explicitly labeled historical;
- a regression test proving the bypass is refused;
- a migration note saying it no longer authorizes output.

Current operational instructions must not recommend it.

This audit is a hard shipping checklist item. Its command/result is recorded in the Slice 1 stage
manifest and cannot be replaced by a prose assertion.

## 11. Acceptance mapping

| Slice criterion | Evidence |
|---|---|
| read ACK alone cannot expose plaintext | fresh-marker Read test |
| Bash ACK cannot expose plaintext | prefix-plus-reader and prefix-plus-env tests |
| block -> read ACK -> stdout disclosure blocked | named incident regression |
| no secret in logs/errors/tests | synthetic fixture plus output assertions |
| existing safe use remains | metadata, template, key-name, SOPS consumer tests |
| cross-CLI parity | payload/deny-shape fixtures |

Issue #108 criteria for destination binding, known-host transfer, recent context, and recurrence
remain explicitly pending in Slices 2 and 3.

## 12. Rollout

This is a security-breaking compatibility change.

Release notes state:

- legacy read ACKs no longer reveal credential values;
- direct plaintext inspection through agent tools is unavailable until the destination-bound
  capability ships;
- safe metadata and `sops exec-env` consumers remain supported.

No credential files, global state, or user configuration are modified by installation.

### 12.1 Bounded effort and evidence

Primary metric: live legacy credential-output authorization paths fall from two to zero.

Guardrail: all named safe-operation tests remain green.

Budget:

- focused code/test/docs work: at most 5 operator-hours;
- complete design and implementation review path: at most 8 operator-hours and 12 wall-clock hours;
- model-launch budget: the campaign guard's fixed 16 weighted launches, never extended.

If the evidence path reaches the effort cut line without discovering a new in-scope bypass, defer
redundant fixture breadth and keep the focused removal. Neither deny invariant may be deferred.

### 12.2 Resume checkpoint

Maintain a value-free stage manifest at:

```text
docs/designs/ISSUE_108/.dual-magi/slice1-execution/stage.json
```

The existing `docs/**/.dual-magi/` ignore contract must be verified before implementation. It is
state, not a review authority; the campaign ledger remains canonical for charged launches and
plateau provenance.

Before every stage, while holding a sibling `stage.lock` with `flock`:

1. evaluate the fixed whole-run deadline;
2. write `stage.json.tmp.<pid>` in the same directory with `status=in_progress`, stage name,
   artifact SHA, `started_at`, and the fixed deadline;
3. flush the file and atomically rename it to `stage.json`.

After the stage, publish the same way with `status=completed`, end time, command identifier, exit
status, evidence path, and next resumable stage. Each completed stage records:

- stage name;
- exact design or implementation artifact SHA;
- start/end timestamps;
- status;
- test command identifier and exit status;
- review artifact path;
- next resumable stage.

On restart, a corrupt/unreadable manifest, stale SHA, `in_progress` record, missing evidence, or
deadline breach fails closed. Resume from the first incomplete SHA-matching stage and rerun its
local deterministic work; never reset the canonical campaign ledger or reuse stale review
artifacts. A mismatch invalidates all later stage evidence.

The whole Slice 1 run deadline is 12 wall-clock hours from the first design fan-out. If exceeded,
stop expanding evidence, preserve the focused fail-closed patch and value-free state, and report
the exact incomplete gate. Evaluate this deadline before starting every new stage. Deadline expiry
does not authorize shipping.

## 13. Rollback

The code change is reversible through Git, but the safe rollback posture is fail-closed:

- if the new guard breaks benign commands, fix the classifier or exemption;
- do not restore the generic plaintext bypass;
- if emergency access is required, the operator handles the file outside agent tool output.

No migration or cleanup command is needed.

## 14. Implementation sequence

1. complete Codex same-family design fan-out and synthesis;
2. run Grok exact-revision design review and obtain the mechanical design plateau marker;
3. checkpoint the accepted design SHA; only now begin code;
4. update failing regression expectations first, including fixture-setup failure;
5. remove Bash bypass logic and stale guidance;
6. migrate the Read denial to `emit_deny`, remove its marker bypass, and update guidance;
7. add only the cross-CLI/read-marker fixtures required by this design;
8. audit tracked operational documentation;
9. run direct harness-core, hook-sync, and plugin validation tests independently;
10. checkpoint the exact implementation/test SHA and construct the implementation review packet;
11. run Codex bug-hunt and mandatory Grok cross-family implementation review;
12. pass the mechanical implementation plateau gate, apply final fixes, and rerun tests before
    shipping.

## 15. Residual risks

- unknown credential files outside the existing classifier remain outside this guard;
- a malicious same-UID process can modify hooks;
- PostToolUse scrubbers remain reactive;
- operators temporarily lack an agent-mediated exceptional plaintext path;
- historical docs may retain the old token for accurate release history.

These are explicit and do not weaken the Slice 1 invariant.
