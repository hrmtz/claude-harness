#!/usr/bin/env bash
# tmux_self_name_core.sh — chassis-agnostic identity inject markdown emitter.
#
# Goal: prevent identity drift across agents (= numbered pane ids are ambiguous,
# AI loses track of "who am I"). Each new SessionStart on a tmux pane assigns a
# sticky codename that the AI uses for self-reference and writes into the tmux
# window/pane title for the user to see.
#
# Behavior:
#   - first SessionStart in a session  → emit naming instructions
#   - subsequent SessionStarts (= compact/resume) → emit identity anchor reminder
#   - non-tmux invocation              → silent exit 0
#
# Sentinel: $HOME/.local/state/tmux_self_name/<session_id>  (one line: <name>)
set -uo pipefail

[ "${HARNESS_TMUX_SELF_NAME_DISABLE:-0}" = "1" ] && exit 0
[ "${CLAUDE_TMUX_NAME_DISABLE:-0}" = "1" ] && exit 0
[ "${HIPPOCAMPUS_TMUX_NAME_DISABLE:-0}" = "1" ] && exit 0

CHASSIS="claude"
SESSION_ID=""
while [ $# -gt 0 ]; do
    case "$1" in
        --chassis)    CHASSIS="$2"; shift 2 ;;
        --session-id) SESSION_ID="$2"; shift 2 ;;
        *)            shift ;;
    esac
done

[ -z "${TMUX_PANE:-}" ] && exit 0

if [ -z "$SESSION_ID" ]; then
    SESSION_ID="${TMUX_PANE//[^a-zA-Z0-9]/_}"
fi

SENTINEL_DIR="$HOME/.local/state/tmux_self_name"
mkdir -p "$SENTINEL_DIR"
SENTINEL="$SENTINEL_DIR/${SESSION_ID}"

find "$SENTINEL_DIR" -type f -mtime +30 -delete 2>/dev/null || true

# Formation owns this pane's routing and self-reference identity. Bypass random
# and pane-keyed sentinel naming so compact/resume and pane-id reuse cannot
# split the worker into multiple identities.
if [ -n "${FORMATION_SELF:-}" ]; then
    PANE_FORMATION_ID=$(tmux display-message -p -t "$TMUX_PANE" '#{@formation_id}' 2>/dev/null || true)
    [ "$PANE_FORMATION_ID" = "$FORMATION_SELF" ] || exit 0
    NAME="${CHASSIS}-${FORMATION_SELF}"
    WINDOW_PANES=$(tmux display-message -p -t "$TMUX_PANE" '#{window_panes}' 2>/dev/null || true)
    if [ "$WINDOW_PANES" = "1" ]; then
        tmux rename-window -t "$TMUX_PANE" "$NAME" 2>/dev/null || true
    fi
    tmux select-pane -t "$TMUX_PANE" -T "$NAME" 2>/dev/null || true
    cat <<EOF
## Formation identity anchor (tmux pane $TMUX_PANE)

あなたの Formation identity は **${FORMATION_SELF}** デス (= routing id / self-reference の source of truth、 ${CHASSIS} chassis)。 window/pane title は **${NAME}**。 user への第一声と以降の self-reference には **${FORMATION_SELF}** を使う。 compact/resume 後も変更禁止。
EOF
    exit 0
fi

# Standalone sessions can also carry a stable mailbox identity. Prefer it over
# a session sentinel so routing, display, and self-reference cannot diverge.
PANE_FORMATION_ID=$(tmux display-message -p -t "$TMUX_PANE" '#{@formation_id}' 2>/dev/null || true)
CURRENT_WINDOW_NAME=$(tmux display-message -p -t "$TMUX_PANE" '#{window_name}' 2>/dev/null || true)
WINDOW_PANES=$(tmux display-message -p -t "$TMUX_PANE" '#{window_panes}' 2>/dev/null || true)

has_foreign_chassis_ancestor() {
    local pid="${PPID:-}" comm next
    while [[ "$pid" =~ ^[0-9]+$ ]] && [ "$pid" -gt 1 ]; do
        comm="$(ps -o comm= -p "$pid" 2>/dev/null | awk 'NR==1 { print $1 }')"
        case "$comm" in
            codex|kimi|kimi-code|grok) return 0 ;;
        esac
        next="$(ps -o ppid= -p "$pid" 2>/dev/null | awk 'NR==1 { print $1 }')"
        [ "$next" = "$pid" ] && break
        pid="$next"
    done
    return 1
}

routing_id_in_use() {
    local ident="$1"
    tmux list-panes -a -F '#{pane_id}|#{@formation_id}' 2>/dev/null \
        | awk -F '|' -v target="$TMUX_PANE" -v ident="$ident" \
            '$1 != target && $2 == ident { found=1 } END { exit !found }'
}

# A Claude child launched inside another chassis inherits the parent's pane.
# Permit sequential reuse once that foreign chassis is no longer an ancestor.
case "$CURRENT_WINDOW_NAME" in
    codex-*|kimi-*|grok-*)
        [ "$WINDOW_PANES" = "1" ] || exit 0
        has_foreign_chassis_ancestor && exit 0
        ;;
esac

if [ -n "$PANE_FORMATION_ID" ]; then
    if tmux list-panes -a -F '#{pane_id}|#{@formation_id}' 2>/dev/null \
        | awk -F '|' -v target="$TMUX_PANE" -v ident="$PANE_FORMATION_ID" \
            '$1 != target && $2 == ident { found=1 } END { exit !found }'; then
        exit 0
    fi
    NAME="${CHASSIS}-${PANE_FORMATION_ID}"
    printf '%s\n' "$NAME" > "$SENTINEL" || exit 0
    if [ "$WINDOW_PANES" = "1" ]; then
        tmux rename-window -t "$TMUX_PANE" "$NAME" 2>/dev/null || true
    fi
    tmux select-pane -t "$TMUX_PANE" -T "$NAME" 2>/dev/null || true
    cat <<EOF
## Identity anchor (tmux pane $TMUX_PANE)

あなたの identity は **${PANE_FORMATION_ID}** デス (= routing id / self-reference の source of truth、 ${CHASSIS} chassis)。 window/pane title は **${NAME}**。 user への第一声と以降の self-reference には **${PANE_FORMATION_ID}** を使う。
EOF
    exit 0
fi

if [ -f "$SENTINEL" ]; then
    EXISTING_NAME=$(head -n1 "$SENTINEL" 2>/dev/null)
    if [ -n "$EXISTING_NAME" ]; then
        case "$EXISTING_NAME" in
            "${CHASSIS}-"*)
                EXISTING_ID="${EXISTING_NAME#${CHASSIS}-}"
                routing_id_in_use "$EXISTING_ID" && exit 0
                tmux set-option -p -t "$TMUX_PANE" @formation_id "$EXISTING_ID" >/dev/null 2>&1 || true
                ;;
            *) exit 0 ;;
        esac
        cat <<EOF
## Identity anchor (tmux pane $TMUX_PANE)

あなたの名前は **${EXISTING_NAME}** デス (= ${CHASSIS} chassis、 session 継続中)。 self-reference 時はこの名前を使い、 identity drift を防ぐ。 自分が誰か曖昧になったら \`cat "$SENTINEL"\` で再確認可。
EOF
        exit 0
    fi
fi

# ── First SessionStart: the HOOK picks the codename (gh #61).
# LLM "random" self-selection collapses onto a handful of names — measured over
# 286 sessions, amber-koto alone was 39 (13.6%), dusk-koto 22, slate-heron 16,
# pulled hard by the codenames shown as examples in the old prompt. The hook now
# draws uniformly from a wordlist via /dev/urandom and rejects any candidate that
# is already live (tmux window/pane titles) or claimed by a recent sentinel, so
# the injected name is collision-free. The model's only remaining job is to run
# the rename command with the name it is given.
POOL_ADJ=(
    amber ashen cobalt cinder crimson dusk ember frost hazel indigo
    iron jade midnight moss onyx pewter russet rust sable silent
    slate steady storm swift umber verdant
)
POOL_NOUN=(
    cairn cedar crane falcon fox glyph harrow heron koan koto
    lantern lattice otter petal quill raven reed rook sparrow tanuki
    thistle vireo willow wren
)

rand_mod() { echo $(( $(od -An -N2 -tu2 /dev/urandom | tr -d ' ') % $1 )); }

name_in_use() {
    local cand="$1" bare f n
    bare="${cand#${CHASSIS}-}"
    routing_id_in_use "$bare" && return 0
    tmux list-windows -a -F '#{window_name}' 2>/dev/null | grep -qx "$cand" && return 0
    tmux list-panes   -a -F '#{pane_title}'  2>/dev/null | grep -qx "$cand" && return 0
    for f in "$SENTINEL_DIR"/*; do
        [ -f "$f" ] || continue
        [ "$f" = "$SENTINEL" ] && continue
        n=$(head -n1 "$f" 2>/dev/null)
        [ "$n" = "$cand" ] && return 0
    done
    return 1
}

NAME=""
for _ in $(seq 1 40); do
    cand="${CHASSIS}-${POOL_ADJ[$(rand_mod ${#POOL_ADJ[@]})]}-${POOL_NOUN[$(rand_mod ${#POOL_NOUN[@]})]}"
    if ! name_in_use "$cand"; then NAME="$cand"; break; fi
done
# Pool saturated (all combos taken): append a numeric suffix so we still emit a
# unique name rather than looping forever.
if [ -z "$NAME" ]; then
    NAME="${CHASSIS}-${POOL_ADJ[$(rand_mod ${#POOL_ADJ[@]})]}-${POOL_NOUN[$(rand_mod ${#POOL_NOUN[@]})]}-$(rand_mod 1000)"
fi

# Claim the identity now so a concurrently-starting pane sees it during its own
# collision check (narrows the near-simultaneous race). The rename itself is left
# to the model per this chassis's design.
echo "$NAME" > "$SENTINEL"

CODENAME="${NAME#${CHASSIS}-}"
tmux set-option -p -t "$TMUX_PANE" @formation_id "$CODENAME" >/dev/null 2>&1 || true
cat <<EOF
## Identity assignment (tmux self-naming)

あなたは tmux pane **\`$TMUX_PANE\`** で動作中の **${CHASSIS}** agent、 codename は **${NAME}** デス (= hook が衝突チェック済みで割当、 LLM 側での再選定は不要・禁止)。 並走する他 agent との混同 (= identity drift、 番号 ID 通信で多発) 防止のため、 pane rename を最初に実行スル。

**手順 (= 最初の Bash 呼び出しで実施、 user への返答前)**:
1. 以下を 1 つの Bash 呼び出しで実行 (= **pane id \`$TMUX_PANE\` を必ず \`-t\` で明示**。 \`-t\` 省略すると bash subprocess が attach する別 tmux client の current window を上書きする事故が起きる、 過去再発事例あり):
   \`\`\`bash
   PANE="$TMUX_PANE" && \\
   tmux rename-window -t "\$PANE" "${NAME}" && \\
   tmux select-pane -t "\$PANE" -T "${NAME}" && \\
   echo "identity locked: ${NAME} on pane \$PANE"
   \`\`\`
2. user への第一声で「ドーモ、 **${CODENAME}** デス」と名乗り、 以降 self-reference にはこの codename を使う

この codename は session 終了まで固定 (= sentinel \`$SENTINEL\`、 hook が記録済み)。 compact/resume 後は anchor reminder で同じ名前が再 inject される。
EOF
