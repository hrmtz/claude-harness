#!/bin/bash
# credential_leak_followup.sh — post-leak automation, step 2 of 3.
#
# Chain: a scrubber detects + sanitizes a credential leak (step 1) → invokes this
# script DETACHED (setsid) so gh latency never blocks the PostToolUse hook → this
# files/appends a GitHub incident issue (step 2) → the scrubber's terse "resume"
# context lets Claude keep working (step 3).
#
# Design invariants:
#   - NEVER includes a credential value. Inputs are pattern prefixes / key names
#     only (the calling scrubbers pass non-sensitive metadata by construction).
#   - Public-safe default: gh issue filing is disabled unless the target repo is
#     configured explicitly.
#   - Fail-safe: gh absent / unauthed / network down / repo unreachable → log + exit 0.
#     A failed issue filing must never disrupt the session.
#   - Dedup: one rolling issue per repo (label `credential-leak`); per-session +
#     per-pattern marker prevents comment spam when the same value re-prints.
#
# Invoked (detached) by:
#   - credential_value_scrub.sh  (regex scrubber)
#   - credential_scrub.py        (hash scrubber, via subprocess)
#
# Inputs (env):
#   LEAK_SOURCE       which scrubber fired (value_scrub | hash_scrub)
#   LEAK_DETAIL       non-sensitive descriptor (pattern prefixes / key names)
#   LEAK_REPLACED     replacement count (string ok)
#   LEAK_TRANSCRIPT   transcript path that was sanitized
#   LEAK_SESSION_ID   session id (for dedup + audit)
#   CREDENTIAL_LEAK_ISSUE_REPO   target repo for rolling incident issue, e.g. owner/repo
#   HARNESS_CREDENTIAL_LEAK_ISSUES=1  opt in to issue filing

set -u

REPO="${CREDENTIAL_LEAK_ISSUE_REPO:-}"
ISSUES_ENABLED="${HARNESS_CREDENTIAL_LEAK_ISSUES:-0}"
STATE_DIR="$HOME/.claude/state/credential_scrub"
FILED_DIR="$STATE_DIR/filed"
LOG_DIR="$HOME/.claude/state/hook_logs"
mkdir -p "$FILED_DIR" "$LOG_DIR" 2>/dev/null

_log() {
    echo "[$(date +%F_%T)] [credential_leak_followup] $*" >> "$LOG_DIR/hooks.log" 2>/dev/null
}

LEAK_SOURCE="${LEAK_SOURCE:-unknown}"
LEAK_DETAIL="${LEAK_DETAIL:-(unspecified)}"
LEAK_REPLACED="${LEAK_REPLACED:-?}"
LEAK_TRANSCRIPT="${LEAK_TRANSCRIPT:-}"
LEAK_SESSION_ID="${LEAK_SESSION_ID:-unknown}"

if [ "$ISSUES_ENABLED" != "1" ] || [ -z "$REPO" ]; then
    _log "incident issue filing disabled; set HARNESS_CREDENTIAL_LEAK_ISSUES=1 and CREDENTIAL_LEAK_ISSUE_REPO=owner/repo to enable"
    exit 0
fi

# ----------------------------------------------------------------------------
# Dedup: hash(session + detail) → one comment per leak class per session.
# Always log; only the gh issue write is suppressed on repeat.
# ----------------------------------------------------------------------------
DEDUP_KEY=$(printf '%s|%s' "$LEAK_SESSION_ID" "$LEAK_DETAIL" \
    | { command -v sha256sum >/dev/null 2>&1 && sha256sum || shasum -a 256; } 2>/dev/null \
    | cut -c1-32)
MARKER="$FILED_DIR/${DEDUP_KEY:-fallback}"
if [ -f "$MARKER" ]; then
    _log "dedup hit (session=$LEAK_SESSION_ID); issue write skipped, leak already filed this session"
    exit 0
fi

# ----------------------------------------------------------------------------
# gh preflight — fail-safe, no disruption if unavailable
# ----------------------------------------------------------------------------
if ! command -v gh >/dev/null 2>&1; then
    _log "gh absent; incident NOT filed (transcript already sanitized by scrubber)"
    exit 0
fi
if ! timeout 10 gh auth status >/dev/null 2>&1; then
    _log "gh not authenticated; incident NOT filed (transcript already sanitized)"
    exit 0
fi

HOSTNAME_SHORT=$(hostname -s 2>/dev/null || echo "?")
TS=$(date +%F_%T)

COMMENT=$(cat <<EOF
**Incident** \`$TS\` on \`$HOSTNAME_SHORT\`

| field | value |
|---|---|
| source | \`$LEAK_SOURCE\` |
| pattern / key | $LEAK_DETAIL |
| replacements | $LEAK_REPLACED |
| transcript | \`$LEAK_TRANSCRIPT\` |
| session | \`$LEAK_SESSION_ID\` |
| transcript sanitized | ✅ in-place (automatic) |

**Action tracked here:** plan rotation for the affected credential, then close this comment thread item.

_Auto-filed by \`credential_leak_followup\` hook. No credential value is included by design — only the matching pattern/key name and counts._
EOF
)

ISSUE_BODY=$(cat <<'EOF'
Rolling incident log for credential leaks auto-detected + sanitized by the
harness-core scrubbers (`credential_value_scrub.sh` regex + `credential_scrub.py`
hash). Each comment below is one incident.

**By the time a comment lands here, the leak is already neutralized:**
1. ✅ the active session transcript (jsonl) was sanitized in-place
2. ✅ this incident comment was filed automatically
3. ➡️ remaining human/agent action = **rotate the affected credential**, then note it resolved

No credential values ever appear in this issue — only pattern/key names + counts.

Related: global SOPS rule, `feedback_credential_leak_5_incidents` memory.
EOF
)

# ----------------------------------------------------------------------------
# Ensure label, find-or-create rolling issue, append comment.
# Each gh call is bounded by `timeout` so a hung network self-cleans.
# ----------------------------------------------------------------------------
timeout 15 gh label create credential-leak -R "$REPO" \
    --color B60205 --description "Auto-filed credential leak incidents" >/dev/null 2>&1

NUM=$(timeout 20 gh issue list -R "$REPO" --label credential-leak --state open \
    --limit 1 --json number -q '.[0].number' 2>/dev/null)

if [ -z "$NUM" ]; then
    NUM=$(timeout 30 gh issue create -R "$REPO" \
        --title "🔐 Credential leak incident log (auto)" \
        --label credential-leak \
        --body "$ISSUE_BODY" 2>/dev/null | grep -oE '[0-9]+$' | tail -1)
    if [ -n "$NUM" ]; then
        _log "created rolling incident issue $REPO#$NUM"
    fi
fi

if [ -z "$NUM" ]; then
    _log "could not resolve/create incident issue in $REPO; transcript already sanitized"
    exit 0
fi

if timeout 20 gh issue comment -R "$REPO" "$NUM" --body "$COMMENT" >/dev/null 2>&1; then
    touch "$MARKER" 2>/dev/null
    echo "$REPO#$NUM" > "$STATE_DIR/last_issue" 2>/dev/null
    _log "appended incident to $REPO#$NUM (source=$LEAK_SOURCE)"
else
    _log "comment append failed on $REPO#$NUM; transcript already sanitized"
fi

exit 0
