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
if [ -n "${FORMATION_SELF:-}" ] && [ "$PANE_FORMATION_ID" != "$FORMATION_SELF" ]; then
    exit 0
fi

if ! CURRENT_WINDOW_NAME=$(tmux display-message -p -t "$PANE" '#{window_name}' 2>/dev/null); then
    exit 0
fi
CURRENT_PANE_TITLE=$(tmux display-message -p -t "$PANE" '#{pane_title}' 2>/dev/null || true)
TARGET_WINDOW_ID=$(tmux display-message -p -t "$PANE" '#{window_id}' 2>/dev/null || true)

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
CLAIM_DIR="$SENTINEL_DIR/.claims"
mkdir -p "$CLAIM_DIR"
# A process killed between mkdir and sentinel write can leave an empty claim.
find "$CLAIM_DIR" -mindepth 1 -maxdepth 1 -type d -mmin +5 -delete 2>/dev/null || true

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
    local cand="$1" f n
    tmux list-windows -a -F '#{window_id}|#{window_name}' 2>/dev/null \
        | awk -F '|' -v target="$TARGET_WINDOW_ID" -v cand="$cand" \
            '$1 != target && $2 == cand { found=1 } END { exit !found }' \
        && return 0
    tmux list-panes -a -F '#{pane_id}|#{pane_title}' 2>/dev/null \
        | awk -F '|' -v target="$PANE" -v cand="$cand" \
            '$1 != target && $2 == cand { found=1 } END { exit !found }' \
        && return 0
    for f in "$SENTINEL_DIR"/*; do
        [ -f "$f" ] || continue
        [ "$f" = "$SENTINEL" ] && continue
        n=$(head -n1 "$f" 2>/dev/null)
        [ "$n" = "$cand" ] && return 0
    done
    return 1
}

# Checking tmux/sentinels alone races when two hooks start together. mkdir is
# the atomic winner election; the winner publishes its sentinel before release.
CLAIM=""
try_claim_name() {
    local cand="$1"
    name_in_use "$cand" && return 1
    if mkdir "$CLAIM_DIR/$cand" 2>/dev/null; then
        CLAIM="$CLAIM_DIR/$cand"
        return 0
    fi
    return 1
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

# Never replace an established codex window with a different sentinel identity.
# Adopt it only when this pane already carries the same title; otherwise this is
# likely a new split pane sharing another worker's window.
if [[ "$CURRENT_WINDOW_NAME" == codex-* ]]; then
    if [ "$CURRENT_PANE_TITLE" = "$CURRENT_WINDOW_NAME" ]; then
        NAME="$CURRENT_WINDOW_NAME"
        RESUMED=1
    elif [ -z "$NAME" ] || [ "$NAME" != "$CURRENT_WINDOW_NAME" ]; then
        exit 0
    fi
fi

# A resumed sentinel must not reclaim a name that another live window, pane, or
# session has acquired since this session last ran.
if [ -n "$NAME" ] && name_in_use "$NAME"; then
    exit 0
fi

if [ -z "$NAME" ]; then
    for _ in $(seq 1 40); do
        CANDIDATE="codex-$(generate_codename)"
        if try_claim_name "$CANDIDATE"; then
            NAME="$CANDIDATE"
            break
        fi
    done
fi

# The base pool is finite. Use a numeric suffix rather than accepting the last
# colliding candidate when it is saturated.
if [ -z "$NAME" ]; then
    for _ in $(seq 1 40); do
        CANDIDATE="codex-$(generate_codename)-$(($(od -An -N2 -tu2 /dev/urandom | tr -d ' ') % 10000))"
        if try_claim_name "$CANDIDATE"; then
            NAME="$CANDIDATE"
            break
        fi
    done
fi

# Collision safety wins over naming: never mutate tmux with an unclaimed name.
if [ -z "$NAME" ]; then
    exit 0
fi

# Publish the claim before renaming. Concurrent hooks now see either the atomic
# claim directory or this sentinel, so they cannot choose the same name.
if ! printf '%s\n' "$NAME" > "$SENTINEL"; then
    [ -n "$CLAIM" ] && rmdir "$CLAIM" 2>/dev/null || true
    exit 0
fi
[ -n "$CLAIM" ] && rmdir "$CLAIM" 2>/dev/null || true

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
