# Issue #108 Slice 2 — Destination-bound delivery

Status: Slice 2A implemented; maintainer-approved minimal scope
Parent: `docs/designs/ISSUE_108_SECRET_DISCLOSURE_AUTHORIZATION.md`
Dependency: Slice 1 merged in PR #146 at `9e2bcd1`

## 2A — Explicit one-use SCP delivery

Contract: `docs/designs/ISSUE_108/02A-SCP-DELIVERY.md`

One exact user prompt binds a classified local source file and one `scp://` destination to a
session-specific receipt. The receipt expires after 120 seconds and is consumed before one fixed
`scp` attempt. Credential bytes are never returned through hook, wrapper, or child output.

The implementation intentionally uses the operator's existing SSH configuration, including host
aliases and identity selection. It forces batch mode, known-host verification, no password or
keyboard-interactive fallback, and no forwarding or local commands.

## Deferred UX

Recent-destination recall for an ambiguous `送って` prompt is a separate optional slice. It is not
required for explicit delivery and cannot mint a receipt by itself.

Remote temporary files, rename protocols, crash supervisors, recovery daemons, concurrency pools,
capacity scheduling, executable hashes, and telemetry are not part of Issue #108's minimum
destination-bound requirement.

## Completion

- [x] classified-path mode shares the Read guard catalog
- [x] exact prompt mints a 120-second session-bound receipt
- [x] fixed `scp` wrapper consumes the receipt once
- [x] Claude, Codex, Grok, and Kimi prompt-hook selection is wired
- [x] synthetic disclosure, mutation, session, port, and one-use tests pass
- [ ] merge and link the implementation from Issue #108
