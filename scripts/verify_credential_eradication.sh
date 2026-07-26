#!/bin/bash
# Verify credential eradication without printing matched values.
#
# Default roots cover the session, persistent-backup, legacy-agent, temporary
# session, and project surfaces named in issue #155. Optional positional roots
# replace the defaults. Exit 0 = complete and clean, 1 = matching paths remain,
# 2 = at least one root scan was incomplete.

set -u

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$REPO_ROOT/plugins/harness-core/hooks/credential_patterns.sh"

if [ "$#" -gt 0 ]; then
    ROOTS=("$@")
else
    ROOTS=(
        "$HOME/.claude/projects"
        "$HOME/sanada_backup_persistent"
        "$HOME/.njslyr7"
        /tmp/claude-*
        "$HOME/projects"
    )
fi

PATTERN_FILE=$(mktemp)
CANDIDATE_FILE=$(mktemp)
trap 'rm -f "$PATTERN_FILE" "$CANDIDATE_FILE"' EXIT
credential_shape_patterns >"$PATTERN_FILE"
chmod 600 "$PATTERN_FILE" "$CANDIDATE_FILE"

SCAN_TIMEOUT="${HARNESS_ERADICATION_SCAN_TIMEOUT:-120}"
case "$SCAN_TIMEOUT" in
    ''|*[!0-9]*) printf 'credential-eradication: invalid scan timeout\n'; exit 2 ;;
esac
[ "$SCAN_TIMEOUT" -gt 0 ] || {
    printf 'credential-eradication: invalid scan timeout\n'
    exit 2
}

found=0
incomplete=0
checked=0
for root in "${ROOTS[@]}"; do
    [ -e "$root" ] || continue
    : >"$CANDIDATE_FILE"
    timeout "$SCAN_TIMEOUT" \
        grep -IlrZE -f "$PATTERN_FILE" -- "$root" >"$CANDIDATE_FILE" 2>/dev/null
    scan_rc=$?
    case "$scan_rc" in
        0|1) ;;
        *)
            printf 'INCOMPLETE %s\n' "$root"
            incomplete=1
            ;;
    esac

    while IFS= read -r -d '' path; do
        [ -f "$path" ] || continue
        checked=$((checked + 1))
        if credential_path_has_shape "$path"; then
            printf 'MATCH %s\n' "$(credential_redact_text "$path")"
            found=1
        fi
    done <"$CANDIDATE_FILE"
done

if [ "$incomplete" -eq 1 ]; then
    printf 'credential-eradication: INCOMPLETE (candidate-files-checked=%d)\n' "$checked"
    exit 2
fi
if [ "$found" -eq 1 ]; then
    printf 'credential-eradication: MATCHES REMAIN (values suppressed)\n'
    exit 1
fi
printf 'credential-eradication: CLEAN\n'
exit 0
