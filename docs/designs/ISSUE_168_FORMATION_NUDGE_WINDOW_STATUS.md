# Issue 168: land Formation nudge and window-status helpers

Status: implementation-ready design  
Issue: https://github.com/hrmtz/claude-harness/issues/168  
Related: #95, #136, #165, #166, #175, #186

## 1. Provenance and current state

Two useful helpers exist only as untracked files in the primary checkout:

```text
plugins/harness-formation/bin/formation-mail-nudge
sha256 a7f5a31d204dcbb5e3199be0d9d53fd8891232a8cb845266701e88fbb0276eed

plugins/harness-formation/bin/formation-window-status
sha256 91381c002db9cd0c830582caa251e554395ca757809540904cc966c9638b4702
```

The first is currently running manually as:

```text
/home/hrmtz/projects/claude-harness/plugins/harness-formation/bin/formation-mail-nudge --watch
```

This design treats those exact bytes as the implementation starting point, not
as an already approved contract. The live process and primary untracked files
are read-only during implementation. Activation or replacement of that process
is a separate post-merge operator action.

## 2. Decisions

1. Both helpers belong in Formation and must be tracked, tested, and documented.
2. A mail badge remains zero-keystroke by default. Automatic escalation is
   allowed only for a worker carrying the same conjunctive registry plus live
   pane `--exclusive-input` contract as `formation msg --inject`.
3. `formation-mail-nudge` must call the shared
   `mailbox_inject_nudge` primitive. It may not grow a second prompt injection
   implementation.
4. An unconfirmed nudge is never retried automatically. If it produces no
   observable effect within a bounded verification interval, append one
   durable alert to the worker's recorded parent and signal that parent with
   the existing zero-keystroke path.
5. `--watch` is never started by plugin install, `formation spawn`, a hook, or
   a default command.
6. Persistent operation uses an explicit opt-in systemd user service installer,
   not cron. systemd owns process restart and shutdown; its unit points at the
   canonical checkout by absolute path.
7. `formation-window-status` is an explicit, reversible presentation tool. It
   is never run by spawn or install. Global tmux formats and any requested
   window rearrangement are journaled before mutation and restored only against
   the same tmux server.
8. Existing Kimi/Codex mailbox-first instruction surfaces and their regression
   tests must remain mutually consistent in the same PR. If current `dev`
   already satisfies the contract, no cosmetic rewrite is required.

## 3. Shared safety invariants

- No default path sends a prompt keystroke.
- Nonexclusive, missing-registry, stale-registry, lead, and unresolved panes
  receive zero prompt keystrokes.
- A nudge contains only a short pull instruction; never the mailbox body.
- Every attempted nudge is reported as `receipt unconfirmed` and is attempted
  at most once for one pending sequence.
- `--dry-run` writes no files, pane options, tmux options, PID files, or service
  state and sends no keys.
- No helper reads credential files or serializes environment values.
- No installer persists a disposable worktree path.
- No helper is activated merely because its file was installed.
- Uninstall of the optional watcher service stops only the verified service and
  removes or archives only its namespaced state.

## 4. Mail nudge design

### 4.1 Inputs

One sweep reads:

- live panes and these pane options:
  `@formation_mail_pending`, `@formation_exclusive_input`,
  `@formation_task`, `@harness_role`, and locked Formation identity;
- the latest matching row in
  `${FORMATION_HOME:-$HOME/.formation}/formation/registry.jsonl`;
- namespaced watcher observation state below
  `${FORMATION_HOME:-$HOME/.formation}/state/mail-nudge/`.

The mailbox body is neither needed nor read.

### 4.2 Conjunctive eligibility

A pane is eligible only if:

1. it resolves and has a numeric pending sequence;
2. it is a spawned worker (`@formation_task` non-empty);
3. it is not a lead;
4. the latest canonical registry row for its locked identity resolves to this
   exact pane and has `exclusive_input: true`;
5. the live pane independently has `@formation_exclusive_input=1`;
6. the same pending sequence has been observed for at least
   `FORMATION_MAIL_NUDGE_STALE` seconds;
7. the pane snapshot has remained byte-identical for at least
   `FORMATION_MAIL_NUDGE_IDLE` seconds;
8. this pending sequence has no prior attempt receipt.

`--include-leads` is removed. An automatic prompt writer has no valid lead
exception.

Registry and pane checks happen again immediately before the shared injection
primitive is called. A check failure records a bounded refusal reason and sends
no key.

### 4.3 Observation state

Use one strict JSON file per pane under the namespaced state directory:

```json
{
  "schema_version": 1,
  "pane_id": "%123",
  "worker_id": "worker-name",
  "pending_seq": 456,
  "first_seen_at": 0,
  "snapshot_crc": "12345",
  "snapshot_since": 0,
  "attempted": false,
  "attempt_result": "",
  "attempted_at": 0,
  "attempt_snapshot_crc": "",
  "attempt_mailbox_seq": 0,
  "effect": "",
  "parent_alerted": false,
  "receipt": "not-attempted",
  "no_attempt_reason": ""
}
```

Writes are lock-protected and atomic. Pane IDs, worker IDs, integers, enum
results, and checksums are bounded before serialization. No captured pane text
is stored.

When a pending sequence changes, reset `first_seen_at`, snapshot age, and
attempt state. When a badge clears or a pane disappears, archive or remove only
that pane's state. A stale state file can never authorize a nudge because all
live and registry gates are rechecked.

The existing untracked helper's `@formation_mail_since` option is not retained:
it is never cleared by inbox, so a later sequence could inherit an old age.

### 4.4 Idle measurement

Snapshot the last 40 pane lines and store only a checksum. A new checksum resets
`snapshot_since`. Equality alone is insufficient; the elapsed time must meet
the configured idle threshold. Validate all interval/age inputs as bounded
positive integers.

An idle screen is only a heuristic. The exclusive-input contract is the actual
authorization boundary.

### 4.5 Injection and receipt

Source `lib/mailbox_delivery.sh` and call:

```text
mailbox_inject_nudge <pane> <seq> mail-nudge 1
```

This reuses the live-pane half of the conjunctive check and
`tmux_send_submit`. Mark the sequence attempted before the call so an
unconfirmed paste or process crash cannot cause automatic retry. Persist one of
`attempted-unconfirmed`, `refused`, or `failed`; never claim delivery or read.

The command output names pane, worker, sequence, age, and result, but contains
no captured text or mailbox body.

### 4.6 Effect verification and parent escalation

One field observation showed why `receipt unconfirmed` must lead somewhere:
the watcher attempted a nudge against verifier `%340`, reported the attempt,
and the pane remained idle with the same unread sequence until a human
intervened.

After an attempted nudge, wait
`FORMATION_MAIL_NUDGE_VERIFY` seconds (bounded positive integer; default 30).
Success is not inferred from `receipt unconfirmed` or from a repainting pane.
The helper records only a durable observable effect:

- the pending badge cleared or advanced, proving the inbox was pulled; or
- the mailbox gained a new row whose canonical `from` is the target worker and
  whose sequence is greater than the bounded durable-log high-water captured
  immediately before the attempt. Its timestamp must not predate
  `attempted_at`, but timestamp is only a secondary sanity check because it has
  insufficient resolution to order same-second events.

A pane snapshot change is not success evidence: a spinner, startup dialog, or
human navigation can repaint without the worker reading or acting on mail.

If neither happens, do not retry the prompt injection. Append exactly one
durable mailbox alert containing fixed metadata:

```text
mail-nudge escalation: worker=<id> pane=<pane> seq=<seq>
attempt remained unconfirmed after <N>s; operator/orchestrator inspection required
```

To make this routable, `formation spawn` additively records `parent_id` and
`parent_pane` in the worker registry row and matching immutable pane options.
The watcher requires those values to agree with the live pane before using
them. It appends through `mailbox_append` and signals through
`mailbox_signal_durable_row`; it never injects into the parent prompt.

If a legacy worker has no trustworthy parent route, emit one bounded
`parent-route-unavailable` alert to watcher stdout/state and set a visible
tmux server option. Do not guess a recipient or retry the child.

State marks `parent_alerted=true` before signaling, so a crash or unconfirmed
parent signal cannot duplicate durable alerts. A later new pending sequence
gets fresh state.

The watcher also has a bounded no-attempt ceiling,
`FORMATION_MAIL_NUDGE_NO_ATTEMPT_ALERT` (default 300 seconds). If one pending
sequence remains unchanged beyond that ceiling but no injection was attempted
because the pane never satisfied the idle heuristic or an exclusive-input gate,
send the same at-most-once parent alert with a distinct reason:
`idle-never-stable`, `nonexclusive`, or `registry-route-invalid`. This alert
does not authorize a child prompt write. It prevents “never attempted” from
being silently conflated with “attempted but ineffective”.

Receipts and state therefore distinguish:

- `not-attempted`: no child prompt write occurred, with a bounded reason;
- `attempted-unconfirmed`: one shared child nudge was attempted;
- `effective`: durable worker activity or badge progress followed;
- `parent-alerted-no-attempt`;
- `parent-alerted-no-effect`.

### 4.7 Dry run

`--dry-run` performs selection against existing state but does not create,
update, or clear observation state. For a never-observed sequence it reports
`would-observe`, not `would-nudge`. A test snapshots the filesystem and tmux
mock log before and after and requires byte identity plus zero paste/load/send
operations.

### 4.8 Watch mode

`--watch` uses a namespaced lock plus a PID identity record. A numeric live PID
alone is not sufficient; reject PID reuse by verifying the executable/argv
shape, as the mailbox relay does.

SIGTERM/SIGINT cause clean exit and state flush. The systemd service, when
enabled, is the only restart owner.

## 5. Optional systemd user service

Add:

```text
plugins/harness-formation/bin/install-formation-mail-nudge-service
```

Interface:

```text
install-formation-mail-nudge-service install
install-formation-mail-nudge-service uninstall
install-formation-mail-nudge-service status
install-formation-mail-nudge-service --dry-run install|uninstall
```

Rules:

- resolve the canonical checkout from the first `git worktree list
  --porcelain` entry;
- require the target helper to be tracked, executable, and inside that
  canonical checkout;
- write a user unit whose `ExecStart` is the absolute canonical helper path
  plus `--watch`;
- never embed credentials or caller worktree paths;
- `install` is explicit, backs up an existing unit to a unique persistent
  Sanada directory, writes atomically, daemon-reloads, and enables/starts;
- `uninstall` stops/disables the exact unit, backs up its unit and namespaced
  state, removes the unit, daemon-reloads, and removes only verified nudge
  PID/state;
- `--dry-run` is side-effect free;
- if user systemd is unavailable, fail with an actionable message. Do not
  silently fall back to cron.

The PR must not invoke `install`. Post-merge activation requires an explicit
operator decision after verifying the canonical checkout contains the merged
helper.

## 6. Window-status design

### 6.1 Explicit interface

Replace implicit mutation with:

```text
formation-window-status apply [--lead <pane>] [--task <text>] [--pane <pane>]
                              [--arrange] [--dry-run]
formation-window-status revert [--dry-run]
formation-window-status status
```

No arguments prints usage and exits 64. `status` is read-only.

Task text is bounded, stripped of control characters, and stored only as a pane
option. Locked identity/window names are never changed.

### 6.2 Journal

Before the first `apply` against one tmux server, atomically write a strict
journal below:

```text
${FORMATION_HOME:-$HOME/.formation}/state/window-status/<server-pid>.json
```

The journal records:

- tmux server PID;
- whether each global window-status option was unset and its exact prior value;
- prior `@harness_role` / `@harness_task` values for panes changed by this run;
- for `--arrange`, every affected stable window ID, session, and original
  index.

The journal contains no pane capture or process environment. Later apply calls
extend the same journal without overwriting an earlier preimage.

### 6.3 Apply and arrange

Formatting is applied only by the `apply` subcommand. Repeated apply is
idempotent.

`--arrange` remains opt-in. It computes and prints the full move plan before
mutation, parks by stable window ID, then renumbers. Any failed move aborts
further moves and leaves the journal for recovery.

### 6.4 Revert

`revert` requires a journal for the current tmux server. It restores exact
previous option presence/values, pane task/role values, and arrangement only if
the affected window-ID set still matches. On drift it restores safe independent
format/pane options, refuses index moves, and reports the unresolved layout.

After complete restoration, move the journal to a timestamped history file
rather than deleting it. Re-running revert without an active journal is a
read-only no-op.

`--dry-run` prints the apply/revert plan with no tmux or filesystem mutation.

## 7. Documentation and discovery

Update both Formation README languages and the Formation skill:

- explain badge-only default and why nudge is exceptional;
- document the conjunctive exclusive-input gate and receipt-unconfirmed truth;
- document one-shot, dry-run, watch, explicit service install/uninstall;
- document window-status apply/status/revert and global effect;
- state clearly that neither helper starts automatically.

Update command inventories so repository searches find both helpers and the
service installer.

The Kimi and Codex pane messaging surfaces must continue to state:

- `formation msg`/mailbox first;
- default zero keystrokes;
- `--inject` only for exclusive workers;
- `formation inbox` pull;
- receipt unconfirmed;
- shared delayed submit primitive.

## 8. Tests

Add dedicated shell tests with a complete fake tmux/systemctl/registry surface.

Mail nudge:

- nonexclusive registry row, exclusive pane -> zero keys;
- exclusive registry row, nonexclusive pane -> zero keys;
- missing/stale/mismatched registry -> zero keys;
- lead/standalone pane -> zero keys;
- first observation and changing snapshots do not nudge;
- stable snapshot younger than idle does not nudge;
- stale plus idle plus both exclusive gates -> exactly one short shared nudge;
- same sequence never retries, including unconfirmed result;
- an attempted nudge followed by badge clear/advance or a durable worker report
  emits no parent
  alert;
- an attempted nudge with no effect emits exactly one durable parent alert and
  uses zero parent prompt keystrokes;
- a stale sequence that never becomes eligible for an attempt emits exactly one
  distinct no-attempt parent alert and uses zero child/parent prompt
  keystrokes;
- missing/mismatched legacy parent routes are visible and never guessed;
- new sequence gets a fresh timer;
- dry run is byte-for-byte side-effect free;
- PID reuse does not block startup or kill an unrelated process;
- no spawn/install/hook path auto-starts the watcher.

Service installer:

- disposable caller worktree resolves canonical `ExecStart`;
- untracked or non-executable canonical helper refuses install;
- install/update/uninstall back up overwritten/removed state;
- dry run has no writes or systemctl actions;
- uninstall targets only the exact unit and verified namespaced process/state.

Window status:

- no-arg and status are non-mutating;
- apply snapshots exact option presence/value then sets expected format;
- repeated apply preserves the first preimage;
- task/lead changes journal prior values;
- arrange plan is deterministic and opt-in;
- revert restores exact values and indices;
- window-set drift refuses index mutation;
- dry-run apply/revert is side-effect free.

Run all existing Formation tests, especially:

```text
test_formation_hardening.sh
test_mailbox_send_delivery.sh
test_pane_messaging_rail.sh
test_wake_submit.sh
```

## 9. Acceptance

1. Both exact source helpers are represented in tracked history, with reviewed
   safety changes rather than silently copied as approved code.
2. Fresh install/spawn starts no nudge watcher and sends zero prompt keys.
3. Every nonexclusive/mismatched path is proven zero-keystroke.
4. The sole eligible path uses the shared nudge primitive exactly once and
   reports receipt unconfirmed.
5. No-effect verification produces one durable parent alert and never a second
   child prompt attempt.
6. Dry-run for both helpers and service management is proven side-effect free.
7. Service unit path is the canonical checkout and install remains explicit.
8. Service uninstall leaves no verified daemon or live namespaced state.
9. Window formatting/arrangement is explicit and journal-reversible.
10. README/SKILL discovery prevents another reimplementation caused by an
   invisible file.
11. Kimi/Codex rail surfaces and tests pass together.
12. A real tmux smoke test demonstrates: nonexclusive badge remains
    zero-keystroke; an exclusive idle fixture receives one short pull nudge; a
    second sweep sends none; a no-effect attempt alerts its parent exactly once;
    window apply/revert restores the original format.
13. Independent Kimi reviewer returns PASS or PASS WITH NOTE before merge.
    BLOCK prevents merge or service activation.

## 10. Ownership and activation

hc-orch owns this design and acceptance comparison. A separate Codex subagent
implements it in `/home/hrmtz/projects/claude-harness-wt-i168`. Kimi
`verifier` independently reviews the PR.

The implementer may not stop/restart the live primary watcher, install the
systemd service, change global tmux status, merge, or close #168. After merge,
hc-orch verifies the canonical checkout and asks the operator before replacing
the manually running watcher with the reviewed service.
