#!/usr/bin/env bash
# Isolated real-tmux smoke for #168. Never connects to or mutates the live server.
set -euo pipefail
trap 'echo "FAIL: test_i168_real_tmux_smoke line $LINENO" >&2' ERR
command -v tmux >/dev/null 2>&1 || {
  echo "test_i168_real_tmux_smoke: SKIP (tmux unavailable)"
  exit 0
}

HERE="$(cd "$(dirname "$0")" && pwd)"
FIXTURE="$(mktemp -d)"
SOCKET="formation-i168-smoke-$$"
cleanup() {
  tmux -L "$SOCKET" kill-server >/dev/null 2>&1 || true
  rm -r "$FIXTURE"
}
trap cleanup EXIT

tmux -L "$SOCKET" new-session -d -s i168 -n parent \
  "bash -c 'stty -echo; sleep 120'"
tmux -L "$SOCKET" new-window -d -t i168 -n child \
  "bash -c 'stty -echo; sleep 120'"
parent="$(tmux -L "$SOCKET" list-panes -t i168:parent -F '#{pane_id}')"
child="$(tmux -L "$SOCKET" list-panes -t i168:child -F '#{pane_id}')"
server_env="$(tmux -L "$SOCKET" display-message -p '#{socket_path},#{pid},0')"

tmux -L "$SOCKET" set-option -p -t "$child" @formation_identity_locked child
tmux -L "$SOCKET" set-option -p -t "$child" @formation_task fixture
tmux -L "$SOCKET" set-option -p -t "$child" @harness_role worker
tmux -L "$SOCKET" set-option -p -t "$child" @formation_exclusive_input 1
tmux -L "$SOCKET" set-option -p -t "$child" @formation_parent_id parent
tmux -L "$SOCKET" set-option -p -t "$child" @formation_parent_pane "$parent"
tmux -L "$SOCKET" set-option -p -t "$child" @formation_mail_pending 9
mkdir -p "$FIXTURE/home/formation"
jq -cn --arg pane "$child" --arg parent "$parent" \
  '{id:"child",pane_id:$pane,exclusive_input:true,parent_id:"parent",parent_pane:$parent}' \
  >"$FIXTURE/home/formation/registry.jsonl"

NUDGE="$HERE/../bin/formation-mail-nudge"
env TMUX="$server_env" FORMATION_HOME="$FIXTURE/home" \
  FORMATION_SUBMIT_SETTLE_S=0 FORMATION_SUBMIT_RETRY_S=0 \
  "$NUDGE" --stale 1 --idle 1 --verify 1 >/dev/null
sleep 1.1
env TMUX="$server_env" FORMATION_HOME="$FIXTURE/home" \
  FORMATION_SUBMIT_SETTLE_S=0 FORMATION_SUBMIT_RETRY_S=0 \
  "$NUDGE" --stale 1 --idle 1 --verify 1 >/dev/null
state="$FIXTURE/home/state/mail-nudge/pane-${child#%}.json"
jq -e '.attempted == true and .attempt_result == "attempted-unconfirmed"' "$state" >/dev/null

# A second sweep never retries the child. After the verification interval the
# unchanged badge/snapshot creates exactly one parent row and signal.
sleep 1.1
env TMUX="$server_env" FORMATION_HOME="$FIXTURE/home" \
  FORMATION_SUBMIT_SETTLE_S=0 FORMATION_SUBMIT_RETRY_S=0 \
  "$NUDGE" --stale 1 --idle 1 --verify 1 >/dev/null
jq -e '.parent_alerted == true and .effect == "no-effect"' "$state" >/dev/null
[[ "$(jq -Rsc '[splits("\n") | fromjson? | select(.to=="parent")] | length' \
  "$FIXTURE/home/mailbox/log.jsonl")" -eq 1 ]]
[[ "$(tmux -L "$SOCKET" show-options -pqv -t "$parent" @formation_mail_pending)" == "1" ]]
env TMUX="$server_env" FORMATION_HOME="$FIXTURE/home" "$NUDGE" \
  --stale 1 --idle 1 --verify 1 >/dev/null
[[ "$(jq -Rsc '[splits("\n") | fromjson? | select(.to=="parent")] | length' \
  "$FIXTURE/home/mailbox/log.jsonl")" -eq 1 ]]

# New nonexclusive sequence remains badge-only and sends zero prompt input.
tmux -L "$SOCKET" set-option -p -t "$child" @formation_mail_pending 10
tmux -L "$SOCKET" set-option -p -t "$child" @formation_exclusive_input 0
env TMUX="$server_env" FORMATION_HOME="$FIXTURE/home" "$NUDGE" \
  --stale 1 --idle 1 --verify 1 >/dev/null
sleep 1.1
env TMUX="$server_env" FORMATION_HOME="$FIXTURE/home" "$NUDGE" \
  --stale 1 --idle 1 --verify 1 >/dev/null
jq -e '.pending_seq == 10 and .attempted == false' "$state" >/dev/null

# Window formatting is confined to this socket and reverts exact option state.
tmux -L "$SOCKET" set-option -g window-status-format "old format"
tmux -L "$SOCKET" set-option -gu window-status-current-format
tmux -L "$SOCKET" set-option -g window-status-separator " | "
old_current="$(tmux -L "$SOCKET" show-options -gqv window-status-current-format)"
WINDOW="$HERE/../bin/formation-window-status"
env TMUX="$server_env" FORMATION_HOME="$FIXTURE/window-home" \
  "$WINDOW" apply --lead "$parent" --task smoke --pane "$parent" >/dev/null
env TMUX="$server_env" FORMATION_HOME="$FIXTURE/window-home" \
  "$WINDOW" revert >/dev/null
[[ "$(tmux -L "$SOCKET" show-options -gqv window-status-format)" == "old format" ]]
[[ "$(tmux -L "$SOCKET" show-options -gqv window-status-current-format)" == "$old_current" ]]
[[ "$(tmux -L "$SOCKET" show-options -gqv window-status-separator)" == " | " ]]

echo "test_i168_real_tmux_smoke: passed socket=$SOCKET child=$child parent=$parent"
