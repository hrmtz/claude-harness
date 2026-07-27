#!/usr/bin/env bash
set -euo pipefail
trap 'echo "FAIL: test_mail_nudge_service line $LINENO" >&2' ERR
HERE="$(cd "$(dirname "$0")" && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/canonical/.git" "$TMP/canonical/plugins/harness-formation/bin" "$TMP/home"
cp "$HERE/../bin/formation-mail-nudge" \
  "$TMP/canonical/plugins/harness-formation/bin/formation-mail-nudge"
chmod +x "$TMP/canonical/plugins/harness-formation/bin/formation-mail-nudge"

cat >"$TMP/bin/git" <<'EOF'
#!/usr/bin/env bash
case " $* " in
  *" rev-parse --show-toplevel "*) printf '%s\n' "$CALLER_REPO" ;;
  *" worktree list --porcelain "*) printf 'worktree %s\n\nworktree %s\n' "$CANONICAL_REPO" "$CALLER_REPO" ;;
  *" ls-files --error-unmatch "*) [[ "${HELPER_TRACKED:-1}" == "1" ]] ;;
  *) exit 1 ;;
esac
EOF
cat >"$TMP/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$SYSTEMCTL_LOG"
[[ "${SYSTEMD_AVAILABLE:-1}" == "1" ]]
EOF
chmod +x "$TMP/bin/git" "$TMP/bin/systemctl"

export PATH="$TMP/bin:/usr/bin:/bin"
export HOME="$TMP/home" CALLER_REPO="$TMP/disposable" CANONICAL_REPO="$TMP/canonical"
export SYSTEMCTL_LOG="$TMP/systemctl.log"
INSTALLER="$HERE/../bin/install-formation-mail-nudge-service"

# Dry-run resolves the canonical checkout and performs no writes/actions.
before="$(find "$HOME" -type f -print0 | sort -z | xargs -0 -r sha256sum)"
"$INSTALLER" --dry-run install >"$TMP/dry.out"
grep -Fq "canonical ExecStart=$CANONICAL_REPO/plugins/harness-formation/bin/formation-mail-nudge --watch" "$TMP/dry.out"
after="$(find "$HOME" -type f -print0 | sort -z | xargs -0 -r sha256sum)"
[[ "$before" == "$after" && ! -e "$SYSTEMCTL_LOG" ]]

# Untracked and non-executable canonical helpers are refused.
if HELPER_TRACKED=0 "$INSTALLER" install >/dev/null 2>&1; then exit 1; else [[ $? -eq 66 ]]; fi
chmod -x "$CANONICAL_REPO/plugins/harness-formation/bin/formation-mail-nudge"
if "$INSTALLER" install >/dev/null 2>&1; then exit 1; else [[ $? -eq 66 ]]; fi
chmod +x "$CANONICAL_REPO/plugins/harness-formation/bin/formation-mail-nudge"

# Explicit install writes the exact canonical unit and invokes only its unit.
"$INSTALLER" install >/dev/null
UNIT="$HOME/.config/systemd/user/formation-mail-nudge.service"
grep -Fqx "ExecStart=$CANONICAL_REPO/plugins/harness-formation/bin/formation-mail-nudge --watch" "$UNIT"
grep -Fq 'enable --now formation-mail-nudge.service' "$SYSTEMCTL_LOG"

# Update backs up the old unit; uninstall archives exact unit + namespace.
"$INSTALLER" install >/dev/null
[[ "$(find "$HOME/sanada_backup_persistent" -type f -name formation-mail-nudge.service | wc -l)" -ge 1 ]]
mkdir -p "$HOME/.formation/state/mail-nudge"
printf '{"fixture":true}\n' >"$HOME/.formation/state/mail-nudge/fixture.json"
"$INSTALLER" uninstall >/dev/null
[[ ! -e "$UNIT" && ! -e "$HOME/.formation/state/mail-nudge" ]]
grep -Fq 'disable --now formation-mail-nudge.service' "$SYSTEMCTL_LOG"
[[ "$(find "$HOME/sanada_backup_persistent" -type f -name fixture.json | wc -l)" -eq 1 ]]

# No silent cron fallback.
if SYSTEMD_AVAILABLE=0 "$INSTALLER" install >/dev/null 2>&1; then
  exit 1
else
  [[ $? -eq 69 ]]
fi

echo "test_mail_nudge_service: passed"
