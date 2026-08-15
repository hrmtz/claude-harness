#!/usr/bin/env bash
# Durable review request/verdict tracking (#221).

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
FIXTURE="$(mktemp -d)"
cleanup() { rm -rf "$FIXTURE"; }
trap cleanup EXIT

export FORMATION_HOME="$FIXTURE/formation-home"
export FORMATION_MAILBOX="$FORMATION_HOME/mailbox/log.jsonl"
export FORMATION_REVIEW_DIR="$FORMATION_HOME/reviews"
export FORMATION_REVIEW_LOG="$FORMATION_REVIEW_DIR/events.jsonl"
export FORMATION_SELF=requester-a
export FORMATION_PARENT=manager-a
export FORMATION_PARENT_PANE=%100
unset TMUX_PANE

TMUX_LOG="$FIXTURE/tmux.log"
: >"$TMUX_LOG"
tmux() {
  printf 'tmux %s\n' "$*" >>"$TMUX_LOG"
  case "${1:-}" in
    display-message) printf '%%101\n' ;;
    show-options|set-option|capture-pane|kill-pane) return 0 ;;
  esac
}

# shellcheck source=/dev/null
source "$HERE/../bin/formation"

self_id() { printf '%s\n' "$FORMATION_SELF"; }
mailbox_resolve_caller_pane() { printf '%%101\n'; }

registry_add reviewer-a %200 formation-reviewer-a \
  "$FIXTURE/review.md" sid kimi review goal 0

# Credential-shaped subjects are rejected before state or transport mutation.
set +e
cmd_review_request reviewer-a 'api_key=not-a-real-value' \
  >"$FIXTURE/credential-request.out" 2>&1
credential_request_rc=$?
set -e
[[ "$credential_request_rc" -eq 3 ]]
[[ ! -s "$FORMATION_REVIEW_LOG" ]]

review_id="$(cmd_review_request reviewer-a 'Review PR head abc123')"
[[ "$review_id" == review-* ]]
[[ "$review_id" != *signal=* ]]
review_current_one "$review_id" | jq -e '
  .state == "WAITING_REVIEW"
  and .closed == false
  and .reviewer_id == "reviewer-a"
  and .requester_id == "requester-a"
  and .manager_id == "manager-a"
' >/dev/null

jq -Rsc --arg id "$review_id" '
  ([splits("\n") | fromjson?
    | select(.to == "reviewer-a"
             and (.body | startswith("[REVIEW request_id=" + $id + "]"))) ]
   | length) == 1
  and
  ([splits("\n") | fromjson?
    | select(.to == "manager-a"
             and (.body | startswith("[REVIEW-COPY request_id=" + $id + "]"))) ]
   | length) == 1
' "$MAILBOX_LOG" | grep -qx true

# Exact open retry is one semantic request and one transport row per recipient.
[[ "$(cmd_review_request reviewer-a 'Review PR head abc123')" == "$review_id" ]]
[[ "$(wc -l <"$FORMATION_REVIEW_LOG")" -eq 1 ]]
[[ "$(wc -l <"$MAILBOX_LOG")" -eq 2 ]]

# Unresolved output is a stable watcher surface.
cmd_reviews --json | jq -e \
  --arg id "$review_id" 'length == 1 and .[0].request_id == $id and .[0].age_seconds >= 0' \
  >/dev/null
cmd_reviews --stale-minutes 999999 | grep -qx 'no unresolved reviews'

# A type-valid but unparsable timestamp cannot hide healthy requests or fail
# open as "no unresolved reviews"; unknown age is conservatively stale.
jq -cn '{
  request_id:"review-corrupt\u001b-time",event:"request",state:"WAITING_REVIEW",
  closed:false,reviewer_id:"reviewer-b\nspoof",requester_id:"requester-b\u0007",
  manager_id:"manager-b\rspoof",subject:"Timestamp fixture\u001b[31m",
  created_at:"not-a-date",updated_at:"not-a-date"
}' >>"$FORMATION_REVIEW_LOG"
printf '%s\n' '{malformed review row' >>"$FORMATION_REVIEW_LOG"
cmd_reviews --stale-minutes 999999 --json | jq -e '
  length == 1
  and .[0].request_id == "review-corrupt\u001b-time"
  and .[0].age_seconds == null
' >/dev/null
if cmd_reviews | grep -q $'\033'; then
  echo "FAIL: review listing emitted a raw terminal control" >&2
  exit 1
fi

# Only the assigned reviewer may close.
FORMATION_SELF=intruder
set +e
cmd_verdict "$review_id" PASS nope >"$FIXTURE/intruder.out" 2>&1
rc=$?
set -e
[[ "$rc" -eq 5 ]]
review_current_one "$review_id" | jq -e '.closed == false' >/dev/null

FORMATION_SELF=reviewer-a
set +e
cmd_verdict "$review_id" PASS 'api_key=not-a-real-value' \
  >"$FIXTURE/credential-verdict.out" 2>&1
credential_verdict_rc=$?
set -e
[[ "$credential_verdict_rc" -eq 3 ]]
review_current_one "$review_id" | jq -e '.closed == false' >/dev/null

cmd_verdict "$review_id" PASS 'No actionable findings' |
  grep -Fq "review=$review_id verdict=PASS requester_notified=true manager_notified=true transitioned=true"
review_current_one "$review_id" | jq -e '
  .closed == true and .state == "REVIEWED"
  and .event == "verdict" and .verdict == "PASS"
' >/dev/null

jq -Rsc --arg id "$review_id" '
  ([splits("\n") | fromjson?
    | select(.to == "requester-a"
             and (.body | startswith("[VERDICT request_id=" + $id + " verdict=PASS]"))) ]
   | length) == 1
  and
  ([splits("\n") | fromjson?
    | select(.to == "manager-a"
             and (.body | startswith("[VERDICT-COPY request_id=" + $id + " verdict=PASS]"))) ]
   | length) == 1
' "$MAILBOX_LOG" | grep -qx true

# Retry is idempotent. A conflicting retry cannot rewrite state or notifications.
cmd_verdict "$review_id" BLOCK 'Conflicting retry' |
  grep -Fq "verdict=PASS requester_notified=false manager_notified=false transitioned=false"
[[ "$(wc -l <"$FORMATION_REVIEW_LOG")" -eq 4 ]]
[[ "$(wc -l <"$MAILBOX_LOG")" -eq 4 ]]
if grep -Fq 'Conflicting retry' "$MAILBOX_LOG"; then
  echo "FAIL: conflicting retry escaped persisted verdict state" >&2
  exit 1
fi
cmd_reviews --json | jq -e '
  length == 1 and .[0].request_id == "review-corrupt\u001b-time"
' >/dev/null

# A reviewer cannot be reaped while its assigned review has no terminal
# verdict. --force remains an explicit escape hatch and preserves durable state.
FORMATION_SELF=requester-a
pending_review_id="$(cmd_review_request reviewer-a 'Review PR head def456')"
set +e
cmd_reap reviewer-a >"$FIXTURE/reap-reviewer.out" 2>&1
reap_rc=$?
set -e
[[ "$reap_rc" -eq 6 ]]
grep -Fq "$pending_review_id" "$FIXTURE/reap-reviewer.out"
cmd_reap --force reviewer-a >"$FIXTURE/reap-reviewer-force.out" 2>&1
grep -Fq 'unresolved review state preserved' "$FIXTURE/reap-reviewer-force.out"
review_current_one "$pending_review_id" |
  jq -e '.closed == false and .state == "WAITING_REVIEW"' >/dev/null

echo "PASS: durable review request/verdict tracking"
