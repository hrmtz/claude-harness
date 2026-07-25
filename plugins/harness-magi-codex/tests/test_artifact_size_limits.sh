#!/usr/bin/env bash
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FANOUT="$HERE/../scripts/magi_fanout_codex.sh"
XFAMILY="$HERE/../scripts/magi_xfamily.sh"

TMP="$(mktemp -d)" || exit 1
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/fanout-state" "$TMP/xfamily-state"
printf '1234' > "$TMP/exact.patch"
printf '12345' > "$TMP/over.patch"
: > "$TMP/empty.patch"

# Keep provider CLIs absent so an accepted exact-limit artifact stops before accounting/launch.
LIMITED_PATH="/usr/bin:/bin"
MAGI_MAX_ARTIFACT_BYTES=4 PATH="$LIMITED_PATH" \
  "$FANOUT" "$TMP/exact.patch" 1 "$TMP/fanout-state" --persona-set bug-hunt \
  >"$TMP/fanout-exact.log" 2>&1
grep -q 'review artifact exceeds' "$TMP/fanout-exact.log" && {
  echo "fanout rejected the exact artifact limit" >&2; exit 1; }
MAGI_MAX_ARTIFACT_BYTES=4 PATH="$LIMITED_PATH" \
  "$FANOUT" "$TMP/over.patch" 1 "$TMP/fanout-state" --persona-set bug-hunt \
  >"$TMP/fanout-over.log" 2>&1
grep -q 'review artifact exceeds 4-byte limit' "$TMP/fanout-over.log" || {
  echo "fanout accepted limit+1 artifact" >&2; exit 1; }
MAGI_MAX_ARTIFACT_BYTES=4 PATH="$LIMITED_PATH" \
  "$FANOUT" "$TMP/empty.patch" 1 "$TMP/fanout-state" --persona-set bug-hunt \
  >"$TMP/fanout-empty.log" 2>&1
grep -q 'review artifact must not be empty' "$TMP/fanout-empty.log" || {
  echo "fanout accepted an empty artifact" >&2; exit 1; }

MAGI_MAX_ARTIFACT_BYTES=4 PATH="$LIMITED_PATH" \
  "$XFAMILY" --reviewer grok "$TMP/exact.patch" 1 - "$TMP/xfamily-state/round_1_xfamily" \
  >"$TMP/xfamily-exact.log" 2>&1
grep -q 'review artifact exceeds' "$TMP/xfamily-exact.log" && {
  echo "cross-family rejected the exact artifact limit" >&2; exit 1; }
MAGI_MAX_ARTIFACT_BYTES=4 PATH="$LIMITED_PATH" \
  "$XFAMILY" --reviewer grok "$TMP/over.patch" 1 - "$TMP/xfamily-state/round_1_xfamily" \
  >"$TMP/xfamily-over.log" 2>&1
grep -q 'review artifact exceeds 4-byte limit' "$TMP/xfamily-over.log" || {
  echo "cross-family accepted limit+1 artifact" >&2; exit 1; }
MAGI_MAX_ARTIFACT_BYTES=4 PATH="$LIMITED_PATH" \
  "$XFAMILY" --reviewer grok "$TMP/empty.patch" 1 - "$TMP/xfamily-state/round_1_empty" \
  >"$TMP/xfamily-empty.log" 2>&1
grep -q 'review artifact must not be empty' "$TMP/xfamily-empty.log" || {
  echo "cross-family accepted an empty artifact" >&2; exit 1; }
python3 - "$TMP/xfamily-state/round_1_xfamily.FAILED.json" <<'PY' >/dev/null 2>&1
import json, sys
payload = json.load(open(sys.argv[1]))
assert payload["diagnostic_unavailable"] == "missing"
PY
[ $? -eq 0 ] || {
  echo "missing provider stderr was not explicitly classified" >&2; exit 1; }

# If both detailed and fallback diagnostic writers fail, the adapter must not claim a path exists.
writer_bin="$TMP/writer-failure-bin"
mkdir -p "$writer_bin"
real_python="$(command -v python3)"
failed_path="$TMP/xfamily-state/round_1_diagfail.FAILED.json"
cat > "$writer_bin/python3" <<'STUB'
#!/usr/bin/env bash
if [ "$1" = "-" ] && [ "${2:-}" = "$FAILED_PATH" ]; then
  exit 73
fi
exec "$REAL_PYTHON" "$@"
STUB
chmod +x "$writer_bin/python3"
MAGI_MAX_ARTIFACT_BYTES=4 PATH="$writer_bin:$LIMITED_PATH" \
  REAL_PYTHON="$real_python" FAILED_PATH="$failed_path" \
  "$XFAMILY" --reviewer grok "$TMP/exact.patch" 1 - \
  "$TMP/xfamily-state/round_1_diagfail" >"$TMP/diagfail.log" 2>&1
diagfail_rc=$?
[ "$diagfail_rc" -eq 2 ] \
  && grep -q 'detailed failure diagnostic publication failed' "$TMP/diagfail.log" \
  && grep -q 'Durable diagnostics unavailable' "$TMP/diagfail.log" \
  && [ ! -e "$failed_path" ] || {
  echo "cross-family falsely claimed failed diagnostic publication" >&2; exit 1; }

find "$TMP" -name 'CAMPAIGN.*.json' | grep -q . && {
  echo "artifact-size refusal charged a campaign attempt" >&2; exit 1; }
echo "test_artifact_size_limits: PASS"
