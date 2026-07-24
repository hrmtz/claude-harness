#!/bin/bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CORE_ROOT="$(cd "$HERE/.." && pwd)"
INSTALLER="$CORE_ROOT/bin/install-cache-safe-entrypoints"
FIXTURE="$(mktemp -d)"
trap 'rm -rf "$FIXTURE"' EXIT

TEST_HOME="$FIXTURE/home"
FAKE_BIN="$TEST_HOME/.codex/packages/standalone/current/bin"
mkdir -p "$TEST_HOME" "$FAKE_BIN"

printf '%s\n' \
  '#!/bin/bash' \
  'if [[ "${1:-} ${2:-} ${3:-}" == "plugin list --json" ]]; then' \
  '  jq -cn --arg source "$TEST_CORE_SOURCE" '"'"'{installed:[{pluginId:"harness-core@claude-harness",marketplaceName:"claude-harness",name:"harness-core",version:"1.0.0",installed:true,enabled:true,marketplaceSource:{sourceType:"local"},source:{path:$source}}]}'"'"'' \
  '  exit 0' \
  'fi' \
  'exit 0' >"$FAKE_BIN/codex"
chmod +x "$FAKE_BIN/codex"

HOME="$TEST_HOME" PATH="$FAKE_BIN:/usr/bin:/bin" \
  TEST_CORE_SOURCE="$CORE_ROOT" CODEX_THREAD_ID=test \
  "$INSTALLER"

actual_hook="$(readlink -f "$TEST_HOME/.local/bin/harness-hook")"
actual_safe="$(readlink -f "$TEST_HOME/.local/bin/codex-cache-safe")"
actual_codex="$(readlink -f "$TEST_HOME/.local/bin/codex")"
actual_real="$(readlink -f "$TEST_HOME/.local/libexec/claude-harness-codex-real")"
[[ "$actual_hook" == "$(readlink -f "$CORE_ROOT/bin/harness-hook")" ]]
[[ "$actual_safe" == "$(readlink -f "$CORE_ROOT/bin/codex-cache-safe")" ]]
[[ "$actual_codex" == "$(readlink -f "$CORE_ROOT/bin/codex-cache-safe")" ]]
[[ "$actual_real" == "$(readlink -f "$FAKE_BIN/codex")" ]]
[[ "$(readlink "$TEST_HOME/.local/libexec/claude-harness-codex-real")" == "$FAKE_BIN/codex" ]]
jq -e --arg source "$CORE_ROOT" \
  '. == [{plugin_id:"harness-core@claude-harness",marketplace:"claude-harness",plugin_name:"harness-core",source_path:$source}]' \
  "$TEST_HOME/.codex/plugins/harness-local-inventory.json" >/dev/null
"$TEST_HOME/.local/bin/harness-hook" --identity |
  grep -Fxq 'claude-harness/harness-hook/v1'

REGULAR_HOME="$FIXTURE/regular-home"
mkdir -p "$REGULAR_HOME/.local/bin"
cp "$FAKE_BIN/codex" "$REGULAR_HOME/.local/bin/codex"
HOME="$REGULAR_HOME" PATH="$REGULAR_HOME/.local/bin:/usr/bin:/bin" \
  TEST_CORE_SOURCE="$CORE_ROOT" "$INSTALLER" --replace
[[ -x "$REGULAR_HOME/.local/libexec/claude-harness-codex-real" ]]
[[ ! -L "$REGULAR_HOME/.local/libexec/claude-harness-codex-real" ]]
[[ "$(readlink -f "$REGULAR_HOME/.local/bin/codex")" == \
   "$(readlink -f "$CORE_ROOT/bin/codex-cache-safe")" ]]
HOME="$REGULAR_HOME" TEST_CORE_SOURCE="$CORE_ROOT" \
  "$REGULAR_HOME/.local/libexec/claude-harness-codex-real" plugin list --json \
  | jq -e '.installed | length == 1' >/dev/null

CUSTOM_HOME="$FIXTURE/custom-home"
CUSTOM_STATE="$FIXTURE/custom-state"
mkdir -p "$CUSTOM_HOME"
HOME="$CUSTOM_HOME" CODEX_HOME="$CUSTOM_STATE" \
  PATH="$FAKE_BIN:/usr/bin:/bin" TEST_CORE_SOURCE="$CORE_ROOT" \
  CODEX_THREAD_ID=test "$INSTALLER"
[[ -f "$CUSTOM_STATE/plugins/harness-local-inventory.json" ]]
[[ ! -e "$CUSTOM_HOME/.codex/plugins/harness-local-inventory.json" ]]

printf 'test_cache_safe_entrypoints: PASS\n'
