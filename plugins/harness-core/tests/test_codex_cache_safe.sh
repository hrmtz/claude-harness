#!/bin/bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WRAPPER="$HERE/../bin/codex-cache-safe"
FIXTURE="$(mktemp -d)"
trap 'rm -rf "$FIXTURE"' EXIT

TEST_HOME="$FIXTURE/home"
SOURCE="$FIXTURE/source"
FAKE_CODEX="$FIXTURE/real-codex"
EXEC_LOG="$FIXTURE/exec.log"
mkdir -p "$TEST_HOME/.codex/plugins/cache/test-market/safety-hooks/1.0.0"
mkdir -p "$SOURCE/.codex-plugin"
printf '%s\n' '{"name":"safety-hooks","version":"1.1.0"}' > "$SOURCE/.codex-plugin/plugin.json"
mkdir -p "$TEST_HOME/.codex/plugins"
jq -cn --arg source "$SOURCE" \
  '[{plugin_id:"safety-hooks@test-market",marketplace:"test-market",plugin_name:"safety-hooks",source_path:$source}]' \
  >"$TEST_HOME/.codex/plugins/harness-local-inventory.json"

cat > "$FAKE_CODEX" <<'FAKE'
#!/bin/bash
set -euo pipefail
if [[ "${1:-}" == "plugin" && "${2:-}" == "list" && "${3:-}" == "--json" ]]; then
  count=0
  [[ -f "${TEST_LIST_COUNT_FILE:-}" ]] && count="$(<"$TEST_LIST_COUNT_FILE")"
  count=$((count + 1))
  [[ -z "${TEST_LIST_COUNT_FILE:-}" ]] || printf '%s\n' "$count" >"$TEST_LIST_COUNT_FILE"
  expected="$(jq -r '.version' "$TEST_PLUGIN_SOURCE/.codex-plugin/plugin.json")"
  state_home="${CODEX_HOME:-$HOME/.codex}"
  if [[ -n "${TEST_DELETE_PATH:-}" && "$count" -eq 1 &&
        ! -d "$state_home/plugins/cache/test-market/safety-hooks/$expected" ]]; then
    mv "$TEST_DELETE_PATH" "$TEST_DELETE_DEST"
  fi
  if [[ "${TEST_MALFORMED_VERIFY:-0}" == "1" && "$count" -eq 2 ]]; then
    jq -cn --arg source "$TEST_PLUGIN_SOURCE" \
      '{installed:[{pluginId:"safety-hooks@test-market",marketplaceName:"test-market",name:"safety-hooks",version:"1.0.0",installed:"yes",enabled:true,marketplaceSource:{sourceType:"local"},source:{path:$source}}]}'
    exit 0
  fi
  if [[ "${TEST_MALFORMED_NESTED:-0}" == "1" ]]; then
    jq -cn \
      '{installed:[{pluginId:"safety-hooks@test-market",marketplaceName:"test-market",name:"safety-hooks",version:"1.0.0",installed:true,enabled:true,marketplaceSource:{sourceType:"local"},source:{path:{bad:true}}}]}'
    exit 0
  fi
  if [[ "${TEST_MALFORMED_FLAGS:-0}" == "1" ]]; then
    jq -cn --arg source "$TEST_PLUGIN_SOURCE" \
      '{installed:[{pluginId:"safety-hooks@test-market",marketplaceName:"test-market",name:"safety-hooks",version:"1.0.0",installed:"yes",enabled:true,marketplaceSource:{sourceType:"local"},source:{path:$source}}]}'
    exit 0
  fi
  active=1.0.0
  [[ -d "$state_home/plugins/cache/test-market/safety-hooks/$expected" ]] && active="$expected"
  jq -cn --arg active "$active" --arg source "$TEST_PLUGIN_SOURCE" \
    '{installed:[{pluginId:"safety-hooks@test-market",marketplaceName:"test-market",name:"safety-hooks",version:$active,installed:true,enabled:true,marketplaceSource:{sourceType:"local"},source:{path:$source}}]}'
  exit 0
fi
if [[ "${1:-} ${2:-}" == "plugin add" && -n "${TEST_LOCK_PATH:-}" ]]; then
  held=0
  for fd in /proc/$$/fd/*; do
    [[ "$(readlink "$fd" 2>/dev/null || true)" == "$TEST_LOCK_PATH" ]] && held=1
  done
  [[ "$held" == "1" ]] || exit 8
  printf '%s\n' "mutation lock held" >>"$TEST_EXEC_LOG"
fi
printf '%s\n' "$*" >> "$TEST_EXEC_LOG"
FAKE
chmod +x "$FAKE_CODEX"

HOME="$TEST_HOME" TEST_PLUGIN_SOURCE="$SOURCE" TEST_EXEC_LOG="$EXEC_LOG" \
  TEST_DELETE_PATH="$TEST_HOME/.codex/plugins/cache/test-market/safety-hooks/1.0.0" \
  TEST_DELETE_DEST="$FIXTURE/deleted-during-first-list" \
  CODEX_REAL_BIN="$FAKE_CODEX" "$WRAPPER" exec "safe fixture"

[[ -d "$TEST_HOME/.codex/plugins/cache/test-market/safety-hooks/1.0.0" ]]
[[ -d "$TEST_HOME/.codex/plugins/cache/test-market/safety-hooks/1.1.0" ]]
[[ ! -e "$FIXTURE/deleted-during-first-list" ]]
grep -Fxq 'exec safe fixture' "$EXEC_LOG"

mv "$TEST_HOME/.codex/plugins/cache/test-market/safety-hooks/1.1.0" \
  "$FIXTURE/missing-active-generation"
HOME="$TEST_HOME" TEST_PLUGIN_SOURCE="$SOURCE" TEST_EXEC_LOG="$EXEC_LOG" \
  CODEX_REAL_BIN="$FAKE_CODEX" "$WRAPPER" exec "restore fixture"
[[ -d "$TEST_HOME/.codex/plugins/cache/test-market/safety-hooks/1.1.0" ]]
grep -Fxq 'exec restore fixture' "$EXEC_LOG"

if HOME="$TEST_HOME" TEST_PLUGIN_SOURCE="$SOURCE" TEST_EXEC_LOG="$EXEC_LOG" \
  TEST_MALFORMED_NESTED=1 CODEX_REAL_BIN="$FAKE_CODEX" \
  CODEX_CACHE_SAFE_CHECK_ONLY=1 "$WRAPPER" 2>/dev/null; then
  echo "nested malformed inventory unexpectedly passed" >&2
  exit 1
fi

if HOME="$TEST_HOME" TEST_PLUGIN_SOURCE="$SOURCE" TEST_EXEC_LOG="$EXEC_LOG" \
  TEST_MALFORMED_FLAGS=1 CODEX_REAL_BIN="$FAKE_CODEX" \
  CODEX_CACHE_SAFE_CHECK_ONLY=1 "$WRAPPER" 2>/dev/null; then
  echo "malformed inventory state flags unexpectedly passed" >&2
  exit 1
fi

VERIFY_HOME="$FIXTURE/verify-home"
VERIFY_COUNT="$FIXTURE/verify-count"
mkdir -p "$VERIFY_HOME/.codex/plugins/cache/test-market/safety-hooks/1.0.0"
mkdir -p "$VERIFY_HOME/.codex/plugins"
cp "$TEST_HOME/.codex/plugins/harness-local-inventory.json" \
  "$VERIFY_HOME/.codex/plugins/harness-local-inventory.json"
if HOME="$VERIFY_HOME" TEST_PLUGIN_SOURCE="$SOURCE" TEST_EXEC_LOG="$EXEC_LOG" \
  TEST_LIST_COUNT_FILE="$VERIFY_COUNT" TEST_MALFORMED_VERIFY=1 \
  CODEX_REAL_BIN="$FAKE_CODEX" CODEX_CACHE_SAFE_CHECK_ONLY=1 \
  "$WRAPPER" 2>/dev/null; then
  echo "malformed verification inventory unexpectedly passed" >&2
  exit 1
fi

SNAPSHOT_HOME="$FIXTURE/snapshot-home"
mkdir -p "$SNAPSHOT_HOME/.codex/plugins"
jq -cn --arg source "$SOURCE" \
  '[{plugin_id:"safety-hooks@bad",marketplace:"bad market",plugin_name:"safety-hooks",source_path:$source}]' \
  >"$SNAPSHOT_HOME/.codex/plugins/harness-local-inventory.json"
if HOME="$SNAPSHOT_HOME" TEST_PLUGIN_SOURCE="$SOURCE" TEST_EXEC_LOG="$EXEC_LOG" \
  CODEX_REAL_BIN="$FAKE_CODEX" "$WRAPPER" exec "bad snapshot" 2>/dev/null; then
  echo "invalid inventory snapshot segment unexpectedly passed" >&2
  exit 1
fi

STALE_HOME="$FIXTURE/stale-home"
mkdir -p "$STALE_HOME/.codex/plugins"
jq -cn \
  '[{plugin_id:"removed@test-market",marketplace:"test-market",plugin_name:"removed",source_path:"/path/that/no/longer/exists"}]' \
  >"$STALE_HOME/.codex/plugins/harness-local-inventory.json"
HOME="$STALE_HOME" TEST_PLUGIN_SOURCE="$SOURCE" TEST_EXEC_LOG="$EXEC_LOG" \
  CODEX_REAL_BIN="$FAKE_CODEX" CODEX_CACHE_SAFE_CHECK_ONLY=1 "$WRAPPER"
jq -e --arg source "$SOURCE" \
  '. == [{plugin_id:"safety-hooks@test-market",marketplace:"test-market",plugin_name:"safety-hooks",source_path:$source}]' \
  "$STALE_HOME/.codex/plugins/harness-local-inventory.json" >/dev/null

printf '%s\n' '{"name":"safety-hooks","version":"1.2.0"}' > "$SOURCE/.codex-plugin/plugin.json"
HOME="$TEST_HOME" TEST_PLUGIN_SOURCE="$SOURCE" TEST_EXEC_LOG="$EXEC_LOG" \
  CODEX_REAL_BIN="$FAKE_CODEX" "$WRAPPER" exec "next generation fixture"
[[ -d "$TEST_HOME/.codex/plugins/cache/test-market/safety-hooks/1.2.0" ]]
[[ -d "$TEST_HOME/.codex/plugins/cache/test-market/safety-hooks/1.0.0" ]]

HOME="$TEST_HOME" TEST_PLUGIN_SOURCE="$SOURCE" TEST_EXEC_LOG="$EXEC_LOG" \
  TEST_LOCK_PATH="$TEST_HOME/.codex/plugins/cache/.harness-generation.lock" \
  CODEX_REAL_BIN="$FAKE_CODEX" "$WRAPPER" plugin add fixture
grep -Fxq 'mutation lock held' "$EXEC_LOG"
grep -Fxq 'plugin add fixture' "$EXEC_LOG"

printf 'test_codex_cache_safe: PASS\n'
