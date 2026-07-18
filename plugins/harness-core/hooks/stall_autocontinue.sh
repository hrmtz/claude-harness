#!/bin/bash
# stall_autocontinue.sh — Stop hook: recover from a stalled turn caused by a
# MALFORMED tool call (the model emitted broken tool-call syntax, so nothing ran
# and the turn ended; the session then idles waiting for the user).
#
# This does NOT fix the malformation (that's model output-side). It only revives
# the specific stall: if the last assistant turn left a malformed-call signature
# in its TEXT (an `<invoke name=` / `<parameter name=` literal that never parsed
# into a tool_use), nudge the model to re-issue it correctly and continue.
#
# SAFETY (a Stop hook that wrongly BLOCKS traps the session in a loop):
#   - stop_hook_active == true       → ALLOW stop (harness loop guard).
#   - bounded retries per session    → after MAX auto-continues, ALLOW stop.
#   - only block on a SPECIFIC signal (malformed-call text + NO tool_use in the
#     same turn). Normal completions and successful tool calls are never touched.
#   - FAIL-OPEN: any parse error / missing field / uncertainty → ALLOW stop.
#     Worst case this hook does nothing; it can never trap the session.
#
# UX (gh #23 — the give-up path used to be SILENT, indistinguishable from "not
# covered"). Fixes:
#   1. Visible give-up: when the retry budget is exhausted we emit a
#      `systemMessage` (still ALLOW stop) so the user knows the hook detected the
#      stall but stood down for manual intervention.
#   2. Time-decaying budget: a budget burned during one malformed burst must not
#      permanently lock out a later burst in the same long session. The counter
#      resets after DECAY_SECS of no stall, and MAX_RETRIES is roomier (5).
#   3. Mixed-turn notice: if a turn made progress (real tool_use) yet ALSO left
#      an unparsed malformed literal in its text, we don't block (not a stall)
#      but surface a `systemMessage` so the dropped call isn't silently lost.

set -u
MAX_RETRIES=5
DECAY_SECS=180          # reset the budget after this long with no stall
STATE_DIR="$HOME/.claude/state/stall_autocontinue"
mkdir -p "$STATE_DIR" 2>/dev/null

allow() { exit 0; }     # allow the stop (default / fail-open)

# Surface a message to the user but DO NOT block (no `decision` field → stop
# proceeds). Fail-open if jq can't render the JSON.
notify_allow() {
    jq -n --arg m "$1" '{systemMessage: $m}' 2>/dev/null || true
    exit 0
}

INPUT=$(cat 2>/dev/null) || allow
command -v jq >/dev/null 2>&1 || allow

# harness's own loop guard: we're already inside a hook-driven continuation → stop.
STOP_ACTIVE=$(printf '%s' "$INPUT" | jq -r '.stop_hook_active // false' 2>/dev/null)
[ "$STOP_ACTIVE" = "true" ] && allow

SID=$(printf '%s' "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null)
CNT_FILE="$STATE_DIR/$SID"

# --- Signals in that turn -------------------------------------------------
# Codex provides the stable last_assistant_message field. Its rollout JSONL is
# explicitly not a stable hook interface, so do not parse it here. Claude does
# not provide that field and keeps the existing transcript parser.
if printf '%s' "$INPUT" | jq -e 'has("turn_id") and has("last_assistant_message")' >/dev/null 2>&1; then
    TXT=$(printf '%s' "$INPUT" | jq -r '.last_assistant_message // empty' 2>/dev/null)
    HAS_TOOLUSE=false
else
    TP=$(printf '%s' "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null)
    [ -n "$TP" ] && [ -f "$TP" ] || allow
    LAST=$(tac "$TP" 2>/dev/null | grep -m1 '"type":"assistant"') || allow
    [ -n "$LAST" ] || allow
    HAS_TOOLUSE=$(printf '%s' "$LAST" | jq -r '[.message.content[]?.type] | any(. == "tool_use")' 2>/dev/null)
    TXT=$(printf '%s' "$LAST" | jq -r '.message.content[]?.text // empty' 2>/dev/null)
fi

# Malformed-call signature in the assistant text (an `<invoke ...>` /
# `<parameter ...>` literal that never parsed). Never appears in normal prose.
HAS_MALFORMED=false
if printf '%s' "$TXT" | grep -qE '<(invoke|parameter|function_calls) name=|<(invoke|parameter|function_calls)>'; then
    HAS_MALFORMED=true
fi

# Clean turn (no malformed signature at all) → not a stall. Reset budget, allow.
if [ "$HAS_MALFORMED" != "true" ]; then
    rm -f "$CNT_FILE" 2>/dev/null
    allow
fi

# Mixed turn: a real tool_use ran AND a malformed literal remained in the text.
# The turn made progress, so this is NOT a pure stall — never block. But the
# leftover literal means an intended call was likely dropped; flag it. (gh #23)
if [ "$HAS_TOOLUSE" = "true" ]; then
    notify_allow "直前のターンは tool 呼び出しに成功しましたが、テキスト中に未 parse の malformed tool-call 構文（<invoke ...> 等のリテラル）が残っています。意図した呼び出しが取りこぼされている可能性があるので、確認のうえ必要なら正しい構文で再発行してください。"
fi

# --- Pure stall: malformed text, no tool_use. Bounded, decaying budget. ---
NOW=$(date +%s 2>/dev/null || echo 0)
CNT=0; TS=0
if [ -f "$CNT_FILE" ]; then
    read -r CNT TS < "$CNT_FILE" 2>/dev/null
fi
case "$CNT" in ''|*[!0-9]*) CNT=0 ;; esac
case "$TS"  in ''|*[!0-9]*) TS=0  ;; esac

# Decay: a budget exhausted long ago must not lock out a fresh burst later.
if [ "$NOW" -gt 0 ] && [ "$TS" -gt 0 ] && [ $((NOW - TS)) -gt "$DECAY_SECS" ]; then
    CNT=0
fi

if [ "$CNT" -ge "$MAX_RETRIES" ]; then
    # Give up so the user can step in — but VISIBLY (gh #23). Reset so a later
    # post-decay burst re-engages instead of staying silently disarmed.
    rm -f "$CNT_FILE" 2>/dev/null
    notify_allow "stall_autocontinue: malformed tool-call による stall を検知しましたが、連続 ${MAX_RETRIES} 回の自動再開上限に達したため stand down します（無限ループ防止）。手動で「続けて」等と促してください（しばらく置けば自動で再武装します）。"
fi

echo "$((CNT + 1)) $NOW" > "$CNT_FILE" 2>/dev/null

# issue #24: telemetry — record each malformed-call stall to back out the inducing
# conditions (long preceding prose was the suspected trigger). txt_len is a context-length
# proxy. Best-effort / fail-open: never affects the block decision.
{ printf '%s pure-stall txt_len=%s retry=%s sid=%s\n' "$NOW" "${#TXT}" "$((CNT + 1))" "$SID" \
    >> "$STATE_DIR/telemetry.log"; } 2>/dev/null || true

# Block the stop and tell the model what went wrong.
jq -n '{
  decision: "block",
  reason: "あなたの直前のターンは tool 呼び出しの構文が壊れて parse されず、何も実行されないまま終了しました（stall）。正しい tool-call 構文で直前の呼び出しを再発行し、作業を続行してください。本当に作業が完了している場合のみ、tool を使わず完了報告で終えてください。"
}' 2>/dev/null || allow
exit 0
