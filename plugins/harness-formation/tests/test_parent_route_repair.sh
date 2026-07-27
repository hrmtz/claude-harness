#!/bin/bash
# Regression tests for locked caller-pane parent fallback and legacy repair.
# Synthetic only: no live tmux server or Formation state is touched.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$HERE/../bin/formation"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

export FORMATION_HOME="$TMP/formation-home"
export FORMATION_MAILBOX="$FORMATION_HOME/mailbox/log.jsonl"
export FORMATION_PARENT_REPAIR_BACKUP_ROOT="$TMP/sanada"
mkdir -p "$FORMATION_PARENT_REPAIR_BACKUP_ROOT"
unset FORMATION_SELF
export TMUX_PANE="%42"
printf '# parent route fixture\n' >"$TMP/briefing.md"

CALLER_TTY="pts/216"
PANE_ROWS='%42|1|/dev/pts/216'
TMUX_LOCKED_ID="lead-locked"
TMUX_NEW_WINDOW_LOG="$TMP/new-window.log"
TMUX_OPTION_DIR="$TMP/tmux-options"
TMUX_PARENT_SET_COUNT="$TMP/parent-set-count"
TMUX_PARENT_ID_READ_COUNT="$TMP/parent-id-read-count"
TMUX_CHILD_ID_READ_COUNT="$TMP/child-id-read-count"
TMUX_FAIL_PARENT_SET_AT=0
TMUX_PARENT_ID_CHANGE_AT=0
TMUX_CHILD_ID_CHANGE_AT=0
TMUX_DEAD_PANE=""
PS_FAIL_TTY=0
mkdir -p "$TMUX_OPTION_DIR"
: >"$TMUX_NEW_WINDOW_LOG"
: >"$TMUX_PARENT_SET_COUNT"
: >"$TMUX_PARENT_ID_READ_COUNT"
: >"$TMUX_CHILD_ID_READ_COUNT"

# The caller's controlling TTY proves %42 even though the pane root PID is not
# in this wrapper process's ancestry. This is the real 104dbdc gap: a locked
# identity exists, but the PID-only resolver refuses the spawn.
ps() {
  case "$*" in
    *"-o ppid="*) printf '1\n' ;;
    *"-o tty="*)
      [[ "$PS_FAIL_TTY" == "0" ]] || return 1
      printf '%s\n' "$CALLER_TTY"
      ;;
    *) return 1 ;;
  esac
}

tmux() {
  local command="${1:-}" target="" option="" value="" unset_option=0
  local value_only=0
  local read_count
  shift || true
  local -a args=("$@")
  local i
  for ((i=0; i<${#args[@]}; i++)); do
    case "${args[$i]}" in
      -t)
        i=$((i + 1))
        target="${args[$i]}"
        ;;
      -u) unset_option=1 ;;
      -v) value_only=1 ;;
      @*)
        option="${args[$i]}"
        if ((i + 1 < ${#args[@]})); then value="${args[$((i + 1))]}"; fi
        ;;
    esac
  done
  local option_file=""
  if [[ -n "$target" && -n "$option" ]]; then
    option_file="$TMUX_OPTION_DIR/${target#%}.${option#@}"
  fi
  case "$command" in
    list-panes)
      printf '%b\n' "$PANE_ROWS"
      ;;
    display-message)
      [[ "$target" != "$TMUX_DEAD_PANE" ]] || return 1
      if [[ "$target" == "%42" ]]; then
        read_count="$(cat "$TMUX_PARENT_ID_READ_COUNT" 2>/dev/null || echo 0)"
        read_count=$((read_count + 1))
        printf '%s\n' "$read_count" >"$TMUX_PARENT_ID_READ_COUNT"
        if [[ "$TMUX_PARENT_ID_CHANGE_AT" -gt 0 &&
              "$read_count" -ge "$TMUX_PARENT_ID_CHANGE_AT" ]]; then
          printf 'changed-parent\n'
        else
          printf '%s\n' "$TMUX_LOCKED_ID"
        fi
      elif [[ -f "$TMUX_OPTION_DIR/${target#%}.formation_identity_locked" ]]; then
        read_count="$(cat "$TMUX_CHILD_ID_READ_COUNT" 2>/dev/null || echo 0)"
        read_count=$((read_count + 1))
        printf '%s\n' "$read_count" >"$TMUX_CHILD_ID_READ_COUNT"
        if [[ "$TMUX_CHILD_ID_CHANGE_AT" -gt 0 &&
              "$read_count" -ge "$TMUX_CHILD_ID_CHANGE_AT" ]]; then
          printf 'recycled-child\n'
        else
          cat "$TMUX_OPTION_DIR/${target#%}.formation_identity_locked"
        fi
      elif [[ -f "$TMUX_OPTION_DIR/${target#%}.formation_id" ]]; then
        cat "$TMUX_OPTION_DIR/${target#%}.formation_id"
      else
        return 1
      fi
      ;;
    new-window)
      printf 'new-window\n' >>"$TMUX_NEW_WINDOW_LOG"
      printf '%%99\n'
      ;;
    show-options)
      [[ "$target" != "$TMUX_DEAD_PANE" ]] || return 1
      if [[ -n "$option_file" && -f "$option_file" ]]; then
        value="$(cat "$option_file")"
        if [[ "$value_only" == "1" ]]; then
          printf '%s\n' "$value"
        elif [[ "$value" == %* || "$value" == *[\ \;\"]* ]]; then
          # Match the tmux syntax that exposed #216: ordinary show-options
          # quotes values such as pane ids, while show-options -v is raw.
          printf '%s "%s"\n' "$option" "${value//\\/\\\\}"
        else
          printf '%s %s\n' "$option" "$value"
        fi
      fi
      ;;
    set-option)
      [[ "$target" != "$TMUX_DEAD_PANE" ]] || return 1
      if [[ "$option" == "@formation_parent_id" ||
            "$option" == "@formation_parent_pane" ]]; then
        local count
        count="$(cat "$TMUX_PARENT_SET_COUNT" 2>/dev/null || echo 0)"
        count=$((count + 1))
        printf '%s\n' "$count" >"$TMUX_PARENT_SET_COUNT"
        if [[ -n "$option_file" ]]; then
          if [[ "$unset_option" == "1" ]]; then
            rm -f "$option_file"
          else
            printf '%s\n' "$value" >"$option_file"
          fi
        fi
        case ",$TMUX_FAIL_PARENT_SET_AT," in
          *",$count,"*) return 1 ;;
        esac
      elif [[ -n "$option_file" ]]; then
        if [[ "$unset_option" == "1" ]]; then
          rm -f "$option_file"
        else
          printf '%s\n' "$value" >"$option_file"
        fi
      fi
      ;;
    *)
      return 0
      ;;
  esac
}

# shellcheck source=/dev/null
source "$BIN"
codex_cache_preflight() {
  CODEX_LAUNCH_BIN=/bin/true
}
resolve_cross_cli_guard() {
  printf '/bin/true'
}
start_mailbox_relay() {
  return 0
}
sleep() {
  return 0
}

cmd_spawn --cli codex "$TMP/briefing.md" tty-parent-worker

row="$(registry_get tty-parent-worker)"
[[ "$(printf '%s\n' "$row" | jq -r '.parent_id')" == "lead-locked" ]]
[[ "$(printf '%s\n' "$row" | jq -r '.parent_pane')" == "%42" ]]
[[ "$(printf '%s\n' "$row" | jq -r '.parent_route_state')" == "ROUTABLE" ]]
[[ "$(wc -l <"$REGISTRY")" -eq 1 ]]

# A verified pane cannot fall through to shell-$$/pane-%N when its
# locked/legacy Formation identity is empty. Refusal precedes pane+row creation.
before_rows="$(wc -l <"$REGISTRY")"
before_windows="$(wc -l <"$TMUX_NEW_WINDOW_LOG")"
TMUX_LOCKED_ID=""
if empty_identity_out="$(cmd_spawn --cli codex "$TMP/briefing.md" \
    empty-identity-worker 2>&1)"; then
  echo "FAIL: empty pane identity spawned a worker" >&2
  exit 1
fi
grep -Fq 'no valid locked/legacy Formation identity' <<<"$empty_identity_out"
[[ "$(wc -l <"$REGISTRY")" == "$before_rows" ]]
[[ "$(wc -l <"$TMUX_NEW_WINDOW_LOG")" == "$before_windows" ]]
TMUX_LOCKED_ID="invalid identity"
if cmd_spawn --cli codex "$TMP/briefing.md" invalid-identity-worker \
    >/dev/null 2>&1; then
  echo "FAIL: invalid pane identity spawned a worker" >&2
  exit 1
fi
[[ "$(wc -l <"$REGISTRY")" == "$before_rows" ]]
[[ "$(wc -l <"$TMUX_NEW_WINDOW_LOG")" == "$before_windows" ]]
TMUX_LOCKED_ID="lead-locked"

# Existing two-field list-panes mocks retain their ancestry proof.
PANE_ROWS="%55|$$"
[[ "$(mailbox_resolve_caller_pane)" == "%55" ]]
PANE_ROWS='%42|1|/dev/pts/216'

# A stale inherited pane id cannot redirect the TTY proof to a sibling.
TMUX_PANE="%77"
[[ "$(mailbox_resolve_caller_pane)" == "%42" ]]
TMUX_PANE="%42"

# Duplicate TTY matches, an unrelated pane, and headless callers all remain
# unverified even if TMUX_PANE names a live-looking target.
PANE_ROWS=$'%42|1|/dev/pts/216\n%43|1|/dev/pts/216'
if mailbox_resolve_caller_pane >/dev/null; then
  echo "FAIL: ambiguous duplicate TTY match acquired a parent route" >&2
  exit 1
fi
PANE_ROWS='%42|1|/dev/pts/999'
if mailbox_resolve_caller_pane >/dev/null; then
  echo "FAIL: unrelated pane passed the controlling-TTY proof" >&2
  exit 1
fi
CALLER_TTY="?"
if mailbox_resolve_caller_pane >/dev/null; then
  echo "FAIL: headless caller acquired a parent route" >&2
  exit 1
fi
CALLER_TTY=""
if mailbox_resolve_caller_pane >/dev/null; then
  echo "FAIL: empty controlling TTY acquired a parent route" >&2
  exit 1
fi
PS_FAIL_TTY=1
if mailbox_resolve_caller_pane >/dev/null; then
  echo "FAIL: failed ps TTY lookup acquired a parent route" >&2
  exit 1
fi
PS_FAIL_TTY=0

# Explicit FORMATION_SELF remains the intentional pull-only/headless route.
FORMATION_SELF="cron-lead"
cmd_spawn --cli codex "$TMP/briefing.md" pull-only-worker
row="$(registry_get pull-only-worker)"
[[ "$(printf '%s\n' "$row" | jq -r '.parent_id')" == "cron-lead" ]]
[[ "$(printf '%s\n' "$row" | jq -r '.parent_pane // empty')" == "" ]]
[[ "$(printf '%s\n' "$row" | jq -r '.parent_route_state')" == "UNROUTABLE" ]]
pull_status="$(cmd_status)"
grep -E '^pull-only-worker .*parent=UNROUTABLE' <<<"$pull_status" >/dev/null
registry_remove pull-only-worker
unset FORMATION_SELF

CALLER_TTY="pts/216"
PANE_ROWS='%42|1|/dev/pts/216'

# A legacy null route is visibly unhealthy, and status remains read-only.
registry_remove tty-parent-worker
printf 'legacy-null\n' >"$TMUX_OPTION_DIR/99.formation_identity_locked"
tmux set-option -p -u -t %99 @formation_parent_id
tmux set-option -p -u -t %99 @formation_parent_pane
: >"$TMUX_PARENT_SET_COUNT"
jq -cn '{
  id:"legacy-null",pane_id:"%99",session_name:"formation-legacy-null",
  briefing:"legacy.md",spawned:"2026-07-27T00:00:00Z",cli:"codex",
  exclusive_input:true,parent_id:null,parent_pane:null
}' >>"$REGISTRY"
before_status="$(sha256sum "$REGISTRY" | awk '{print $1}')"
status_out="$(cmd_status)"
after_status="$(sha256sum "$REGISTRY" | awk '{print $1}')"
[[ "$before_status" == "$after_status" ]]
[[ "$(grep -c '^legacy-null ' <<<"$status_out")" -eq 1 ]]
grep -Fq 'parent=UNROUTABLE' <<<"$status_out"

# Repair derives both fields from the verified current pane and its immutable
# identity. It updates in place under the registry lock; retry is a true no-op.
before_count="$(wc -l <"$REGISTRY")"
before_backups="$(find "$FORMATION_PARENT_REPAIR_BACKUP_ROOT" -mindepth 1 \
  -maxdepth 1 -type d 2>/dev/null | wc -l)"
if first_repair="$(cmd_repair_parent legacy-null 2>&1)"; then
  :
else
  first_repair_rc=$?
  echo "FAIL: initial repair rc=$first_repair_rc: $first_repair" >&2
  exit 1
fi
after_first_count="$(wc -l <"$REGISTRY")"
after_first_hash="$(sha256sum "$REGISTRY" | awk '{print $1}')"
after_first_backups="$(find "$FORMATION_PARENT_REPAIR_BACKUP_ROOT" -mindepth 1 \
  -maxdepth 1 -type d | wc -l)"
second_repair="$(cmd_repair_parent legacy-null)"
after_second_count="$(wc -l <"$REGISTRY")"
after_second_hash="$(sha256sum "$REGISTRY" | awk '{print $1}')"
after_second_backups="$(find "$FORMATION_PARENT_REPAIR_BACKUP_ROOT" -mindepth 1 \
  -maxdepth 1 -type d | wc -l)"
[[ "$first_repair" == *"parent route repaired"* ]]
[[ "$first_repair" == *"backup=$FORMATION_PARENT_REPAIR_BACKUP_ROOT/"* ]]
[[ "$second_repair" == *"(no-op)"* ]]
[[ "$before_count" == "$after_first_count" ]]
[[ "$after_first_count" == "$after_second_count" ]]
[[ "$after_first_hash" == "$after_second_hash" ]]
[[ "$after_first_backups" -eq $((before_backups + 1)) ]]
[[ "$after_second_backups" -eq "$after_first_backups" ]]
[[ "$(cat "$TMUX_OPTION_DIR/99.formation_parent_id")" == "lead-locked" ]]
[[ "$(cat "$TMUX_OPTION_DIR/99.formation_parent_pane")" == "%42" ]]
row="$(registry_get legacy-null)"
[[ "$(printf '%s\n' "$row" | jq -r '.parent_id')" == "lead-locked" ]]
[[ "$(printf '%s\n' "$row" | jq -r '.parent_pane')" == "%42" ]]
[[ "$(printf '%s\n' "$row" | jq -r '.parent_route_state')" == "ROUTABLE" ]]
[[ "$(printf '%s\n' "$row" | jq -r '.parent_route_reason')" == "null" ]]
[[ "$(printf '%s\n' "$row" | jq -r '.parent_route_repaired_via')" == \
   "locked-caller" ]]
before_repaired_status="$(sha256sum "$REGISTRY" | awk '{print $1}')"
status_out="$(cmd_status)"
after_repaired_status="$(sha256sum "$REGISTRY" | awk '{print $1}')"
[[ "$before_repaired_status" == "$after_repaired_status" ]]
[[ "$(grep -c '^legacy-null ' <<<"$status_out")" -eq 1 ]]
grep -Fq 'parent=lead-locked@%42' <<<"$status_out"
backup_dir="${first_repair##*backup=}"
[[ -s "$backup_dir/registry.jsonl.preimage" ]]
[[ -s "$backup_dir/target-row.json" ]]
jq -e '
  .worker_id == "legacy-null"
  and .pane_id == "%99"
  and .parent_id.set == false
  and .parent_pane.set == false
' "$backup_dir/pane-options.json" >/dev/null

# The repaired registry+pane pair passes formation-mail-nudge's own strict
# route predicate; dry-run would alert the verified parent, not report the
# route unavailable.
NUDGE_FAKE="$TMP/nudge-bin"
NUDGE_CFG="$TMP/nudge-pane.json"
mkdir -p "$NUDGE_FAKE"
jq -n '{
  pane:"%99",seq:"9",exclusive:"1",task:"fixture",role:"worker",
  worker:"legacy-null",parent_id:"lead-locked",parent_pane:"%42"
}' >"$NUDGE_CFG"
cat >"$NUDGE_FAKE/tmux" <<'PY'
#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
p = json.loads(Path(os.environ["NUDGE_CFG"]).read_text())
cmd = sys.argv[1]
if cmd == "list-panes":
    print("|".join(p[k] for k in (
        "pane", "seq", "exclusive", "task", "role", "worker",
        "parent_id", "parent_pane"
    )))
elif cmd == "capture-pane":
    print("idle")
PY
chmod +x "$NUDGE_FAKE/tmux"
NUDGE_HELPER="$HERE/../bin/formation-mail-nudge"
NUDGE_CFG="$NUDGE_CFG" PATH="$NUDGE_FAKE:/usr/bin:/bin" \
  FORMATION_HOME="$FORMATION_HOME" "$NUDGE_HELPER" \
  --stale 1 --idle 1 --verify 1 >/dev/null
nudge_state="$FORMATION_HOME/state/mail-nudge/pane-99.json"
jq '
  .attempted=true
  | .attempt_result="attempted-unconfirmed"
  | .attempted_at=0
  | .attempt_snapshot_crc=.snapshot_crc
  | .attempt_mailbox_seq=9
  | .receipt="attempted-unconfirmed"
' "$nudge_state" >"$nudge_state.tmp"
mv "$nudge_state.tmp" "$nudge_state"
nudge_dry_out="$(NUDGE_CFG="$NUDGE_CFG" PATH="$NUDGE_FAKE:/usr/bin:/bin" \
  FORMATION_HOME="$FORMATION_HOME" "$NUDGE_HELPER" --dry-run \
  --stale 1 --idle 1 --verify 1)"
grep -Fq 'result=would-alert-parent' <<<"$nudge_dry_out"
! grep -Fq 'parent-route-unavailable' <<<"$nudge_dry_out"

add_null_worker() {
  local worker="$1" pane="$2"
  registry_add "$worker" "$pane" "formation-$worker" "$TMP/briefing.md"
  printf '%s\n' "$worker" >"$TMUX_OPTION_DIR/${pane#%}.formation_identity_locked"
  rm -f "$TMUX_OPTION_DIR/${pane#%}.formation_parent_id" \
    "$TMUX_OPTION_DIR/${pane#%}.formation_parent_pane"
}

capture_repair() {
  local worker="$1"
  if REPAIR_OUT="$(cmd_repair_parent "$worker" 2>&1)"; then
    REPAIR_RC=0
  else
    REPAIR_RC=$?
  fi
}

reset_tmux_faults() {
  TMUX_FAIL_PARENT_SET_AT=0
  TMUX_PARENT_ID_CHANGE_AT=0
  TMUX_CHILD_ID_CHANGE_AT=0
  TMUX_DEAD_PANE=""
  : >"$TMUX_PARENT_SET_COUNT"
  : >"$TMUX_PARENT_ID_READ_COUNT"
  : >"$TMUX_CHILD_ID_READ_COUNT"
}

# Missing, conflicting, invalid, headless, and ambiguous repairs fail closed.
capture_repair absent-worker
[[ "$REPAIR_RC" -eq 2 ]]
registry_add conflict-worker %98 formation-conflict "$TMP/briefing.md" \
  "" codex conflict goal 0 other-parent %88
capture_repair conflict-worker
[[ "$REPAIR_RC" -eq 5 ]]
registry_add invalid-lock-worker %97 formation-invalid "$TMP/briefing.md"
TMUX_LOCKED_ID="invalid identity"
capture_repair invalid-lock-worker
[[ "$REPAIR_RC" -eq 2 ]]
TMUX_LOCKED_ID="lead-locked"
CALLER_TTY="?"
capture_repair invalid-lock-worker
[[ "$REPAIR_RC" -eq 2 ]]
CALLER_TTY="pts/216"
registry_add duplicate-worker %95 formation-duplicate-a "$TMP/briefing.md"
registry_add duplicate-worker %96 formation-duplicate-b "$TMP/briefing.md"
duplicate_count="$(wc -l <"$REGISTRY")"
capture_repair duplicate-worker
[[ "$REPAIR_RC" -eq 5 ]]
[[ "$(wc -l <"$REGISTRY")" == "$duplicate_count" ]]

# Closed/recycled child panes and caller/child identity races are refused.
add_null_worker identity-mismatch %94
printf 'different-worker\n' >"$TMUX_OPTION_DIR/94.formation_identity_locked"
capture_repair identity-mismatch
[[ "$REPAIR_RC" -eq 5 ]]
add_null_worker dead-worker %93
TMUX_DEAD_PANE="%93"
capture_repair dead-worker
[[ "$REPAIR_RC" -eq 5 ]]
reset_tmux_faults
add_null_worker parent-race %92
TMUX_PARENT_ID_CHANGE_AT=2
capture_repair parent-race
[[ "$REPAIR_RC" -eq 5 ]]
[[ ! -e "$TMUX_OPTION_DIR/92.formation_parent_id" ]]
reset_tmux_faults
add_null_worker child-race %91
TMUX_CHILD_ID_CHANGE_AT=2
capture_repair child-race
[[ "$REPAIR_RC" -eq 5 ]]
[[ ! -e "$TMUX_OPTION_DIR/91.formation_parent_id" ]]
reset_tmux_faults

# Either pane-option write failure rolls back both options and leaves the
# registry route null. A failed atomic registry replace does the same.
add_null_worker fail-first-set %90
TMUX_FAIL_PARENT_SET_AT=1
capture_repair fail-first-set
[[ "$REPAIR_RC" -eq 7 ]]
[[ ! -e "$TMUX_OPTION_DIR/90.formation_parent_id" ]]
[[ ! -e "$TMUX_OPTION_DIR/90.formation_parent_pane" ]]
registry_get fail-first-set | jq -e \
  '.parent_id == null and .parent_pane == null' >/dev/null
reset_tmux_faults

add_null_worker fail-second-set %89
TMUX_FAIL_PARENT_SET_AT=2
capture_repair fail-second-set
[[ "$REPAIR_RC" -eq 7 ]]
[[ ! -e "$TMUX_OPTION_DIR/89.formation_parent_id" ]]
[[ ! -e "$TMUX_OPTION_DIR/89.formation_parent_pane" ]]
registry_get fail-second-set | jq -e \
  '.parent_id == null and .parent_pane == null' >/dev/null
reset_tmux_faults

add_null_worker fail-registry %88
mv() {
  if [[ "${1:-}" == *".registry.parent-repair."* ]]; then
    return 1
  fi
  command mv "$@"
}
capture_repair fail-registry
unset -f mv
[[ "$REPAIR_RC" -eq 7 ]]
[[ ! -e "$TMUX_OPTION_DIR/88.formation_parent_id" ]]
[[ ! -e "$TMUX_OPTION_DIR/88.formation_parent_pane" ]]
registry_get fail-registry | jq -e \
  '.parent_id == null and .parent_pane == null' >/dev/null

reset_tmux_faults
add_null_worker fail-rollback %87
TMUX_FAIL_PARENT_SET_AT="2,3"
capture_repair fail-rollback
[[ "$REPAIR_RC" -eq 8 ]]
grep -Fq 'PARTIAL REPAIR' <<<"$REPAIR_OUT"
registry_get fail-rollback | jq -e \
  '.parent_id == null and .parent_pane == null' >/dev/null

echo "test_parent_route_repair: passed"
