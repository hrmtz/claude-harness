#!/usr/bin/env bash
# gh #105 regression: the Formation pane-messaging double-submit rail must be present
# on the always-loaded instruction surfaces (canonical rail, Kimi AGENTS template) and
# must survive the install paths (pane-rail upsert, kimi AGENTS install).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FORMATION="$HERE/.."
RAIL="$FORMATION/agents/pane-messaging-rail.md"
INSTALLER="$FORMATION/bin/install-pane-messaging-rail.sh"
KIMI_TEMPLATE="$HERE/../../harness-kimi/AGENTS.md.template"
KIMI_INSTALLER="$HERE/../../harness-kimi/install-kimi-agents.sh"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

pass=0; fail=0
ok()  { echo "  ok   - $1"; pass=$((pass+1)); }
bad() { echo "  FAIL - $1"; fail=$((fail+1)); }

# Tokens that pin the contract on every surface: preferred route, copy-mode cancel,
# bracketed paste, both delays, double Enter, and the shell-launch distinction.
check_surface() {
  local label="$1" file="$2" single_token="$3"
  local missing=""
  for tok in "formation msg" "tmux_send_submit" "send-keys -X cancel" "#{pane_in_mode}" \
             "load-buffer" "paste-buffer -p" "0.4" "0.5" "$single_token"; do
    grep -qF -- "$tok" "$file" || missing="$missing $tok"
  done
  # Double Enter: two distinct Enter submissions separated by the second delay.
  grep -qF -- "sleep ~0.4s" "$file" && grep -qF -- "sleep ~0.5s" "$file" \
    || missing="$missing delayed-double-submit"
  [ "$(grep -cF -- 'Enter' "$file")" -ge 2 ] || missing="$missing double-Enter"
  if [ -z "$missing" ]; then ok "$label carries the double-submit contract"
  else bad "$label missing:$missing"; fi
}

check_surface "canonical rail" "$RAIL" 'single-`Enter`'
check_surface "kimi AGENTS template" "$KIMI_TEMPLATE" "1発"

# Installer: fresh target gets the managed block with the full contract.
TARGET="$TMP/AGENTS.md"
printf '%s\n' '# pre-existing foreign content' > "$TARGET"
HOME="$TMP/home" bash "$INSTALLER" "$TARGET" >/dev/null
check_surface "installed codex AGENTS surface" "$TARGET" 'single-`Enter`'
grep -qF 'harness-formation:pane-messaging-rail:start' "$TARGET" \
  && ok "managed block is marker-bounded" || bad "markers missing"
grep -qF 'pre-existing foreign content' "$TARGET" \
  && ok "foreign content preserved on install" || bad "foreign content lost on install"

# Idempotent re-run.
cp "$TARGET" "$TMP/first"
HOME="$TMP/home" bash "$INSTALLER" "$TARGET" >/dev/null
cmp -s "$TMP/first" "$TARGET" && ok "installer is idempotent" \
                              || bad "installer re-run changed the file"

# A stale managed block is replaced, not duplicated.
sed -i 's/visible-but-unsubmitted/STALE-WORD/' "$TARGET"
HOME="$TMP/home" bash "$INSTALLER" "$TARGET" >/dev/null
if ! grep -qF 'STALE-WORD' "$TARGET" \
   && [ "$(grep -cF 'pane-messaging-rail:start' "$TARGET")" -eq 1 ]; then
  ok "stale managed block is replaced in place"
else
  bad "stale managed block survived or duplicated"
fi

# A persistent Sanada backup is taken before modifying an existing file.
bk_count="$(find "$TMP/home/sanada_backup_persistent" -name 'AGENTS.md' -type f 2>/dev/null | wc -l)"
[ "$bk_count" -ge 3 ] && ok "sanada backup taken on every modifying run ($bk_count)" \
                      || bad "expected >=3 sanada backups, got $bk_count"
bk_latest=""
for f in "$TMP/home/sanada_backup_persistent"/*/AGENTS.md; do
  [ -f "$f" ] || continue
  grep -qF 'STALE-WORD' "$f" && bk_latest="$f"
done
[ -n "$bk_latest" ] \
  && ok "backup preserves the pre-modification bytes" \
  || bad "no backup holds the pre-modification (STALE-WORD) content"

# Same-second burst: every modifying run must get its OWN backup directory
# (atomic mktemp -d uniqueness — no timestamp/PID collision, no overwrite),
# and every run's preimage must be individually restorable.
BURST_HOME="$TMP/home3"
BURST_T="$TMP/burst-AGENTS.md"
printf 'v0 original\n' > "$BURST_T"
HOME="$BURST_HOME" bash "$INSTALLER" "$BURST_T" >/dev/null   # preimage: v0
printf '# foreign append v1\n' >> "$BURST_T"
HOME="$BURST_HOME" bash "$INSTALLER" "$BURST_T" >/dev/null   # preimage: v0+rail+v1
printf '# foreign append v2\n' >> "$BURST_T"
HOME="$BURST_HOME" bash "$INSTALLER" "$BURST_T" >/dev/null   # preimage: v0+rail+v1+v2
burst_dirs="$(find "$BURST_HOME/sanada_backup_persistent" -mindepth 1 -maxdepth 1 -type d | wc -l)"
[ "$burst_dirs" -eq 3 ] && ok "burst runs produce 3 distinct backup dirs" \
                        || bad "backup dir collision in burst ($burst_dirs dirs)"
pre1=""; pre2=""; pre3=""
for f in "$BURST_HOME/sanada_backup_persistent"/*/burst-AGENTS.md; do
  [ -f "$f" ] || continue
  if ! grep -qF 'foreign append v1' "$f"; then pre1="$f"
  elif ! grep -qF 'foreign append v2' "$f"; then pre2="$f"
  else pre3="$f"; fi
done
if [ -n "$pre1" ] && [ -n "$pre2" ] && [ -n "$pre3" ] \
   && [ "$pre1" != "$pre2" ] && [ "$pre2" != "$pre3" ] && [ "$pre1" != "$pre3" ] \
   && grep -qF 'v0 original' "$pre1" && grep -qF 'foreign append v2' "$pre3"; then
  ok "every burst run's preimage is individually restorable"
else
  bad "burst preimages lost or overwritten (pre1=$pre1 pre2=$pre2 pre3=$pre3)"
fi

# Marker-mismatch states fail closed: target untouched, nonzero exit.
S='<!-- harness-formation:pane-messaging-rail:start -->'
E='<!-- harness-formation:pane-messaging-rail:end -->'
expect_fail_closed() {
  local label="$1"; shift
  local victim="$TMP/victim.md"
  printf '%s\n' '# keep me' "$@" > "$victim"
  cp "$victim" "$TMP/victim.before"
  if HOME="$TMP/home2" bash "$INSTALLER" "$victim" >/dev/null 2>&1; then
    bad "$label: installer succeeded on an inconsistent marker state"
  elif cmp -s "$TMP/victim.before" "$victim"; then
    ok "$label: fail-closed, target untouched"
  else
    bad "$label: installer modified the target despite failing"
  fi
}
expect_fail_closed "dangling start marker" "$S" "body"
expect_fail_closed "orphan end marker" "body" "$E"
expect_fail_closed "duplicated start marker" "$S" "$S" "body" "$E"
expect_fail_closed "inverted marker order" "$E" "body" "$S"
expect_fail_closed "marker embedded in a longer line" "$S trailing comment" "body" "$E"
expect_fail_closed "indented marker line" "  $S" "body" "$E"

# Kimi install path propagates the rail into a target project.
mkdir -p "$TMP/proj"
bash "$KIMI_INSTALLER" "$TMP/proj" >/dev/null
check_surface "kimi-installed project AGENTS.md" "$TMP/proj/AGENTS.md" "1発"

echo "test_pane_messaging_rail: $pass passed, $fail failed"
exit $((fail > 0))
