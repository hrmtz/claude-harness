#!/bin/bash
# Shared utilities for harness hooks (Claude Code + Codex).
#
# Codex compat: set HOOK_INPUT="$(cat)" at the top of each hook script before
# calling any lib function. lib functions prefer HOOK_INPUT over stdin so that
# stdin is not consumed twice across multiple calls (e.g. parse_tool_output +
# active_jsonl in the same script). Hooks that don't set HOOK_INPUT fall back
# to reading stdin directly (backward-compatible with older Claude Code style).

CLAUDE_HOME="$HOME/.claude"
STATE_DIR="$CLAUDE_HOME/state"
LOG_DIR="$CLAUDE_HOME/state/hook_logs"
mkdir -p "$STATE_DIR" "$LOG_DIR"

# ----------------------------------------
# Active session jsonl path resolver
# ----------------------------------------
# Prefers transcript_path from hook JSON context (works for both Claude Code and
# Codex). Falls back to scanning ~/.claude/projects/ for Claude Code sessions
# that don't set HOOK_INPUT.
active_jsonl() {
    if [ -n "${HOOK_INPUT:-}" ]; then
        local tp
        tp=$(printf '%s' "$HOOK_INPUT" | jq -r '.transcript_path // empty' 2>/dev/null)
        if [ -n "$tp" ] && [ -f "$tp" ]; then
            echo "$tp"
            return 0
        fi
    fi
    ls -t "$CLAUDE_HOME"/projects/*/[a-z0-9-]*.jsonl 2>/dev/null | head -1
}

# ----------------------------------------
# Parse user prompt from hook stdin (UserPromptSubmit, SessionStart, etc.)
# ----------------------------------------
parse_prompt() {
    local input
    if [ -n "${HOOK_INPUT:-}" ]; then
        input="$HOOK_INPUT"
    else
        input=$(cat)
    fi
    printf '%s' "$input" | jq -r '.prompt // .input // .content // .message // empty' 2>/dev/null \
        | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

# Parse tool output from PostToolUse hook stdin.
#
# Shape-agnostic (issue #7): the named-field extraction below handles the
# success-shaped tool_response (clean per-field text, real newlines — best for
# the keyword/YAML scanners). But exit-non-zero Bash calls deliver the output in
# an *error-wrapped* tool_response whose payload lives under a different field
# (or carries it as a bare string), so named-field extraction returns empty and
# the whole scan silently skips. The trailing `tostring` fallback serializes the
# ENTIRE tool_response regardless of shape, so any leaked credential text is
# still surfaced to the scanner. Redundant with the named fields on success
# (harmless — grep just matches twice); load-bearing on failure.
parse_tool_output() {
    local input
    if [ -n "${HOOK_INPUT:-}" ]; then
        input="$HOOK_INPUT"
    else
        input=$(cat)
    fi
    # The `?` after each index suppresses jq's "cannot index string with X" error
    # when tool_response is a bare string (an error-wrapped shape) — without it the
    # whole filter aborts before the tostring fallback runs.
    printf '%s' "$input" | jq -r '
        (.tool_response.stdout? // empty),
        (.tool_response.stderr? // empty),
        (.tool_response.output? // empty),
        (.tool_response.content? // empty),
        (.tool_response | select(. != null) | tostring)
    ' 2>/dev/null
}

# ----------------------------------------
# Emit hookSpecificOutput JSON (for context injection or blocking)
# ----------------------------------------
emit_context() {
    local event="$1" content="$2"
    jq -n --arg ctx "$content" --arg ev "$event" '{
        "hookSpecificOutput": {
            "hookEventName": $ev,
            "additionalContext": $ctx
        }
    }'
}

# ----------------------------------------
# Log hook events for debug + audit
# ----------------------------------------
hook_log() {
    local hook_name="$1"
    shift
    local msg="$*"
    echo "[$(date +%F_%T)] [$hook_name] $msg" >> "$LOG_DIR/hooks.log"
}

# ----------------------------------------
# Source-trust classification for credential-leak autorotation (gh #41).
# Echoes: trusted | untrusted | ambiguous  for a given producing command.
# AUTO-rotation is ALLOWLIST-based: "trusted" requires a SINGLE (no chain/subshell/
# backtick) command whose LEADING verb is a positively-identified local credential
# op (leading-token parse, NOT substring — so `echo pg_dump` or an appended
# `; sops ...` cannot forge trust). "untrusted" is an advisory denylist of external
# fetchers (incomplete by nature — NOT load-bearing; it only yields a clearer
# escalate). Everything else is "ambiguous" -> human-ack gated. The load-bearing
# guarantee is: an evasive external fetch (python/node/openssl/base64) is NOT a
# trusted-op, so it lands in ambiguous and NEVER auto-rotates. (codex #41 REJECT.)
classify_leak_trust() {
    local cmd="$1" work v0 var
    # inert PG connection env vars that are safe to precede a trusted verb. Anything
    # else (PATH=, LD_PRELOAD=, DYLD_*, BASH_ENV, ENV, IFS, PYTHONPATH, ...) can
    # re-route or hijack the bare verb -> NOT trusted (codex #41 round-3 CRITICAL).
    # inert = does NOT redirect the connection or alter TLS trust (codex #41 round-4:
    # PGHOST/PGPORT/PGUSER/PGDATABASE/PGSSL* can point the trusted binary at an
    # attacker endpoint, so they are NOT inert and are excluded).
    local INERT='^(PGPASSWORD|PGAPPNAME|PGCLIENTENCODING|PGCONNECT_TIMEOUT)$'
    # (1) UNTRUSTED denylist — ADVISORY only (incomplete by nature, NOT load-bearing).
    if printf '%s' "$cmd" | grep -qiE '(^|[^a-z])(curl|wget|ncat|nc|ssh|scp|sftp|rsync|telnet|ftp|aria2c|httpie|http|fetch)([^a-z]|$)|openssl[[:space:]]+s_client|https?://|ftp://|gh[[:space:]]+api[[:space:]]|/mailbox/|[^a-z]\.jsonl([^a-z]|$)|njslyr7/mailbox|/\.claude/projects/'; then
        echo untrusted; return
    fi
    # (2) Any chaining / command-substitution / PROCESS SUBSTITUTION / backtick makes
    #     the command non-single -> cannot attribute the DSN -> ambiguous. (codex #41
    #     CRIT: `<( )`/`>( )` run arbitrary children and were previously missed.)
    if printf '%s' "$cmd" | grep -qE '(\|\||&&|[;|&`]|\$\(|<\(|>\()'; then
        echo ambiguous; return
    fi
    work=$(printf '%s' "$cmd" | sed -E 's/^[[:space:]]+//')
    # strip a leading bare `env` (reject `env` with any option: -i/-S/-u/...)
    if printf '%s' "$work" | grep -qE '^env([[:space:]]|$)'; then
        work=$(printf '%s' "$work" | sed -E 's/^env[[:space:]]+//')
        printf '%s' "$work" | grep -qE '^-' && { echo ambiguous; return; }
    fi
    # consume leading VAR=val assignments; EACH must be an inert PG var, else ambiguous
    while printf '%s' "$work" | grep -qE '^[A-Za-z_][A-Za-z0-9_]*='; do
        var=$(printf '%s' "$work" | sed -E 's/^([A-Za-z_][A-Za-z0-9_]*)=.*/\1/')
        printf '%s' "$var" | grep -qE "$INERT" || { echo ambiguous; return; }
        work=$(printf '%s' "$work" | sed -E 's/^[A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]*//')
    done
    v0=$(printf '%s' "$work" | awk '{print $1}')   # RAW (no basename) — reject path masquerade (./pg_dump, /tmp/pg_dump)
    # pg_dump/pg_dumpall trusted ONLY when NO arg can route the connection to an
    # attacker DB (codex #41 r4/r5: -h/-p/-U/-d in spaced OR attached form (-hx),
    # libpq conninfo key=value (host=/service=/dbname=...), and URIs all redirect).
    # Principled (not form-by-form): reject ANY '=' token, ANY '://', any long
    # connection opt, and any SHORT-opt token that CONTAINS a connection letter
    # anywhere in a cluster (codex #41 r6: `-vh127` parses as -v -h127). Only a
    # bareword positional dbname + safe spaced flags survive -> ambient local conn.
    # long opts: pg_dump accepts UNIQUE PREFIXES (codex #41 r7: --hos==--host), so
    # match any prefix of host/port/username/dbname. The nested optionals keep
    # --data-only (--da...) distinct from --dbname (--db...). short opts: any cluster
    # containing a connection letter. Plus any '=' (conninfo/=forms) and any '://'.
    local CONN_OVERRIDE='=|://|(^|[[:space:]])--(h(o(s(t)?)?)?|p(o(r(t)?)?)?|u(s(e(r(n(a(m(e)?)?)?)?)?)?)?|d(b(n(a(m(e)?)?)?)?)?)([[:space:]]|=|$)|(^|[[:space:]])-[A-Za-z]*[hpUd]'
    case "$v0" in
        pg_dump|pg_dumpall)
            # PLAIN-TOKEN requirement (codex #41 r9): the classifier sees the RAW
            # string, but bash expands brace `--{h..h}ost`, quotes `--ho"st"`, $VAR,
            # globs, etc. INTO routing args before exec. So a trusted dump must contain
            # ONLY plain token chars — any shell-expansion/metacharacter -> ambiguous.
            printf '%s' "$work" | grep -qE '[^A-Za-z0-9 _./,-]' && { echo ambiguous; return; }
            # ...and even in plain form, no connection-routing flag may appear.
            printf '%s' "$work" | grep -qE "$CONN_OVERRIDE" && { echo ambiguous; return; }
            echo trusted; return ;;
        # NB: `sops exec-env <file> ...` is deliberately NOT trusted (codex #41 r8):
        # the (possibly attacker-selected) secrets file can inject PGHOST/PGSERVICE/
        # PGSSL* routing env that re-points an otherwise-clean pg_dump at attacker
        # data, and the classifier cannot see inside the encrypted file. sops-wrapped
        # dumps therefore fall through to ambiguous -> human-ack.
    esac
    echo ambiguous
}

# ----------------------------------------
# Recent assistant turns from active jsonl
# ----------------------------------------
recent_assistant_turns() {
    local n="${1:-3}"
    local jsonl
    jsonl=$(active_jsonl)
    [ -z "$jsonl" ] && return 1
    [ ! -f "$jsonl" ] && return 1
    tac "$jsonl" 2>/dev/null \
        | jq -r 'select(.type == "assistant") | .message.content[]?.text // empty' 2>/dev/null \
        | head -n "$n"
}
