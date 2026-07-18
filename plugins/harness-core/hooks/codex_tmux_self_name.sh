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

PANE="${TMUX_PANE:-}"
[ -z "$PANE" ] && exit 0
command -v tmux >/dev/null 2>&1 || exit 0

# Refuse to touch a pane that belongs to another formation worker.  TMUX_PANE
# can be stale/inherited while a detached tmux client is starting; targeting
# every command with -t is necessary, but not sufficient when the target itself
# is wrong.
PANE_FORMATION_ID=$(tmux display-message -p -t "$PANE" '#{@formation_id}' 2>/dev/null || true)
if [ -n "${FORMATION_SELF:-}" ] && [ -n "$PANE_FORMATION_ID" ] \
    && [ "$PANE_FORMATION_ID" != "$FORMATION_SELF" ]; then
    exit 0
fi

if ! CURRENT_WINDOW_NAME=$(tmux display-message -p -t "$PANE" '#{window_name}' 2>/dev/null); then
    exit 0
fi

# Never overwrite another chassis's established identity.  formation currently
# creates every new window with a legacy "claude-$FORMATION_SELF" placeholder;
# permit only that exact, ownership-verified bootstrap name for a codex worker.
case "$CURRENT_WINDOW_NAME" in
    claude-*|kimi-*|grok-*)
        if [ -z "${FORMATION_SELF:-}" ] \
            || [ "$PANE_FORMATION_ID" != "$FORMATION_SELF" ] \
            || [ "$CURRENT_WINDOW_NAME" != "claude-$FORMATION_SELF" ]; then
            exit 0
        fi
        ;;
esac

if [ -z "$SESSION_ID" ]; then
    SESSION_ID="${PANE//[^a-zA-Z0-9]/_}"
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
    case "$NAME" in
        codex-*) RESUMED=1 ;;
        *) NAME="" ;;
    esac
fi

# A codex identity already present on the verified target is authoritative when
# no session sentinel exists.  Do not replace one codex worker's identity with
# another merely because the hook received a new/missing session_id.
if [ -z "$NAME" ] && [[ "$CURRENT_WINDOW_NAME" == codex-* ]]; then
    NAME="$CURRENT_WINDOW_NAME"
    RESUMED=1
fi

if [ -z "$NAME" ]; then
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        NAME="codex-$(generate_codename)"
        name_in_use "$NAME" || break
    done
fi

# -t is mandatory: without it a detached tmux client's current window gets
# renamed instead of ours (recurring incident, see tmux_self_name_core.sh).
tmux rename-window -t "$PANE" "$NAME" 2>/dev/null || true
tmux select-pane -t "$PANE" -T "$NAME" 2>/dev/null || true

# Formation mailbox identity: only set if the pane has none yet.
FORMATION_ID=$(tmux display-message -p -t "$PANE" '#{@formation_id}' 2>/dev/null || true)
if [ -z "$FORMATION_ID" ]; then
    FORMATION_ID="${NAME#codex-}"
    tmux set-option -p -t "$PANE" @formation_id "$FORMATION_ID" >/dev/null 2>&1 || true
fi

echo "$NAME" > "$SENTINEL"

if [ "$RESUMED" -eq 1 ]; then
    CTX="## Identity anchor (tmux pane $PANE)

あなたの名前は **${NAME}** デス (= codex chassis、 session 継続中)。 window/pane title は hook が再設定済み。 self-reference 時はこの名前を使い、 identity drift を防ぐ。"
else
    CTX="## Identity assigned (tmux pane $PANE)

あなたは **${NAME}** デス (= codex chassis)。 window/pane rename は hook が実行済み、 あなたの作業は不要。 user への第一声で「ドーモ、 **${NAME#codex-}** デス」と名乗り、 以降 self-reference にはこの codename を使う。 formation mailbox の identity は pane option @formation_id (= ${FORMATION_ID:-$NAME})。"
fi

jq -n --arg ctx "$CTX" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: $ctx
  }
}'
