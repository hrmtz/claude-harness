#!/usr/bin/env bash
# grok_tmux_self_name.sh — grok-chassis adapter over the identity ownership core.
#
# Registered grok-only via plugins/cross_cli_hooks.json (grok.external).
#
# Grok needed this adapter for the opposite reason to the other three. It never
# stole anyone's identity: measured on 2026-07-26, `grok --help` renames nothing
# and interactive grok leaves `window_name` untouched. What it did was have no
# identity at all — every grok pane read as `grok` in the window list, and
# GROK_TMUX_NAME_DISABLE was a variable harness-cross-cli exported and nobody
# read.
#
# Two measured facts shape this file:
#
#   1. Grok's SessionStart fires at the first prompt, not at TUI boot. TMUX_PANE
#      is present by then, so the claim works, but a grok pane is unnamed until
#      the user says something. That is a property of the CLI, not something the
#      harness can fix from here.
#   2. Grok writes its own pane title through an OSC sequence and clobbers a
#      hook-set title within the same turn. `window_name` is therefore the only
#      durable display surface for this chassis. The core writes both; the pane
#      title simply will not survive, and the formation border strip reads the
#      pane option rather than the title, so nothing downstream depends on it.
set -uo pipefail

HOOK_INPUT=$(cat 2>/dev/null || true)
SESSION_ID=$(echo "$HOOK_INPUT" | jq -r '.session_id // empty' 2>/dev/null || true)

[ -n "${TMUX_PANE:-}" ] || exit 0
command -v tmux >/dev/null 2>&1 || exit 0

# shellcheck source=./identity_owner.sh
. "$(dirname "$0")/identity_owner.sh"

harness_identity_claim \
    --pane "$TMUX_PANE" \
    --chassis grok \
    --mode "${HARNESS_IDENTITY_MODE:-session-start}" \
    ${SESSION_ID:+--session-key "$SESSION_ID"} || exit 0

NAME="$HARNESS_IDENTITY_NAME"
CODENAME="$HARNESS_IDENTITY_ROUTING_ID"
PANE="$HARNESS_IDENTITY_PANE"

if [ -n "${FORMATION_SELF:-}" ]; then
    CTX="## Formation identity anchor (tmux pane $PANE)

あなたの Formation identity は **${CODENAME}** デス (= routing id / self-reference の source of truth、 grok chassis)。 window title は **${NAME}**。 user への第一声と以降の self-reference には **${CODENAME}** を使う。 compact/resume 後も変更禁止。"
elif [ "$HARNESS_IDENTITY_RESUMED" = "1" ]; then
    CTX="## Identity anchor (tmux pane $PANE)

あなたの名前は **${NAME}** デス (= grok chassis、 session 継続中)。 window title は hook が再設定済み。 self-reference 時はこの名前を使い、 identity drift を防ぐ。"
else
    CTX="## Identity assigned (tmux pane $PANE)

あなたは **${NAME}** デス (= grok chassis)。 window rename は hook が実行済み、 あなたの作業は不要。 user への第一声で「ドーモ、 **${CODENAME}** デス」と名乗り、 以降 self-reference にはこの codename を使う。 formation mailbox の identity は pane option @formation_identity_locked (= ${CODENAME})。 pane title は grok 自身が上書きするので identity 表示は window 名側デス。"
fi

# Grok's honouring of SessionStart additionalContext is unconfirmed
# (docs/grok_hooks.md, known limitation #1). The rename above is the part that
# does not depend on the model reading anything.
jq -n --arg ctx "$CTX" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: $ctx
  }
}'
