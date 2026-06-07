#!/bin/bash
# PostToolUse Bash hook: scan tool output for credential value patterns,
# in-place sanitize active jsonl if leak detected.
# Defense in depth #1: catches silent leaks even if Claude doesn't notice.

source "$(dirname "$0")/lib.sh"

# Read stdin once; export for lib.sh functions (Codex compat — avoids double-consume).
HOOK_INPUT=$(cat)
export HOOK_INPUT

OUTPUT=$(parse_tool_output)
[ -z "$OUTPUT" ] && exit 0

JSONL=$(active_jsonl)
[ -z "$JSONL" ] && exit 0

# ----------------------------------------
# Pattern catalog — each line: <regex>|<replacement>
# ----------------------------------------
# Part 1: value-prefix patterns (先頭文字列で識別できるもの)
# ----------------------------------------
PATTERNS=(
    'sk-ant-[a-zA-Z0-9_-]{20,}|sk-ant-<REDACTED>'
    'sk-or-[a-zA-Z0-9_-]{20,}|sk-or-<REDACTED>'
    'sk_live_[a-zA-Z0-9]{20,}|sk_live_<REDACTED>'
    'tskey-[a-zA-Z0-9_-]{20,}|tskey-<REDACTED>'
    'AKIA[0-9A-Z]{16}|AKIA<REDACTED>'
    'cfut_[A-Za-z0-9_-]{20,}|cfut_<REDACTED>'
    'ghp_[a-zA-Z0-9]{30,}|ghp_<REDACTED>'
    'ghs_[a-zA-Z0-9]{30,}|ghs_<REDACTED>'
    'postgresql://[^:/@[:space:]]+:[^@[:space:]]+@|postgresql://<REDACTED>:<REDACTED>@'
    'postgres://[^:/@[:space:]]+:[^@[:space:]]+@|postgres://<REDACTED>:<REDACTED>@'
    'mysql://[^:/@[:space:]]+:[^@[:space:]]+@|mysql://<REDACTED>:<REDACTED>@'
    'mongodb://[^:/@[:space:]]+:[^@[:space:]]+@|mongodb://<REDACTED>:<REDACTED>@'
    'mongodb\+srv://[^:/@[:space:]]+:[^@[:space:]]+@|mongodb+srv://<REDACTED>:<REDACTED>@'
    'redis://[^:/@[:space:]]+:[^@[:space:]]+@|redis://<REDACTED>:<REDACTED>@'
    'amqp://[^:/@[:space:]]+:[^@[:space:]]+@|amqp://<REDACTED>:<REDACTED>@'
    'libsql://[^:/@[:space:]]+:[^@[:space:]]+@|libsql://<REDACTED>:<REDACTED>@'
)

# Part 2: キーワードベース catch-all
# [A-Z_]*(TOKEN|SECRET|KEY|PASSWORD|...) = 16文字以上の乱数っぽい羅列 → 値だけ REDACTED
# env 形式 (KEY=value) と YAML 形式 (KEY: "value") の両方に対応
KEYWORD_PATTERN='[A-Z_]*(TOKEN|SECRET|KEY|PASSWORD|CREDENTIAL|PWD|AUTH|CERT|PRIVATE)[A-Z_]*'
VALUE_PATTERN='[a-zA-Z0-9_/+=.:-]{16,}'

# ----------------------------------------
# Allow-list (placeholder values that should NOT be scrubbed)
# ----------------------------------------
# `\[\^` / `\[:space:\]` / `[^@[:` skip this hook's OWN pattern-catalog text
# (issue #7 tertiary): reading/grepping the catalog via Bash surfaces the
# DSN-shaped regexes (`postgresql://[^:/@[:space:]]+:...@`) which self-match and
# trigger a harmless-but-noisy in-place scrub. Real credentials never contain a
# `[^` char-class or a `[:space:]` POSIX class, so this is a safe discriminator.
ALLOWLIST_REGEX='<REDACTED|placeholder|example|changeme|<your-key>|test-token|dummy|YOUR_|\[\^|\[:space:\]'

LEAK_DETECTED=0
LEAK_SUMMARY=""
LEAK_PG_ROLES=""   # roles parsed from leaked DSNs (usernames, NOT secrets) for autorotate

for entry in "${PATTERNS[@]}"; do
    pattern="${entry%%|*}"
    replacement="${entry#*|}"

    # Check if output contains this pattern
    if echo "$OUTPUT" | grep -qE "$pattern"; then
        # Get matched value (without printing it to log)
        matched=$(echo "$OUTPUT" | grep -oE "$pattern" | head -1)

        # Allow-list check: skip if matched value is a placeholder
        if echo "$matched" | grep -qE "$ALLOWLIST_REGEX"; then
            continue
        fi

        # Sanitize active jsonl in place
        sed -i -E "s|${pattern}|${replacement}|g" "$JSONL"
        LEAK_DETECTED=1
        # Log without exposing the value
        prefix=$(echo "$pattern" | head -c 30)
        LEAK_SUMMARY="${LEAK_SUMMARY}\n  - pattern matched: ${prefix}..."
        hook_log "credential_value_scrub" "scrubbed pattern in $JSONL (pattern prefix: ${prefix}...)"

        # Parse role (username) from leaked PG DSNs for autonomous rotation (step 4).
        # Only the role name is extracted — never the password. Handles multi-role leaks.
        case "$pattern" in
            postgresql://*|postgres://*)
                _roles=$(echo "$OUTPUT" | grep -oE "$pattern" \
                    | sed -E 's#^postgres(ql)?://([^:]+):.*#\2#' | sort -u)
                LEAK_PG_ROLES="$LEAK_PG_ROLES $_roles" ;;
        esac
    fi
done

# ----------------------------------------
# Part 2: キーワードベース catch-all
# ----------------------------------------
# env 形式: KEY=value
if echo "$OUTPUT" | grep -qE "${KEYWORD_PATTERN}=${VALUE_PATTERN}"; then
    if ! echo "$OUTPUT" | grep -oE "${KEYWORD_PATTERN}=${VALUE_PATTERN}" | grep -qE "$ALLOWLIST_REGEX"; then
        sed -i -E "s|(${KEYWORD_PATTERN})=(${VALUE_PATTERN})|\1=<REDACTED>|g" "$JSONL"
        LEAK_DETECTED=1
        LEAK_SUMMARY="${LEAK_SUMMARY}\n  - pattern matched: keyword=value (env format)"
        hook_log "credential_value_scrub" "scrubbed keyword=value patterns in $JSONL"
    fi
fi

# YAML 形式: KEY: "value" または KEY: 'value'
if echo "$OUTPUT" | grep -qE "${KEYWORD_PATTERN}: [\"']${VALUE_PATTERN}"; then
    if ! echo "$OUTPUT" | grep -oE "${KEYWORD_PATTERN}: [\"']${VALUE_PATTERN}" | grep -qE "$ALLOWLIST_REGEX"; then
        sed -i -E "s|(${KEYWORD_PATTERN}: [\"'])(${VALUE_PATTERN})|\1<REDACTED>|g" "$JSONL"
        LEAK_DETECTED=1
        LEAK_SUMMARY="${LEAK_SUMMARY}\n  - pattern matched: keyword: \"value\" (YAML format)"
        hook_log "credential_value_scrub" "scrubbed keyword: \"value\" patterns in $JSONL"
    fi
fi

if [ "$LEAK_DETECTED" -eq 1 ]; then
    # Step 2 (issue) — fire the follow-up DETACHED so gh latency never blocks this
    # hook. setsid + </dev/null + redirected fds = fully decoupled from the hook
    # process group; it self-dedups and is fail-safe. We do NOT wait on it.
    SESSION_ID=$(printf '%s' "$HOOK_INPUT" | jq -r '.session_id // empty' 2>/dev/null)
    FOLLOWUP="$(dirname "$0")/credential_leak_followup.sh"
    if [ -f "$FOLLOWUP" ]; then
        LEAK_SOURCE="value_scrub" \
        LEAK_DETAIL="$(printf '%b' "$LEAK_SUMMARY" | tr '\n' ' ' | sed 's/  */ /g')" \
        LEAK_REPLACED="see-log" \
        LEAK_TRANSCRIPT="$JSONL" \
        LEAK_SESSION_ID="$SESSION_ID" \
            setsid bash "$FOLLOWUP" </dev/null >/dev/null 2>&1 &
    fi

    # Step 4 (rotate) — close the loop: rotate the leaked credential, DETACHED so
    # rotation latency never blocks this hook. autorotate_leaked_cred.sh policy:
    # non-prod PG role → autonomous rotate + distribute to all edges; prod role /
    # API key → escalate (needs rolling redeploy / provider-side rotation).
    AUTOROTATE="$(dirname "$0")/autorotate_leaked_cred.sh"
    if [ -f "$AUTOROTATE" ]; then
        for r in $(printf '%s' "$LEAK_PG_ROLES" | tr ' ' '\n' | sort -u); do
            [ -z "$r" ] && continue
            LEAK_ROLE="$r" LEAK_CLASS="pg_dsn" LEAK_SESSION_ID="$SESSION_ID" \
                setsid bash "$AUTOROTATE" </dev/null >/dev/null 2>&1 &
        done
    fi

    # Step 3 (resume) — terse context: the leak is ALREADY neutralized + logged,
    # so Claude should keep going rather than stop to do manual cleanup.
    LAST_ISSUE=$(cat "$HOME/.claude/state/credential_scrub/last_issue" 2>/dev/null)
    ISSUE_REF=""
    [ -n "$LAST_ISSUE" ] && ISSUE_REF=" (tracked in claude-harness#${LAST_ISSUE})"
    MSG="⚠️  credential leak auto-handled: transcript sanitized in-place + incident logged to the claude-harness \`credential-leak\` issue${ISSUE_REF}. No manual steps needed — continue your current task. Rotation for the affected credential is tracked in that issue."
    emit_context "PostToolUse" "$MSG"
fi
