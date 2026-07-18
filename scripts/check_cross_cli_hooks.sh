#!/bin/bash
# check_cross_cli_hooks.sh — validate the cross-CLI hook overlay (gh #55).
#
# Repo-level checks (always):
#   1. every overlay entry points at an existing hook file under plugins/
#   2. every overlay entry is registered in the owning plugin's hooks/hooks.json
#      (the SSOT that drives Claude via sync_hooks_to_live.py)
#
# Live checks (--live):
#   3. the claude-harness-owned Codex block contains exactly the overlay commands
#   4. installed kimi guard core (~/.kimi-code/bin/guarded-bash-dir/guard-check.sh)
#      is identical to the repo version
#   5. ~/.grok/hooks/harness.json contains exactly the overlay's grok hook commands
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
mapfile -t ALL_HOOKS < <(jq -r '[.codex.hooks[], .grok.hooks[], .kimi.insurance[], .kimi.gates[], .kimi.hints[]] | map(if type == "object" then .path else . end) | unique | .[]' "$OVERLAY")

for hook in "${ALL_HOOKS[@]}"; do
    plugin="${hook%%/*}"
    name="${hook##*/}"

    # 1. file exists. Overlay entries may include arguments when the owning
    # hooks.json command is not a simple `bash <file>` shape.
    hook_file="${hook%% *}"
    [[ -f "$PLUGINS_DIR/$hook_file" ]] || err "$hook: file missing under plugins/"

    # 2. registered in owning plugin's hooks.json
    hooks_json="$PLUGINS_DIR/$plugin/hooks/hooks.json"
    if [[ ! -f "$hooks_json" ]]; then
        err "$hook: $plugin has no hooks/hooks.json"
    elif ! jq -e --arg n "/$name" '[.hooks[][].hooks[]?.command] | any(endswith($n))' "$hooks_json" >/dev/null; then
        err "$hook: not registered in $plugin/hooks/hooks.json (SSOT)"
    fi
done

if [[ $LIVE -eq 1 ]]; then
    # 3. Compare only the marker-bounded block owned by this installer. Hooks
    # from other sources are valid and intentionally invisible to this check.
    CODEX_CONFIG="$HOME/.codex/config.toml"
    if [[ -f "$CODEX_CONFIG" ]]; then
        want=$(mktemp); got=$(mktemp)
        {
            python3 - "$OVERLAY" "$HARNESS_DIR" <<'PYEOF'
import json, sys
overlay_path, harness_dir = sys.argv[1], sys.argv[2]
plugins_dir = f"{harness_dir}/plugins"
overlay = json.load(open(overlay_path))["codex"]
specs = [({"path": item} if isinstance(item, str) else item)
         for item in overlay["hooks"]]
lookup = {}
for plugin in sorted({spec["path"].split("/")[0] for spec in specs}):
    hooks_json = json.load(open(f"{plugins_dir}/{plugin}/hooks/hooks.json"))
    for event, blocks in hooks_json.get("hooks", {}).items():
        for blk in blocks:
            for h in blk.get("hooks", []):
                name = h["command"].split("/hooks/")[-1]
                lookup.setdefault(f"{plugin}/hooks/{name}", []).append((event, blk.get("matcher"), h["command"]))
for spec in specs:
    hook = spec["path"]
    candidates = lookup.get(hook, [])
    if "event" in spec:
        candidates = [item for item in candidates if item[0] == spec["event"]]
    if "matcher" in spec:
        candidates = [item for item in candidates if item[1] == spec["matcher"]]
    if len(candidates) != 1:
        print(f"INVALID OVERLAY SELECTION: {hook}", file=sys.stderr)
        sys.exit(1)
    plugin = hook.split("/", 1)[0]
    plugin_root = f"{plugins_dir}/{plugin}"
    print(candidates[0][2].replace("${CLAUDE_PLUGIN_ROOT}", plugin_root))
PYEOF
            python3 "$HARNESS_DIR/scripts/lib/cross_cli_externals.py" "$OVERLAY" codex "$HARNESS_DIR"
        } | sort > "$want"
        if ! python3 - "$CODEX_CONFIG" "$HARNESS_DIR/scripts/lib/merge_codex_hooks.py" > "$got" <<'PYEOF'
import importlib.util, pathlib, re, sys
config_path, helper_path = map(pathlib.Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location("merge_codex_hooks", helper_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
block = module.managed_block(config_path.read_text())
if block is None:
    print("error: no claude-harness managed hook block", file=sys.stderr)
    sys.exit(1)
for match in re.finditer(r'^\s*command\s*=\s*"(.*)"\s*$', block, re.MULTILINE):
    print(match.group(1))
PYEOF
        then
            err "codex managed hook block is absent or malformed (run install-codex-hooks.sh + re-trust)"
        else
            sort -o "$got" "$got"
            missing="$(comm -23 "$want" "$got")"
            extra="$(comm -13 "$want" "$got")"
            if [[ -n "$missing" ]]; then
                printf 'MISSING managed Codex hooks:\n%s\n' "$missing" >&2
                err "codex managed block is missing overlay hooks"
            fi
            if [[ -n "$extra" ]]; then
                printf 'DUPLICATE/UNEXPECTED managed Codex hooks:\n%s\n' "$extra" >&2
                err "codex managed block has duplicate or unexpected hooks"
            fi
        fi
        rm -f "$want" "$got"
    else
        echo "skip: $CODEX_CONFIG not present"
    fi

    # 5. grok harness.json carries exactly the overlay set (commands only)
    GROK_HOOKS="$HOME/.grok/hooks/harness.json"
    if [[ -f "$GROK_HOOKS" ]]; then
        want=$(mktemp); got=$(mktemp)
        {
            jq -r '.grok.hooks[]' "$OVERLAY" | sed "s|^|bash $PLUGINS_DIR/|"
            python3 "$HARNESS_DIR/scripts/lib/cross_cli_externals.py" "$OVERLAY" grok "$HARNESS_DIR"
        } | sort > "$want"
        jq -r '.hooks | to_entries[] | .value[] | .hooks[] | .command' "$GROK_HOOKS" 2>/dev/null \
            | grep -E 'hooks/' | sort > "$got"
        if ! diff -u "$want" "$got" >&2; then
            err "grok harness.json hook set differs from overlay (run install-grok-hooks.sh)"
        fi
        rm -f "$want" "$got"
    else
        echo "skip: $GROK_HOOKS not present"
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
