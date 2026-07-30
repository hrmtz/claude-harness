# Formation pane messaging — mailbox-first rail

Canonical source for the cross-CLI instruction rail (gh #105 / #166). This text is
embedded in the always-loaded instruction surfaces (Kimi `AGENTS.md.template`,
and — via `bin/install-pane-messaging-rail.sh` — the Codex global
`~/AGENTS.md`). Durable delivery lives in `lib/mailbox.sh`; non-destructive
delivery policy and recipient resolution live in `lib/mailbox_delivery.sh`;
signaling primitives live in `lib/mailbox_notify.sh`; the exceptional
prompt-submit contract lives in `lib/wake.sh` (`tmux_send_submit`). Keep this
rail in sync with `skills/formation/SKILL.md`.

---

Messaging a Formation worker pane:

The mailbox-first contract is:

1. Prefer `formation msg <worker-id> <message>`. It appends an immutable
   mailbox row; the relay sets a badge/signal with **zero keystrokes** sent to
   the recipient prompt. `mailbox-send <pane> <body>` has the same safe
   default. Worker `formation report/done/ask` and parent `ack/resolve` use the
   same relay-or-direct signaling policy after their durable append. The
   mailbox row is the delivery guarantee; a signal is not a receipt.
   Recipients run `formation inbox` at turn boundaries.
   Lifecycle senders return exit `4` only when the row/state is already durable
   but a known pane could not be signaled. Do not automatically retry
   `report`/`done` on that code; the retry would append a duplicate row. An
   absent or unverified route is pull-only exit `0` and says
   `signal=unavailable`.
2. Never paste a body into a normal pane to wake it. An idle agent is not proof
   that its prompt is empty, and paste alone can merge with a human draft.
   `tmux capture-pane` is rendered pixels, not an editor-buffer API. Visible
   prompt text may be an editable draft, a last-input ghost, or a
   chassis-generated auto-suggestion, so its prompt state is always
   **UNKNOWN**. Never diagnose "un-submitted" or "stuck", nor send `Enter`,
   `C-u`, or a character probe, from that appearance alone. `C-u` mutates a
   real draft and only separates editable text from non-buffer UI; it cannot
   distinguish a ghost from an auto-suggestion. Use durable mailbox cursors,
   ASK state, process liveness, and pane-hash change only as structural
   activity signals. Pane stability is not proof of prompt state.
3. Prompt injection is exceptional: only an explicitly exclusive worker may
   use `formation msg --inject <worker-id> <body>` or
   `mailbox-send <pane> <body> --inject`. Exclusivity is established only by
   `formation spawn --exclusive-input`, which records
   `@formation_exclusive_input=1`; apparent idleness is not enough. Output remains
   `receipt unconfirmed`; the body stays in the mailbox and only a short pull
   nudge is injected.
4. If that exclusive direct injection is genuinely unavoidable, use the
   shared `tmux_send_submit` helper:
   - cancel copy-mode first (`send-keys -X cancel` when `#{pane_in_mode}` is 1);
   - inject via bracketed paste (`load-buffer` + `paste-buffer -p`), never
     `send-keys -l`;
   - a single `Enter` can remain visible-but-unsubmitted, so use delayed
     double-submit: `sleep ~0.4s` → `Enter` → `sleep ~0.5s` → `Enter`.
5. Shell-command launches (starting a CLI or running a command in a pane)
   stay single-`Enter`. The double-submit rail applies to pane MESSAGE
   textareas only — do not conflate the two.

`formation ask` is a semantic protocol above mailbox transport. It creates a
durable request id and `WAITING_PARENT` state in a separate request event
store. Transport receipt and semantic acknowledgement are not interchangeable:
only `formation ack <request-id>` or `formation resolve <request-id> <summary>`
closes an ASK. Ordinary reports and messages never clear it.

Review requests use their own durable ids. Send work with
`formation review-request <reviewer-id> <subject>` and return the decision with
`formation verdict <review-id> <PASS|BLOCK> <summary>`. A verdict is incomplete
until it carries the original id; Formation then copies it to both the
requester and the requester's manager. Use
`formation reviews --stale-minutes <N>` to detect unanswered review work.
