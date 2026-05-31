#!/bin/bash
# Regression tests for the credential-leak prevent/catch layers.
# Covers the holes plugged 2026-05-31 (issues #6, #7, #10) plus existing guards.
#
# Run: bash plugins/harness-core/tests/test_credential_guard.sh
# No network, no real credentials — synthetic fixtures only.

set -u
HOOKS="$(cd "$(dirname "$0")/../hooks" && pwd)"
PASS=0
FAIL=0

ok()   { PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m %s\n' "$1"; }

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
expect_allow 'HRMTZ_ACK_CRED_READ=1 printenv MARS_POSTGRES_URL' '#10 ack-prefixed intentional read bypasses'

# --- existing guards still fire (no regression) ---
expect_block 'sops -d secrets.enc.yaml | head' 'existing: sops -d'
expect_block 'env | grep KEY' 'existing: env | grep'
expect_allow 'ls -la /tmp' 'existing: benign ls'
expect_allow 'git commit -m "fix postgresql://[^:/@]+:...@ self-match note"' 'existing: DSN-shaped text inside -m message is stripped'

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
