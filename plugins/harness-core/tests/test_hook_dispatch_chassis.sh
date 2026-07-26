#!/usr/bin/env bash
# test_hook_dispatch_chassis.sh — the layer every other identity test skips.
#
# #177 lived for weeks with every unit suite green. It could not have been
# caught by them: test_identity_owner.sh and test_formation_identity.sh call the
# core (or an adapter) directly, and the defect was in the two components those
# calls bypass — the wrapper that chooses an adapter, and the dispatcher that
# sets the environment the wrapper reads. Claude's settings.json goes
# hook -> harness-hook -> tmux_self_name.sh -> adapter, and only the whole chain
# shows that harness-hook's PLUGIN_ROOT export made every Claude session run the
# codex adapter.
#
# So this suite asserts on the chain, in a real tmux pane, from the entry point
# a real session uses. It is deliberately slower and less isolated than the unit
# suites; that is the point.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
DISPATCH="$ROOT/plugins/harness-core/bin/harness-hook"
WRAPPER="$ROOT/plugins/harness-core/hooks/tmux_self_name.sh"
HIPPO="$ROOT/plugins/harness-core/hooks/codex_hippocampus_session_start.sh"

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n     %s\n' "$1" "${2:-}"; }
check() { [ "$2" = "$3" ] && ok "$1" || bad "$1" "expected '$2', got '$3'"; }

# A suite that skips is indistinguishable from a suite that passes, and this one
# exists precisely because a gate that never runs looks green. CI sets
# HARNESS_TEST_REQUIRE_TMUX=1 so an environment without tmux is a failure there,
# while a developer without a server still gets a skip rather than a wall.
if ! command -v tmux >/dev/null 2>&1 || ! tmux display-message -p '#{pid}' >/dev/null 2>&1; then
    if [ "${HARNESS_TEST_REQUIRE_TMUX:-0}" = "1" ]; then
        echo "test_hook_dispatch_chassis: tmux is required here and is unavailable." >&2
        echo "  This suite drives real panes; skipping it would hide the very class of" >&2
        echo "  defect it was written for (#177). Install tmux and start a server." >&2
        exit 1
    fi
    echo "test_hook_dispatch_chassis: no tmux server; skipping (this suite is about real panes)"
    echo "  set HARNESS_TEST_REQUIRE_TMUX=1 to make that a failure instead"
    exit 0
fi

WORK="$(mktemp -d)"
SESSION="hookchassis-$$"
cleanup() { tmux kill-session -t "$SESSION" 2>/dev/null; rm -rf "$WORK"; }
trap cleanup EXIT

export HARNESS_IDENTITY_SENTINEL_DIR="$WORK/sentinels"
mkdir -p "$HARNESS_IDENTITY_SENTINEL_DIR"

# A pane per case: identity is per-pane state, and reusing one would let an
# earlier claim decide a later case.
new_pane() {
    local name="$1"
    if tmux has-session -t "$SESSION" 2>/dev/null; then
        tmux new-window -d -t "$SESSION" -n "$name" >/dev/null
    else
        tmux new-session -d -s "$SESSION" -n "$name" -x 120 -y 30 >/dev/null
    fi
    tmux list-panes -t "$SESSION:$name" -F '#{pane_id}' | head -1
}

# Run the chain the way a session does, and wait for the run to actually finish
# rather than for its command line to echo — a marker grep on the pane matches
# the typed text before anything has executed, which is how an earlier version
# of this harness reported drift that had not happened yet.
run_chain() { # pane env... --
    local pane="$1"; shift
    local done_file="$WORK/done.$RANDOM"
    tmux send-keys -t "$pane" "printf '{}' | $* >$WORK/out.$$ 2>&1; touch $done_file" Enter
    local n=0
    while [ "$n" -lt 40 ]; do
        [ -f "$done_file" ] && return 0
        sleep 0.5; n=$((n+1))
    done
    return 1
}

pane_state() { tmux display-message -p -t "$1" '#{window_name}|#{@harness_chassis}'; }

echo "dispatcher -> wrapper -> adapter, on real panes"

# ── the regression itself ────────────────────────────────────────────────────
P=$(new_pane claude-path)
run_chain "$P" "HARNESS_IDENTITY_SENTINEL_DIR=$HARNESS_IDENTITY_SENTINEL_DIR" \
    "$DISPATCH" harness-core hooks/tmux_self_name.sh || bad "claude chain timed out" ""
S=$(pane_state "$P")
check "a Claude session through harness-hook lands on the claude adapter" "claude" "${S#*|}"
case "${S%%|*}" in
    claude-*) ok "and the pane is named claude-<codename>" ;;
    *)        bad "and the pane is named claude-<codename>" "window_name=${S%%|*}" ;;
esac

# ── the dispatcher must not fabricate a plugin host ──────────────────────────
P=$(new_pane no-fabricate)
run_chain "$P" "$DISPATCH" harness-core hooks/tmux_self_name.sh >/dev/null
tmux send-keys -t "$P" "printf '%s' \"\${PLUGIN_ROOT:-UNSET}\" > $WORK/plugin_root; $DISPATCH harness-core hooks/tmux_self_name.sh </dev/null >/dev/null 2>&1; touch $WORK/pr.done" Enter
n=0; while [ $n -lt 40 ]; do [ -f "$WORK/pr.done" ] && break; sleep 0.5; n=$((n+1)); done
check "PLUGIN_ROOT is not set in the caller's own environment" "UNSET" "$(cat "$WORK/plugin_root" 2>/dev/null)"
if grep -q 'PLUGIN_ROOT' "$DISPATCH" && grep -q '\[ -n "\${PLUGIN_ROOT:-}" \] && export PLUGIN_ROOT' "$DISPATCH"; then
    ok "harness-hook passes PLUGIN_ROOT through instead of inventing it"
else
    bad "harness-hook passes PLUGIN_ROOT through instead of inventing it" \
        "the export is unconditional again — this is exactly #177"
fi

# ── the explicit chassis ─────────────────────────────────────────────────────
P=$(new_pane codex-explicit)
run_chain "$P" "HARNESS_IDENTITY_SENTINEL_DIR=$HARNESS_IDENTITY_SENTINEL_DIR HARNESS_CHASSIS=codex" \
    "$DISPATCH" harness-core hooks/tmux_self_name.sh || bad "codex chain timed out" ""
S=$(pane_state "$P")
check "HARNESS_CHASSIS=codex routes to the codex adapter" "codex" "${S#*|}"

P=$(new_pane claude-explicit)
run_chain "$P" "HARNESS_IDENTITY_SENTINEL_DIR=$HARNESS_IDENTITY_SENTINEL_DIR HARNESS_CHASSIS=claude PLUGIN_ROOT=$ROOT/plugins/harness-core" \
    "$DISPATCH" harness-core hooks/tmux_self_name.sh || bad "explicit claude chain timed out" ""
S=$(pane_state "$P")
check "an explicit claude chassis wins over a stray PLUGIN_ROOT" "claude" "${S#*|}"

# ── back-compat for a genuine plugin host ────────────────────────────────────
# A native Codex plugin install that predates HARNESS_CHASSIS still has to reach
# the codex adapter, so PLUGIN_ROOT remains a signal — just no longer a
# fabricated one.
P=$(new_pane legacy-plugin-host)
run_chain "$P" "HARNESS_IDENTITY_SENTINEL_DIR=$HARNESS_IDENTITY_SENTINEL_DIR PLUGIN_ROOT=$ROOT/plugins/harness-core" \
    bash "$WRAPPER" || bad "legacy host chain timed out" ""
S=$(pane_state "$P")
check "a genuine plugin host still reaches the codex adapter" "codex" "${S#*|}"

# ── the second instance of the same misread ──────────────────────────────────
# codex_hippocampus_session_start.sh read PLUGIN_ROOT the same way, so it also
# ran inside Claude sessions instead of no-opping.
OUT=$(printf '{}' | env -u PLUGIN_ROOT HARNESS_CHASSIS=claude bash "$HIPPO" 2>&1; echo "rc=$?")
case "$OUT" in
    rc=0) ok "the codex companion adapter no-ops on a claude chassis" ;;
    *)    bad "the codex companion adapter no-ops on a claude chassis" "$OUT" ;;
esac
OUT=$(printf '{}' | env -u PLUGIN_ROOT -u HARNESS_CHASSIS bash "$HIPPO" 2>&1; echo "rc=$?")
case "$OUT" in
    rc=0) ok "and still no-ops with neither signal present" ;;
    *)    bad "and still no-ops with neither signal present" "$OUT" ;;
esac

# ── the installers state the chassis rather than leaving it inferred ─────────
for pair in "install-codex-hooks.sh:codex" "install-grok-hooks.sh:grok" "install-kimi-hooks.sh:kimi"; do
    f="${pair%%:*}"; c="${pair##*:}"
    if grep -q "HARNESS_CHASSIS=$c" "$ROOT/$f"; then
        ok "$f stamps HARNESS_CHASSIS=$c on the commands it writes"
    else
        bad "$f stamps HARNESS_CHASSIS=$c on the commands it writes" "no stamp found"
    fi
done

echo
echo "─────────────────────────────────────────"
printf '%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
