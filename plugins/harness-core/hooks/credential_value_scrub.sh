#!/bin/bash
# PostToolUse Bash hook: scan tool output for credential value patterns,
# in-place sanitize active jsonl if leak detected.
# Defense in depth #1: catches silent leaks even if Claude doesn't notice.

source "$(dirname "$0")/lib.sh"

OUTPUT=$(parse_tool_output)
[ -z "$OUTPUT" ] && exit 0

JSONL=$(active_jsonl)
[ -z "$JSONL" ] && exit 0

# ----------------------------------------
# Pattern catalog
# ----------------------------------------
# Each line is: <regex>|<replacement>
# Use distinctive markers to avoid clobbering normal text.
PATTERNS=(
    'POSTGRES_PASSWORD=[a-zA-Z0-9_!@#$%^&*-]+|POSTGRES_PASSWORD=<REDACTED>'
    'PGPASSWORD=[a-zA-Z0-9_!@#$%^&*-]+|PGPASSWORD=<REDACTED>'
    'sk-ant-[a-zA-Z0-9_-]{20,}|sk-ant-<REDACTED>'
    'sk-or-[a-zA-Z0-9_-]{20,}|sk-or-<REDACTED>'
    'sk_live_[a-zA-Z0-9]{20,}|sk_live_<REDACTED>'
    'tskey-[a-zA-Z0-9_-]{20,}|tskey-<REDACTED>'
    'AKIA[0-9A-Z]{16}|AKIA<REDACTED>'
    'TURSO_AUTH[A-Z_]*=eyJ[a-zA-Z0-9._-]+|TURSO_AUTH=<REDACTED>'
    'ANTHROPIC_API_KEY=sk-[a-zA-Z0-9_-]+|ANTHROPIC_API_KEY=<REDACTED>'
    'OPENAI_API_KEY=sk-[a-zA-Z0-9_-]+|OPENAI_API_KEY=<REDACTED>'
    'GEMINI_API_KEY=[a-zA-Z0-9_-]{20,}|GEMINI_API_KEY=<REDACTED>'
    'CF_API_TOKEN=[a-zA-Z0-9_-]{20,}|CF_API_TOKEN=<REDACTED>'
    'GITHUB_TOKEN=gh[ps]_[a-zA-Z0-9]{30,}|GITHUB_TOKEN=<REDACTED>'
)

# ----------------------------------------
# Allow-list (placeholder values that should NOT be scrubbed)
# ----------------------------------------
ALLOWLIST_REGEX='<REDACTED|placeholder|example|changeme|<your-key>|test-token|dummy|YOUR_'

LEAK_DETECTED=0
LEAK_SUMMARY=""

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
    fi
done

if [ "$LEAK_DETECTED" -eq 1 ]; then
    # Emit context warning so Claude is aware
    MSG="⚠️  credential_value_scrub: leak pattern detected in tool output, active session jsonl was sanitized in-place.${LEAK_SUMMARY}\n\nProcedure: (1) review which tool call leaked, (2) plan rotation for affected credential, (3) update memory \`feedback_credential_leak_5_incidents\` with new vector if novel."
    emit_context "PostToolUse" "$MSG"
fi
