#!/usr/bin/env bash
set -euo pipefail
trap 'echo "FAIL: test_mail_nudge line $LINENO" >&2' ERR
HERE="$(cd "$(dirname "$0")" && pwd)"
TMP="$(mktemp -d)"
trap '[[ "${KEEP_MAIL_NUDGE_TMP:-0}" == "1" ]] || rm -rf "$TMP"' EXIT
FAKE="$TMP/bin"
mkdir -p "$FAKE"

cat >"$FAKE/tmux" <<'PY'
#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
cfg = json.loads(Path(os.environ["TMUX_FIXTURE"]).read_text())
args = sys.argv[1:]
cmd = args[0]
if cmd == "list-panes":
    p = cfg["pane"]
    print("|".join(str(p.get(k, "")) for k in
        ("pane", "seq", "exclusive", "task", "role", "worker", "parent_id", "parent_pane")))
elif cmd == "capture-pane":
    print(cfg["pane"].get("snapshot", "idle"))
elif cmd == "show-options":
    if "@formation_exclusive_input" in args:
        print(cfg["pane"].get("exclusive", "0"))
    else:
        print("")
elif cmd == "display-message":
    if "#{pane_mode}" in args:
        print("0")
    elif "#{pane_id}" in args:
        print(args[args.index("-t") + 1])
elif cmd in ("load-buffer", "paste-buffer", "send-keys", "delete-buffer", "set-option"):
    with open(os.environ["TMUX_MUTATIONS"], "a") as log:
        log.write(cmd + " " + " ".join(args[1:]) + "\n")
    if cmd == "load-buffer":
        sys.stdin.read()
PY
chmod +x "$FAKE/tmux"

HELPER="$HERE/../bin/formation-mail-nudge"
CFG="$TMP/tmux.json"
MUT="$TMP/mutations.log"
export PATH="$FAKE:/usr/bin:/bin" TMUX_FIXTURE="$CFG" TMUX_MUTATIONS="$MUT"
export FORMATION_SUBMIT_SETTLE_S=0 FORMATION_SUBMIT_RETRY_S=0

write_fixture() {
  local home="$1" reg_exclusive="$2" pane_exclusive="$3" role="${4:-worker}"
  mkdir -p "$home/formation"
  jq -cn --arg ex "$reg_exclusive" \
    '{id:"child",pane_id:"%42",exclusive_input:($ex=="1"),
      parent_id:"parent",parent_pane:"%7"}' >"$home/formation/registry.jsonl"
  jq -n --arg ex "$pane_exclusive" --arg role "$role" \
    '{pane:{pane:"%42",seq:"9",exclusive:$ex,task:"fixture",role:$role,
      worker:"child",parent_id:"parent",parent_pane:"%7",snapshot:"idle"}}' >"$CFG"
  : >"$MUT"
}

age_state() {
  local state="$1"
  jq '.first_seen_at=0 | .snapshot_since=0' "$state" >"$state.tmp"
  mv "$state.tmp" "$state"
}

# Both exclusive gates are independently mandatory.
for gates in "0 1" "1 0"; do
  home="$TMP/gate-${gates// /-}"
  write_fixture "$home" $gates
  FORMATION_HOME="$home" "$HELPER" --stale 1 --idle 1 --verify 1 >/dev/null
  age_state "$home/state/mail-nudge/pane-42.json"
  FORMATION_HOME="$home" "$HELPER" --stale 1 --idle 1 --verify 1 >/dev/null
  ! grep -Eq 'load-buffer|paste-buffer|send-keys' "$MUT"
done

# Missing, stale/latest-mismatched registry routes are zero-keystroke.
for mode in missing mismatch; do
  home="$TMP/registry-$mode"
  write_fixture "$home" 1 1
  if [[ "$mode" == "missing" ]]; then
    : >"$home/formation/registry.jsonl"
  else
    jq -cn '{id:"child",pane_id:"%99",exclusive_input:true,
      parent_id:"parent",parent_pane:"%7"}' >>"$home/formation/registry.jsonl"
  fi
  FORMATION_HOME="$home" "$HELPER" --stale 1 --idle 1 --verify 1 >/dev/null
  age_state "$home/state/mail-nudge/pane-42.json"
  FORMATION_HOME="$home" "$HELPER" --stale 1 --idle 1 --verify 1 >/dev/null
  ! grep -Eq 'load-buffer|paste-buffer|send-keys' "$MUT"
done

# Lead panes are never nudged.
home="$TMP/lead"
write_fixture "$home" 1 1 lead
FORMATION_HOME="$home" "$HELPER" --stale 1 --idle 1 --verify 1 >/dev/null
[[ ! -e "$home/state/mail-nudge/pane-42.json" ]]

# First observation, snapshot change, and young idle state do not write keys.
home="$TMP/eligible"
write_fixture "$home" 1 1
FORMATION_HOME="$home" "$HELPER" --stale 1 --idle 1 --verify 1 >"$TMP/observe.out"
grep -Fq 'result=would-observe' "$TMP/observe.out"
age_state "$home/state/mail-nudge/pane-42.json"
jq '.pane.snapshot="changed"' "$CFG" >"$CFG.tmp" && mv "$CFG.tmp" "$CFG"
FORMATION_HOME="$home" "$HELPER" --stale 1 --idle 1 --verify 1 >"$TMP/change.out"
grep -Fq 'snapshot-changed' "$TMP/change.out"
! grep -Eq 'load-buffer|paste-buffer|send-keys' "$MUT"

# Stable + old + conjunctive exclusive path invokes the shared submit once.
age_state "$home/state/mail-nudge/pane-42.json"
FORMATION_HOME="$home" "$HELPER" --stale 1 --idle 1 --verify 1 >"$TMP/nudge.out"
grep -Fq 'result=attempted-unconfirmed receipt=unconfirmed' "$TMP/nudge.out"
[[ "$(grep -c '^load-buffer ' "$MUT")" -eq 1 ]]
[[ "$(grep -c '^send-keys ' "$MUT")" -eq 2 ]]
before="$(grep -c '^load-buffer ' "$MUT")"
FORMATION_HOME="$home" "$HELPER" --stale 1 --idle 1 --verify 1 >/dev/null
[[ "$(grep -c '^load-buffer ' "$MUT")" -eq "$before" ]]

# No observable effect creates one durable parent alert and never retries child.
state="$home/state/mail-nudge/pane-42.json"
jq '.attempted_at=0' "$state" >"$state.tmp" && mv "$state.tmp" "$state"
# A repaint after injection is deliberately not treated as success evidence.
jq '.pane.snapshot="post-attempt repaint"' "$CFG" >"$CFG.tmp" && mv "$CFG.tmp" "$CFG"
FORMATION_HOME="$home" "$HELPER" --stale 1 --idle 1 --verify 1 >"$TMP/alert.out"
jq -e '.effect == "no-effect" and .parent_alerted == true' "$state" >/dev/null
[[ "$(grep -c 'mail-nudge escalation:' "$home/mailbox/log.jsonl")" -eq 1 ]]
[[ "$(grep -c '^load-buffer ' "$MUT")" -eq "$before" ]]
FORMATION_HOME="$home" "$HELPER" --stale 1 --idle 1 --verify 1 >/dev/null
[[ "$(grep -c 'mail-nudge escalation:' "$home/mailbox/log.jsonl")" -eq 1 ]]

# Badge clear removes observation state and emits no second alert.
jq '.pane.seq=""' "$CFG" >"$CFG.tmp" && mv "$CFG.tmp" "$CFG"
FORMATION_HOME="$home" "$HELPER" --stale 1 --idle 1 --verify 1 >/dev/null
[[ ! -e "$state" ]]
[[ "$(grep -c 'mail-nudge escalation:' "$home/mailbox/log.jsonl")" -eq 1 ]]

# A later pending sequence starts a fresh observation timer.
jq '.pane.seq="10"' "$CFG" >"$CFG.tmp" && mv "$CFG.tmp" "$CFG"
FORMATION_HOME="$home" "$HELPER" --stale 1 --idle 1 --verify 1 >"$TMP/new-seq.out"
grep -Fq 'seq=10 result=would-observe' "$TMP/new-seq.out"

# A later durable canonical worker row is effect and suppresses parent alert.
home="$TMP/worker-report"
write_fixture "$home" 1 1
FORMATION_HOME="$home" "$HELPER" --stale 1 --idle 1 --verify 1 >/dev/null
age_state "$home/state/mail-nudge/pane-42.json"
FORMATION_HOME="$home" "$HELPER" --stale 1 --idle 1 --verify 1 >/dev/null
report_state="$home/state/mail-nudge/pane-42.json"
jq '.attempted_at=0' "$report_state" >"$report_state.tmp" && mv "$report_state.tmp" "$report_state"
mkdir -p "$home/mailbox"
jq -cn --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  '{seq:100,ts:$ts,from:"child",to:"parent",body:"fixed fixture"}' \
  >"$home/mailbox/log.jsonl"
FORMATION_HOME="$home" "$HELPER" --stale 1 --idle 1 --verify 1 >"$TMP/report.out"
grep -Fq 'effect=durable-worker-row' "$TMP/report.out"
jq -e '.effect == "durable-worker-row" and .parent_alerted == false' "$report_state" >/dev/null
[[ "$(wc -l <"$home/mailbox/log.jsonl")" -eq 1 ]]

# A missing live parent route is visible and never guessed.
home="$TMP/no-parent"
write_fixture "$home" 1 1
jq '.pane.parent_id="" | .pane.parent_pane=""' "$CFG" >"$CFG.tmp" && mv "$CFG.tmp" "$CFG"
FORMATION_HOME="$home" "$HELPER" --stale 1 --idle 1 --verify 1 >/dev/null
age_state "$home/state/mail-nudge/pane-42.json"
FORMATION_HOME="$home" "$HELPER" --stale 1 --idle 1 --verify 1 >/dev/null
no_parent_state="$home/state/mail-nudge/pane-42.json"
jq '.attempted_at=0' "$no_parent_state" >"$no_parent_state.tmp"
mv "$no_parent_state.tmp" "$no_parent_state"
FORMATION_HOME="$home" "$HELPER" --stale 1 --idle 1 --verify 1 >"$TMP/no-parent.out"
grep -Fq 'parent-route-unavailable' "$TMP/no-parent.out"
grep -Fq '@formation_mail_nudge_alert' "$MUT"
[[ ! -e "$home/mailbox/log.jsonl" ]]

# Dry run neither writes state nor invokes any mutating tmux operation.
home="$TMP/dry"
write_fixture "$home" 1 1
before_tree="$(find "$home" -type f -print0 | sort -z | xargs -0 sha256sum)"
: >"$MUT"
FORMATION_HOME="$home" "$HELPER" --dry-run --stale 1 --idle 1 --verify 1 >/dev/null
after_tree="$(find "$home" -type f -print0 | sort -z | xargs -0 sha256sum)"
[[ "$before_tree" == "$after_tree" && ! -s "$MUT" ]]

# A stale pid identity cannot block watch startup or terminate that process.
home="$TMP/pid-reuse"
write_fixture "$home" 1 1 lead
mkdir -p "$home/state/mail-nudge"
jq -n --argjson pid "$$" '{pid:$pid,script:"/unrelated",watch:true}' \
  >"$home/state/mail-nudge/watch.pid.json"
timeout 2 env FORMATION_HOME="$home" "$HELPER" --watch --interval 1 --stale 1 --idle 1 --verify 1 \
  >/dev/null 2>&1 || [[ $? -eq 124 ]]
kill -0 "$$"
[[ ! -e "$home/state/mail-nudge/watch.pid.json" ]]

# Discovery is additive only: no spawn/install/hook path auto-starts watcher.
! rg -n 'formation-mail-nudge[[:space:]]+--watch' \
  "$HERE/../bin/formation" "$HERE/../hooks" "$HERE/../../../scripts" >/dev/null
grep -Fq '@formation_parent_id "$parent_id"' "$HERE/../bin/formation"
grep -Fq '@formation_parent_pane "$parent_pane"' "$HERE/../bin/formation"
grep -Fq 'parent_id:' "$HERE/../bin/formation"

echo "test_mail_nudge: passed"
