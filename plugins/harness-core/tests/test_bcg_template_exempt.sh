#!/bin/bash
# FP regression (the #36 reader-agnostic basename pattern over-blocked credential
# TEMPLATE files for reader verbs): bash_command_guard must ALLOW pure template files
# (.env.example/.sample/.template/.dist/.test/.local-example) while still BLOCKING
# real credential files — including mid-chain template tokens (.env.test.local) and
# mixed reads (.env.example .env). Boundary cases per codex cross-family review.
# Run: bash plugins/harness-core/tests/test_bcg_template_exempt.sh
set -u
GUARD="$(cd "$(dirname "$0")/../hooks" && pwd)/bash_command_guard.sh"
D=".env"
PASS=0; FAIL=0
blocks() { printf '%s' "{\"tool_input\":{\"command\":$(printf '%s' "$1" | jq -Rs .)}}" | bash "$GUARD" 2>/dev/null | grep -q '"permissionDecision": "deny"'; }
check() { if blocks "$2"; then r=BLOCK; else r=ALLOW; fi
  if [ "$r" = "$1" ]; then PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m %s %s\n' "$r" "$2"
  else FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m expected %s got %s : %s\n' "$1" "$r" "$2"; fi; }

echo "== ALLOW: pure credential-template files (the FP this fixes) =="
check ALLOW "cat ${D}.example"
check ALLOW "grep -c KEY ${D}.example"
check ALLOW "cat ${D}.sample"
check ALLOW "cat ${D}.template"
check ALLOW "cat config/${D}.dist"
check ALLOW "cat ${D}.test"
check ALLOW "cat ${D}.local-example"

echo "== BLOCK: real credential files (no FN introduced) =="
check BLOCK "cat ${D}"
check BLOCK "grep KEY ${D}"
check BLOCK "cat ${D}.production"
check BLOCK "cat ${D}.local"

echo "== BLOCK: mid-chain template token is not a final-segment template (codex finding 1) =="
check BLOCK "cat ${D}.test.local"
check BLOCK "cat ${D}.example.bak"
check BLOCK "cat ${D}.example-old"

echo "== BLOCK: mixed / chained / dir-form reads (real .env survives the strip) =="
check BLOCK "cat ${D}.example ${D}"
check BLOCK "cat ${D}.example && cat ${D}"
check BLOCK "cat ${D}.example/${D}"
check BLOCK "cat ${D}.exampleXYZ"

echo
echo "RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
