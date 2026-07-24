# Formation pane messaging — double-submit rail

Canonical source for the cross-CLI instruction rail (gh #105). This text is
embedded in the always-loaded instruction surfaces (Kimi `AGENTS.md.template`,
and — via `bin/install-pane-messaging-rail.sh` — the Codex global
`~/AGENTS.md`). The runtime contract itself lives in `lib/wake.sh`
(`tmux_send_submit`) and is pinned by `tests/test_wake_submit.sh`; the
instruction surfaces are pinned by `tests/test_pane_messaging_rail.sh`.
Keep the wording, helper names, and timings in sync with
`skills/formation/SKILL.md`.

---

Messaging a Formation worker pane:

1. Prefer `formation msg <worker-id> <message>` (or the shared
   `tmux_send_submit` helper, used by `formation msg` and the mailbox relay).
   Do not hand-roll `tmux send-keys` injection when those paths exist.
2. A single `Enter` can leave the text visible-but-unsubmitted — the worker
   stays stuck. Submit is ALWAYS the delayed double-submit:
   `sleep ~0.4s` → `Enter` → `sleep ~0.5s` → `Enter`.
3. If direct tmux injection is genuinely unavoidable:
   - cancel copy-mode first (`send-keys -X cancel` when `#{pane_in_mode}` is 1);
   - inject via bracketed paste (`load-buffer` + `paste-buffer -p`), never
     `send-keys -l`;
   - then the delayed double-submit above.
4. Shell-command launches (starting a CLI or running a command in a pane)
   stay single-`Enter`. The double-submit rail applies to pane MESSAGE
   textareas only — do not conflate the two.
