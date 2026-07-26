#!/usr/bin/env bash
# codex_tmux_self_name.sh — codex-chassis adapter over the identity ownership core.
#
# Registered codex-only via plugins/cross_cli_hooks.json (codex.external) — NOT
# in hooks.json, so Claude never loads it.
#
# All ownership logic lives in identity_owner.sh and is shared with the claude,
# kimi and grok adapters (#95). This file declares the chassis and mode, and
# wraps the result in Codex's hookSpecificOutput envelope.
#
# Mode transport: a SessionStart hook cannot see argv, so it cannot tell
# `codex exec` from an interactive TUI on its own. Launchers that know say so
# through HARNESS_IDENTITY_MODE; harness-cross-cli sets it for every
# non-interactive child. Defaulting to session-start is the safe half of that
# gap — a bare `codex exec` in a free pane may name it, but a `codex exec`
# inside another CLI's pane is refused by ownership, which is the case that
# actually caused drift.
set -uo pipefail

HOOK_INPUT=$(cat 2>/dev/null || true)
SESSION_ID=$(echo "$HOOK_INPUT" | jq -r '.session_id // empty' 2>/dev/null || true)

[ -n "${TMUX_PANE:-}" ] || exit 0
command -v tmux >/dev/null 2>&1 || exit 0

# shellcheck source=./identity_owner.sh
. "$(dirname "$0")/identity_owner.sh"

harness_identity_claim \
    --pane "$TMUX_PANE" \
    --chassis codex \
    --mode "${HARNESS_IDENTITY_MODE:-session-start}" \
    ${SESSION_ID:+--session-key "$SESSION_ID"} || exit 0

NAME="$HARNESS_IDENTITY_NAME"
CODENAME="$HARNESS_IDENTITY_ROUTING_ID"
PANE="$HARNESS_IDENTITY_PANE"

if [ -n "${FORMATION_SELF:-}" ]; then
    CTX="## Formation identity anchor (tmux pane $PANE)

あなたの Formation identity は **${CODENAME}** デス (= routing id / self-reference の source of truth、 codex chassis)。 window/pane title は **${NAME}**。 user への第一声と以降の self-reference には **${CODENAME}** を使う。 compact/resume 後も変更禁止。"
elif [ "$HARNESS_IDENTITY_RESUMED" = "1" ]; then
    CTX="## Identity anchor (tmux pane $PANE)

あなたの名前は **${NAME}** デス (= codex chassis、 session 継続中)。 window/pane title は hook が再設定済み。 self-reference 時はこの名前を使い、 identity drift を防ぐ。"
else
    CTX="## Identity assigned (tmux pane $PANE)

あなたは **${NAME}** デス (= codex chassis)。 window/pane rename は hook が実行済み、 あなたの作業は不要。 user への第一声で「ドーモ、 **${CODENAME}** デス」と名乗り、 以降 self-reference にはこの codename を使う。 formation mailbox の identity は pane option @formation_identity_locked (= ${CODENAME})。@formation_id は互換 alias。"
fi

jq -n --arg ctx "$CTX" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: $ctx
  }
}'
