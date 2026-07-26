#!/bin/bash
# PostToolUse Bash hook: scan tool output for credential value patterns,
# in-place sanitize active jsonl if leak detected.
# Defense in depth #1: catches silent leaks even if Claude doesn't notice.

source "$(dirname "$0")/lib.sh"
source "$(dirname "$0")/credential_patterns.sh"

# Read stdin once; export for lib.sh functions (Codex compat — avoids double-consume).
HOOK_INPUT=$(cat)
export HOOK_INPUT

OUTPUT=$(parse_tool_output)
[ -z "$OUTPUT" ] && exit 0

JSONL=$(active_jsonl)
[ -z "$JSONL" ] && exit 0

LEAK_DETECTED=0
LEAK_SUMMARY=""
LEAK_PG_ROLES=""   # roles parsed from leaked DSNs (usernames, NOT secrets) for autorotate

for entry in "${PATTERNS[@]}"; do
    pattern="${entry%%|*}"
    replacement="${entry#*|}"

    # Check if output contains this pattern
    if echo "$OUTPUT" | grep -qE "$pattern"; then
        # Allow-list check (gh #13 fix): skip ONLY if EVERY match is a placeholder.
        # The old `head -1` check let a single placeholder poison the whole pattern,
        # so a REAL secret co-occurring with a placeholder escaped scrubbing. Now we
        # skip only when there is no non-placeholder (real) match.
        if ! echo "$OUTPUT" | grep -oE "$pattern" | grep -qvE "$ALLOWLIST_REGEX"; then
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
    if echo "$OUTPUT" | grep -oE "${KEYWORD_PATTERN}=${VALUE_PATTERN}" | grep -qvE "$ALLOWLIST_REGEX"; then
        sed -i -E "s#(${KEYWORD_PATTERN})=(${VALUE_PATTERN})#\1=<REDACTED>#g" "$JSONL"
        LEAK_DETECTED=1
        LEAK_SUMMARY="${LEAK_SUMMARY}\n  - pattern matched: keyword=value (env format)"
        hook_log "credential_value_scrub" "scrubbed keyword=value patterns in $JSONL"
    fi
fi

# YAML 形式: KEY: "value" または KEY: 'value'
if echo "$OUTPUT" | grep -qE "${KEYWORD_PATTERN}: [\"']${VALUE_PATTERN}"; then
    if echo "$OUTPUT" | grep -oE "${KEYWORD_PATTERN}: [\"']${VALUE_PATTERN}" | grep -qvE "$ALLOWLIST_REGEX"; then
        sed -i -E "s#(${KEYWORD_PATTERN}: [\"'])(${VALUE_PATTERN})#\1<REDACTED>#g" "$JSONL"
        LEAK_DETECTED=1
        LEAK_SUMMARY="${LEAK_SUMMARY}\n  - pattern matched: keyword: \"value\" (YAML format)"
        hook_log "credential_value_scrub" "scrubbed keyword: \"value\" patterns in $JSONL"
    fi
fi

if [ "$LEAK_DETECTED" -eq 1 ]; then
    # Step 2 (issue) — fire the follow-up DETACHED so gh latency never blocks this
    # hook. setsid + </dev/null + redirected fds = fully decoupled from the hook
    # process group; it self-dedups and is fail-safe. We do NOT wait on it.
    SESSION_ID=$(printf '%s' "$HOOK_INPUT" | jq -r '.session_id // .sessionId // empty' 2>/dev/null)
    FOLLOWUP="$(dirname "$0")/credential_leak_followup.sh"
    if [ -f "$FOLLOWUP" ]; then
        LEAK_SOURCE="value_scrub" \
        LEAK_DETAIL="$(printf '%b' "$LEAK_SUMMARY" | tr '\n' ' ' | sed 's/  */ /g')" \
        LEAK_REPLACED="see-log" \
        LEAK_TRANSCRIPT="$JSONL" \
        LEAK_SESSION_ID="$SESSION_ID" \
            setsid bash "$FOLLOWUP" </dev/null >/dev/null 2>&1 &
    fi

    # Step 4 (rotate) — SOURCE-TRUST gated (gh #41 refine, supersedes the blanket
    # human-ack which killed autonomous response). The self-DoS root is not "auto"
    # but the TRIGGER SOURCE: a DSN-shaped string in attacker-controllable output
    # (curl/WebFetch/MCP response, peer mailbox body, transcript) must never auto-
    # rotate the configured backend. We classify the PRODUCING COMMAND's trust here and pass
    # it to autorotate, which hard-blocks untrusted sources, auto-rotates trusted
    # ones, and falls back to a human-ack only for ambiguous sources.
    # Source-trust classification (gh #41, shared lib fn). AUTO requires a
    # positively-identified trusted-op (allowlist); untrusted/ambiguous never auto
    # without a human ack — so a denylist-evading external fetch cannot reach auto.
    CMD_TEXT=$(printf '%s' "$HOOK_INPUT" | jq -r '.tool_input.command // .toolInput.command // empty' 2>/dev/null)
    LEAK_TRUST=$(classify_leak_trust "$CMD_TEXT")
    AUTOROTATE="$(dirname "$0")/autorotate_leaked_cred.sh"
    if [ -f "$AUTOROTATE" ]; then
        for r in $(printf '%s' "$LEAK_PG_ROLES" | tr ' ' '\n' | sort -u); do
            [ -z "$r" ] && continue
            LEAK_ROLE="$r" LEAK_CLASS="pg_dsn" LEAK_SESSION_ID="$SESSION_ID" \
            LEAK_TRUST="$LEAK_TRUST" \
                setsid bash "$AUTOROTATE" </dev/null >/dev/null 2>&1 &
        done
    fi

    # Step 3 (resume) — terse context: the leak is ALREADY neutralized + logged,
    # so Claude should keep going rather than stop to do manual cleanup.
    if [ "${HARNESS_CREDENTIAL_LEAK_ISSUES:-0}" = "1" ] && [ -n "${CREDENTIAL_LEAK_ISSUE_REPO:-}" ]; then
        LAST_ISSUE_REF=$(cat "$HOME/.claude/state/credential_scrub/last_issue" 2>/dev/null)
        ISSUE_REF=""
        case "$LAST_ISSUE_REF" in
            "${CREDENTIAL_LEAK_ISSUE_REPO}#"*) ISSUE_REF=" (tracked in ${LAST_ISSUE_REF})" ;;
        esac
        INCIDENT_STATUS="incident issue filing is enabled${ISSUE_REF}"
    else
        INCIDENT_STATUS="incident issue filing is disabled; set HARNESS_CREDENTIAL_LEAK_ISSUES=1 and CREDENTIAL_LEAK_ISSUE_REPO=owner/repo to enable it"
    fi
    if [ -n "${HARNESS_AUTOROTATE_SCRIPT:-}" ] && [ -f "${HARNESS_AUTOROTATE_SCRIPT:-}" ]; then
        ROTATION_STATUS="rotation is SOURCE-TRUST gated — a trusted-source leak auto-rotates, an untrusted-source one (external fetch / mailbox / transcript) is REFUSED, an ambiguous one awaits a human ack"
    else
        ROTATION_STATUS="autorotation is disabled; set HARNESS_AUTOROTATE_SCRIPT to an audited runbook to enable SOURCE-TRUST-gated rotation"
    fi
    MSG="⚠️  credential leak detected: transcript sanitized in-place; ${INCIDENT_STATUS}. Transcript is safe to continue. NOTE: ${ROTATION_STATUS}."
    emit_context "PostToolUse" "$MSG"
fi
