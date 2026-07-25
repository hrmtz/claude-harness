#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL="$HERE/../install-codex-skills.sh"
TMP="$(mktemp -d)" || { echo "cannot create temporary directory" >&2; exit 1; }
[ -n "$TMP" ] || { echo "mktemp returned an empty path" >&2; exit 1; }
trap 'rm -rf "$TMP"' EXIT

CODEX_HOME="$TMP/codex" bash "$INSTALL" >/dev/null || exit 1
for skill in magi dual-magi-review ultramagi; do
  [ -L "$TMP/codex/skills/$skill" ] || { echo "missing $skill" >&2; exit 1; }
done
CODEX_HOME="$TMP/codex" bash "$INSTALL" >/dev/null || { echo "idempotent reinstall failed" >&2; exit 1; }
env -u HOME -u CODEX_HOME bash "$INSTALL" --copy >/dev/null 2>"$TMP/no-codex-home.err" && {
  echo "Codex installer accepted missing HOME and CODEX_HOME" >&2; exit 1; }
grep -Fq 'set CODEX_HOME or HOME' "$TMP/no-codex-home.err" || {
  echo "Codex installer did not report controlled missing-home error" >&2; exit 1; }

UNINSTALL="$HERE/../uninstall-codex-skills.sh"
CODEX_HOME="$TMP/uninstall" bash "$INSTALL" >/dev/null || exit 1
CODEX_HOME="$TMP/uninstall" bash "$UNINSTALL" >/dev/null || exit 1
for skill in magi dual-magi-review ultramagi; do
  [ ! -e "$TMP/uninstall/skills/$skill" ] || {
    echo "uninstall left $skill behind" >&2; exit 1; }
done

mkdir -p "$TMP/invalid-marker/skills/magi"
: > "$TMP/invalid-marker/skills/magi/.harness-magi-codex"
CODEX_HOME="$TMP/invalid-marker" bash "$UNINSTALL" >/dev/null 2>&1 && {
  echo "uninstall reported success after invalid-marker refusal" >&2; exit 1; }
[ -d "$TMP/invalid-marker/skills/magi" ] || {
  echo "uninstall removed a skill with an invalid marker" >&2; exit 1; }

CODEX_HOME="$TMP/trailing-marker" bash "$INSTALL" --copy >/dev/null || exit 1
printf '\n' >> "$TMP/trailing-marker/skills/magi/.harness-magi-codex"
CODEX_HOME="$TMP/trailing-marker" bash "$INSTALL" --copy >/dev/null 2>&1 && {
  echo "installer accepted an ownership marker with trailing blank bytes" >&2; exit 1; }
[ -f "$TMP/trailing-marker/skills/magi/SKILL.md" ] || {
  echo "invalid trailing marker caused the skill to be removed" >&2; exit 1; }

mkdir -p "$TMP/failing-bin"
printf '#!/usr/bin/env bash\nexit 1\n' > "$TMP/failing-bin/unlink"
chmod +x "$TMP/failing-bin/unlink"
CODEX_HOME="$TMP/unlink-failure" bash "$INSTALL" >/dev/null || exit 1
PATH="$TMP/failing-bin:$PATH" CODEX_HOME="$TMP/unlink-failure" \
  bash "$UNINSTALL" >/dev/null 2>&1 && {
  echo "uninstall reported success after unlink failure" >&2; exit 1; }
[ -L "$TMP/unlink-failure/skills/magi" ] || {
  echo "unlink failure did not preserve the installed skill" >&2; exit 1; }

mkdir -p "$TMP/failing-rm-bin"
printf '#!/usr/bin/env bash\nexit 1\n' > "$TMP/failing-rm-bin/rm"
chmod +x "$TMP/failing-rm-bin/rm"
CODEX_HOME="$TMP/remove-failure" bash "$INSTALL" >/dev/null || exit 1
PATH="$TMP/failing-rm-bin:$PATH" CODEX_HOME="$TMP/remove-failure" \
  bash "$INSTALL" >"$TMP/remove-failure.log" 2>&1 && {
  echo "installer reported success after owned-entry removal failed" >&2; exit 1; }
grep -q 'cannot remove old symlink' "$TMP/remove-failure.log" || {
  echo "installer did not report the failed owned-entry removal" >&2; exit 1; }
[ -L "$TMP/remove-failure/skills/magi" ] || {
  echo "failed owned-entry removal did not preserve the skill" >&2; exit 1; }

for skill in magi dual-magi-review ultramagi; do
  foreign="$TMP/foreign-$skill"
  mkdir -p "$foreign/skills/$skill"
  printf 'foreign\n' > "$foreign/skills/$skill/SKILL.md"
  CODEX_HOME="$foreign" bash "$INSTALL" >/dev/null 2>&1 && {
    echo "foreign $skill was overwritten" >&2; exit 1; }
  grep -qx foreign "$foreign/skills/$skill/SKILL.md" || {
    echo "foreign $skill changed" >&2; exit 1; }
  for candidate in magi dual-magi-review ultramagi; do
    if [ "$candidate" != "$skill" ] && [ -e "$foreign/skills/$candidate" ]; then
      echo "foreign $skill refusal partially installed $candidate" >&2
      exit 1
    fi
  done

  symlink_home="$TMP/foreign-link-$skill"
  foreign_target="$TMP/foreign-target-$skill"
  mkdir -p "$symlink_home/skills" "$foreign_target"
  ln -s "$foreign_target" "$symlink_home/skills/$skill"
  CODEX_HOME="$symlink_home" bash "$INSTALL" >/dev/null 2>&1 && {
    echo "foreign symlink $skill was overwritten" >&2; exit 1; }
  [ -L "$symlink_home/skills/$skill" ] || {
    echo "foreign symlink $skill was removed" >&2; exit 1; }
  [ "$(readlink -f "$symlink_home/skills/$skill")" = "$foreign_target" ] || {
    echo "foreign symlink $skill was retargeted" >&2; exit 1; }
done

# A non-cooperating writer can replace an owned destination after phase 1. The readlink wrapper
# performs that swap immediately after returning the owned target to preflight; phase 2 must
# revalidate and refuse the now-foreign directory without deleting it.
race_home="$TMP/race-after-preflight"
CODEX_HOME="$race_home" bash "$INSTALL" >/dev/null || exit 1
race_bin="$TMP/race-readlink-bin"
mkdir -p "$race_bin"
real_readlink="$(command -v readlink)"
cat > "$race_bin/readlink" <<'STUB'
#!/usr/bin/env bash
result="$("$REAL_READLINK" "$@")"
last="${!#}"
if [ "$last" = "$RACE_HOME/skills/magi" ] && [ ! -e "$RACE_SENTINEL" ]; then
  rm -f "$last"
  mkdir -p "$last"
  printf 'foreign-after-preflight\n' > "$last/SKILL.md"
  : > "$RACE_SENTINEL"
fi
printf '%s\n' "$result"
STUB
chmod +x "$race_bin/readlink"
PATH="$race_bin:$PATH" REAL_READLINK="$real_readlink" RACE_HOME="$race_home" \
  RACE_SENTINEL="$TMP/codex-race-swapped" CODEX_HOME="$race_home" \
  bash "$INSTALL" >"$TMP/codex-race.log" 2>&1 && {
  echo "installer accepted a foreign replacement created after preflight" >&2; exit 1; }
grep -qx 'foreign-after-preflight' "$race_home/skills/magi/SKILL.md" || {
  echo "installer deleted the foreign replacement created after preflight" >&2; exit 1; }
grep -q 'refusing foreign skill directory' "$TMP/codex-race.log" || {
  echo "installer did not report late destination ownership drift" >&2; exit 1; }

# Swap the path at the destructive boundary itself. The mv wrapper replaces dst after the last
# path validation but before displacement. The installer must validate the displaced object,
# restore it, and refuse without deletion.
post_race_home="$TMP/race-before-displacement"
CODEX_HOME="$post_race_home" bash "$INSTALL" >/dev/null || exit 1
post_race_bin="$TMP/race-mv-bin"
mkdir -p "$post_race_bin"
real_mv="$(command -v mv)"
cat > "$post_race_bin/mv" <<'STUB'
#!/usr/bin/env bash
if [ "$1" = "$RACE_HOME/skills/magi" ] && [ ! -e "$RACE_SENTINEL" ]; then
  rm -f "$1"
  mkdir -p "$1"
  printf 'foreign-at-displacement\n' > "$1/SKILL.md"
  : > "$RACE_SENTINEL"
fi
exec "$REAL_MV" "$@"
STUB
chmod +x "$post_race_bin/mv"
PATH="$post_race_bin:$PATH" REAL_MV="$real_mv" RACE_HOME="$post_race_home" \
  RACE_SENTINEL="$TMP/codex-post-race-swapped" CODEX_HOME="$post_race_home" \
  bash "$INSTALL" >"$TMP/codex-post-race.log" 2>&1 && {
  echo "installer accepted a foreign object swapped at displacement" >&2; exit 1; }
grep -qx 'foreign-at-displacement' "$post_race_home/skills/magi/SKILL.md" || {
  echo "installer deleted the foreign object displaced after validation" >&2; exit 1; }

# Create a foreign directory after the owned predecessor has been displaced and validated, at the
# exact publication call. RENAME_NOREPLACE must refuse without nesting the stage into that tree.
publish_race_home="$TMP/race-before-publication"
CODEX_HOME="$publish_race_home" bash "$INSTALL" >/dev/null || exit 1
publish_race_bin="$TMP/race-python-bin"
mkdir -p "$publish_race_bin"
real_python="$(command -v python3)"
rename_helper="$(readlink -f "$HERE/../scripts/magi_rename_noreplace.py")"
cat > "$publish_race_bin/python3" <<'STUB'
#!/usr/bin/env bash
if [ "$1" = "$RENAME_HELPER" ] && [ "$3" = "$RACE_HOME/skills/magi" ] \
    && [ ! -e "$RACE_SENTINEL" ]; then
  mkdir -p "$3"
  printf 'foreign-before-publish\n' > "$3/SKILL.md"
  : > "$RACE_SENTINEL"
fi
exec "$REAL_PYTHON" "$@"
STUB
chmod +x "$publish_race_bin/python3"
PATH="$publish_race_bin:$PATH" REAL_PYTHON="$real_python" RENAME_HELPER="$rename_helper" \
  RACE_HOME="$publish_race_home" RACE_SENTINEL="$TMP/codex-publish-race" \
  CODEX_HOME="$publish_race_home" bash "$INSTALL" >"$TMP/codex-publish-race.log" 2>&1 && {
  echo "installer accepted a foreign destination created before publication" >&2; exit 1; }
grep -qx 'foreign-before-publish' "$publish_race_home/skills/magi/SKILL.md" || {
  echo "installer replaced the late foreign publication destination" >&2; exit 1; }
[ ! -e "$publish_race_home/skills/magi/entry" ] || {
  echo "installer nested its stage into the late foreign directory" >&2; exit 1; }
find "$publish_race_home/skills" -maxdepth 1 -type l -name '.magi.old.*' | grep -q . || {
  echo "installer did not preserve the displaced owned predecessor" >&2; exit 1; }

# A failure while publishing the second skill must roll the already-published first skill back to
# its exact predecessor, not leave a mixed install generation.
transaction_home="$TMP/transaction-second-publish"
CODEX_HOME="$transaction_home" bash "$INSTALL" --copy >/dev/null || exit 1
printf 'local-predecessor-byte\n' >> "$transaction_home/skills/magi/SKILL.md"
transaction_before="$(
  find "$transaction_home/skills/magi" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum
)"
transaction_bin="$TMP/transaction-python-bin"
mkdir -p "$transaction_bin"
cat > "$transaction_bin/python3" <<'STUB'
#!/usr/bin/env bash
if [ "$1" = "$RENAME_HELPER" ] \
    && [ "$3" = "$TRANSACTION_HOME/skills/dual-magi-review" ] \
    && [ ! -e "$TRANSACTION_SENTINEL" ]; then
  mkdir -p "$3"
  printf 'foreign-second-publication\n' > "$3/SKILL.md"
  : > "$TRANSACTION_SENTINEL"
fi
exec "$REAL_PYTHON" "$@"
STUB
chmod +x "$transaction_bin/python3"
PATH="$transaction_bin:$PATH" REAL_PYTHON="$real_python" RENAME_HELPER="$rename_helper" \
  TRANSACTION_HOME="$transaction_home" \
  TRANSACTION_SENTINEL="$TMP/codex-transaction-second" CODEX_HOME="$transaction_home" \
  bash "$INSTALL" --copy >"$TMP/codex-transaction.log" 2>&1 && {
  echo "installer accepted a foreign second-skill publication" >&2; exit 1; }
transaction_after="$(
  find "$transaction_home/skills/magi" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum
)"
[ "$transaction_after" = "$transaction_before" ] || {
  echo "second-skill failure did not restore the first skill byte-for-byte" >&2; exit 1; }
grep -Eq '\\] (linked|copied) ' "$TMP/codex-transaction.log" && {
  echo "failed Codex transaction emitted committed-success output" >&2; exit 1; }
grep -qx 'foreign-second-publication' \
  "$transaction_home/skills/dual-magi-review/SKILL.md" || {
  echo "transaction rollback removed the late foreign second skill" >&2; exit 1; }

# Replace the private quarantine immediately after its ownership marker validates. Rollback must
# never delete either the substituted foreign quarantine or the displaced owned publication.
rollback_race_home="$TMP/rollback-quarantine-race"
CODEX_HOME="$rollback_race_home" bash "$INSTALL" --copy >/dev/null || exit 1
printf 'rollback-predecessor-byte\n' >> "$rollback_race_home/skills/magi/SKILL.md"
rollback_before="$(
  cd "$rollback_race_home/skills/magi" \
    && find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum
)"
rollback_race_bin="$TMP/rollback-race-bin"
mkdir -p "$rollback_race_bin"
cp "$transaction_bin/python3" "$rollback_race_bin/python3"
cat > "$rollback_race_bin/cmp" <<'STUB'
#!/usr/bin/env bash
"$REAL_CMP" "$@"
rc=$?
last="${!#}"
case "$last" in
  "$TRANSACTION_HOME"/skills/.magi.rollback.*/.harness-magi-codex)
    if [ "$rc" -eq 0 ] && [ ! -e "$ROLLBACK_SENTINEL" ]; then
      quarantine="$(dirname "$last")"
      "$REAL_MV" "$quarantine" "${quarantine}.owned"
      mkdir -p "$quarantine"
      printf 'foreign-private-quarantine\n' > "$quarantine/SKILL.md"
      : > "$ROLLBACK_SENTINEL"
    fi ;;
esac
exit "$rc"
STUB
chmod +x "$rollback_race_bin/python3" "$rollback_race_bin/cmp"
real_cmp="$(command -v cmp)"
real_mv="$(command -v mv)"
PATH="$rollback_race_bin:$PATH" REAL_PYTHON="$real_python" REAL_CMP="$real_cmp" \
  REAL_MV="$real_mv" \
  RENAME_HELPER="$rename_helper" TRANSACTION_HOME="$rollback_race_home" \
  TRANSACTION_SENTINEL="$TMP/codex-rollback-second" \
  ROLLBACK_SENTINEL="$TMP/codex-rollback-swapped" CODEX_HOME="$rollback_race_home" \
  bash "$INSTALL" --copy >"$TMP/codex-rollback-race.log" 2>&1 && {
  echo "Codex rollback race unexpectedly succeeded" >&2; exit 1; }
rollback_foreign="$(find "$rollback_race_home/skills" -maxdepth 1 -type d \
  -name '.magi.rollback.*' ! -name '*.owned' -print -quit)"
[ -n "$rollback_foreign" ] \
  && grep -qx 'foreign-private-quarantine' "$rollback_foreign/SKILL.md" || {
  echo "Codex rollback deleted the substituted private quarantine" >&2; exit 1; }
rollback_owned="$(find "$rollback_race_home/skills" -maxdepth 1 -type d \
  -name '.magi.rollback.*.owned' -print -quit)"
[ -n "$rollback_owned" ] || {
  echo "Codex rollback lost the displaced owned publication" >&2; exit 1; }
rollback_after="$(
  cd "$rollback_race_home/skills/magi" \
    && find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum
)"
[ "$rollback_after" = "$rollback_before" ] || {
  echo "Codex rollback did not restore the predecessor byte-for-byte" >&2; exit 1; }

# TERM immediately after displacement must restore the owned predecessor and clear its stage.
signal_move_home="$TMP/signal-after-displacement"
CODEX_HOME="$signal_move_home" bash "$INSTALL" >/dev/null || exit 1
signal_move_bin="$TMP/signal-mv-bin"
mkdir -p "$signal_move_bin"
real_mv="$(command -v mv)"
cat > "$signal_move_bin/mv" <<'STUB'
#!/usr/bin/env bash
"$REAL_MV" "$@"
rc=$?
if [ "$1" = "$SIGNAL_HOME/skills/magi" ] && [ ! -e "$SIGNAL_SENTINEL" ]; then
  : > "$SIGNAL_SENTINEL"
  kill -TERM "$PPID"
fi
exit "$rc"
STUB
chmod +x "$signal_move_bin/mv"
PATH="$signal_move_bin:$PATH" REAL_MV="$real_mv" SIGNAL_HOME="$signal_move_home" \
  SIGNAL_SENTINEL="$TMP/signal-after-move" CODEX_HOME="$signal_move_home" \
  bash "$INSTALL" >"$TMP/signal-after-move.log" 2>&1
[ $? -eq 143 ] || { echo "installer did not preserve TERM status after displacement" >&2; exit 1; }
[ -L "$signal_move_home/skills/magi" ] || {
  echo "installer did not restore predecessor after displacement signal" >&2; exit 1; }
find "$signal_move_home/skills" -maxdepth 1 -name '.magi.stage.*' | grep -q . && {
  echo "installer leaked stage after displacement signal" >&2; exit 1; }

# TERM immediately after no-replace publication keeps the complete new destination and preserves
# the validated predecessor under its recovery name.
signal_publish_home="$TMP/signal-after-publication"
CODEX_HOME="$signal_publish_home" bash "$INSTALL" >/dev/null || exit 1
signal_publish_bin="$TMP/signal-publish-bin"
mkdir -p "$signal_publish_bin"
real_python="$(command -v python3)"
rename_helper="$(readlink -f "$HERE/../scripts/magi_rename_noreplace.py")"
cat > "$signal_publish_bin/python3" <<'STUB'
#!/usr/bin/env bash
"$REAL_PYTHON" "$@"
rc=$?
if [ "$1" = "$RENAME_HELPER" ] && [ "$3" = "$SIGNAL_HOME/skills/magi" ] \
    && [ "$rc" -eq 0 ] && [ ! -e "$SIGNAL_SENTINEL" ]; then
  : > "$SIGNAL_SENTINEL"
  kill -TERM "$PPID"
fi
exit "$rc"
STUB
chmod +x "$signal_publish_bin/python3"
PATH="$signal_publish_bin:$PATH" REAL_PYTHON="$real_python" RENAME_HELPER="$rename_helper" \
  SIGNAL_HOME="$signal_publish_home" SIGNAL_SENTINEL="$TMP/signal-after-publish" \
  CODEX_HOME="$signal_publish_home" bash "$INSTALL" >"$TMP/signal-after-publish.log" 2>&1
[ $? -eq 143 ] || { echo "installer did not preserve TERM status after publication" >&2; exit 1; }
[ -L "$signal_publish_home/skills/magi" ] || {
  echo "installer lost published destination after publication signal" >&2; exit 1; }
find "$signal_publish_home/skills" -maxdepth 1 -type l -name '.magi.old.*' | grep -q . || {
  echo "installer did not preserve predecessor after publication signal" >&2; exit 1; }
echo "test_skill_install: PASS"
