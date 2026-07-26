#!/bin/bash
# Shared credential-shape catalog.
#
# Consumers:
#   - credential_value_scrub.sh redacts matched values from transcripts.
#   - sanada_autobackup.sh refuses to create a second plaintext copy.
#   - verify_credential_eradication.sh reports matching file paths, never values.
#
# Each PATTERNS entry is <extended-regex>|<replacement>. Keep regexes free of
# literal "|" alternation because the transcript scrubber uses it as the entry
# delimiter and as its sed delimiter.

PATTERNS=(
    'sk-ant-[a-zA-Z0-9_-]{20,}|sk-ant-<REDACTED>'
    'sk-or-[a-zA-Z0-9_-]{20,}|sk-or-<REDACTED>'
    'sk_live_[a-zA-Z0-9]{20,}|sk_live_<REDACTED>'
    'tskey-[a-zA-Z0-9_-]{20,}|tskey-<REDACTED>'
    'AKIA[0-9A-Z]{16}|AKIA<REDACTED>'
    'cfut_[A-Za-z0-9_-]{20,}|cfut_<REDACTED>'
    'cfat_[A-Za-z0-9]{20,}|cfat_<REDACTED>'
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
    'eyJ[A-Za-z0-9_=-]{10,}\.eyJ[A-Za-z0-9_=-]{10,}\.[A-Za-z0-9_=-]{10,}|<REDACTED_JWT>'
    'sb-[a-z0-9]{8,}-auth-token|sb-<REDACTED>-auth-token'
    # The mixed-case discriminator avoids lowercase_with_underscores identifiers
    # in vendored Parade touchscreen drivers (issue #99).
    'cmcp_[a-zA-Z0-9_-]*[A-Z][a-zA-Z0-9_-]{10,}|cmcp_<REDACTED>'
    'cmcp_[a-zA-Z0-9_-]{10,}[A-Z][a-zA-Z0-9_-]*|cmcp_<REDACTED>'
)

KEYWORD_PATTERN='[A-Z_]*(TOKEN|SECRET|KEY|PASSWORD|CREDENTIAL|PWD|AUTH|CERT|PRIVATE)[A-Z_]*'
VALUE_PATTERN='[a-zA-Z0-9_/+=.:-]{16,}'

# Matches in documentation and placeholder fixtures are not live credentials.
# The char-class fragments suppress this catalog's own DSN regex source text.
ALLOWLIST_REGEX='<REDACTED|placeholder|example|changeme|<your-key>|test-token|dummy|YOUR_|\[\^|\[:space:\]'

credential_shape_patterns() {
    local entry
    for entry in "${PATTERNS[@]}"; do
        printf '%s\n' "${entry%%|*}"
    done
    printf '%s\n' "${KEYWORD_PATTERN}=${VALUE_PATTERN}"
    printf '%s\n' "${KEYWORD_PATTERN}: [\"']${VALUE_PATTERN}"
}

credential_redact_text() {
    # Redact catalog-shaped substrings in a non-secret label such as a file
    # path before printing it. This does not apply the placeholder allowlist:
    # over-redacting a diagnostic label is safer than reproducing a value.
    local value="$1" entry pattern replacement
    for entry in "${PATTERNS[@]}"; do
        pattern="${entry%%|*}"
        replacement="${entry#*|}"
        value=$(printf '%s' "$value" | sed -E "s|${pattern}|${replacement}|g")
    done
    value=$(printf '%s' "$value" \
        | sed -E "s#(${KEYWORD_PATTERN})=(${VALUE_PATTERN})#\1=<REDACTED>#g")
    value=$(printf '%s' "$value" \
        | sed -E "s#(${KEYWORD_PATTERN}: [\"'])(${VALUE_PATTERN})#\1<REDACTED>#g")
    printf '%s' "$value"
}

credential_path_has_shape() {
    # Return 0 for a non-placeholder match. A scan error/timeout is treated as
    # sensitive: backup insurance must fail closed against making a plaintext
    # copy. Matched text stays inside the pipe and is never printed.
    local path="$1" scan_rc filter_rc
    local -a pipeline_status
    [ -f "$path" ] || [ -d "$path" ] || return 1

    timeout 4 grep -IorhE -f <(credential_shape_patterns) -- "$path" 2>/dev/null \
        | grep -qvE "$ALLOWLIST_REGEX" >/dev/null
    pipeline_status=("${PIPESTATUS[@]}")
    scan_rc=${pipeline_status[0]}
    filter_rc=${pipeline_status[1]}

    [ "$filter_rc" -eq 0 ] && return 0
    case "$scan_rc" in
        0|1) return 1 ;;
        *) return 0 ;;
    esac
}
