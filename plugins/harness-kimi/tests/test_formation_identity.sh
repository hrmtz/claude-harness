#!/usr/bin/env bash
# Regression coverage for gh #101: Formation routing, self-reference, and tmux
# display identity must share FORMATION_SELF; standalone Kimi remains random.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
WRAPPER="$HERE/../kimi-wrapper.sh"
ROOT="$(cd "$HERE/../../.." && pwd)"
FORMATION="$ROOT/plugins/harness-formation/bin/formation"
CLAUDE_CORE="$ROOT/plugins/harness-core/hooks/tmux_self_name_core.sh"
CODEX_HOOK="$ROOT/plugins/harness-core/hooks/codex_tmux_self_name.sh"

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEST_ROOT"' EXIT
mkdir -p "$TEST_ROOT/bin" "$TEST_ROOT/home/.local/state/tmux_self_name"

PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf 'FAIL %s\n' "$1"; }

cat > "$TEST_ROOT/bin/tmux" <<'SH'
#!/usr/bin/env bash
case "$1" in
  display-message)
    format="${@: -1}"
    case "$format" in
      '#{@formation_id}') printf '%s\n' "${TEST_FORMATION_ID:-}" ;;
      '#{window_panes}') printf '%s\n' "${TEST_WINDOW_PANES:-1}" ;;
      '#{window_name}') printf '%s\n' "${TEST_WINDOW_NAME:-shell}" ;;
      '#{pane_title}') printf '%s\n' "${TEST_PANE_TITLE:-shell}" ;;
      '#{window_id}') printf '%s\n' '@1' ;;
    esac
    ;;
  rename-window|select-pane|set-option)
    printf '%s\n' "$*" >> "$TEST_TMUX_LOG"
    ;;
  list-panes|list-windows)
    ;;
esac
SH
chmod +x "$TEST_ROOT/bin/tmux"

cat > "$TEST_ROOT/bin/kimi-real" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "$TEST_ROOT/bin/kimi-real"

run_wrapper() {
  PATH="$TEST_ROOT/bin:$PATH" \
  HOME="$TEST_ROOT/home" \
  HARNESS_KIMI_REAL="$TEST_ROOT/bin/kimi-real" \
  HARNESS_KIMI_TEMPLATE="$TEST_ROOT/missing-template" \
  TMUX_PANE="%77" \
  TEST_TMUX_LOG="$TEST_ROOT/tmux.log" \
  TEST_FORMATION_ID="${TEST_FORMATION_ID:-}" \
  TEST_WINDOW_PANES="${TEST_WINDOW_PANES:-1}" \
  FORMATION_SELF="${FORMATION_SELF:-}" \
  bash "$WRAPPER"
}

expect_log() {
  local pattern="$1" label="$2"
  if grep -Fq -- "$pattern" "$TEST_ROOT/tmux.log"; then ok "$label"; else
    bad "$label (missing: $pattern)"
  fi
}

: > "$TEST_ROOT/tmux.log"
printf '%s\n' 'kimi-stale-worker' > "$TEST_ROOT/home/.local/state/tmux_self_name/_77"
TEST_FORMATION_ID="issue100-k3" FORMATION_SELF="issue100-k3" run_wrapper
expect_log "rename-window -t %77 kimi-issue100-k3" "Formation Kimi window follows routing id"
expect_log "select-pane -t %77 -T kimi-issue100-k3" "Formation Kimi pane title follows routing id"
if grep -Fq 'kimi-stale-worker' "$TEST_ROOT/tmux.log"; then
  bad "Formation Kimi inherited pane-keyed stale sentinel"
else
  ok "Formation Kimi ignores pane-keyed stale sentinel"
fi

: > "$TEST_ROOT/tmux.log"
TEST_FORMATION_ID="next-worker" FORMATION_SELF="next-worker" run_wrapper
expect_log "rename-window -t %77 kimi-next-worker" "recycled pane receives the new Formation identity"

: > "$TEST_ROOT/tmux.log"
TEST_FORMATION_ID="sibling" FORMATION_SELF="issue100-k3" run_wrapper
if [[ -s "$TEST_ROOT/tmux.log" ]]; then
  bad "ownership mismatch mutated sibling pane"
else
  ok "ownership mismatch leaves sibling pane untouched"
fi

: > "$TEST_ROOT/tmux.log"
unset FORMATION_SELF
TEST_FORMATION_ID="" HARNESS_KIMI_DISPLAY_NAME="kimi-standalone-test" run_wrapper
expect_log "rename-window -t %77 kimi-standalone-test" "standalone Kimi display naming remains available"

# Sourcing formation is enough to cover the initial-name constructor without
# launching a real TUI or sleeping on prompt detection.
got="$(FORMATION_HOME="$TEST_ROOT/formation" FORMATION_SELF=tester \
  bash -c 'source "$1"; formation_window_name "$2" "$3"' _ "$FORMATION" kimi issue100-k3)"
if [[ "$got" == "kimi-issue100-k3" ]]; then
  ok "Formation initial window is CLI-specific"
else
  bad "Formation initial window drifted ($got)"
fi

: > "$TEST_ROOT/tmux.log"
claude_ctx="$(PATH="$TEST_ROOT/bin:$PATH" HOME="$TEST_ROOT/home" \
  TMUX_PANE="%77" FORMATION_SELF="issue100-k3" TEST_FORMATION_ID="issue100-k3" \
  TEST_TMUX_LOG="$TEST_ROOT/tmux.log" bash "$CLAUDE_CORE" --chassis claude --session-id old-session)"
if [[ "$claude_ctx" == *"Formation identity"*issue100-k3* ]]; then
  ok "Claude compact/resume context anchors Formation identity"
else
  bad "Claude context omitted Formation identity"
fi
expect_log "rename-window -t %77 claude-issue100-k3" "Claude hook reasserts Formation window identity"

: > "$TEST_ROOT/tmux.log"
codex_json="$(printf '%s' '{"session_id":"old-session"}' | \
  PATH="$TEST_ROOT/bin:$PATH" HOME="$TEST_ROOT/home" TMUX_PANE="%77" \
  FORMATION_SELF="issue100-k3" TEST_FORMATION_ID="issue100-k3" \
  TEST_WINDOW_NAME="codex-random-old" TEST_PANE_TITLE="codex-random-old" \
  TEST_TMUX_LOG="$TEST_ROOT/tmux.log" bash "$CODEX_HOOK")"
if [[ "$codex_json" == *"Formation identity"*issue100-k3* ]]; then
  ok "Codex compact/resume context anchors Formation identity"
else
  bad "Codex context omitted Formation identity"
fi
expect_log "rename-window -t %77 codex-issue100-k3" "Codex hook replaces random drift with Formation identity"

: > "$TEST_ROOT/tmux.log"
standalone_split_json="$(printf '%s' '{"session_id":"standalone-split"}' | \
  PATH="$TEST_ROOT/bin:$PATH" HOME="$TEST_ROOT/home" TMUX_PANE="%77" \
  TEST_FORMATION_ID="" TEST_WINDOW_PANES="2" TEST_WINDOW_NAME="kimi-sibling" \
  TEST_PANE_TITLE="shell" TEST_TMUX_LOG="$TEST_ROOT/tmux.log" \
  bash "$CODEX_HOOK")"
if [[ -z "$standalone_split_json" && ! -s "$TEST_ROOT/tmux.log" ]]; then
  ok "standalone Codex preserves a sibling chassis shared-window identity"
else
  bad "standalone Codex mutated a sibling chassis shared-window identity"
fi

printf 'RESULT: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
