---
name: discord-notify
version: 0.1.0
description: |
  Push a progress notification to the user's Discord via the shared `discord-bot`
  CLI. Use when a milestone lands (phase complete, build green, cutover done), an
  unexpected incident is detected, or an ETA moves by hours — especially while the
  user is away from the pane. Also use when asked to "notify", "報告して",
  "Discord に流して". NOT for routine turn-by-turn status: the user is reading the
  pane, and time-driven pings train them to ignore it.
allowed-tools:
  - Bash
---

# Discord progress notification

`discord-bot` is the canonical CLI (bot API, `claude_code` identity, unified
2026-05-25). Server GUILD_ID `1498858889335144497` ("Mafutsu Dev"); the token
lives in sops `discord.enc.yaml` as `DISCORD_BOT_TOKEN` + `DISCORD_GUILD_ID`.

## Usage

```bash
discord-bot post <github-repo-name> "message"   # channel name == repo name
discord-bot list                                # enumerate channels
```

Example: `discord-bot post PRS-LLM "milestone msg"`

## When to post

Milestone-driven, not time-driven:

- a chain milestone completes (phase done / build green / cutover)
- an unexpected incident is detected
- an ETA refines by hours

The user's working hours are JST 10:00-19:00. Inside that window they respond
quickly, so a milestone ping is worth it. Outside it, expect 11h+ before it is
read — batch to milestones only, and let work that finishes before 10:00 wait.

## Message format

A bold headline plus a code block:

```
**🎯 [milestone]**
```
✅ DONE  — what landed
🔄 NOW   — what is running
⏳ NEXT  — what follows + ETA
```
```

Optionally add `🗺️ Roadmap`.

## Deployment

- Installed at `~/.local/bin/{discord-notify,discord-bot}` on chichibu, laddie,
  and mars.
- talisker / zetithnas / farm do not have it — `scp` the binary plus the sops
  file if you need it there.
- `discord-notify` is a compatibility shim: a thin wrapper around
  `discord-bot post PRS-LLM`, kept so 25+ existing callers stay untouched. New
  scripts should invoke `discord-bot post <tag>` directly.

## Autonomous fallback

Without Manage Channels permission and with no `#<repo>` channel present, the
post degrades to the first text channel with a `**[<repo>]**` prefix. Creating
the channel later switches it back to direct posting automatically. Granting the
bot role Manage Channels + Manage Webhooks + Send Messages + View Channels (or
Administrator) enables channel auto-creation.

## Rollback

Old webhook implementation:
`~/sanada_backup_persistent/discord_notify_retirement_20260525/discord-notify.pre-shim.bak`

## Credential handling

Never echo the token. It is referenced only through `sops exec-env`; the CLI
resolves it itself, so no invocation here should mention the value.
