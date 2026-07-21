#!/bin/bash
# uninstall-kimi-hooks.sh — remove the harness-kimi marker block from
# ~/.kimi-code/config.toml. Backs up before editing; validates the result
# parses as TOML before installing it. Honors KIMI_CODE_HOME.
#
# This only removes the native [[hooks]] wiring. The session-log scrubber cron
# (if installed) is a separate layer: plugins/harness-kimi/uninstall-kimi-scrubber.sh.

set -euo pipefail

KIMI_HOME="${KIMI_CODE_HOME:-$HOME/.kimi-code}"
CONFIG="$KIMI_HOME/config.toml"

MARK_BEGIN='# >>> harness-kimi hooks'
MARK_END='# <<< harness-kimi hooks <<<'

if [[ ! -f "$CONFIG" ]]; then
    echo "nothing to do: $CONFIG does not exist."
    exit 0
fi
if ! grep -qF "$MARK_BEGIN" "$CONFIG"; then
    echo "nothing to do: no harness-kimi hook block in $CONFIG."
    exit 0
fi

BK="$HOME/sanada_backup_persistent/kimi_hooks_uninstall_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BK"
cp "$CONFIG" "$BK/config.toml"

OUT_TMP="$(mktemp)"
trap 'rm -f "$OUT_TMP"' EXIT

python3 - "$CONFIG" > "$OUT_TMP" <<'PYEOF'
import sys

MARK_BEGIN = "# >>> harness-kimi hooks"
MARK_END = "# <<< harness-kimi hooks <<<"

old = open(sys.argv[1]).read()
pre = old[:old.index(MARK_BEGIN)]
post = old[old.index(MARK_END) + len(MARK_END):]
new = pre.rstrip("\n") + "\n" + post.lstrip("\n")
print(new.rstrip("\n") + "\n", end="")
PYEOF

python3 -c 'import sys, tomllib; tomllib.load(open(sys.argv[1], "rb"))' "$OUT_TMP"

cat "$OUT_TMP" > "$CONFIG"
trap - EXIT

echo "removed harness-kimi hook block from $CONFIG (backup: $BK)"
