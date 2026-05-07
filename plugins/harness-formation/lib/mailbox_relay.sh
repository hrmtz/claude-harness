#!/usr/bin/env bash
# mailbox_relay.sh — mailbox write → target pane auto-inject
#
# mailbox append-only log の欠点 (対象 agent が読まない限り気付かない) を補う relay:
#   - inotifywait で log.jsonl の変更監視
#   - 新 line の "to" field が watch 対象 agent なら該当 tmux pane に send-keys inject
#   - agent は次 input wait で injected text を user input として処理
#
# 使い方 (各 pane の裏で独立 daemon として走らせる):
#
#   # main-5 pane 用 (main-5 宛の msg を main-5 tmux pane に inject):
#   nohup bash lib/mailbox_relay.sh main-5 main-5 > /tmp/relay_main5.log 2>&1 &
#
#   # qdrant-parallel pane 用:
#   nohup bash lib/mailbox_relay.sh qdrant-parallel qdrant-exp > /tmp/relay_qdrant.log 2>&1 &
#
# 第 1 引数: mailbox 上の agent 名 (msg の "to" field で filter)
# 第 2 引数: tmux session/pane 名 (send-keys 対象)
#
# 依存: inotifywait (apt install inotify-tools) or fallback to polling
# 停止: pkill -f "mailbox_relay.sh.*$AGENT"

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/wake.sh"

AGENT="${1:?agent name required (msg 'to' field value)}"
PANE="${2:?tmux session/pane target}"
NJSLYR_HOME="${NJSLYR_HOME:-$HOME/.njslyr7}"
MAILBOX="${MAILBOX:-$NJSLYR_HOME/mailbox/log.jsonl}"
LOG_PREFIX="[njslyr7-relay:$AGENT→$PANE]"

# track last line processed
if [[ ! -f "$MAILBOX" ]]; then
  mkdir -p "$(dirname "$MAILBOX")"
  touch "$MAILBOX"
fi
LAST=$(wc -l < "$MAILBOX")
echo "$LOG_PREFIX start, agent=$AGENT pane=$PANE mailbox=$MAILBOX last_line=$LAST"

process_new_lines() {
  local current
  current=$(wc -l < "$MAILBOX")
  if [[ $current -le $LAST ]]; then return; fi
  # read lines [LAST+1 .. current]
  sed -n "$((LAST+1)),${current}p" "$MAILBOX" | while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    # parse JSON
    local to subj from
    to=$(echo "$line" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('to',''))" 2>/dev/null)
    if [[ "$to" != "$AGENT" ]]; then continue; fi
    subj=$(echo "$line" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('subject',''))" 2>/dev/null)
    from=$(echo "$line" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('from',''))" 2>/dev/null)
    echo "$LOG_PREFIX new msg from=$from subj=$(echo "$subj" | head -c 60), injecting into $PANE"
    tmux_send_submit "$PANE" "mailbox: 新着 from $from — '$subj' (tail -1 ~/.njslyr7/mailbox/log.jsonl で内容確認して reply)"
    sleep 1  # debounce、連続 msg で burst 防ぐ
  done
  LAST=$current
}

# inotify 優先、fallback polling
if command -v inotifywait >/dev/null 2>&1; then
  echo "$LOG_PREFIX mode=inotify"
  while true; do
    inotifywait -qq -e modify -e create -e close_write "$MAILBOX" 2>/dev/null || sleep 10
    process_new_lines
  done
else
  echo "$LOG_PREFIX mode=polling (inotify-tools not installed, apt install inotify-tools 推奨)"
  while true; do
    sleep 10
    process_new_lines
  done
fi
