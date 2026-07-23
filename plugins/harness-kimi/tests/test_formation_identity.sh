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
AGENTS_TEMPLATE="$ROOT/plugins/harness-kimi/AGENTS.md.template"

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
  list-panes)
    case "$*" in
      *'#{pane_id}|#{@formation_id}'*)
        [ -n "${TEST_OTHER_FORMATION_ID:-}" ] && printf '%%88|%s\n' "$TEST_OTHER_FORMATION_ID"
        ;;
      *'#{@formation_id}'*)
        [ -n "${TEST_OTHER_FORMATION_ID:-}" ] && printf '%s\n' "$TEST_OTHER_FORMATION_ID"
        ;;
    esac
    ;;
  list-windows)
    ;;
esac
SH
chmod +x "$TEST_ROOT/bin/tmux"

cat > "$TEST_ROOT/bin/kimi-real" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "$TEST_ROOT/bin/kimi-real"

cat > "$TEST_ROOT/bin/ps" <<'SH'
#!/usr/bin/env bash
case "$*" in
  *" comm="*) printf '%s\n' "${TEST_ANCESTOR_COMM:-bash}" ;;
  *" ppid="*) printf '1\n' ;;
esac
SH
chmod +x "$TEST_ROOT/bin/ps"

run_wrapper() {
  PATH="$TEST_ROOT/bin:$PATH" \
  HOME="$TEST_ROOT/home" \
  HARNESS_KIMI_REAL="$TEST_ROOT/bin/kimi-real" \
  HARNESS_KIMI_TEMPLATE="$TEST_ROOT/missing-template" \
  TMUX_PANE="%77" \
  TEST_TMUX_LOG="$TEST_ROOT/tmux.log" \
  TEST_FORMATION_ID="${TEST_FORMATION_ID:-}" \
  TEST_WINDOW_PANES="${TEST_WINDOW_PANES:-1}" \
  TEST_ANCESTOR_COMM="${TEST_ANCESTOR_COMM:-}" \
  TEST_OTHER_FORMATION_ID="${TEST_OTHER_FORMATION_ID:-}" \
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
sentinel_name=""
IFS= read -r sentinel_name < "$TEST_ROOT/home/.local/state/tmux_self_name/_77" || true
if [[ "$sentinel_name" == "kimi-stale-worker" ]]; then
  ok "Formation Kimi does not persist spawn identity in pane-keyed sentinel"
else
  bad "Formation Kimi overwrote pane-keyed sentinel ($sentinel_name)"
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
expect_log "set-option -p -t %77 @formation_id standalone-test" "standalone Kimi display override seeds routing identity"

: > "$TEST_ROOT/tmux.log"
TEST_FORMATION_ID="" HARNESS_KIMI_DISPLAY_NAME="review-agent" run_wrapper
expect_log "rename-window -t %77 review-agent" "standalone Kimi preserves an unprefixed display override"
expect_log "set-option -p -t %77 @formation_id review-agent" "unprefixed Kimi display override seeds routing identity"

: > "$TEST_ROOT/tmux.log"
TEST_FORMATION_ID="" TEST_OTHER_FORMATION_ID="review-agent" HARNESS_KIMI_DISPLAY_NAME="review-agent" run_wrapper
if grep -Fq 'set-option -p -t %77 @formation_id review-agent' "$TEST_ROOT/tmux.log"; then
  bad "duplicate Kimi display override reused a live routing identity"
else
  ok "duplicate Kimi display override receives a unique routing identity"
fi

: > "$TEST_ROOT/tmux.log"
TEST_FORMATION_ID="onyx-raven" TEST_WINDOW_NAME="kimi-muted-fox" run_wrapper
expect_log "rename-window -t %77 kimi-onyx-raven" "standalone Kimi display follows existing routing identity"

: > "$TEST_ROOT/tmux.log"
TEST_FORMATION_ID="parent-id" TEST_WINDOW_NAME="codex-parent-id" TEST_ANCESTOR_COMM="codex" run_wrapper
if [[ -s "$TEST_ROOT/tmux.log" ]]; then
  bad "standalone Kimi child mutated another chassis"
else
  ok "standalone Kimi child preserves another chassis"
fi

: > "$TEST_ROOT/tmux.log"
TEST_FORMATION_ID="parent-id" TEST_WINDOW_NAME="codex-parent-id" TEST_ANCESTOR_COMM="" run_wrapper
expect_log "rename-window -t %77 kimi-parent-id" "sequential Kimi launch reuses routing identity"

# Sourcing formation is enough to cover the initial-name constructor without
# launching a real TUI or sleeping on prompt detection.
got="$(FORMATION_HOME="$TEST_ROOT/formation" FORMATION_SELF=tester \
  bash -c 'source "$1"; formation_window_name "$2" "$3"' _ "$FORMATION" kimi issue100-k3)"
if [[ "$got" == "kimi-issue100-k3" ]]; then
  ok "Formation initial window is CLI-specific"
else
  bad "Formation initial window drifted ($got)"
fi

if grep -Fq -- 'tmux display-message -p -t "$TMUX_PANE" '\''#{@formation_id}|#{window_name}'\''' "$AGENTS_TEMPLATE"; then
  ok "Kimi identity self-check targets its own pane"
else
  bad "Kimi identity self-check can read the parent client window"
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
codex_mismatch_json="$(printf '%s' '{"session_id":"stale-pane"}' | \
  PATH="$TEST_ROOT/bin:$PATH" HOME="$TEST_ROOT/home" TMUX_PANE="%77" \
  FORMATION_SELF="issue100-k3" TEST_FORMATION_ID="sibling-worker" \
  TEST_WINDOW_NAME="codex-sibling-worker" TEST_PANE_TITLE="codex-sibling-worker" \
  TEST_TMUX_LOG="$TEST_ROOT/tmux.log" bash "$CODEX_HOOK")"
if [[ -z "$codex_mismatch_json" && ! -s "$TEST_ROOT/tmux.log" ]]; then
  ok "Codex Formation ownership mismatch leaves sibling pane untouched"
else
  bad "Codex Formation ownership mismatch mutated sibling pane"
fi

: > "$TEST_ROOT/tmux.log"
codex_standalone_json="$(printf '%s' '{"session_id":"standalone-routing"}' | \
  PATH="$TEST_ROOT/bin:$PATH" HOME="$TEST_ROOT/home" TMUX_PANE="%77" \
  TEST_FORMATION_ID="storm-lantern" TEST_WINDOW_NAME="codex-muted-lantern" \
  TEST_PANE_TITLE="codex-muted-lantern" TEST_TMUX_LOG="$TEST_ROOT/tmux.log" \
  bash "$CODEX_HOOK")"
if [[ "$codex_standalone_json" == *"storm-lantern"* ]]; then
  ok "standalone Codex context follows existing routing identity"
else
  bad "standalone Codex context drifted from routing identity"
fi
expect_log "rename-window -t %77 codex-storm-lantern" "standalone Codex display follows existing routing identity"

: > "$TEST_ROOT/tmux.log"
codex_child_json="$(printf '%s' '{"session_id":"standalone-child"}' | \
  PATH="$TEST_ROOT/bin:$PATH" HOME="$TEST_ROOT/home" TMUX_PANE="%77" \
  TEST_FORMATION_ID="parent-id" TEST_WINDOW_PANES="1" \
  TEST_WINDOW_NAME="claude-parent-id" TEST_PANE_TITLE="claude-parent-id" \
  TEST_ANCESTOR_COMM="claude" \
  TEST_TMUX_LOG="$TEST_ROOT/tmux.log" bash "$CODEX_HOOK")"
if [[ -z "$codex_child_json" && ! -s "$TEST_ROOT/tmux.log" ]]; then
  ok "standalone Codex child preserves another chassis"
else
  bad "standalone Codex child mutated another chassis"
fi

: > "$TEST_ROOT/tmux.log"
standalone_split_json="$(printf '%s' '{"session_id":"standalone-split"}' | \
  PATH="$TEST_ROOT/bin:$PATH" HOME="$TEST_ROOT/home" TMUX_PANE="%77" \
  TEST_FORMATION_ID="" TEST_WINDOW_PANES="2" TEST_WINDOW_NAME="kimi-sibling" \
  TEST_PANE_TITLE="shell" TEST_ANCESTOR_COMM="" TEST_TMUX_LOG="$TEST_ROOT/tmux.log" \
  bash "$CODEX_HOOK")"
if [[ -z "$standalone_split_json" && ! -s "$TEST_ROOT/tmux.log" ]]; then
  ok "standalone Codex preserves a sibling chassis shared-window identity"
else
  bad "standalone Codex mutated a sibling chassis shared-window identity"
fi

: > "$TEST_ROOT/tmux.log"
codex_sequential_json="$(printf '%s' '{"session_id":"sequential-codex"}' | \
  PATH="$TEST_ROOT/bin:$PATH" HOME="$TEST_ROOT/home" TMUX_PANE="%77" \
  TEST_FORMATION_ID="parent-id" TEST_WINDOW_PANES="1" \
  TEST_WINDOW_NAME="claude-parent-id" TEST_PANE_TITLE="claude-parent-id" \
  TEST_ANCESTOR_COMM="" TEST_TMUX_LOG="$TEST_ROOT/tmux.log" bash "$CODEX_HOOK")"
if [[ "$codex_sequential_json" == *"parent-id"* ]]; then
  ok "sequential Codex launch reuses routing identity"
else
  bad "sequential Codex launch skipped identity setup"
fi
expect_log "rename-window -t %77 codex-parent-id" "sequential Codex display follows routing identity"

: > "$TEST_ROOT/tmux.log"
claude_standalone_ctx="$(PATH="$TEST_ROOT/bin:$PATH" HOME="$TEST_ROOT/home" \
  TMUX_PANE="%77" TEST_FORMATION_ID="slate-rook" \
  TEST_WINDOW_NAME="claude-iron-lattice" TEST_TMUX_LOG="$TEST_ROOT/tmux.log" \
  bash "$CLAUDE_CORE" --chassis claude --session-id standalone-claude)"
if [[ "$claude_standalone_ctx" == *"slate-rook"* ]]; then
  ok "standalone Claude context follows existing routing identity"
else
  bad "standalone Claude context drifted from routing identity"
fi
expect_log "rename-window -t %77 claude-slate-rook" "standalone Claude display follows existing routing identity"

: > "$TEST_ROOT/tmux.log"
claude_child_ctx="$(PATH="$TEST_ROOT/bin:$PATH" HOME="$TEST_ROOT/home" \
  TMUX_PANE="%77" TEST_FORMATION_ID="parent-id" \
  TEST_WINDOW_NAME="codex-parent-id" TEST_ANCESTOR_COMM="codex" TEST_TMUX_LOG="$TEST_ROOT/tmux.log" \
  bash "$CLAUDE_CORE" --chassis claude --session-id child-claude)"
if [[ -z "$claude_child_ctx" && ! -s "$TEST_ROOT/tmux.log" ]]; then
  ok "standalone Claude child preserves another chassis"
else
  bad "standalone Claude child mutated another chassis"
fi

: > "$TEST_ROOT/tmux.log"
claude_sequential_ctx="$(PATH="$TEST_ROOT/bin:$PATH" HOME="$TEST_ROOT/home" \
  TMUX_PANE="%77" TEST_FORMATION_ID="parent-id" \
  TEST_WINDOW_NAME="codex-parent-id" TEST_ANCESTOR_COMM="" TEST_TMUX_LOG="$TEST_ROOT/tmux.log" \
  bash "$CLAUDE_CORE" --chassis claude --session-id sequential-claude)"
if [[ "$claude_sequential_ctx" == *"parent-id"* ]]; then
  ok "sequential Claude launch reuses routing identity"
else
  bad "sequential Claude launch skipped identity setup"
fi
expect_log "rename-window -t %77 claude-parent-id" "sequential Claude display follows routing identity"

: > "$TEST_ROOT/tmux.log"
claude_split_ctx="$(PATH="$TEST_ROOT/bin:$PATH" HOME="$TEST_ROOT/home" \
  TMUX_PANE="%77" TEST_FORMATION_ID="" TEST_WINDOW_PANES="2" \
  TEST_WINDOW_NAME="codex-sibling" TEST_ANCESTOR_COMM="" TEST_TMUX_LOG="$TEST_ROOT/tmux.log" \
  bash "$CLAUDE_CORE" --chassis claude --session-id split-claude)"
if [[ -z "$claude_split_ctx" && ! -s "$TEST_ROOT/tmux.log" ]]; then
  ok "standalone Claude preserves a sibling chassis shared-window identity"
else
  bad "standalone Claude mutated a sibling chassis shared-window identity"
fi

: > "$TEST_ROOT/tmux.log"
printf '%s\n' 'claude-legacy-raven' > "$TEST_ROOT/home/.local/state/tmux_self_name/legacy-claude"
claude_legacy_ctx="$(PATH="$TEST_ROOT/bin:$PATH" HOME="$TEST_ROOT/home" \
  TMUX_PANE="%77" TEST_FORMATION_ID="" TEST_WINDOW_NAME="claude-legacy-raven" \
  TEST_ANCESTOR_COMM="" TEST_TMUX_LOG="$TEST_ROOT/tmux.log" \
  bash "$CLAUDE_CORE" --chassis claude --session-id legacy-claude)"
if [[ "$claude_legacy_ctx" == *"claude-legacy-raven"* ]]; then
  ok "legacy Claude sentinel remains the identity anchor"
else
  bad "legacy Claude sentinel was not resumed"
fi
expect_log "set-option -p -t %77 @formation_id legacy-raven" "legacy Claude sentinel backfills routing identity"

: > "$TEST_ROOT/tmux.log"
printf '%s\n' 'claude-colliding-raven' > "$TEST_ROOT/home/.local/state/tmux_self_name/colliding-claude"
claude_collision_ctx="$(PATH="$TEST_ROOT/bin:$PATH" HOME="$TEST_ROOT/home" \
  TMUX_PANE="%77" TEST_FORMATION_ID="" TEST_OTHER_FORMATION_ID="colliding-raven" \
  TEST_WINDOW_NAME="claude-colliding-raven" TEST_TMUX_LOG="$TEST_ROOT/tmux.log" \
  bash "$CLAUDE_CORE" --chassis claude --session-id colliding-claude)"
if [[ -z "$claude_collision_ctx" ]] \
    && ! grep -Fq 'set-option -p -t %77 @formation_id colliding-raven' "$TEST_ROOT/tmux.log"; then
  ok "legacy Claude sentinel refuses a duplicate routing identity"
else
  bad "legacy Claude sentinel duplicated a live routing identity"
fi

printf 'RESULT: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
