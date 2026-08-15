#!/usr/bin/env bash
# Real-pane acceptance for `formation register` (#235).

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$(realpath "$HERE/../bin/formation")"
REAL_TMUX="$(command -v tmux)"
FIXTURE="$(mktemp -d)"
SOCKET="formation-register-$$"

cleanup() {
  tmux -L "$SOCKET" kill-server >/dev/null 2>&1 || true
  rm -rf "$FIXTURE"
}
trap cleanup EXIT

mkdir -p "$FIXTURE/home"
[[ ! -e "$FIXTURE/home/formation/registry.jsonl" ]]

tmux -L "$SOCKET" -f /dev/null new-session -d -s register-real -n parent \
  'bash --noprofile --norc'
parent_pane="$(tmux -L "$SOCKET" display-message -p -t register-real:parent '#{pane_id}')"
child_pane="$(tmux -L "$SOCKET" split-window -d -P -F '#{pane_id}' \
  -t "$parent_pane" 'bash --noprofile --norc')"
sleep 0.2

run_register() {
  local pane="$1" label="$2" env_prefix="$3" args="$4"
  local stdout="$FIXTURE/$label.out"
  local stderr="$FIXTURE/$label.err"
  local rcfile="$FIXTURE/$label.rc"
  local command
  rm -f "$stdout" "$stderr" "$rcfile"
  printf -v command \
    '%s FORMATION_HOME=%q %q register %s >%q 2>%q; printf "%%s" "$?" >%q' \
    "$env_prefix" "$FIXTURE/home" "$BIN" "$args" "$stdout" "$stderr" "$rcfile"
  tmux -L "$SOCKET" send-keys -t "$pane" "$command"
  sleep 0.1
  tmux -L "$SOCKET" send-keys -t "$pane" C-m
  for _ in $(seq 1 100); do
    [[ -s "$rcfile" ]] && break
    sleep 0.05
  done
  [[ -s "$rcfile" ]] || {
    echo "registration timed out: $label" >&2
    tmux -L "$SOCKET" capture-pane -p -S -30 -t "$pane" >&2 || true
    return 99
  }
  cat "$rcfile"
}

# A pre-existing exclusive bit must be removed, not copied into the registry.
tmux -L "$SOCKET" set-option -p -t "$parent_pane" @formation_exclusive_input 1
rc="$(run_register "$parent_pane" parent "" "--cli claude --task lead lead")"
[[ "$rc" == "0" ]]

registry="$FIXTURE/home/formation/registry.jsonl"
jq -e --arg pane "$parent_pane" '
  .id == "lead"
  and .pane_id == $pane
  and .cli == "claude"
  and .task == "lead"
  and .briefing == ""
  and .exclusive_input == false
  and .parent_id == null
  and .parent_route_state == "UNROUTABLE"
  and .relay_state == "DEAD"
  and .relay_reason == "manual-registration-no-relay"
' "$registry" >/dev/null
[[ "$(tmux -L "$SOCKET" show-options -p -q -v -t "$parent_pane" \
  @formation_identity_locked)" == "lead" ]]
if [[ -n "$(tmux -L "$SOCKET" show-options -p -q -v -t "$parent_pane" \
    @formation_exclusive_input 2>/dev/null || true)" ]]; then
  echo "exclusive-input option survived manual registration" >&2
  exit 1
fi

# The inherited pane id and ancestry proof must agree. Pointing at a live
# sibling pane fails before either registry or pane options are mutated.
rc="$(run_register "$child_pane" stale-pane \
  "TMUX_PANE=$parent_pane" "--cli codex wrong-target")"
[[ "$rc" == "2" ]]
grep -Fq 'disagrees with ancestry/TTY' "$FIXTURE/stale-pane.err"
[[ "$(wc -l < "$registry" | tr -d ' ')" == "1" ]]
[[ -z "$(tmux -L "$SOCKET" show-options -p -q -v -t "$child_pane" \
  @formation_identity_locked 2>/dev/null || true)" ]]

# Inherited identity mismatch and exclusive-input opt-in are fail-closed before
# any registry or pane mutation.
rc="$(run_register "$child_pane" self-mismatch \
  "FORMATION_SELF=someone-else" "--cli codex child")"
[[ "$rc" == "2" ]]
grep -Fq 'FORMATION_SELF=someone-else does not match' \
  "$FIXTURE/self-mismatch.err"
rc="$(run_register "$child_pane" exclusive-refusal \
  "" "--cli codex --exclusive-input child")"
[[ "$rc" == "2" ]]
grep -Fq 'register refuses --exclusive-input' "$FIXTURE/exclusive-refusal.err"
[[ "$(wc -l < "$registry" | tr -d ' ')" == "1" ]]
[[ -z "$(tmux -L "$SOCKET" show-options -p -q -v -t "$child_pane" \
  @formation_identity_locked 2>/dev/null || true)" ]]

# Credential-shaped task/goal metadata is refused before pane or registry
# mutation. The refusal log stores metadata only, never the rejected value.
rc="$(run_register "$child_pane" credential \
  "" "--cli codex --goal token=fixture child")"
[[ "$rc" == "3" ]]
grep -Fq 'body matches credential pattern' "$FIXTURE/credential.err"
[[ "$(wc -l < "$registry" | tr -d ' ')" == "1" ]]
[[ -z "$(tmux -L "$SOCKET" show-options -p -q -v -t "$child_pane" \
  @formation_identity_locked 2>/dev/null || true)" ]]
grep -Fq $'\tregister\tfrom=child\ttask/goal metadata refused' \
  "$FIXTURE/home/mailbox/refuse.log"
if grep -Fq 'token=fixture' "$FIXTURE/home/mailbox/refuse.log"; then
  echo "credential-shaped registration metadata leaked to refusal log" >&2
  exit 1
fi

# Normalization must happen before credential detection. Deleting the embedded
# newline joins these individually harmless fragments into one GitHub-token shape.
rc="$(run_register "$child_pane" normalized-credential \
  "" "--cli codex --task \$'ghp_1234567890\\n12345678901234567890' child")"
[[ "$rc" == "3" ]]
grep -Fq 'body matches credential pattern' "$FIXTURE/normalized-credential.err"
[[ "$(wc -l < "$registry" | tr -d ' ')" == "1" ]]
[[ -z "$(tmux -L "$SOCKET" show-options -p -q -v -t "$child_pane" \
  @formation_identity_locked 2>/dev/null || true)" ]]
if grep -Fq 'ghp_' "$FIXTURE/home/mailbox/refuse.log"; then
  echo "normalized credential leaked to refusal log" >&2
  exit 1
fi

# A half-known reverse route is refused; it must never create the half-open
# channel documented in #235.
rc="$(run_register "$child_pane" missing-parent \
  "FORMATION_PARENT=missing FORMATION_PARENT_PANE=%99" \
  "--cli codex child")"
[[ "$rc" == "2" ]]
grep -Fq 'parent route is absent or disagrees with registry' \
  "$FIXTURE/missing-parent.err"
[[ "$(wc -l < "$registry" | tr -d ' ')" == "1" ]]

# A matching parent row is insufficient when the live locked identity differs.
tmux -L "$SOCKET" set-option -p -t "$parent_pane" \
  @formation_identity_locked impostor
rc="$(run_register "$child_pane" parent-identity-mismatch \
  "FORMATION_PARENT=lead FORMATION_PARENT_PANE=$parent_pane" \
  "--cli codex child")"
[[ "$rc" == "2" ]]
grep -Fq 'parent pane identity does not confirm lead' \
  "$FIXTURE/parent-identity-mismatch.err"
tmux -L "$SOCKET" set-option -p -t "$parent_pane" \
  @formation_identity_locked lead
[[ "$(wc -l < "$registry" | tr -d ' ')" == "1" ]]

# Registering the parent first makes the reverse route mechanically provable.
rc="$(run_register "$child_pane" child \
  "FORMATION_PARENT=lead FORMATION_PARENT_PANE=$parent_pane" \
  "--cli codex --task review --goal review\\ goal child")"
[[ "$rc" == "0" ]]
jq -s -e --arg pane "$child_pane" --arg parent_pane "$parent_pane" '
  map(select(.id == "child")) | length == 1
  and .[0].pane_id == $pane
  and .[0].parent_id == "lead"
  and .[0].parent_pane == $parent_pane
  and .[0].parent_route_state == "ROUTABLE"
  and .[0].goal == "review goal"
  and .[0].exclusive_input == false
  and .[0].relay_state == "DEAD"
' "$registry" >/dev/null

# DEAD/manual relay rows use the direct signal fallback: the durable body is
# queued, the recipient badge advances, and its prompt receives no keystrokes.
parent_before="$(tmux -L "$SOCKET" capture-pane -p -t "$parent_pane")"
msg_rcfile="$FIXTURE/msg.rc"
printf -v msg_command \
  'FORMATION_HOME=%q %q msg lead %q >/dev/null 2>%q; printf "%%s" "$?" >%q' \
  "$FIXTURE/home" "$BIN" "manual relay smoke" "$FIXTURE/msg.err" "$msg_rcfile"
tmux -L "$SOCKET" send-keys -t "$child_pane" "$msg_command"
sleep 0.1
tmux -L "$SOCKET" send-keys -t "$child_pane" C-m
for _ in $(seq 1 100); do
  [[ -s "$msg_rcfile" ]] && break
  sleep 0.05
done
[[ -s "$msg_rcfile" && "$(cat "$msg_rcfile")" == "0" ]]
[[ "$(tmux -L "$SOCKET" show-options -p -q -v -t "$parent_pane" \
  @formation_mail_pending)" =~ ^[0-9]+$ ]]
parent_after="$(tmux -L "$SOCKET" capture-pane -p -t "$parent_pane")"
[[ "$parent_after" == "$parent_before" ]]
jq -s -e '
  map(select(.to == "lead" and .from == "child"
             and .body == "manual relay smoke")) | length == 1
' "$FIXTURE/home/mailbox/log.jsonl" >/dev/null

# Duplicate id and duplicate pane both fail under the registry lock. The
# established locked identities remain unchanged.
third_pane="$(tmux -L "$SOCKET" new-window -d -P -F '#{pane_id}' \
  -t register-real -n duplicate 'bash --noprofile --norc')"
rc="$(run_register "$third_pane" duplicate-id "" "--cli kimi lead")"
[[ "$rc" == "2" ]]
grep -Fq 'id or pane already registered' "$FIXTURE/duplicate-id.err"
[[ -z "$(tmux -L "$SOCKET" show-options -p -q -v -t "$third_pane" \
  @formation_identity_locked 2>/dev/null || true)" ]]

tmux -L "$SOCKET" set-option -p -t "$third_pane" \
  @formation_identity_locked existing-owner
rc="$(run_register "$third_pane" identity-conflict "" "--cli kimi third")"
[[ "$rc" == "2" ]]
grep -Fq 'pane identity conflicts' "$FIXTURE/identity-conflict.err"
[[ "$(tmux -L "$SOCKET" show-options -p -q -v -t "$third_pane" \
  @formation_identity_locked)" == "existing-owner" ]]
[[ "$(wc -l < "$registry" | tr -d ' ')" == "2" ]]

rc="$(run_register "$child_pane" duplicate-pane "" "--cli codex other-child")"
[[ "$rc" == "2" ]]
grep -Fq 'id or pane already registered' "$FIXTURE/duplicate-pane.err"
[[ "$(tmux -L "$SOCKET" show-options -p -q -v -t "$child_pane" \
  @formation_identity_locked)" == "child" ]]
[[ "$(wc -l < "$registry" | tr -d ' ')" == "2" ]]

# A mid-update tmux failure restores every preimage and appends no row.
rollback_pane="$(tmux -L "$SOCKET" new-window -d -P -F '#{pane_id}' \
  -t register-real -n rollback 'bash --noprofile --norc')"
tmux -L "$SOCKET" set-option -p -t "$rollback_pane" @formation_cli original
tmux -L "$SOCKET" set-option -p -t "$rollback_pane" @formation_task original-task
tmux -L "$SOCKET" set-option -p -t "$rollback_pane" @formation_exclusive_input 1
mkdir -p "$FIXTURE/fakebin"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'if [[ " $* " == *" set-option -p -t $FAIL_PANE @formation_status registered "* ]]; then exit 9; fi' \
  'exec "$REAL_TMUX_BIN" "$@"' \
  > "$FIXTURE/fakebin/tmux"
chmod +x "$FIXTURE/fakebin/tmux"
printf -v rollback_env 'PATH=%q REAL_TMUX_BIN=%q FAIL_PANE=%q' \
  "$FIXTURE/fakebin:$PATH" "$REAL_TMUX" "$rollback_pane"
rc="$(run_register "$rollback_pane" rollback \
  "$rollback_env" \
  "--cli grok rollback-worker")"
[[ "$rc" == "2" ]]
grep -Fq 'pane-option update failed; rolled back' "$FIXTURE/rollback.err"
[[ -z "$(tmux -L "$SOCKET" show-options -p -q -v -t "$rollback_pane" \
  @formation_identity_locked 2>/dev/null || true)" ]]
[[ "$(tmux -L "$SOCKET" show-options -p -q -v -t "$rollback_pane" \
  @formation_cli)" == "original" ]]
[[ "$(tmux -L "$SOCKET" show-options -p -q -v -t "$rollback_pane" \
  @formation_task)" == "original-task" ]]
[[ "$(tmux -L "$SOCKET" show-options -p -q -v -t "$rollback_pane" \
  @formation_exclusive_input)" == "1" ]]
[[ "$(wc -l < "$registry" | tr -d ' ')" == "2" ]]

echo "test_register_real_tmux: PASS"
