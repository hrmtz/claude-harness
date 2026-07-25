#!/bin/bash
# install-codex-hooks.sh — wire harness hooks into ~/.codex/config.toml
#
# The hook SET comes from plugins/cross_cli_hooks.json (gh #55); the
# event/matcher/timeout for each hook is looked up from the owning plugin's
# hooks/hooks.json (the same SSOT that drives Claude via sync_hooks_to_live.py).
# Adding/removing a Codex hook = edit cross_cli_hooks.json, re-run this script,
# re-trust in Codex (trust hashes cover the config block).
#
# Idempotent. Script CONTENT changes need no re-run (config references repo
# paths directly); only hook SET changes do.
# After running: open Codex, press Tab, Enter to review hooks, then 't' to trust.

set -euo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODEX_CONFIG="$HOME/.codex/config.toml"
OVERLAY="$HARNESS_DIR/plugins/cross_cli_hooks.json"

if [[ ! -f "$OVERLAY" ]]; then
    echo "error: $OVERLAY not found." >&2
    exit 1
fi

# ---- prerequisites ----------------------------------------------------------
if ! command -v codex >/dev/null 2>&1; then
    echo "error: codex CLI not found. Install it first." >&2
    exit 1
fi
ORIGINAL_CODEX="$(command -v codex)"
ORIGINAL_CODEX_REAL="$(readlink -f "$ORIGINAL_CODEX")"
STANDALONE_CURRENT="$HOME/.codex/packages/standalone/current/bin/codex"
SAFE_LAUNCHER="$HARNESS_DIR/plugins/harness-core/bin/codex-cache-safe"
STABLE_DISPATCHER="$HARNESS_DIR/plugins/harness-core/bin/harness-hook"
if [[ "$ORIGINAL_CODEX_REAL" == "$(readlink -f "$SAFE_LAUNCHER")" ||
      "$(basename "$ORIGINAL_CODEX_REAL")" == "codex-cache-safe" ]]; then
    if [[ -x "$HOME/.local/libexec/claude-harness-codex-real" ]]; then
        ORIGINAL_CODEX_REAL="$HOME/.local/libexec/claude-harness-codex-real"
    else
        ORIGINAL_CODEX_REAL="$STANDALONE_CURRENT"
    fi
elif [[ -x "$STANDALONE_CURRENT" &&
        "$ORIGINAL_CODEX_REAL" == "$(readlink -f "$STANDALONE_CURRENT")" ]]; then
    # Keep the stable `current` indirection so a later Codex update is picked up.
    ORIGINAL_CODEX_REAL="$STANDALONE_CURRENT"
fi
if [[ ! -x "$ORIGINAL_CODEX_REAL" ]]; then
    echo "error: real Codex binary not executable: $ORIGINAL_CODEX_REAL" >&2
    exit 1
fi
if ! command -v jq >/dev/null 2>&1; then
    echo "error: jq not found." >&2
    exit 1
fi

mkdir -p "$(dirname "$CODEX_CONFIG")"
touch "$CODEX_CONFIG"

BK="$HOME/sanada_backup_persistent/codex_hooks_install_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BK"
cp "$CODEX_CONFIG" "$BK/config.toml"

# Seed the cache-safe launcher's inventory before publishing it as `codex`.
# Legacy-only installations legitimately produce an empty local-plugin array.
CODEX_STATE_HOME="${CODEX_HOME:-$HOME/.codex}"
INVENTORY_SNAPSHOT="$CODEX_STATE_HOME/plugins/harness-local-inventory.json"
mkdir -p "$(dirname "$INVENTORY_SNAPSHOT")"
INVENTORY_STAGE="$(mktemp "$(dirname "$INVENTORY_SNAPSHOT")/.harness-inventory.XXXXXX")"
if ! "$ORIGINAL_CODEX_REAL" plugin list --json 2>/dev/null | jq -e '
    [
      .installed[]
      | select(
          .installed == true and
          .enabled == true and
          (.marketplaceSource.sourceType // "unknown") == "local" and
          .marketplaceName != "openai-curated" and
          .marketplaceName != "openai-curated-remote"
        )
      | {
          plugin_id: .pluginId,
          marketplace: .marketplaceName,
          plugin_name: .name,
          source_path: .source.path
        }
    ]
    | if all(.[]; all(.[]; type == "string"))
      then .
      else error("invalid inventory snapshot row")
      end
' >"$INVENTORY_STAGE"; then
    rm -f "$INVENTORY_STAGE"
    echo "error: unable to seed cache-safe plugin inventory." >&2
    exit 1
fi
mv -f "$INVENTORY_STAGE" "$INVENTORY_SNAPSHOT"

# Publish the shared cross-CLI guard on PATH. Preserve a replaced entrypoint in
# the same persistent backup used for the Codex config.
CROSS_CLI_SRC="$HARNESS_DIR/plugins/harness-core/bin/harness-cross-cli"
CROSS_CLI_DST="$HOME/.local/bin/harness-cross-cli"
mkdir -p "$(dirname "$CROSS_CLI_DST")"
if [[ -e "$CROSS_CLI_DST" || -L "$CROSS_CLI_DST" ]]; then
    if [[ "$(readlink -f "$CROSS_CLI_DST" 2>/dev/null || true)" != \
          "$(readlink -f "$CROSS_CLI_SRC")" ]]; then
        mv "$CROSS_CLI_DST" "$BK/harness-cross-cli.previous"
    fi
fi
if [[ ! -e "$CROSS_CLI_DST" && ! -L "$CROSS_CLI_DST" ]]; then
    ln -s "$CROSS_CLI_SRC" "$CROSS_CLI_DST"
fi

# ---- stable hook/launcher publication (#110) --------------------------------
# Native plugin hooks must not depend on a versioned cache directory that a
# concurrent Codex startup can delete. Publish stable user-level entrypoints,
# preserving any replaced path in the same Sanada backup.
install_stable_link() {
    local target="$1" link="$2" backup_name="$3"
    mkdir -p "$(dirname "$link")"
    if [[ -e "$link" || -L "$link" ]]; then
        if [[ "$(readlink -f "$link" 2>/dev/null || true)" == "$(readlink -f "$target")" ]]; then
            return 0
        fi
        mv "$link" "$BK/$backup_name"
    fi
    ln -s "$target" "$link"
}

REAL_CODEX_LINK="$HOME/.local/libexec/claude-harness-codex-real"
if [[ "$ORIGINAL_CODEX" == "$HOME/.local/bin/codex" &&
      ! -L "$ORIGINAL_CODEX" &&
      ! -e "$REAL_CODEX_LINK" ]]; then
    mkdir -p "$(dirname "$REAL_CODEX_LINK")"
    cp -a "$ORIGINAL_CODEX" "$BK/codex.previous"
    mv "$ORIGINAL_CODEX" "$REAL_CODEX_LINK"
    ORIGINAL_CODEX_REAL="$REAL_CODEX_LINK"
fi
install_stable_link "$ORIGINAL_CODEX_REAL" \
    "$REAL_CODEX_LINK" codex-real.previous
install_stable_link "$STABLE_DISPATCHER" \
    "$HOME/.local/bin/harness-hook" harness-hook.previous
install_stable_link "$SAFE_LAUNCHER" \
    "$HOME/.local/bin/codex-cache-safe" codex-cache-safe.previous
install_stable_link "$SAFE_LAUNCHER" \
    "$HOME/.local/bin/codex" codex.previous

# ---- verify canonical hooks feature ----------------------------------------
FEATURE_LIST="$(codex features list 2>/dev/null)" || {
    echo "error: unable to query Codex feature support." >&2
    exit 1
}
set +e
FEATURE_STATE="$(printf '%s\n' "$FEATURE_LIST" | python3 "$HARNESS_DIR/scripts/lib/codex_hooks_feature.py")"
FEATURE_RC=$?
set -e
if [[ $FEATURE_RC -eq 1 ]]; then
    codex features enable hooks >/dev/null
    FEATURE_LIST="$(codex features list 2>/dev/null)"
    FEATURE_STATE="$(printf '%s\n' "$FEATURE_LIST" | python3 "$HARNESS_DIR/scripts/lib/codex_hooks_feature.py")" || {
        echo "error: Codex accepted 'features enable hooks' but hooks are still unavailable." >&2
        exit 1
    }
elif [[ $FEATURE_RC -ne 0 ]]; then
    echo "error: this Codex version does not expose the canonical 'hooks' feature (state: $FEATURE_STATE). Upgrade Codex." >&2
    exit 1
fi
echo "feature: hooks $FEATURE_STATE"

# ---- rebuild our config.toml block atomically -------------------------------
# Order matters for safety (code-review #52): the OLD code stripped the existing
# hooks from config.toml on disk and THEN ran a fallible generator under
# `set -euo pipefail`; a generator failure left the file with ZERO hooks. Here
# we (1) generate the new block to a temp file first — any failure aborts with
# config.toml untouched — then (2) strip + concatenate into a temp file and
# (3) os.replace() over the original in one atomic step.
BLOCK_TMP="$(mktemp)"
NEWCONF_TMP="$(mktemp)"
trap 'rm -f "$BLOCK_TMP" "$NEWCONF_TMP"' EXIT

# (1) generate the fresh hooks block — must succeed before we touch config.toml
python3 - "$OVERLAY" "$HARNESS_DIR" > "$BLOCK_TMP" <<'PYEOF'
import collections, json, re, sys

overlay_path, harness_dir = sys.argv[1], sys.argv[2]
plugins_dir = f"{harness_dir}/plugins"
overlay = json.load(open(overlay_path))["codex"]
specs = [({"path": item} if isinstance(item, str) else item)
         for item in overlay["hooks"]]

# Look up (event, matcher, timeout, command) for each selected hook from the owning
# plugin's hooks.json — the same SSOT that drives Claude Code.
lookup = {}
for plugin in sorted({spec["path"].split("/")[0] for spec in specs}):
    hooks_json = json.load(open(f"{plugins_dir}/{plugin}/hooks/hooks.json"))
    for event, blocks in hooks_json.get("hooks", {}).items():
        for blk in blocks:
            matcher = blk.get("matcher")
            for h in blk.get("hooks", []):
                tail = h["command"].rsplit(";", 1)[-1]
                match = re.search(r"(?:^|[ /])hooks/(.+)$", tail)
                if not match:
                    continue
                name = match.group(1)
                lookup.setdefault(f"{plugin}/hooks/{name}", []).append((
                    event, matcher, h.get("timeout", 5), h["command"]))

# Group by (event, matcher) preserving overlay order.
groups = collections.OrderedDict()
for spec in specs:
    hook = spec["path"]
    candidates = lookup.get(hook, [])
    if "event" in spec:
        candidates = [item for item in candidates if item[0] == spec["event"]]
    if "matcher" in spec:
        candidates = [item for item in candidates if item[1] == spec["matcher"]]
    if len(candidates) != 1:
        detail = "not registered" if not candidates else "ambiguous; add an event selector"
        print(f"error: {hook}: {detail} in its plugin hooks.json", file=sys.stderr)
        sys.exit(1)
    event, matcher, timeout, command = candidates[0]
    groups.setdefault((event, matcher), []).append((hook, timeout, command))
sys.path.insert(0, f"{harness_dir}/scripts/lib")
from cross_cli_externals import resolve  # noqa: E402
for ext in resolve(overlay_path, "codex", harness_dir):
    groups.setdefault((ext["event"], None), []).append(
        (None, ext["timeout"], ext["command"]))

print("# harness hooks — generated by install-codex-hooks.sh from")
print("# plugins/cross_cli_hooks.json (hook set) + plugins/*/hooks/hooks.json (SSOT).")
print("# Do not edit this block by hand; edit the overlay and re-run.")
print("# To trust: open Codex → Tab → Enter on each Review>0 row → t → Esc")
for (event, matcher), entries in groups.items():
    print(f"\n[[hooks.{event}]]")
    if matcher:
        print(f'matcher = "{matcher}"')
    for entry in entries:
        if entry[0] is None:
            command, timeout = entry[2], entry[1]
        else:
            plugin = entry[0].split("/", 1)[0]
            plugin_root = f"{plugins_dir}/{plugin}"
            command = entry[2].replace("${CLAUDE_PLUGIN_ROOT}", plugin_root)
            timeout = entry[1]
        print(f"\n[[hooks.{event}.hooks]]")
        print('type = "command"')
        print(f"command = {json.dumps(command)}")
        print(f"timeout = {timeout}")
PYEOF

# (2) replace only our marker-bounded block. On first run after an older
# installer, migrate only leaf tables whose commands are in the new managed
# block. Other hook sources and [hooks.state] remain byte-for-byte present.
python3 "$HARNESS_DIR/scripts/lib/merge_codex_hooks.py" \
    "$CODEX_CONFIG" "$BLOCK_TMP" "$NEWCONF_TMP"
echo "rebuilt $CODEX_CONFIG (claude-harness block regenerated atomically)"

echo "wrote hooks to $CODEX_CONFIG (set: $(jq -r '.codex.hooks | length' "$OVERLAY") + $(jq -r '.codex.external | length' "$OVERLAY") external)"

# ---- verify config parses ---------------------------------------------------
if codex features list >/dev/null 2>&1; then
    echo "config OK (codex features list succeeded)"
else
    echo "warning: codex config validation failed — check $CODEX_CONFIG" >&2
fi

# ---- instructions -----------------------------------------------------------
cat <<MSG

Install complete. One-time trust step required:

  1. Run: codex <any prompt>
  2. Press Tab to open the Hooks panel
  3. Press Enter on each event row that shows "Review" > 0
  4. Press 't' to trust the hook
  5. Press Esc to close

Hooks are then active for all future Codex sessions.
Hook set: ${OVERLAY} (codex section)
MSG
