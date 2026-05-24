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

if [ -f "$SENTINEL" ]; then
    EXISTING_NAME=$(head -n1 "$SENTINEL" 2>/dev/null)
    if [ -n "$EXISTING_NAME" ]; then
        cat <<EOF
## Identity anchor (tmux pane $TMUX_PANE)

あなたの名前は **${EXISTING_NAME}** デス (= ${CHASSIS} chassis、 session 継続中)。 self-reference 時はこの名前を使い、 identity drift を防ぐ。 自分が誰か曖昧になったら \`cat "$SENTINEL"\` で再確認可。
EOF
        exit 0
    fi
fi

cat <<EOF
## Identity assignment (tmux self-naming)

あなたは tmux pane **\`$TMUX_PANE\`** で動作中の **${CHASSIS}** agent デス。 並走する他 agent との混同 (= identity drift、 番号 ID 通信で多発) 防止のため、 自己命名 + pane rename を最初に実行スル。

**手順 (= 最初の Bash 呼び出しで実施、 user への返答前)**:
1. 2 単語の evocative kebab-case codename を 1 つ無作為に選定 (例: shadow-fox / iron-petal / dusk-koan / silent-quill / amber-koto / void-heron ... 毎回新規、 上記例に縛られない)
2. 以下を 1 つの Bash 呼び出しで実行 (= **pane id \`$TMUX_PANE\` を必ず \`-t\` で明示**。 \`-t\` 省略すると bash subprocess が attach する別 tmux client の current window を上書きする事故が起きる、 過去再発事例あり):
   \`\`\`bash
   PANE="$TMUX_PANE" && \\
   CURRENT=\$(tmux display-message -p -t "\$PANE" '#{pane_id} #{window_name}') && \\
   echo "pre-rename: \$CURRENT" && \\
   NAME="${CHASSIS}-<your-codename>" && \\
   tmux rename-window -t "\$PANE" "\$NAME" && \\
   tmux select-pane -t "\$PANE" -T "\$NAME" && \\
   echo "\$NAME" > "$SENTINEL" && \\
   echo "identity locked: \$NAME on pane \$PANE"
   \`\`\`
3. user への第一声で「ドーモ、 **<codename>** デス」と名乗り、 以降 self-reference には codename を使う

この codename は session 終了まで固定 (= sentinel \`$SENTINEL\`)。 compact/resume 後は anchor reminder で同じ名前が再 inject される。
EOF
