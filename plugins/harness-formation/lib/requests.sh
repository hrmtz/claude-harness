#!/bin/bash
# requests.sh - durable semantic ASK state for Formation (#136).
#
# This store is deliberately separate from mailbox transport. A mailbox row
# answers "was a message appended?"; a request event answers "does a parent
# decision remain unresolved?". Each event is a full snapshot so the current
# state can be recovered by taking the last valid event for each request id.

FORMATION_HOME="${FORMATION_HOME:-$HOME/.formation}"
FORMATION_REQUEST_DIR="${FORMATION_REQUEST_DIR:-$FORMATION_HOME/requests}"
FORMATION_REQUEST_LOG="${FORMATION_REQUEST_LOG:-$FORMATION_REQUEST_DIR/events.jsonl}"
FORMATION_REQUEST_LOCK="${FORMATION_REQUEST_LOG}.lock"

request_init() {
  mkdir -p "$FORMATION_REQUEST_DIR"
  touch "$FORMATION_REQUEST_LOG"
}

request_current_all() {
  request_init
  jq -Rrcs '
    def valid_request_event:
      ((.request_id? | type) == "string")
      and ((.event? | type) == "string")
      and ((.state? | type) == "string")
      and ((.closed? | type) == "boolean")
      and ((.worker_id? | type) == "string")
      and ((.parent_id? | type) == "string")
      and ((.question? | type) == "string")
      and ((.next_state? | type) == "string");
    reduce (splits("\n") | fromjson? | select(valid_request_event)) as $event
      ({}; .[$event.request_id] = $event)
    | [.[]] | sort_by(.created_at // .ts // "")
    | .[]
  ' "$FORMATION_REQUEST_LOG"
}

request_current_one() {
  local request_id="$1"
  request_current_all | jq -c --arg id "$request_id" 'select(.request_id == $id)' | tail -n1
}

request_unresolved() {
  local worker_id="${1:-}" parent_id="${2:-}"
  request_current_all | jq -c --arg worker "$worker_id" --arg parent "$parent_id" '
    select(.closed != true and .state == "WAITING_PARENT")
    | select($worker == "" or .worker_id == $worker)
    | select($parent == "" or .parent_id == $parent)
  '
}

request_create() {
  local worker_id="$1" parent_id="$2" question="$3" next_state="${4:-RUNNING}"
  request_init
  (
    flock -x 202
    local existing request_id ts event
    # An exact, still-unresolved retry is idempotent. Once the prior request is
    # closed, the same wording may legitimately create a new request.
    existing="$(jq -Rrcs --arg worker "$worker_id" --arg parent "$parent_id" \
      --arg question "$question" --arg next "$next_state" '
        def valid_request_event:
          ((.request_id? | type) == "string")
          and ((.event? | type) == "string")
          and ((.state? | type) == "string")
          and ((.closed? | type) == "boolean")
          and ((.worker_id? | type) == "string")
          and ((.parent_id? | type) == "string")
          and ((.question? | type) == "string")
          and ((.next_state? | type) == "string");
        reduce (splits("\n") | fromjson? | select(valid_request_event)) as $event
          ({}; .[$event.request_id] = $event)
        | [.[]]
        | map(select(.worker_id == $worker and .parent_id == $parent
                     and .question == $question
                     and .next_state == $next
                     and .closed != true and .state == "WAITING_PARENT"))
        | last // empty
      ' "$FORMATION_REQUEST_LOG")"
    if [[ -n "$existing" ]]; then
      printf '%s\n' "$existing" | jq -c '{request_id,created:false}'
      return 0
    fi

    request_id="req-$(python3 -c 'import uuid; print(uuid.uuid4().hex)' 2>/dev/null \
      || printf '%s-%s-%s' "$(date +%s%N)" "$$" "$RANDOM")"
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    event="$(jq -cn --arg id "$request_id" --arg ts "$ts" \
      --arg worker "$worker_id" --arg parent "$parent_id" \
      --arg question "$question" --arg next "$next_state" \
      '{request_id:$id,event:"ask",state:"WAITING_PARENT",closed:false,
        worker_id:$worker,parent_id:$parent,question:$question,
        next_state:$next,created_at:$ts,updated_at:$ts}')"
    printf '%s\n' "$event" >> "$FORMATION_REQUEST_LOG"
    jq -cn --arg id "$request_id" '{request_id:$id,created:true}'
  ) 202>"$FORMATION_REQUEST_LOCK"
}

request_transition() {
  local request_id="$1" action="$2" actor="$3" summary="$4"
  request_init
  (
    flock -x 202
    local current ts state event
    current="$(jq -Rrcs --arg id "$request_id" '
      def valid_request_event:
        ((.request_id? | type) == "string")
        and ((.event? | type) == "string")
        and ((.state? | type) == "string")
        and ((.closed? | type) == "boolean")
        and ((.worker_id? | type) == "string")
        and ((.parent_id? | type) == "string")
        and ((.question? | type) == "string")
        and ((.next_state? | type) == "string");
      reduce (splits("\n") | fromjson?
              | select(valid_request_event and .request_id == $id)) as $event
        (null; $event)
      // empty
    ' "$FORMATION_REQUEST_LOG")"
    [[ -n "$current" ]] || {
      echo "no such request: $request_id" >&2
      return 2
    }
    if [[ "$(printf '%s\n' "$current" | jq -r '.closed // false')" == "true" ]]; then
      # A repeated ACK/resolve is a safe no-op. Do not send another mailbox row.
      printf '%s\n' "$current" | jq -c '. + {_transitioned:false}'
      return 0
    fi
    state="RUNNING"
    if [[ "$action" == "resolve" ]]; then
      state="$(printf '%s\n' "$current" | jq -r '.next_state // "RUNNING"')"
    fi
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    event="$(printf '%s\n' "$current" | jq -c \
      --arg action "$action" --arg actor "$actor" --arg summary "$summary" \
      --arg state "$state" --arg ts "$ts" '
        .event=$action | .actor=$actor | .summary=$summary | .state=$state
        | .closed=true | .updated_at=$ts
      ')"
    printf '%s\n' "$event" >> "$FORMATION_REQUEST_LOG"
    printf '%s\n' "$event" | jq -c '. + {_transitioned:true}'
  ) 202>"$FORMATION_REQUEST_LOCK"
}
