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
#   4. the harness-kimi marker block in ~/.kimi-code/config.toml contains exactly
#      the overlay's kimi hook commands
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
mapfile -t ALL_HOOKS < <(jq -r '[.codex.hooks[], .grok.hooks[], .kimi.hooks[]] | map(if type == "object" then .path else . end) | unique | .[]' "$OVERLAY")

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
        python3 "$HARNESS_DIR/scripts/lib/render_codex_hooks.py" \
            commands "$OVERLAY" "$HARNESS_DIR" | sort > "$want"
        if ! python3 - "$CODEX_CONFIG" "$HARNESS_DIR/scripts/lib/merge_codex_hooks.py" > "$got" <<'PYEOF'
import importlib.util, json, pathlib, re, sys
config_path, helper_path = map(pathlib.Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location("merge_codex_hooks", helper_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
block = module.managed_block(config_path.read_text())
if block is None:
    print("error: no claude-harness managed hook block", file=sys.stderr)
    sys.exit(1)
for match in re.finditer(r'^\s*command\s*=\s*"(.*)"\s*$', block, re.MULTILINE):
    # Installer renders commands with json.dumps, which is valid TOML basic
    # string escaping. Compare decoded command values, not their `\"` source.
    print(json.loads(f'"{match.group(1)}"'))
PYEOF
        then
            err "codex managed hook block is absent or malformed (run install-codex-hooks.sh + re-trust)"
        else
            # Live commands carry the chassis stamp the overlay has no reason to
            # know about (#177). Comparing raw would make every managed block
            # read as missing-and-extra the moment an installer is re-run.
            python3 "$HARNESS_DIR/scripts/lib/chassis_stamp.py" --unstamp < "$got" > "$got.n" && mv "$got.n" "$got"
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
            jq -r '.grok.hooks[]' "$OVERLAY" \
                | awk -v root="$PLUGINS_DIR" '{ runner = ($1 ~ /\.py$/ ? "python3" : "bash"); print runner " " root "/" $0 }'
            python3 "$HARNESS_DIR/scripts/lib/cross_cli_externals.py" "$OVERLAY" grok "$HARNESS_DIR"
        } | sort > "$want"
        jq -r '.hooks | to_entries[] | .value[] | .hooks[] | .command' "$GROK_HOOKS" 2>/dev/null \
            | grep -E 'hooks/' \
            | python3 "$HARNESS_DIR/scripts/lib/chassis_stamp.py" --unstamp | sort > "$got"
        if ! diff -u "$want" "$got" >&2; then
            err "grok harness.json hook set differs from overlay (run install-grok-hooks.sh)"
        fi
        rm -f "$want" "$got"
    else
        echo "skip: $GROK_HOOKS not present"
    fi

    # 4. kimi config.toml marker block carries exactly the overlay set (commands only)
    KIMI_CONFIG="${KIMI_CODE_HOME:-$HOME/.kimi-code}/config.toml"
    if [[ -f "$KIMI_CONFIG" ]] && grep -qF '# >>> harness-kimi hooks' "$KIMI_CONFIG"; then
        want=$(mktemp); got=$(mktemp)
        jq -r '.kimi.hooks[] | if type == "object" then .path else . end' "$OVERLAY" \
            | awk -v root="$PLUGINS_DIR" '{ runner = ($1 ~ /\.py$/ ? "python3" : "bash"); print runner " " root "/" $0 }' \
            | sort > "$want"
        sed -n '/# >>> harness-kimi hooks/,/# <<< harness-kimi hooks <<</p' "$KIMI_CONFIG" \
            | sed -n "s/^command = ['\"]\\(.*\\)['\"]$/\\1/p" \
            | python3 "$HARNESS_DIR/scripts/lib/chassis_stamp.py" --unstamp | sort > "$got"
        if ! diff -u "$want" "$got" >&2; then
            err "kimi config.toml hook block differs from overlay (run install-kimi-hooks.sh)"
        fi
        rm -f "$want" "$got"
    else
        echo "skip: no harness-kimi hook block in $KIMI_CONFIG"
    fi
fi

if [[ $fail -eq 0 ]]; then
    echo "cross-CLI hook overlay: in sync ($(( ${#ALL_HOOKS[@]} )) hooks checked, live=$LIVE)"
fi
exit $fail
