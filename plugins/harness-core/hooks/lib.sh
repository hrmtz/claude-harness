#!/bin/bash
# Shared utilities for harness hooks (Claude Code + Codex + Grok).
#
# Codex compat: set HOOK_INPUT="$(cat)" at the top of each hook script before
# calling any lib function. lib functions prefer HOOK_INPUT over stdin so that
# stdin is not consumed twice across multiple calls (e.g. parse_tool_output +
# active_jsonl in the same script). Hooks that don't set HOOK_INPUT fall back
# to reading stdin directly (backward-compatible with older Claude Code style).
#
# Grok compat (gh #55, harness-grok): Grok delivers a different payload shape —
# tool input under `.toolInput` (camelCase) not `.tool_input`, tool name under
# `.toolName` (= run_terminal_command/read_file/search_replace) not `.tool_name`,
# and a PreToolUse deny is `{"decision":"deny","reason":...}` on stdout instead
# of `hookSpecificOutput.permissionDecision`. Grok also has no `transcript_path`;
# it injects GROK_SESSION_ID + GROK_WORKSPACE_ROOT and stores the transcript at
# ~/.grok/sessions/<pct-encoded-workspace>/<sessionId>/chat_history.jsonl. The
# parse_* / emit_deny / active_jsonl helpers below absorb all three differences so
# the hook scripts stay CLI-agnostic. Without this, a Grok payload slips past the
# named-field jq and the guard silently passes (fail-open) — the exact defect the
# harness-grok port closes.

CLAUDE_HOME="$HOME/.claude"
STATE_DIR="$CLAUDE_HOME/state"
LOG_DIR="$CLAUDE_HOME/state/hook_logs"
mkdir -p "$STATE_DIR" "$LOG_DIR"

# ----------------------------------------
# Active session jsonl path resolver
# ----------------------------------------
# Prefers transcript_path from hook JSON context (works for both Claude Code and
# Codex). For Grok, which has no transcript_path, resolves the session's
# chat_history.jsonl by its unique sessionId. Falls back to scanning
# ~/.claude/projects/ for Claude Code sessions that don't set HOOK_INPUT.
active_jsonl() {
    if [ -n "${HOOK_INPUT:-}" ]; then
        local tp
        tp=$(printf '%s' "$HOOK_INPUT" | jq -r '.transcript_path // .transcriptPath // empty' 2>/dev/null)
        if [ -n "$tp" ] && [ -f "$tp" ]; then
            echo "$tp"
            return 0
        fi
    fi
    # Grok: transcript lives at ~/.grok/sessions/<pct-encoded-workspace>/<sid>/
    # chat_history.jsonl. The sessionId is a UUID (globally unique), so we glob it
    # across the percent-encoded workspace dirs rather than re-deriving Grok's path
    # encoding — robust to whatever quote()-variant Grok uses. sid comes from the
    # runner-injected GROK_SESSION_ID env, or the payload's .sessionId as fallback.
    local sid="${GROK_SESSION_ID:-}"
    if [ -z "$sid" ] && [ -n "${HOOK_INPUT:-}" ]; then
        sid=$(printf '%s' "$HOOK_INPUT" | jq -r '.sessionId // empty' 2>/dev/null)
    fi
    if [ -n "$sid" ]; then
        local gj
        gj=$(ls -t "$HOME"/.grok/sessions/*/"$sid"/chat_history.jsonl 2>/dev/null | head -1)
        [ -n "$gj" ] && { echo "$gj"; return 0; }
    fi
    ls -t "$CLAUDE_HOME"/projects/*/[a-z0-9-]*.jsonl 2>/dev/null | head -1
}

# ----------------------------------------
# Tool-input field parsers (cross-CLI: Claude/Codex snake_case + Grok camelCase)
# ----------------------------------------
# Read HOOK_INPUT (preferred) or stdin, once. Each parser accepts BOTH the
# Claude/Codex `.tool_input.*` shape and the Grok `.toolInput.*` shape so the
# calling hook never has to know which CLI invoked it. A Grok payload that these
# did NOT cover would return empty and the guard would silently pass — so the
# camelCase alternates are the load-bearing part of the harness-grok fix.
_hook_input() {
    if [ -n "${HOOK_INPUT:-}" ]; then printf '%s' "$HOOK_INPUT"; else cat; fi
}

# Bash command string: `.tool_input.command` (Claude/Codex) // `.toolInput.command` (Grok).
parse_tool_command() {
    _hook_input | jq -r '.tool_input.command // .toolInput.command // empty' 2>/dev/null
}

parse_tool_name() {
    _hook_input | jq -r '.tool_use_name // .tool_name // .toolName // .payload.name // empty' 2>/dev/null
}

# Read/Write/Edit target path. Grok's read_file uses `.path`; search_replace and
# Claude Read/Write use `file_path`. Covers all three (Phase 1.5 Read/Write guards).
parse_tool_file_path() {
    _hook_input | jq -r '.tool_input.file_path // .toolInput.file_path // .toolInput.path // .path // empty' 2>/dev/null
}

# Write/Edit content or replacement text: `.content` (Write) or `.new_string`
# (Edit/search_replace), in either snake_case or camelCase.
parse_tool_content() {
    _hook_input | jq -r '.tool_input.content // .toolInput.content // .tool_input.new_string // .toolInput.new_string // .tool_input.patch // .toolInput.patch // .tool_input.input // .toolInput.input // .payload.input // .input // empty' 2>/dev/null
}

# ----------------------------------------
# PreToolUse deny — CLI-aware output shape
# ----------------------------------------
# Claude Code + Codex read `hookSpecificOutput.permissionDecision == "deny"`.
# Grok reads a top-level `{"decision":"deny","reason":...}` and ignores
# hookSpecificOutput. We DETECT Grok via its runner-injected env (GROK_SESSION_ID /
# GROK_HOOK_EVENT are set on EVERY Grok hook process, including Claude-compat ones)
# and emit exactly the one shape that CLI honors — so Claude/Codex output stays
# byte-identical to before (zero regression) and Grok gets a shape it can act on.
# Emitting BOTH as two JSON objects was rejected: Claude parses stdout as a single
# JSON document and a second object would break its parse → fail-open on deny.
# Terminal: prints the decision and exits 0 (Grok honors a stdout deny regardless
# of exit code; Claude/Codex expect exit 0 with the JSON). A deny is always the
# end of a hook's logic, so callers need no separate exit.
emit_deny() {
    local msg="$1"
    if [ -n "${GROK_SESSION_ID:-}${GROK_HOOK_EVENT:-}" ]; then
        jq -n --arg r "$msg" '{"decision":"deny","reason":$r}'
    else
        jq -n --arg msg "$msg" '{
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": $msg
            }
        }'
    fi
    exit 0
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
    printf '%s' "$input" | jq -r '.prompt // .userPrompt // .user_prompt // .input // .content // .message // empty' 2>/dev/null \
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
    # Grok compat (gh #55): Grok's PostToolUse carries the result under camelCase
    # `.toolResponse` (or `.toolOutput`). We add those alternates + a tostring
    # fallback so a Grok-shaped result is scanned too; the trailing tostring on each
    # container serializes whatever shape survives so no leak silently escapes.
    printf '%s' "$input" | jq -r '
        (.tool_response.stdout? // empty),
        (.tool_response.stderr? // empty),
        (.tool_response.output? // empty),
        (.tool_response.content? // empty),
        (.tool_response | select(. != null) | tostring),
        (.toolResponse.stdout? // empty),
        (.toolResponse.stderr? // empty),
        (.toolResponse.output? // empty),
        (.toolResponse.content? // empty),
        (.toolResponse | select(. != null) | tostring),
        (.toolOutput? // empty)
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
    # Claude/Codex: assistant text under .message.content[].text (array of parts).
    # Grok chat_history.jsonl: {"type":"assistant","content":"<string>"} — plain
    # string content. Try the Claude array walk first, then the Grok string.
    tac "$jsonl" 2>/dev/null \
        | jq -r 'select(.type == "assistant")
                 | (.message.content[]?.text // (.content | select(type == "string")) // empty)' 2>/dev/null \
        | head -n "$n"
}
