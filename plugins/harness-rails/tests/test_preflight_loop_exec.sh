#!/bin/bash
# #5: pipeline_preflight_gate bulk-parallel-loop must fire ONLY when a shell actually
# EXECUTES the loop — not when for...do...& appears as inert quoted argument text
# (echo/printf/python -c prose, doc strings). `... | bash` / `bash -c` / `eval` still gate.
# Run: bash plugins/harness-rails/tests/test_preflight_loop_exec.sh
set -u
GATE="$(cd "$(dirname "$0")/../hooks" && pwd)/pipeline_preflight_gate.sh"
PASS=0; FAIL=0
blocks() {
  printf '%s' "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":$(printf '%s' "$1" | jq -Rs .)}}" \
    | bash "$GATE" >/dev/null 2>&1
  [ "$?" -eq 2 ]
}
check() { if blocks "$2"; then r=BLOCK; else r=ALLOW; fi
  if [ "$r" = "$1" ]; then PASS=$((PASS+1)); printf '  PASS %s %s\n' "$r" "$2"
  else FAIL=$((FAIL+1)); printf '  FAIL expected %s got %s : %s\n' "$1" "$r" "$2"; fi; }

L='for i in 0 1 2 3; do x & done'

echo "== FP: loop is inert quoted DATA (must ALLOW) =="
check ALLOW "echo '$L'"
check ALLOW "printf '%s\\n' '$L'"
check ALLOW "python3 -c 'print(\"$L\")'"
check ALLOW "echo \"$L\" > script.sh"

echo "== must still BLOCK: a shell executes the loop =="
check BLOCK "$L"
check BLOCK "nohup $L"
check BLOCK "echo '$L' | bash"
check BLOCK "bash -c '$L'"
check BLOCK 'for i in $(seq 0 9); do x & done'

echo "== git/gh prose whitelist unchanged (ALLOW) =="
check ALLOW "gh issue create --body '$L'"

echo
echo "RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
