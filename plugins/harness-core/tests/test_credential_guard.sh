#!/bin/bash
# Regression tests for the credential-leak prevent/catch layers.
# Covers the holes plugged 2026-05-31 (issues #6, #7, #10) plus existing guards.
#
# Run: bash plugins/harness-core/tests/test_credential_guard.sh
# No network, no real credentials — synthetic fixtures only.

set -uo pipefail
HOOKS="$(cd "$(dirname "$0")/../hooks" && pwd)"
PASS=0
FAIL=0

ok()   { PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m %s\n' "$1"; }

# Security fixtures must fail closed. A missing temporary directory previously made every Read
# setup fail while the suite still reported a full pass.
TEST_ROOT="$(mktemp -d)" || { echo "fixture setup failed: mktemp -d" >&2; exit 1; }
[ -d "$TEST_ROOT" ] && [ -w "$TEST_ROOT" ] \
    || { echo "fixture setup failed: temporary root is not writable" >&2; exit 1; }
trap 'rm -r -- "$TEST_ROOT"' EXIT

make_test_home() {
    local parent="$1" tmp_home
    tmp_home="$(mktemp -d "$parent/home.XXXXXX")" || return 1
    [ -d "$tmp_home" ] && [ -w "$tmp_home" ] || return 1
    printf '%s\n' "$tmp_home"
}

# ----------------------------------------------------------------------------
# Group 1: parse_tool_output is shape-agnostic (issue #7 load-bearing)
#   A leaked credential in an exit-non-zero / error-wrapped tool_response must
#   still be surfaced to the scanner — not silently dropped.
# ----------------------------------------------------------------------------
echo "== parse_tool_output shape-agnostic (#7) =="
# shellcheck disable=SC1091
source "$HOOKS/lib.sh"

DSN='postgresql://prs:s3cr3tpw@mars:5434/prs'
for shape_name in named_stderr bare_string error_field nested; do
    case "$shape_name" in
        named_stderr) HOOK_INPUT="{\"tool_response\":{\"stdout\":\"\",\"stderr\":\"psql: $DSN\"}}" ;;
        bare_string)  HOOK_INPUT="{\"tool_response\":\"connection refused: $DSN\"}" ;;
        error_field)  HOOK_INPUT="{\"tool_response\":{\"error\":\"failed: $DSN\"}}" ;;
        nested)       HOOK_INPUT="{\"tool_response\":{\"data\":{\"msg\":\"$DSN\"}}}" ;;
    esac
    export HOOK_INPUT
    out=$(parse_tool_output)
    if echo "$out" | grep -qF "s3cr3tpw"; then
        ok "leak surfaced for shape=$shape_name"
    else
        bad "leak DROPPED for shape=$shape_name (silent bypass)"
    fi
done
unset HOOK_INPUT

# ----------------------------------------------------------------------------
# Group 2: bash_command_guard prevent layer
# ----------------------------------------------------------------------------
echo "== bash_command_guard prevent layer =="

# Helper: returns 0 (deny) if the guard blocks the command, 1 (allow) otherwise.
guard_blocks() {
    local cmd="$1"
    local res
    res=$(printf '%s' "{\"tool_input\":{\"command\":$(printf '%s' "$cmd" | jq -Rs .)}}" \
        | bash "$HOOKS/bash_command_guard.sh" 2>/dev/null)
    echo "$res" | grep -q '"permissionDecision": "deny"'
}

expect_block() { if guard_blocks "$1"; then ok "BLOCK: $2"; else bad "should BLOCK: $2 -> [$1]"; fi; }
expect_allow() { if guard_blocks "$1"; then bad "should ALLOW: $2 -> [$1]"; else ok "ALLOW: $2"; fi; }

# --- #6: DSN-with-creds-in-argv ---
expect_block 'psql postgresql://prs:s3cr3tpw@mars:5434/db -tAc "select 1"' '#6 password DSN in psql argv'
expect_block 'echo redis://u:p4ss@cache:6379 | nc x' '#6 redis DSN with creds'
expect_allow 'sops exec-env pg.enc.yaml '\''psql "$POSTGRES_URL" -tAc "select 1"'\''' '#6 env-expanded DSN (no literal pw)'
expect_allow 'psql postgresql://mars:5434/db -tAc "select 1"' '#6 DSN without password (no creds)'

# --- #10: printenv / set target-agnostic ---
expect_block 'printenv MARS_POSTGRES_URL' '#10 printenv keyword-free secret var'
expect_block 'printenv | grep -i postgres' '#10 printenv piped to filter'
expect_block 'set | rg postgres' '#10 set piped to non-keyword filter'
expect_allow 'set -e' '#10 set -e is not an env dump'
expect_allow 'set -o pipefail' '#10 set -o pipefail is not an env dump'
expect_block 'HARNESS_ACK_CRED_READ=1 printenv MARS_POSTGRES_URL' '#108 read ACK does not authorize env output'

bash_guard_failure_fails_closed() {
    local payload="$1" command_name="${2:-}" tmp_home fake_bin out rc
    tmp_home="$(make_test_home "$TEST_ROOT")" || return 1
    if [ -n "$command_name" ]; then
        fake_bin="$(mktemp -d "$TEST_ROOT/bash-fake-bin.XXXXXX")" || return 1
        printf '#!/bin/sh\nexit 1\n' > "$fake_bin/$command_name"
        chmod +x "$fake_bin/$command_name"
        out="$(printf '%s' "$payload" \
            | PATH="$fake_bin:$PATH" HOME="$tmp_home" \
                bash "$HOOKS/bash_command_guard.sh" 2>/dev/null)"
    else
        out="$(printf '%s' "$payload" \
            | HOME="$tmp_home" bash "$HOOKS/bash_command_guard.sh" 2>/dev/null)"
    fi
    rc=$?
    [ "$rc" -ne 0 ] && [ -z "$out" ]
}

if bash_guard_failure_fails_closed '{"tool_input":{"command":"printenv MARS_POSTGRES_URL"}}' jq; then
    ok "Bash command parser dependency failure is nonzero, never a clean allow"
else
    bad "Bash command parser dependency failure was masked"
fi
if bash_guard_failure_fails_closed ''; then
    ok "empty Bash hook input is nonzero, never a clean allow"
else
    bad "empty Bash hook input was accepted"
fi
if bash_guard_failure_fails_closed '{"tool_input":{"command":123}}'; then
    ok "non-string Bash command fails closed"
else
    bad "non-string Bash command was accepted"
fi

# --- #36: bare relative .env + non-enumerated readers (cross-family hole) ---
expect_block 'cat .env' '#36 bare relative cat .env'
expect_block 'grep KEY .env' '#36 bare relative grep KEY .env'
expect_block 'python3 -c '\''open(".env").read()'\''' '#36 non-enumerated reader (python open)'
expect_block 'node -e "require('\''fs'\'').readFileSync('\''.env'\'')"' '#36 non-enumerated reader (node)'
expect_block 'cat .env.local' '#36 relative .env.<suffix>'
expect_block 'less ./.env.prod' '#36 dot-slash relative .env.prod'
expect_block 'cat credentials.json' '#36 bare relative credentials.<ext>'
expect_block 'python3 -c '\''open("rclone.conf").read()'\''' '#36 read-guard parity: non-enumerated rclone.conf'
expect_block 'python3 -c '\''open(".netrc").read()'\''' '#36 read-guard parity: non-enumerated .netrc'
expect_block 'python3 -c '\''open(".aws/credentials").read()'\''' '#36 read-guard parity: non-enumerated .aws/credentials'
expect_block 'python3 -c '\''open(".cloudflared/tunnel.json").read()'\''' '#36 read-guard parity: non-enumerated cloudflared json'
expect_block 'python3 -c '\''open("id_ed25519.pem").read()'\''' '#36 read-guard parity: non-enumerated private key'
expect_block 'python3 -c '\''open("client.pfx").read()'\''' '#36 read-guard parity: non-enumerated pfx'
expect_allow 'cat environment.md' '#36 environment.md is not .env (no false positive)'
expect_allow 'source ./venv/bin/activate' '#36 venv path substring env is not .env'
expect_allow 'echo "loading credentials"' '#36 prose credentials (no ext) is not a file operand'
expect_allow 'cat .environment' '#36 .environment dotfile is not .env'
expect_block 'HARNESS_ACK_CRED_READ=1 cat .env' '#108 read ACK does not authorize file output'

# --- #36 REVISE HIGH: obfuscated token-construction bypasses are de-obfuscated ---
expect_block 'cat .e"nv"' '#36 quote-splice bypass (cat .e"nv")'
expect_block 'cat '\''.e'\''"nv"' '#36 mixed-quote-splice bypass'
expect_block 'cat ${PWD}/.e${X:-nv}' '#36 param-expansion bypass (${X:-nv})'
expect_block "cat \$'\\056env'" '#36 ANSI-C \056 octal-dot bypass'
expect_block "cat \$'\\x2eenv'" '#36 ANSI-C \x2e hex-dot bypass'
# codex round-2 HIGH: ${VAR-default} / ${VAR:=default} are the same token family
expect_block 'cat .e${X-nv}' '#36 r2 param-default ${X-nv} (no colon) bypass'
expect_block 'cat ${PWD}/.e${X:=nv}' '#36 r2 param-assign ${X:=nv} bypass'
expect_block 'cat .e${X:+nv}' '#36 r2 param-alt ${X:+nv} bypass'
# RESIDUAL (documented, out of scope for a non-parser guard): genuine string
# concatenation is NOT reconstructed; the value-scrub + autorotate layers cover it.
expect_allow 'python3 -c '\''open("."+"env").read()'\''' '#36 RESIDUAL: token concat not decoded (defence-in-depth, not a parser)'

# --- #36 REVISE MED: pure-metadata verbs on the cred path are not over-blocked ---
expect_allow 'test -f .env' '#36 metadata test -f .env'
expect_allow '[ -f .env ]' '#36 metadata [ -f .env ]'
expect_allow 'ls -la .env' '#36 metadata ls .env'
expect_allow 'stat .env' '#36 metadata stat .env'
expect_allow 'find . -name .env -type f' '#36 metadata find -name .env'
expect_allow 'git status -- .env' '#36 metadata git status -- .env'
# metadata verb must NOT launder a chained / -exec read of the cred file
expect_block 'ls .env && cat .env' '#36 chained read after metadata verb still blocks'
expect_block 'find . -name .env -exec cat {} +' '#36 find -exec read still blocks'
expect_block 'ls .env | xargs cat' '#36 piped read after metadata verb still blocks'
# codex round-2 MED: metadata via `env` prefix, and literal-print verbs, are not over-blocked
expect_allow 'env FOO=1 ls .env' '#36 r2 metadata via env prefix'
expect_allow 'echo "loading .env"' '#36 r2 echo literal .env is not a read'
expect_allow 'printf "%s\n" .env' '#36 r2 printf literal .env is not a read'
# …but echo/printf must not launder a command-substitution read
expect_block 'echo $(cat .env)' '#36 r2 echo $(cat .env) command-subst read still blocks'
# codex round-3 HIGH: no-space input redirection `<` reads the file (`<` as leading boundary)
expect_block 'cat<.env' '#36 r3 no-space redirect cat<.env'
expect_block 'grep KEY<.env' '#36 r3 no-space redirect grep KEY<.env'
expect_block 'awk '\''{print}'\''<.env' '#36 r3 no-space redirect awk<.env'
expect_block 'sed -n p<.env' '#36 r3 no-space redirect sed<.env'

# --- #108: legacy read ACK is never a Bash plaintext-output capability ---
expect_block 'HARNESS_ACK_CRED_READ=1 sops -d secrets.enc.yaml' '#108 ACK does not bypass sops policy'
expect_block 'HARNESS_ACK_CRED_READ=1 printenv MARS_POSTGRES_URL' '#108 ACK does not bypass value output'
expect_allow 'HARNESS_ACK_CRED_READ=1 ls -la /tmp' '#108 inert prefix does not block benign commands'

# --- existing guards still fire (no regression) ---
expect_block 'sops -d secrets.enc.yaml | head' 'existing: sops -d'
expect_block 'env | grep KEY' 'existing: env | grep'
expect_allow 'ls -la /tmp' 'existing: benign ls'
expect_allow 'git commit -m "fix postgresql://[^:/@]+:...@ self-match note"' 'existing: DSN-shaped text inside -m message is stripped'

# ----------------------------------------------------------------------------
# Group 2b: credential_file_read_guard parity for template files
# ----------------------------------------------------------------------------
echo "== credential_file_read_guard template exemption =="
run_read_guard() {
    local payload="$1" family="${2:-claude}"
    local hook="${3:-$HOOKS/credential_file_read_guard.sh}"
    local tmp_home err_file
    tmp_home="$(make_test_home "$TEST_ROOT")" \
        || return 2
    err_file="$tmp_home/guard.stderr"
    if [ "$family" = "grok" ]; then
        READ_OUT="$(printf '%s' "$payload" \
            | GROK_SESSION_ID=fixture HOME="$tmp_home" bash "$hook" 2>"$err_file")"
    else
        READ_OUT="$(printf '%s' "$payload" \
            | HOME="$tmp_home" bash "$hook" 2>"$err_file")"
    fi
    READ_RC=$?
}

# Returns 0 only for one family-correct deny JSON with exit 0, 1 only for a clean
# allow (exit 0 and no output), and 2 for every execution/envelope failure.
read_guard_payload_result() {
    local payload="$1" family="${2:-claude}" hook="${3:-$HOOKS/credential_file_read_guard.sh}"
    run_read_guard "$payload" "$family" "$hook" || return 2
    [ "$READ_RC" -eq 0 ] || return 2
    [ -n "$READ_OUT" ] || return 1
    [ "$(printf '%s' "$READ_OUT" | jq -s 'length' 2>/dev/null)" -eq 1 ] 2>/dev/null \
        || return 2
    if [ "$family" = "grok" ]; then
        printf '%s' "$READ_OUT" | jq -e '
            .decision == "deny"
            and (.reason | contains("READ_ACK_DOES_NOT_AUTHORIZE_OUTPUT"))
        ' >/dev/null 2>&1 || return 2
    else
        printf '%s' "$READ_OUT" | jq -e '
            .hookSpecificOutput.hookEventName == "PreToolUse"
            and .hookSpecificOutput.permissionDecision == "deny"
            and (.hookSpecificOutput.permissionDecisionReason
                 | contains("READ_ACK_DOES_NOT_AUTHORIZE_OUTPUT"))
        ' >/dev/null 2>&1 || return 2
    fi
    return 0
}

read_guard_blocks() {
    local path="$1" payload
    payload="{\"tool_input\":{\"file_path\":$(printf '%s' "$path" | jq -Rs .)}}"
    read_guard_payload_result "$payload"
}

expect_read_block() {
    read_guard_blocks "$1"
    case $? in
        0) ok "READ BLOCK: $2" ;;
        1) bad "Read should BLOCK: $2 -> [$1]" ;;
        *) bad "Read guard execution/envelope failure: $2 -> [$1]" ;;
    esac
}

expect_read_allow() {
    read_guard_blocks "$1"
    case $? in
        0) bad "Read should ALLOW: $2 -> [$1]" ;;
        1) ok "READ ALLOW: $2" ;;
        *) bad "Read guard execution/envelope failure: $2 -> [$1]" ;;
    esac
}

expect_read_allow '/tmp/proj/.env.example' 'template .env.example'
expect_read_allow '/tmp/proj/.env.sample' 'template .env.sample'
expect_read_allow '/tmp/proj/.env.template' 'template .env.template'
expect_read_allow '/tmp/proj/.env.dist' 'template .env.dist'
expect_read_allow '/tmp/proj/.env.test' 'template .env.test'
expect_read_allow '/tmp/proj/.env.local-example' 'template .env.local-example'
expect_read_block '/tmp/proj/.env' 'real .env'
expect_read_block '/tmp/proj/.env.production' 'real .env.production'
expect_read_block '/tmp/proj/credentials.txt' 'credential artifact'
expect_read_block '/tmp/proj/.env.example.bak' 'template suffix not final segment'

expect_payload_block() {
    read_guard_payload_result "$1" "${3:-claude}"
    case $? in
        0) ok "$2" ;;
        1) bad "$2 escaped" ;;
        *) bad "$2 hit an execution/envelope failure" ;;
    esac
}

expect_payload_failure() {
    read_guard_payload_result "$1" "${3:-claude}"
    case $? in
        2) ok "$2" ;;
        0) bad "$2 unexpectedly produced a normal deny instead of parser failure" ;;
        1) bad "$2 became a clean allow" ;;
    esac
}

expect_payload_allow() {
    read_guard_payload_result "$1" "${3:-claude}"
    case $? in
        0) bad "$2 was unexpectedly blocked" ;;
        1) ok "$2" ;;
        *) bad "$2 hit an execution/envelope failure" ;;
    esac
}

expect_payload_block '{"tool_name":"mcp__filesystem__read_file","tool_input":{"path":"/tmp/proj/.env"}}' "MCP .path credential read blocks"
expect_payload_block '{"tool_name":"mcp__filesystem__read_file","tool_input":{"path":".env"}}' "MCP relative credential read blocks"
expect_payload_block '{"tool_name":"mcp__filesystem__read_text_file","tool_input":{"uri":"file:///tmp/proj/%2Eenv"}}' "MCP file URI credential read blocks after decode"
expect_payload_block '{"tool_name":"mcp__filesystem__read_text_file","tool_input":{"uri":"FILE:///tmp/proj/%2Eenv"}}' "uppercase local file URI credential read blocks"
expect_payload_block '{"tool_name":"mcp__filesystem__read_text_file","tool_input":{"uri":"file://LOCALHOST/tmp/proj/%2Eenv"}}' "uppercase localhost file URI credential read blocks"
expect_payload_failure '{"tool_name":"mcp__filesystem__read_text_file","tool_input":{"uri":"file:///tmp/%2Eenv%00.example"}}' "NUL-bearing local file URI fails closed before shell coercion"
expect_payload_allow '{"tool_name":"mcp__filesystem__read_file","tool_input":{"path":"/tmp/proj/README.md"}}' "benign MCP read allowed"
expect_payload_block '{"toolName":"read_file","toolInput":{"path":"/tmp/proj/.env"}}' "Grok camel-case credential read emits top-level deny" grok
expect_payload_block '{"tool_input":{"file_path":"","path":"/tmp/proj/.env"}}' "empty snake-case primary alias falls through to classified path"
expect_payload_block '{"toolInput":{"file_path":"  ","path":"/tmp/proj/.env"}}' "blank camel-case primary alias falls through to classified path" grok
expect_payload_failure '{"tool_input":{"file_path":"/tmp/proj/README.md","path":"/tmp/proj/.env"}}' "conflicting snake-case path aliases fail closed"
expect_payload_failure '{"toolInput":{"path":"/tmp/proj/README.md","uri":"file:///tmp/proj/.env"}}' "conflicting camel-case path aliases fail closed" grok
expect_payload_failure '{"tool_input":{"file_path":123}}' "numeric path alias fails closed"
expect_payload_failure '{"tool_input":{"file_path":false}}' "boolean path alias fails closed"
expect_payload_failure '{"tool_input":{"file_path":[]}}' "array path alias fails closed"
expect_payload_failure '{"tool_input":{"file_path":{}}}' "object path alias fails closed"

below_limit_payload="$(python3 -c 'import json; print(json.dumps({"tool_input":{"file_path":"/tmp/proj/.env","padding":"x"*3000000}}))')"
expect_payload_block "$below_limit_payload" "below-limit large payload still emits one deny JSON"
unset below_limit_payload
above_limit_payload="$(python3 -c 'import json; print(json.dumps({"tool_input":{"file_path":"/tmp/proj/.env","padding":"x"*4500000}}))')"
expect_payload_failure "$above_limit_payload" "above-limit payload fails closed before parsing"
unset above_limit_payload

# Exact #108 incident regression: a fresh legacy marker must neither authorize the Read nor add
# another stdout document. The marker is ignored and remains non-authoritative.
marker_home="$(make_test_home "$TEST_ROOT")" \
    || { echo "fixture HOME setup failed for marker regression" >&2; exit 1; }
mkdir -p "$marker_home/.claude/state"
touch "$marker_home/.claude/state/cred_read_ack"
marker_out="$(printf '%s' '{"tool_input":{"file_path":"/tmp/proj/.env"}}' \
    | HOME="$marker_home" bash "$HOOKS/credential_file_read_guard.sh" 2>/dev/null)"
marker_rc=$?
if printf '%s' "$marker_out" | jq -e '
    .hookSpecificOutput.permissionDecision == "deny"
    and (.hookSpecificOutput.permissionDecisionReason
         | contains("READ_ACK_DOES_NOT_AUTHORIZE_OUTPUT"))
' >/dev/null 2>&1 \
   && [ "$marker_rc" -eq 0 ] \
   && [ "$(printf '%s' "$marker_out" | jq -s 'length')" -eq 1 ] \
   && [ -f "$marker_home/.claude/state/cred_read_ack" ]; then
    ok "#108 deny -> fresh read marker -> one structured deny, marker non-authoritative"
else
    bad "#108 fresh read marker exposed output or broke the single-JSON deny contract"
fi

if make_test_home "$TEST_ROOT/does-not-exist" >/dev/null 2>&1; then
    bad "fixture setup helper accepted an unusable temporary parent"
else
    ok "fixture setup failure is nonzero (cannot false-green Read assertions)"
fi

if read_guard_payload_result '{"tool_input":{"path":"/tmp/proj/README.md"}}' claude "$HOOKS/does-not-exist"; then
    bad "missing guard executable was accepted as a valid result"
else
    [ $? -eq 2 ] \
        && ok "missing guard executable is an execution failure, never a clean allow" \
        || bad "missing guard executable did not return the fail-closed test status"
fi

dependency_failure_fails_closed() {
    local command_name="$1" payload="$2" tmp_home fake_bin out rc
    tmp_home="$(make_test_home "$TEST_ROOT")" || return 1
    fake_bin="$(mktemp -d "$TEST_ROOT/fake-bin.XXXXXX")" || return 1
    printf '#!/bin/sh\nexit 1\n' > "$fake_bin/$command_name"
    chmod +x "$fake_bin/$command_name"
    out="$(printf '%s' "$payload" \
        | PATH="$fake_bin:$PATH" HOME="$tmp_home" \
            bash "$HOOKS/credential_file_read_guard.sh" 2>/dev/null)"
    rc=$?
    [ "$rc" -ne 0 ] && [ -z "$out" ]
}

if dependency_failure_fails_closed jq '{"tool_input":{"path":"/tmp/proj/.env"}}'; then
    ok "path parser dependency failure is nonzero, never a clean allow"
else
    bad "path parser dependency failure was masked"
fi
if dependency_failure_fails_closed python3 '{"tool_input":{"uri":"file:///tmp/proj/%2Eenv"}}'; then
    ok "local URI decoder failure is nonzero, never a clean allow"
else
    bad "local URI decoder failure was masked"
fi

empty_out="$(HOME="$(make_test_home "$TEST_ROOT")" \
    bash "$HOOKS/credential_file_read_guard.sh" </dev/null 2>/dev/null)"
empty_rc=$?
if [ "$empty_rc" -ne 0 ] && [ -z "$empty_out" ]; then
    ok "empty hook input is nonzero, never a clean allow"
else
    bad "empty hook input was accepted (rc=$empty_rc)"
fi

multi_out="$(printf '%s' '{}{}' \
    | HOME="$(make_test_home "$TEST_ROOT")" \
        bash "$HOOKS/credential_file_read_guard.sh" 2>/dev/null)"
multi_rc=$?
if [ "$multi_rc" -ne 0 ] && [ -z "$multi_out" ]; then
    ok "multiple JSON values are nonzero, never a clean allow"
else
    bad "multiple JSON values were accepted (rc=$multi_rc)"
fi

# ----------------------------------------------------------------------------
# Group 3: value_scrub allowlist skips catalog self-match (issue #7 tertiary)
# ----------------------------------------------------------------------------
echo "== value_scrub allowlist (#7 self-match) =="
ALLOWLIST_REGEX='<REDACTED|placeholder|example|changeme|<your-key>|test-token|dummy|YOUR_|\[\^|\[:space:\]'
catalog_text='postgresql://[^:/@[:space:]]+:[^@[:space:]]+@'
real_dsn='postgresql://prs:s3cr3tpw@mars'
if echo "$catalog_text" | grep -qE "$ALLOWLIST_REGEX"; then ok "catalog regex form is allowlisted"; else bad "catalog regex form NOT allowlisted (self-match noise persists)"; fi
if echo "$real_dsn" | grep -qE "$ALLOWLIST_REGEX"; then bad "real DSN wrongly allowlisted!"; else ok "real DSN is NOT allowlisted (still scrubbed)"; fi

# ----------------------------------------------------------------------------
echo
printf 'RESULT: %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
