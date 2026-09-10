# Native context lifecycle — Epic #306, slice B

## Contract

Keep unfinished work, active jobs, ASK semantics, worker routing and verification
receipts while reducing repeatedly supplied conversation history. Claude native
auto-compaction controls context size; a durable small checkpoint assists task
continuation. A native compact is not a new conversation or a task completion.

The originally considered automatic kill/restart has been rejected. No process
restart, input injection, mailbox consumption, permission change or routing
migration is implemented. This reduces the work from an irreversible session
transition to reversible configuration plus an advisory observer. Normal focused
implementation review and offline contract tests apply; no design plateau is
claimed. Ultramagi remains required if an irreversible rotation is later added.

## Implementation

- Formation launches set `CLAUDE_CODE_AUTO_COMPACT_WINDOW=100000` unless the
  caller explicitly supplied that native environment variable. Claude caps the
  window at model capacity; native disable/override settings remain authoritative.
- `scripts/configure_token_lifecycle.py` previews by default; `--apply` atomically
  sets the native `autoCompactWindow` user setting after persistent backup. It
  preserves every other setting and does not restart existing sessions.
- The Claude-only `context_lifecycle.py` hook scans appended transcript records
  under a session lock. Split assistant records count once per message/request
  ID. Native compact boundary resets the generation, response set and reminder.
- At 50 responses, one advisory requests a short JSON handoff. It does not claim
  to force a save or compact at 50. SessionStart supplies the path and schema;
  compact/resume asks the agent to read a valid existing packet and verify refs.
- PreCompact atomically snapshots the bounded handoff and routing metadata. Mail,
  unresolved ASK records, PR receipts and processes remain in their canonical
  stores. Hooks never acknowledge ASK or duplicate message bodies into state.

## Validation and rollback

Fixture tests exercise deduplication, partial JSONL writes, compact reset,
at-most-once reminders, packet bounds, and unchanged external state. A localhost
Anthropic mock drives the installed Claude binary to exercise native compaction
without external model calls. Configuration is tested with temporary settings.

Before activation, focused independent review and these checks must pass. Revert
the implementation commit and restore the saved settings backup to remove the
policy. Existing work, logs, handoffs and process state are retained.

## Sources / limitations

Verified installed Claude Code 2.1.265. Native window controls are documented at
https://code.claude.com/docs/en/model-config#set-the-auto-compact-window and hooks
at https://code.claude.com/docs/en/hooks . No weekly-quota savings percentage is
claimed. Compaction has its own cost and may fail; the observer never substitutes
a silent reset. A genuinely new conversation receives the handoff path through
its briefing; this change does not automatically clear a human-owned session.
