#!/usr/bin/env bash
set -euo pipefail
trap 'echo "FAIL: test_window_status line $LINENO" >&2' ERR
HERE="$(cd "$(dirname "$0")" && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin"
STATE="$TMP/tmux-state.json"
LOG="$TMP/tmux-mutations.log"

jq -n '{
  pid:4242,
  globals:{
    "window-status-format":{present:true,value:"old format"},
    "window-status-current-format":{present:false,value:""},
    "window-status-separator":{present:true,value:" | "}
  },
  panes:{
    "%1":{session:"s",role:"old-lead",task:"old task"},
    "%2":{session:"s",role:"",task:""}
  },
  windows:[
    {id:"@1",session:"s",index:0,worker:true},
    {id:"@2",session:"s",index:1,worker:false}
  ]
}' >"$STATE"

cat >"$TMP/bin/tmux" <<'PY'
#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
path = Path(os.environ["WINDOW_TMUX_STATE"])
state = json.loads(path.read_text())
args = sys.argv[1:]
cmd = args[0]
mutating = cmd in ("set-option", "move-window", "refresh-client")
if mutating:
    with open(os.environ["WINDOW_TMUX_LOG"], "a") as log:
        log.write(" ".join(args) + "\n")
def save():
    path.write_text(json.dumps(state, sort_keys=True) + "\n")
if cmd == "display-message":
    fmt = args[-1]
    if fmt == "#{pid}":
        print(state["pid"])
    elif "-t" in args:
        target = args[args.index("-t")+1]
        if target not in state["panes"]:
            sys.exit(1)
        print(target if fmt == "#{pane_id}" else state["panes"][target]["session"])
elif cmd == "show-options":
    name = args[-1]
    if "-g" in args:
        item = state["globals"].get(name, {"present":False,"value":""})
    else:
        target = args[args.index("-t")+1]
        key = "role" if name == "@harness_role" else "task"
        value = state["panes"][target][key]
        item = {"present": bool(value), "value": value}
    if not item["present"]:
        sys.exit(1)
    print(item["value"])
elif cmd == "set-option":
    if "-g" in args or "-gu" in args:
        unset = "-gu" in args
        marker = "-gu" if unset else "-g"
        pos = args.index(marker)
        name = args[pos+1]
        state["globals"][name] = {
            "present": not unset,
            "value": "" if unset else args[pos+2],
        }
    else:
        unset = "-pu" in args
        target = args[args.index("-t")+1]
        name = args[-1] if unset else args[-2]
        value = "" if unset else args[-1]
        key = "role" if name == "@harness_role" else "task"
        state["panes"][target][key] = value
    save()
elif cmd == "list-panes":
    for pane, item in state["panes"].items():
        print(f"{pane}|{item['session']}|{item['role']}")
elif cmd == "list-windows":
    for item in sorted(state["windows"], key=lambda x:(x["session"],x["index"])):
        print(f"{item['id']}|{item['session']}|{item['index']}|{'task' if item['worker'] else ''}")
elif cmd == "move-window":
    wid = args[args.index("-s")+1]
    target = args[args.index("-t")+1]
    index = int(target.rsplit(":",1)[1])
    next(item for item in state["windows"] if item["id"] == wid)["index"] = index
    save()
elif cmd == "refresh-client":
    pass
PY
chmod +x "$TMP/bin/tmux"

export PATH="$TMP/bin:/usr/bin:/bin"
export WINDOW_TMUX_STATE="$STATE" WINDOW_TMUX_LOG="$LOG"
export FORMATION_HOME="$TMP/formation-home"
HELPER="$HERE/../bin/formation-window-status"

before="$(sha256sum "$STATE")"
if "$HELPER" >"$TMP/noarg.out" 2>&1; then
  exit 1
else
  [[ $? -eq 64 ]]
fi
"$HELPER" status | grep -Fq 'inactive server_pid=4242'
[[ "$before" == "$(sha256sum "$STATE")" && ! -e "$LOG" ]]

"$HELPER" apply --lead %2 --task "safe"$'\x01'" task" --pane %2 --arrange --dry-run >"$TMP/dry.out"
grep -Fq 'move @2 session=s 1->0' "$TMP/dry.out"
[[ ! -e "$FORMATION_HOME" && ! -e "$LOG" ]]

"$HELPER" apply --lead %2 --task "safe"$'\x01'" task" --pane %2 --arrange >"$TMP/apply.out"
JOURNAL="$FORMATION_HOME/state/window-status/4242.json"
jq -e '
  .global_options["window-status-format"] == {present:true,value:"old format"}
  and .global_options["window-status-current-format"] == {present:false,value:""}
  and .pane_options["%2"]["@harness_role"].present == false
  and .pane_options["%2"]["@harness_task"].present == false
  and (.arrangement | length) == 2
' "$JOURNAL" >/dev/null
jq -e '
  .panes["%2"].role == "lead"
  and .panes["%2"].task == "safe task"
  and ([.windows[] | select(.id=="@2") | .index][0]) == 0
  and ([.windows[] | select(.id=="@1") | .index][0]) == 1
' "$STATE" >/dev/null
preimage="$(sha256sum "$JOURNAL")"
"$HELPER" apply >/dev/null
[[ "$preimage" == "$(sha256sum "$JOURNAL")" ]]

before_revert="$(sha256sum "$STATE" "$JOURNAL")"
"$HELPER" revert --dry-run >/dev/null
[[ "$before_revert" == "$(sha256sum "$STATE" "$JOURNAL")" ]]
"$HELPER" revert >/dev/null
[[ ! -e "$JOURNAL" ]]
[[ "$(find "$FORMATION_HOME/state/window-status/history" -type f | wc -l)" -eq 1 ]]
jq -e '
  .globals["window-status-format"] == {present:true,value:"old format"}
  and .globals["window-status-current-format"].present == false
  and .globals["window-status-separator"] == {present:true,value:" | "}
  and .panes["%2"].role == ""
  and .panes["%2"].task == ""
  and ([.windows[] | select(.id=="@1") | .index][0]) == 0
  and ([.windows[] | select(.id=="@2") | .index][0]) == 1
' "$STATE" >/dev/null
"$HELPER" revert | grep -Fq 'nothing to revert'

echo "test_window_status: passed"
