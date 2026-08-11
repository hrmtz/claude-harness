#!/usr/bin/env bash
# mailbox_unread_stop_gate.sh — Stop hook: 未読 Formation mail を持ったまま
# turn を終えようとしたら 1 回だけ block して inbox を読ませる。
#
# gh #273 の実事故 (worker の DONE が durable に届いていたのに、親 orchestrator
# が turn を終えて idle 化し、user が手動で起こすまで未読のままだった) への
# structural rail。badge/signal は「配達」であって「受領」ではない — この gate
# が turn 終了という最後の境界で受領を強制する。
#
# SAFETY (Stop hook の誤 block は session を loop に閉じ込める):
#   - stop_hook_active == true → ALLOW (harness loop guard。block 後の再 Stop
#     では新着があっても再 block しない — 保守側に倒す)。
#   - 同じ max_seq では at-most-once: block 記録を emit 前に書く (crash しても
#     再 block しない、mail-nudge の attempted-first と同じ規律)。
#   - formation CLI 不在 / identity 未解決 / parse 失敗 → 全て無言 fail-open。
#   - kill switch: FORMATION_MAILBOX_STOP_GATE_DISABLE=1

set -uo pipefail

[[ "${FORMATION_MAILBOX_STOP_GATE_DISABLE:-0}" == "1" ]] && exit 0

payload=$(head -c 65536 2>/dev/null || true)

stop_active="$(printf '%s' "$payload" |
  jq -r '.stop_hook_active // false' 2>/dev/null || true)"
[[ "$stop_active" == "true" ]] && exit 0

formation_bin="$(command -v formation 2>/dev/null || true)"
if [[ -z "$formation_bin" && -n "${HOME:-}" &&
      -x "${HOME}/.local/bin/formation" ]]; then
  formation_bin="${HOME}/.local/bin/formation"
fi
[[ -n "$formation_bin" ]] || exit 0

count_line="$(timeout 3 "$formation_bin" inbox --count 2>/dev/null)" || exit 0
read -r count max_seq oldest_ts extra <<<"$count_line"
[[ -z "${extra:-}" &&
   "${count:-}" =~ ^[0-9]+$ &&
   "${max_seq:-}" =~ ^[0-9]+$ &&
   -n "${oldest_ts:-}" ]] || exit 0
[[ "$count" -gt 0 ]] || exit 0

session="$(printf '%s' "$payload" |
  jq -r '.session_id // empty' 2>/dev/null || true)"
[[ -n "$session" ]] || session="ppid-$PPID"
session="${session//[^a-zA-Z0-9_-]/_}"

state_dir="${XDG_RUNTIME_DIR:-/tmp}/claude-mailbox-stopgate"
state_file="$state_dir/$session"
umask 077
mkdir -p "$state_dir" 2>/dev/null || exit 0

last_blocked=0
if [[ -f "$state_file" ]]; then
  read -r last_blocked _ <"$state_file" 2>/dev/null || true
fi
[[ "$last_blocked" =~ ^[0-9]+$ ]] || last_blocked=0
[[ "$max_seq" -gt "$last_blocked" ]] || exit 0

# emit より先に記録: crash / 出力破損でも同じ seq で二度 block しない。
state_tmp="${state_file}.$$"
printf '%s\n' "$max_seq" >"$state_tmp" 2>/dev/null || exit 0
mv "$state_tmp" "$state_file" 2>/dev/null || {
  rm -f "$state_tmp" 2>/dev/null || true
  exit 0
}

reason="📬 formation inbox ${count} 件未読 (最古 ${oldest_ts}) のまま turn を終了しようとしている — \`formation inbox\` で読み、必要な対応・返信をしてから終えること。対応不要と判断したらその根拠を報告してよい。この gate は同じ mail では再 block しない (gh claude-harness#273)"
jq -n --arg reason "$reason" \
  '{decision: "block", reason: $reason}' 2>/dev/null || exit 0

exit 0
