#!/usr/bin/env bash
# An ordinary named CLI pane can receive mail without a Formation registry row.
set -euo pipefail
trap 'echo "FAIL: test_unregistered_mailbox line $LINENO" >&2' ERR
HERE="$(cd "$(dirname "$0")" && pwd)"
FIXTURE="$(mktemp -d)"
SOCKET="formation-unregistered-$$"
cleanup() {
  tmux -L "$SOCKET" kill-server >/dev/null 2>&1 || true
  rm -r "$FIXTURE"
}
trap cleanup EXIT
export FORMATION_HOME="$FIXTURE/home"
export FORMATION_MAILBOX="$FORMATION_HOME/mailbox/log.jsonl"
export FORMATION_REVIEW_DIR="$FORMATION_HOME/reviews"
export FORMATION_REVIEW_LOG="$FORMATION_REVIEW_DIR/events.jsonl"
export FORMATION_SELF=fixture-sender
unset FORMATION_PARENT FORMATION_PARENT_PANE FORMATION_REGISTRY

tmux -L "$SOCKET" -f /dev/null new-session -d -s fixture 'sleep 120'
export TMUX="$(tmux -L "$SOCKET" display-message -p '#{socket_path},#{pid},0')"
export TMUX_PANE=""
pane="$(tmux list-panes -t fixture -F '#{pane_id}')"
tmux set-option -p -t "$pane" @formation_identity_locked ordinary-agent
tmux set-option -p -t "$pane" @formation_exclusive_input 1
before="$(tmux capture-pane -p -t "$pane")"
formation() { bash "$HERE/../bin/formation" "$@"; }
send() { bash "$HERE/../bin/mailbox-send" "$@"; }

formation msg ordinary-agent 'hello without registration' >"$FIXTURE/msg"
rg -q 'signal=sent-directly' "$FIXTURE/msg"
send ordinary-agent 'second entrypoint' >"$FIXTURE/send"
[[ "$(tmux show-options -pqv -t "$pane" @formation_mail_pending)" == 2 ]]
[[ "$(tmux capture-pane -p -t "$pane")" == "$before" ]]
[[ ! -s "$FORMATION_HOME/formation/registry.jsonl" ]]
jq -se 'length == 2 and all(.to == "ordinary-agent")' "$FORMATION_MAILBOX" >/dev/null
FORMATION_SELF=ordinary-agent formation inbox >"$FIXTURE/inbox"
rg -q 'hello without registration' "$FIXTURE/inbox"
rg -q 'second entrypoint' "$FIXTURE/inbox"
FORMATION_SELF=ordinary-agent formation inbox | rg -q '^\(empty\)$'

# Read from the real recipient process without supplying FORMATION_SELF: the
# ancestry-verified pane's startup identity must resolve the same mailbox.
cat >"$FIXTURE/pull.sh" <<'SH'
while [[ ! -f "$PULL_READY" ]]; do sleep 0.05; done
unset FORMATION_SELF
bash "$PULL_CLI" inbox --history >"$PULL_OUTPUT"
sleep 120
SH
tmux respawn-pane -k -t "$pane" \
  -e "PULL_READY=$FIXTURE/ready" -e "PULL_CLI=$HERE/../bin/formation" \
  -e "PULL_OUTPUT=$FIXTURE/pane-inbox" \
  -e "FORMATION_HOME=$FORMATION_HOME" -e "FORMATION_MAILBOX=$FORMATION_MAILBOX" \
  "bash '$FIXTURE/pull.sh'"
touch "$FIXTURE/ready"
for _ in {1..80}; do
  if [[ -f "$FIXTURE/pane-inbox" ]] && rg -q 'second entrypoint' "$FIXTURE/pane-inbox"; then break; fi
  sleep 0.05
done
rg -q 'hello without registration' "$FIXTURE/pane-inbox"
before="$(tmux capture-pane -p -t "$pane")"

# Shared resolver callers must carry the discovered pane through to signaling.
formation review-request ordinary-agent 'review fixture' >"$FIXTURE/review"
[[ "$(tmux show-options -pqv -t "$pane" @formation_mail_pending)" == 3 ]]

# A live option alone cannot grant the registry+pane injection authority.
if send ordinary-agent 'no implicit exclusive input' --inject >"$FIXTURE/inject" 2>&1; then
  exit 1
else
  [[ "$?" == 5 ]]
fi
[[ "$(tmux capture-pane -p -t "$pane")" == "$before" ]]

# Duplicate locked identities are refused before append; display names alone
# cannot address an unrelated pane. Dead retained panes are not candidates.
other="$(tmux new-window -d -P -F '#{pane_id}' -t fixture 'sleep 120')"
tmux set-option -p -t "$other" @formation_identity_locked ordinary-agent
count="$(wc -l <"$FORMATION_MAILBOX")"
if formation msg ordinary-agent ambiguous >"$FIXTURE/duplicate" 2>&1; then exit 1; fi
rg -q 'ambiguous live identity' "$FIXTURE/duplicate"
[[ "$(wc -l <"$FORMATION_MAILBOX")" == "$count" ]]
tmux set-option -p -u -t "$other" @formation_identity_locked
tmux rename-window -t "$other" codex-display-only
if send display-only ignored >"$FIXTURE/display" 2>&1; then exit 1; fi
dead="$(tmux new-window -d -P -F '#{pane_id}' -t fixture 'sleep 120')"
tmux set-option -w -t "$dead" remain-on-exit on
tmux set-option -p -t "$dead" @formation_identity_locked dead-agent
tmux respawn-pane -k -t "$dead" 'exit 0'
for _ in {1..40}; do
  [[ "$(tmux display-message -p -t "$dead" '#{pane_dead}')" == 1 ]] && break
  sleep 0.05
done
if send dead-agent ignored >"$FIXTURE/dead" 2>&1; then exit 1; fi
[[ "$(wc -l <"$FORMATION_MAILBOX")" == "$count" ]]

# Registered routes remain authoritative; --no-nudge never needs live lookup.
jq -cn --arg pane "$other" '{id:"ordinary-agent",pane_id:$pane}' \
  >"$FORMATION_HOME/formation/registry.jsonl"
formation msg ordinary-agent 'registered route wins' >"$FIXTURE/registered"
rg -Fq "pane=$other" "$FIXTURE/registered"
source "$HERE/../lib/mailbox_delivery.sh"
tmux() { echo unexpected-tmux >>"$FIXTURE/unexpected"; return 1; }
mailbox_resolve_recipient ordinary-agent "$FORMATION_HOME/formation/registry.jsonl" 1 0
[[ "$MAILBOX_RECIPIENT_PANE" == "$other" ]]
mailbox_resolve_recipient %99 /dev/null 1 0
if mailbox_resolve_recipient missing /dev/null 1 0; then exit 1; fi
[[ ! -e "$FIXTURE/unexpected" ]]
unset -f tmux
echo 'test_unregistered_mailbox: PASS'
