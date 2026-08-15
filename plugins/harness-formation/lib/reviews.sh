#!/bin/bash
# reviews.sh - durable review request/verdict tracking (#221).

FORMATION_HOME="${FORMATION_HOME:-$HOME/.formation}"
FORMATION_REVIEW_DIR="${FORMATION_REVIEW_DIR:-$FORMATION_HOME/reviews}"
FORMATION_REVIEW_LOG="${FORMATION_REVIEW_LOG:-$FORMATION_REVIEW_DIR/events.jsonl}"
FORMATION_REVIEW_LOCK="${FORMATION_REVIEW_LOG}.lock"

review_init() {
  mkdir -p "$FORMATION_REVIEW_DIR"
  touch "$FORMATION_REVIEW_LOG" "$FORMATION_REVIEW_LOCK"
}

_review_snapshot_unlocked() {
  review_init
  jq -Rrcs '
    def valid_review_event:
      ((.request_id? | type) == "string")
      and ((.event? | type) == "string")
      and ((.state? | type) == "string")
      and ((.closed? | type) == "boolean")
      and ((.reviewer_id? | type) == "string")
      and ((.requester_id? | type) == "string")
      and ((.manager_id? | type) == "string")
      and ((.subject? | type) == "string")
      and ((.created_at? | type) == "string")
      and ((.updated_at? | type) == "string");
    reduce (splits("\n") | fromjson? | select(valid_review_event)) as $event
      ({}; .[$event.request_id] = $event)
    | [.[]] | sort_by(.created_at // .updated_at // "")
  ' "$FORMATION_REVIEW_LOG"
}

review_snapshot() {
  review_init
  (
    flock -s 205
    _review_snapshot_unlocked
  ) 205<"$FORMATION_REVIEW_LOCK"
}

review_current_all() {
  review_snapshot | jq -c '.[]'
}

review_current_one() {
  local request_id="$1"
  review_current_all |
    jq -c --arg id "$request_id" 'select(.request_id == $id)' |
    tail -n1
}

review_unresolved() {
  review_current_all | jq -c 'select(.closed != true and .state == "WAITING_REVIEW")'
}

review_create() {
  local reviewer_id="$1" requester_id="$2" manager_id="$3"
  local requester_pane="$4" manager_pane="$5" subject="$6"
  review_init
  (
    flock -x 205
    local existing request_id ts event
    existing="$(_review_snapshot_unlocked | jq -c \
      --arg reviewer "$reviewer_id" --arg requester "$requester_id" \
      --arg manager "$manager_id" --arg subject "$subject" '
      map(select(
        .reviewer_id == $reviewer
        and .requester_id == $requester
        and .manager_id == $manager
        and .subject == $subject
        and .closed != true
        and .state == "WAITING_REVIEW"
      )) | last // empty
    ')"
    if [[ -n "$existing" ]]; then
      printf '%s\n' "$existing" | jq -c '{request_id,created:false}'
      return 0
    fi

    request_id="review-$(python3 -c 'import uuid; print(uuid.uuid4().hex)' 2>/dev/null \
      || printf '%s-%s-%s' "$(date +%s%N)" "$$" "$RANDOM")"
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    event="$(jq -cn \
      --arg id "$request_id" --arg ts "$ts" \
      --arg reviewer "$reviewer_id" --arg requester "$requester_id" \
      --arg manager "$manager_id" --arg requester_pane "$requester_pane" \
      --arg manager_pane "$manager_pane" --arg subject "$subject" '
      {
        request_id:$id,event:"request",state:"WAITING_REVIEW",closed:false,
        reviewer_id:$reviewer,requester_id:$requester,manager_id:$manager,
        requester_pane:(if $requester_pane == "" then null else $requester_pane end),
        manager_pane:(if $manager_pane == "" then null else $manager_pane end),
        subject:$subject,created_at:$ts,updated_at:$ts
      }
    ')"
    printf '%s\n' "$event" >>"$FORMATION_REVIEW_LOG"
    jq -cn --arg id "$request_id" '{request_id:$id,created:true}'
  ) 205>"$FORMATION_REVIEW_LOCK"
}

review_close() {
  local request_id="$1" actor="$2" verdict="$3" summary="$4"
  review_init
  (
    flock -x 205
    local current ts event
    current="$(_review_snapshot_unlocked | jq -c --arg id "$request_id" '
      map(select(.request_id == $id)) | last // empty
    ')"
    [[ -n "$current" ]] || {
      echo "no such review request: $request_id" >&2
      return 2
    }
    [[ "$(printf '%s\n' "$current" | jq -r '.reviewer_id')" == "$actor" ]] || {
      echo "REFUSED: review $request_id belongs to reviewer $(printf '%s\n' "$current" | jq -r '.reviewer_id'), not $actor" >&2
      return 5
    }
    if [[ "$(printf '%s\n' "$current" | jq -r '.closed // false')" == "true" ]]; then
      printf '%s\n' "$current" | jq -c '. + {_transitioned:false}'
      return 0
    fi
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    event="$(printf '%s\n' "$current" | jq -c \
      --arg actor "$actor" --arg verdict "$verdict" \
      --arg summary "$summary" --arg ts "$ts" '
      .event="verdict"
      | .state="REVIEWED"
      | .closed=true
      | .actor=$actor
      | .verdict=$verdict
      | .summary=$summary
      | .updated_at=$ts
    ')"
    printf '%s\n' "$event" >>"$FORMATION_REVIEW_LOG"
    printf '%s\n' "$event" | jq -c '. + {_transitioned:true}'
  ) 205>"$FORMATION_REVIEW_LOCK"
}
