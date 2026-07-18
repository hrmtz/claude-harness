#!/usr/bin/env bash
# Remove only the legacy claude-harness inline Codex block after plugin migration.
set -euo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODEX_CONFIG="${CODEX_CONFIG:-$HOME/.codex/config.toml}"
[[ -f "$CODEX_CONFIG" ]] || { echo "no Codex config: $CODEX_CONFIG"; exit 0; }

BK="$HOME/sanada_backup_persistent/codex_hooks_uninstall_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BK"
cp "$CODEX_CONFIG" "$BK/config.toml"

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
python3 - "$CODEX_CONFIG" "$TMP" "$HARNESS_DIR/scripts/lib/merge_codex_hooks.py" <<'PY'
import importlib.util
import os
import pathlib
import sys

config, output, helper = map(pathlib.Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location("merge_codex_hooks", helper)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
content = config.read_text()
cleaned, removed = module.remove_managed_block(content)
if not removed:
    print("no claude-harness managed hook block; unchanged")
    raise SystemExit(0)
output.write_text(cleaned)
os.replace(output, config)
print("removed claude-harness managed hook block; unrelated config preserved")
PY

codex features list >/dev/null
echo "backup: $BK/config.toml"
