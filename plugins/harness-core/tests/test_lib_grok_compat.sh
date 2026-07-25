#!/bin/bash
# test_lib_grok_compat.sh — lib.sh cross-CLI (Grok camelCase) normalization.
#
# Covers the harness-grok port (gh #55): a Grok-shaped payload (`.toolInput`,
# `.toolName`, `.sessionId`) must resolve exactly like the Claude/Codex shape,
# and a PreToolUse deny must switch to Grok's `{"decision":"deny"}` shape when
# the Grok runner env is present. Without these the guard silently passes a
# Grok payload (fail-open) — the defect this port closes.
#
# Run: bash plugins/harness-core/tests/test_lib_grok_compat.sh

set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS="$(cd "$HERE/../hooks" && pwd)"
source "$HOOKS/lib.sh"

pass=0; fail=0
ok()   { echo "  ok: $1"; pass=$((pass+1)); }
bad()  { echo "FAIL: $1"; fail=$((fail+1)); }
eq()   { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (want [$3] got [$2])"; fi; }

# The dangerous command strings live only in these variables (never on a literal
# Bash command line) so the harness's own bash_command_guard doesn't fire on the
# test runner itself. sopsd is assembled to avoid the literal trigger substring.
sopsd='sops -'"d"' secrets.enc.yaml'

# --- parse_tool_command: both shapes ---
got=$(HOOK_INPUT='{"toolInput":{"command":"'"$sopsd"'"}}' parse_tool_command)
eq "parse_tool_command Grok camelCase" "$got" "$sopsd"

got=$(HOOK_INPUT='{"tool_input":{"command":"echo hi"}}' parse_tool_command)
eq "parse_tool_command Claude snake_case" "$got" "echo hi"

# --- parse_tool_file_path: Grok read_file .path + search_replace .file_path ---
got=$(HOOK_INPUT='{"toolInput":{"path":"/x/.env"}}' parse_tool_file_path)
eq "parse_tool_file_path Grok .path" "$got" "/x/.env"
got=$(HOOK_INPUT='{"tool_input":{"file_path":"/y/.env"}}' parse_tool_file_path)
eq "parse_tool_file_path Claude .file_path" "$got" "/y/.env"
got=$(HOOK_INPUT='{"tool_name":"mcp__filesystem__read_file","tool_input":{"path":"/m/.env"}}' parse_tool_file_path)
eq "parse_tool_file_path Codex MCP .path" "$got" "/m/.env"
got=$(HOOK_INPUT='{"tool_name":"mcp__filesystem__read_file","tool_input":{"uri":"file:///u/.env"}}' parse_tool_file_path)
eq "parse_tool_file_path Codex MCP .uri" "$got" "file:///u/.env"
got=$(HOOK_INPUT='{"tool_input":{"file_path":"","path":"/fallback/.env"}}' parse_tool_file_path)
eq "parse_tool_file_path empty snake alias falls through" "$got" "/fallback/.env"
got=$(HOOK_INPUT='{"toolInput":{"file_path":"  ","path":"/grok-fallback/.env"}}' parse_tool_file_path)
eq "parse_tool_file_path blank Grok alias falls through" "$got" "/grok-fallback/.env"
if HOOK_INPUT='{"tool_input":{"file_path":"/benign.txt","path":"/classified/.env"}}' \
    parse_tool_file_path >/dev/null; then
    bad "parse_tool_file_path accepted conflicting non-empty aliases"
else
    ok "parse_tool_file_path rejects conflicting non-empty aliases"
fi

got=$(HOOK_INPUT='{"tool_name":"Agent","tool_response":{"output":"agent-result"}}' parse_tool_output)
printf '%s' "$got" | grep -q 'agent-result' \
    && ok "parse_tool_output Codex Agent result" \
    || bad "parse_tool_output Codex Agent result missing"

# --- parse_tool_content: Write .content + Edit/search_replace .new_string ---
got=$(HOOK_INPUT='{"toolInput":{"content":"body"}}' parse_tool_content)
eq "parse_tool_content Grok .content" "$got" "body"
got=$(HOOK_INPUT='{"tool_input":{"new_string":"repl"}}' parse_tool_content)
eq "parse_tool_content Claude .new_string" "$got" "repl"

# --- emit_deny: shape switches on Grok env, exits 0 both ways ---
out=$( (unset GROK_SESSION_ID GROK_HOOK_EVENT; emit_deny "m") ); rc=$?
d=$(printf '%s' "$out" | jq -r '.hookSpecificOutput.permissionDecision // empty')
eq "emit_deny Claude shape permissionDecision" "$d" "deny"
eq "emit_deny Claude shape exit 0" "$rc" "0"
[ "$(printf '%s' "$out" | jq -r '.decision // "none"')" = "none" ] \
    && ok "emit_deny Claude shape has NO top-level decision" \
    || bad "emit_deny Claude shape leaked a top-level decision key"

out=$( (GROK_SESSION_ID=abc emit_deny "m2") ); rc=$?
eq "emit_deny Grok shape decision"  "$(printf '%s' "$out" | jq -r '.decision')" "deny"
eq "emit_deny Grok shape reason"    "$(printf '%s' "$out" | jq -r '.reason')"   "m2"
eq "emit_deny Grok shape exit 0"    "$rc" "0"

# A serializer failure must not be converted into a successful empty hook result.
fake_bin=$(mktemp -d)
printf '#!/bin/sh\nexit 1\n' > "$fake_bin/jq"
chmod +x "$fake_bin/jq"
out=$( (PATH="$fake_bin:$PATH" emit_deny "must fail closed") 2>/dev/null ); rc=$?
rm -r -- "$fake_bin"
[ "$rc" -ne 0 ] && [ -z "$out" ] \
    && ok "emit_deny serializer failure exits nonzero with no false allow" \
    || bad "emit_deny serializer failure was masked (rc=$rc)"

# --- active_jsonl: Grok sessionId glob (only if a real grok session exists) ---
grok_dir=$(ls -td "$HOME"/.grok/sessions/*/[0-9a-f]*/ 2>/dev/null | head -1)
grok_dir="${grok_dir%/}"   # strip trailing slash so the join has no double '//'
if [ -n "$grok_dir" ] && [ -f "$grok_dir/chat_history.jsonl" ]; then
    sid=$(basename "$grok_dir")
    got=$(GROK_SESSION_ID="$sid" HOOK_INPUT='{}' active_jsonl)
    eq "active_jsonl Grok glob-by-sessionId" "$got" "$grok_dir/chat_history.jsonl"
else
    echo "  skip: no live grok session to test active_jsonl"
fi

echo "---"
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
