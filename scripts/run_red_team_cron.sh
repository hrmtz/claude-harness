#!/bin/bash
# Weekly unattended harness red-team audit (issue #27 cadence: weekly).
# Runs the harness_red_team workflow headlessly via `claude -p`, which writes a report
# to docs/redteam/, posts a Discord summary, and auto-files ONLY CRITICAL findings.
# Read-only audit; mutation is human-gated. Install: see the crontab line in this repo's
# scripts/ (harness_red_team_weekly). flock is applied in the crontab line.
set -u

REPO="${HARNESS_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PROMPT="$REPO/scripts/red_team_cron_prompt.md"
LOG="$HOME/.local/log/harness_red_team_cron.log"
DISCORD_CHANNEL="${HARNESS_RED_TEAM_DISCORD_CHANNEL:-}"
CROSS_CLI_GUARD="${HARNESS_CROSS_CLI_GUARD:-$REPO/plugins/harness-core/bin/harness-cross-cli}"
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
# codex (cross-family reviewer) lives under nvm's node bin, NOT in the default cron PATH.
# Glob all node versions so an nvm upgrade doesn't silently degrade the codex round.
for _d in "$HOME"/.nvm/versions/node/*/bin; do [ -d "$_d" ] && PATH="$PATH:$_d"; done
export PATH
export SOPS_AGE_KEY_FILE="$HOME/.config/sops/age/keys.txt"
mkdir -p "$(dirname "$LOG")"

echo "==== red-team cron start $(date -Iseconds) ===="
cd "$REPO" || { echo "FATAL: cannot cd $REPO"; exit 1; }

if [ ! -f "$PROMPT" ]; then echo "FATAL: prompt missing $PROMPT"; exit 1; fi
if [ ! -x "$CROSS_CLI_GUARD" ]; then
    echo "FATAL: cross-CLI identity guard missing: $CROSS_CLI_GUARD"
    exit 1
fi

# Headless run. --dangerously-skip-permissions is required for an unattended session to
# use Workflow/Bash/gh/codex; the prompt is a fixed, version-controlled spec (no external
# input is interpolated), so the blast surface is the audit itself (read-only + CRITICAL-only filing).
"$CROSS_CLI_GUARD" --isolate-tmux -- timeout 1800 claude -p "$(cat "$PROMPT")" \
    --dangerously-skip-permissions \
    --model claude-opus-4-8 < /dev/null
RC=$?

echo "==== red-team cron end rc=$RC $(date -Iseconds) ===="
if [ "$RC" -ne 0 ]; then
    if [ -n "$DISCORD_CHANNEL" ]; then
        discord-bot post "$DISCORD_CHANNEL" "⚠️ weekly harness red-team cron FAILED (rc=$RC) on $(hostname -s 2>/dev/null || echo host) — see $LOG" 2>/dev/null || true
    fi
fi
exit "$RC"
