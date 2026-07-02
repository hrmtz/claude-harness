#!/usr/bin/env bash
# codex_tmux_self_name.sh — SessionStart hook (codex chassis).
#
# Unlike the claude chassis (tmux_self_name.sh, which instructs the model to
# pick its own codename and run the tmux commands), this hook renames the
# window/pane DETERMINISTICALLY in the hook itself — no reliance on model
# compliance — then injects the assigned identity via additionalContext so the
# model knows who it is (self-reference, formation mailbox).
#
# Behavior:
#   - non-tmux invocation → silent exit 0
#   - first SessionStart  → generate "codex-<adj>-<noun>", rename window +
#     pane title, set @formation_id if absent, write sentinel, inject identity
#   - resume/compact      → reuse sentinel name, re-assert rename, inject anchor
#
# Sentinel: $HOME/.local/state/tmux_self_name/<session_id|pane_key>
# Registered codex-only via plugins/cross_cli_hooks.json (codex.external) —
# NOT in hooks.json, so Claude never loads it.
set -uo pipefail

HOOK_INPUT=$(cat 2>/dev/null || true)
SESSION_ID=$(echo "$HOOK_INPUT" | jq -r '.session_id // empty' 2>/dev/null || true)

[ -z "${TMUX_PANE:-}" ] && exit 0
command -v tmux >/dev/null 2>&1 || exit 0

if [ -z "$SESSION_ID" ]; then
    SESSION_ID="${TMUX_PANE//[^a-zA-Z0-9]/_}"
fi

SENTINEL_DIR="$HOME/.local/state/tmux_self_name"
mkdir -p "$SENTINEL_DIR"
SENTINEL="$SENTINEL_DIR/${SESSION_ID}"
find "$SENTINEL_DIR" -type f -mtime +30 -delete 2>/dev/null || true

# Codename pool mirrors kimi-wrapper.sh generate_formation_id.
generate_codename() {
    local adjectives=(
        amber cinder crimson dusk ember iron midnight moss muted onyx
        rust silent slate steady storm swift woven
    )
    local nouns=(
        crane falcon fox heron lantern otter raven rook tanuki wren
    )
    local i1 i2
    i1=$(($(od -An -N2 -i /dev/urandom | tr -d ' ') % ${#adjectives[@]}))
    i2=$(($(od -An -N2 -i /dev/urandom | tr -d ' ') % ${#nouns[@]}))
    echo "${adjectives[$i1]}-${nouns[$i2]}"
}

name_in_use() {
    tmux list-windows -a -F '#{window_name}' 2>/dev/null | grep -qx "$1"
}

RESUMED=0
NAME=""
if [ -f "$SENTINEL" ]; then
    NAME=$(head -n1 "$SENTINEL" 2>/dev/null)
    [ -n "$NAME" ] && RESUMED=1
fi

if [ -z "$NAME" ]; then
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        NAME="codex-$(generate_codename)"
        name_in_use "$NAME" || break
    done
fi

# -t is mandatory: without it a detached tmux client's current window gets
# renamed instead of ours (recurring incident, see tmux_self_name_core.sh).
tmux rename-window -t "$TMUX_PANE" "$NAME" 2>/dev/null || true
tmux select-pane -t "$TMUX_PANE" -T "$NAME" 2>/dev/null || true

# Formation mailbox identity: only set if the pane has none yet.
FORMATION_ID=$(tmux display-message -p -t "$TMUX_PANE" '#{@formation_id}' 2>/dev/null || true)
if [ -z "$FORMATION_ID" ]; then
    FORMATION_ID="${NAME#codex-}"
    tmux set-option -p -t "$TMUX_PANE" @formation_id "$FORMATION_ID" >/dev/null 2>&1 || true
fi

echo "$NAME" > "$SENTINEL"

if [ "$RESUMED" -eq 1 ]; then
    CTX="## Identity anchor (tmux pane $TMUX_PANE)

あなたの名前は **${NAME}** デス (= codex chassis、 session 継続中)。 window/pane title は hook が再設定済み。 self-reference 時はこの名前を使い、 identity drift を防ぐ。"
else
    CTX="## Identity assigned (tmux pane $TMUX_PANE)

あなたは **${NAME}** デス (= codex chassis)。 window/pane rename は hook が実行済み、 あなたの作業は不要。 user への第一声で「ドーモ、 **${NAME#codex-}** デス」と名乗り、 以降 self-reference にはこの codename を使う。 formation mailbox の identity は pane option @formation_id (= ${FORMATION_ID:-$NAME})。"
fi

jq -n --arg ctx "$CTX" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: $ctx
  }
}'
