#!/usr/bin/env bash
# mailbox_relay.sh — mailbox write → target pane non-destructive signal
#
# mailbox append-only log の欠点 (対象 agent が読まない限り気付かない) を補う relay:
#   - inotifywait で log.jsonl の変更監視
#   - 新 line の "to" field が watch 対象 agent なら該当 tmux pane に signal
#   - signal = @formation_mail_pending + display-message (TUI prompt に keystroke しない)
#   - body inject はしない (#166: draft 連結 / silent miss)。pull が正本。
#
# 使い方 (各 pane の裏で独立 daemon として走らせる):
#
#   # main-5 pane 用 (main-5 宛の msg を main-5 tmux pane に signal):
#   nohup bash lib/mailbox_relay.sh main-5 main-5 > /tmp/relay_main5.log 2>&1 &
#
#   # qdrant-parallel pane 用:
#   nohup bash lib/mailbox_relay.sh qdrant-parallel qdrant-exp > /tmp/relay_qdrant.log 2>&1 &
#
# 第 1 引数: mailbox 上の agent 名 (msg の "to" field で filter)
# 第 2 引数: tmux session/pane 名 (signal 対象)
#
# 依存: inotifywait (apt install inotify-tools) or fallback to polling
# 停止: pkill -f "mailbox_relay.sh.*$AGENT"

set -u

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/mailbox.sh"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/mailbox_notify.sh"

AGENT="${1:?agent name required (msg 'to' field value)}"
PANE="${2:?tmux session/pane target}"
MAILBOX="${MAILBOX:-$MAILBOX_LOG}"
RELAY_READY_FILE="${FORMATION_RELAY_READY_FILE:-}"
POLL_INTERVAL="${FORMATION_RELAY_POLL_INTERVAL:-1}"
LOG_PREFIX="[formation-relay:$AGENT→$PANE]"

relay_ready_cleanup() {
  [[ -n "$RELAY_READY_FILE" && -f "$RELAY_READY_FILE" ]] || return 0
  [[ "$(cat "$RELAY_READY_FILE" 2>/dev/null || true)" == "$$" ]] &&
    rm -f "$RELAY_READY_FILE"
}
trap relay_ready_cleanup EXIT

# Track the mailbox sequence high-water, not a physical line offset. Retention
# and recovery may replace/truncate the file; seq remains monotonic in
# log.jsonl.seq and lets a live relay follow the new generation safely.
if [[ ! -f "$MAILBOX" ]]; then
  mkdir -p "$(dirname "$MAILBOX")"
  touch "$MAILBOX"
fi
LAST_SEQ="$(jq -Rsc '[splits("\n") | fromjson? | .seq? | numbers] | max // 0' "$MAILBOX")"
echo "$LOG_PREFIX start, agent=$AGENT pane=$PANE mailbox=$MAILBOX last_seq=$LAST_SEQ"
if [[ -n "$RELAY_READY_FILE" ]]; then
  mkdir -p "$(dirname "$RELAY_READY_FILE")"
  ready_tmp="${RELAY_READY_FILE}.$$"
  printf '%s\n' "$$" > "$ready_tmp"
  mv "$ready_tmp" "$RELAY_READY_FILE"
fi

process_new_lines() {
  local current_seq
  current_seq="$(jq -Rsc '[splits("\n") | fromjson? | .seq? | numbers] | max // 0' "$MAILBOX")"
  [[ "$current_seq" =~ ^[0-9]+$ ]] || current_seq=0
  if [[ "$current_seq" -lt "$LAST_SEQ" ]]; then
    echo "$LOG_PREFIX sequence generation moved backwards; re-anchor seq=$LAST_SEQ -> 0"
    LAST_SEQ=0
  fi
  if [[ "$current_seq" -le "$LAST_SEQ" ]]; then return 1; fi
  # Bound the read to this snapshot. A later append is handled by the next
  # event instead of being signaled twice.
  jq -Rrc --argjson after "$LAST_SEQ" --argjson through "$current_seq" \
    'fromjson?
     | select(((.seq? | type) == "number")
              and .seq > $after and .seq <= $through)' "$MAILBOX" |
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    local to from seq
    to=$(echo "$line" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('to',''))" 2>/dev/null) || continue
    if [[ "$to" != "$AGENT" ]]; then continue; fi
    from=$(echo "$line" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('from',''))" 2>/dev/null)
    seq=$(echo "$line" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('seq',''))" 2>/dev/null)
    echo "$LOG_PREFIX new msg seq=$seq from=$from — signal only (no prompt inject)"
    if ! mailbox_signal_pane "$PANE" "${seq:-0}" "${from:-unknown}"; then
      echo "$LOG_PREFIX WARN: msg seq=${seq:-0} remains in mailbox but pane signal failed" >&2
      continue
    fi
    sleep 1  # debounce、連続 msg で burst 防ぐ
  done
  LAST_SEQ=$current_seq
  return 0
}

# inotify 優先、fallback polling
if [[ "${FORMATION_RELAY_FORCE_POLL:-0}" != "1" ]] &&
   command -v inotifywait >/dev/null 2>&1; then
  echo "$LOG_PREFIX mode=inotify"
  while true; do
    # The timeout is a correctness backstop for the tiny startup/re-arm window:
    # even if an append occurs before the one-shot watcher is armed, seq drain
    # observes it within one second instead of waiting for a later write.
    inotifywait -qq -t 1 -e modify -e create -e close_write "$MAILBOX" 2>/dev/null || true
    # inotifywait is one-shot. An append can happen while processing/debouncing
    # the previous batch, when no watcher exists. Drain by seq until stable
    # before arming the next watcher so that event cannot be lost.
    while process_new_lines; do :; done
  done
else
  echo "$LOG_PREFIX mode=polling (inotify-tools not installed, apt install inotify-tools 推奨)"
  while true; do
    sleep "$POLL_INTERVAL"
    while process_new_lines; do :; done
  done
fi
