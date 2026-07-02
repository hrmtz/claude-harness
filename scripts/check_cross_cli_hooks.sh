#!/bin/bash
# check_cross_cli_hooks.sh — validate the cross-CLI hook overlay (gh #55).
#
# Repo-level checks (always):
#   1. every overlay entry points at an existing hook file under plugins/
#   2. every overlay entry is registered in the owning plugin's hooks/hooks.json
#      (the SSOT that drives Claude via sync_hooks_to_live.py)
#
# Live checks (--live):
#   3. ~/.codex/config.toml contains exactly the overlay's codex hook commands
#   4. installed kimi guard core (~/.kimi-code/bin/guarded-bash-dir/guard-check.sh)
#      is identical to the repo version
#
# Exit: 0 in sync, 1 drift found.
set -uo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGINS_DIR="$HARNESS_DIR/plugins"
OVERLAY="$PLUGINS_DIR/cross_cli_hooks.json"
LIVE=0
[[ "${1:-}" == "--live" ]] && LIVE=1

fail=0
err() { echo "DRIFT: $*" >&2; fail=1; }

[[ -f "$OVERLAY" ]] || { echo "error: $OVERLAY missing" >&2; exit 1; }

# All hook paths referenced anywhere in the overlay.
mapfile -t ALL_HOOKS < <(jq -r '[.codex.hooks[], .kimi.insurance[], .kimi.gates[], .kimi.hints[]] | unique | .[]' "$OVERLAY")

for hook in "${ALL_HOOKS[@]}"; do
    plugin="${hook%%/*}"
    name="${hook##*/}"

    # 1. file exists
    [[ -f "$PLUGINS_DIR/$hook" ]] || err "$hook: file missing under plugins/"

    # 2. registered in owning plugin's hooks.json
    hooks_json="$PLUGINS_DIR/$plugin/hooks/hooks.json"
    if [[ ! -f "$hooks_json" ]]; then
        err "$hook: $plugin has no hooks/hooks.json"
    elif ! jq -e --arg n "/$name" '[.hooks[][].hooks[]?.command] | any(endswith($n))' "$hooks_json" >/dev/null; then
        err "$hook: not registered in $plugin/hooks/hooks.json (SSOT)"
    fi
done

if [[ $LIVE -eq 1 ]]; then
    # 3. codex config.toml carries exactly the overlay set
    CODEX_CONFIG="$HOME/.codex/config.toml"
    if [[ -f "$CODEX_CONFIG" ]]; then
        want=$(mktemp); got=$(mktemp)
        {
            jq -r '.codex.hooks[]' "$OVERLAY" | sed "s|^|bash $PLUGINS_DIR/|"
            jq -r '.codex.external[].command' "$OVERLAY"
        } | sort > "$want"
        grep -E '^command = ' "$CODEX_CONFIG" | sed 's/^command = "//;s/"$//' \
            | grep -E 'hooks/' | sort > "$got"
        if ! diff -u "$want" "$got" >&2; then
            err "codex config.toml hook set differs from overlay (run install-codex-hooks.sh + re-trust)"
        fi
        rm -f "$want" "$got"
    else
        echo "skip: $CODEX_CONFIG not present"
    fi

    # 4. installed kimi guard core is current
    KIMI_CHECK="$HOME/.kimi-code/bin/guarded-bash-dir/guard-check.sh"
    if [[ -f "$KIMI_CHECK" ]]; then
        cmp -s "$PLUGINS_DIR/harness-kimi/guard-check.sh" "$KIMI_CHECK" \
            || err "installed kimi guard-check.sh is stale (run harness-kimi/install-kimi-bash-guard.sh)"
    else
        echo "skip: kimi guard not installed"
    fi
fi

if [[ $fail -eq 0 ]]; then
    echo "cross-CLI hook overlay: in sync ($(( ${#ALL_HOOKS[@]} )) hooks checked, live=$LIVE)"
fi
exit $fail
