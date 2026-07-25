#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK="$ROOT/hooks/tmux_hyperlink_guard.sh"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEST_ROOT"' EXIT
mkdir -p "$TEST_ROOT/bin"
LOG="$TEST_ROOT/tmux.log"

cat >"$TEST_ROOT/bin/tmux" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$TEST_TMUX_LOG"
case "${1:-}" in
    display-message)
        [ "${TEST_VALID_PANE:-1}" = "1" ]
        ;;
    show-options)
        printf '%s\n' "${TEST_FEATURES:-xterm*:RGB}"
        ;;
    set-option)
        exit "${TEST_SET_RC:-0}"
        ;;
esac
SH
chmod +x "$TEST_ROOT/bin/tmux"

run_hook() {
    : >"$LOG"
    PATH="$TEST_ROOT/bin:$PATH" \
        TEST_TMUX_LOG="$LOG" \
        TMUX="/tmp/tmux-test/default,1,0" \
        TMUX_PANE="%7" \
        TEST_FEATURES="${1:-xterm*:RGB}" \
        TEST_VALID_PANE="${2:-1}" \
        bash "$HOOK"
}

run_hook
grep -Fqx "set-option -as terminal-features ,*:hyperlinks" "$LOG"

run_hook $'xterm*:RGB\n*:hyperlinks'
if grep -Fq "set-option" "$LOG"; then
    echo "duplicate hyperlink feature mutation" >&2
    exit 1
fi

run_hook $'foot:hyperlinks\nxterm*:RGB'
grep -Fqx "set-option -as terminal-features ,*:hyperlinks" "$LOG"

run_hook "*:RGB:hyperlinks:title"
if grep -Fq "set-option" "$LOG"; then
    echo "duplicate wildcard hyperlink feature mutation" >&2
    exit 1
fi

run_hook "xterm*:RGB" 0
if grep -Fq "set-option" "$LOG"; then
    echo "stale pane mutated tmux options" >&2
    exit 1
fi

: >"$LOG"
env -u TMUX -u TMUX_PANE \
    PATH="$TEST_ROOT/bin:$PATH" TEST_TMUX_LOG="$LOG" bash "$HOOK"
if [[ -s "$LOG" ]]; then
    echo "non-tmux invocation called tmux" >&2
    exit 1
fi

echo "tmux hyperlink guard tests passed"
