#!/usr/bin/env bash
# tmux_self_name_core.sh — claude-chassis adapter over the identity ownership core.
#
# Goal: prevent identity drift across agents (= numbered pane ids are ambiguous,
# AI loses track of "who am I"). Each SessionStart on a tmux pane resolves a
# sticky codename that the AI uses for self-reference and that appears in the
# tmux window/pane title for the user to see.
#
# All ownership logic — who may claim a pane, when a nested launch must keep its
# hands off, how a codename is picked without colliding — lives in
# identity_owner.sh and is shared with the codex, kimi and grok adapters (#95).
# This file only declares "I am the claude chassis, in this mode" and turns the
# decision into the markdown the session sees.
#
# Behavior:
#   - CLAIM, first time  → emit the identity assignment
#   - CLAIM, resumed     → emit the identity anchor reminder (compact/resume)
#   - PRESERVE / REFUSE  → silent exit 0, pane untouched
set -uo pipefail

CHASSIS="claude"
SESSION_ID=""
MODE="${HARNESS_IDENTITY_MODE:-session-start}"
while [ $# -gt 0 ]; do
    case "$1" in
        --chassis)    CHASSIS="$2"; shift 2 ;;
        --session-id) SESSION_ID="$2"; shift 2 ;;
        --mode)       MODE="$2"; shift 2 ;;
        *)            shift ;;
    esac
done

[ -n "${TMUX_PANE:-}" ] || exit 0

# shellcheck source=./identity_owner.sh
. "$(dirname "$0")/identity_owner.sh"

harness_identity_claim \
    --pane "$TMUX_PANE" \
    --chassis "$CHASSIS" \
    --mode "$MODE" \
    ${SESSION_ID:+--session-key "$SESSION_ID"} || exit 0

NAME="$HARNESS_IDENTITY_NAME"
CODENAME="$HARNESS_IDENTITY_ROUTING_ID"
PANE="$HARNESS_IDENTITY_PANE"

if [ -n "${FORMATION_SELF:-}" ]; then
    cat <<EOF
## Formation identity anchor (tmux pane $PANE)

あなたの Formation identity は **${CODENAME}** デス (= routing id / self-reference の source of truth、 ${CHASSIS} chassis)。 window/pane title は **${NAME}**。 user への第一声と以降の self-reference には **${CODENAME}** を使う。 compact/resume 後も変更禁止。
EOF
    exit 0
fi

if [ "$HARNESS_IDENTITY_RESUMED" = "1" ]; then
    cat <<EOF
## Identity anchor (tmux pane $PANE)

あなたの名前は **${NAME}** デス (= ${CHASSIS} chassis、 session 継続中)。 window/pane title は hook が再設定済み。 self-reference 時はこの名前を使い、 identity drift を防ぐ。
EOF
    exit 0
fi

# The HOOK picks the codename, not the model (gh #61). LLM "random"
# self-selection collapses onto a handful of names — measured over 286 sessions,
# amber-koto alone was 39 (13.6%), pulled hard by the codenames shown as
# examples in the old prompt. The core draws uniformly from a wordlist via
# /dev/urandom and rejects any candidate already live in tmux or claimed by a
# recent sentinel.
#
# The rename is also the hook's job now. It used to be instructions for the
# model to run, which made identity depend on model compliance and on the model
# remembering to pass `-t "$TMUX_PANE"` — omitting it renames whatever window
# another attached client happens to be looking at, an incident that recurred.
cat <<EOF
## Identity assignment (tmux self-naming)

あなたは tmux pane **\`$PANE\`** で動作中の **${CHASSIS}** agent、 codename は **${CODENAME}** デス (= hook が衝突チェック済みで割当、 LLM 側での再選定は不要・禁止)。 window/pane title (**${NAME}**) の rename は hook が実行済みなので、 あなたの作業は不要デス。

user への第一声で「ドーモ、 **${CODENAME}** デス」と名乗り、 以降 self-reference にはこの codename を使う。 並走する他 agent との混同 (= identity drift、 番号 ID 通信で多発) を防ぐため、 session 終了まで固定。 compact/resume 後は anchor reminder で同じ名前が再 inject される。
EOF
