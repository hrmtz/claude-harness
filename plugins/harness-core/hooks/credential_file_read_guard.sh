#!/bin/bash
# PreToolUse Read hook: block full-file Read of plain credential SoT files.
#
# 背景: 2026-05-11 incident #21 で Read tool が .env 全 32 行を会話ログに dump、
# 7 key 漏洩 (TURSO_TOKEN / CLOUDFLARE_TUNNEL_TOKEN / R2 secret / etc)。
# scrub hook (= PostToolUse credential_value_scrub) は 4 key 未登録で覆えなかった。
#
# 防御方針: そもそも credential SoT を Read tool に流さない。 構造把握は
# `grep -c <KEY> <file>` (件数のみ) / `cut -d= -f1 <file>` (key 名のみ) に置換。
# ⚠ `grep -n <KEY>` は match 行全体 (= 値込み) を出すので NG (gh #15 訂正)。
#
# bypass: HRMTZ_ACK_CRED_READ=1 env を Bash invocation の前に立てる
#         (= 月 1-2 回想定の正当 archeology 用、 1 回 limit、 意識的に書く必要)
#
# coverage: .env / .env.<host> / rclone.conf / .netrc / .aws/credentials /
#           .cloudflared/*.json / *.pem / *.key / *.p12

source "$(dirname "$0")/lib.sh"

HOOK_INPUT=$(cat)
export HOOK_INPUT

FILE_PATH=$(printf '%s' "$HOOK_INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
[ -z "$FILE_PATH" ] && exit 0

# ----------------------------------------
# Exempt suffix list (= dummy / template / test fixture、 block しない)
# ----------------------------------------
case "$FILE_PATH" in
  *.env.example | *.env.template | *.env.sample | *.env.dist | *.env.test | *.env.local-example)
    exit 0
    ;;
esac

# ----------------------------------------
# Block target patterns (= 完全 match)
# ----------------------------------------
BLOCK=0
case "$FILE_PATH" in
  # plain .env / .env.<host> family
  */.env | */.env.common | */.env.prod | */.env.production | */.env.local | */.env.dev | */.env.staging)
    BLOCK=1; REASON=".env (= plain credential SoT)"
    ;;
  */.env.hetzner | */.env.laddie | */.env.chichibu | */.env.zetithnas | */.env.talisker | */.env.mars | */.env.farm)
    BLOCK=1; REASON=".env.<host> (= host-specific credentials)"
    ;;
  # rclone / aws / netrc
  */rclone.conf | *.config/rclone/rclone.conf)
    BLOCK=1; REASON="rclone.conf (= R2/S3 secret access key)"
    ;;
  */.aws/credentials)
    BLOCK=1; REASON="AWS credentials"
    ;;
  */.netrc)
    BLOCK=1; REASON=".netrc (= HTTP basic auth credentials)"
    ;;
  # cloudflared tunnel credentials
  */.cloudflared/*.json)
    BLOCK=1; REASON="cloudflared tunnel credentials"
    ;;
  # private key files
  *.pem | *.key | *.p12 | *.pfx)
    BLOCK=1; REASON="private key file"
    ;;
esac

[ "$BLOCK" -eq 0 ] && exit 0

# ----------------------------------------
# ack bypass — genuinely ONE-TIME + EXPIRING (gh #19). The old `$HRMTZ_ACK_CRED_READ`
# env check was EXPORTABLE: `export HRMTZ_ACK_CRED_READ=1` once → the bypass persisted
# for ALL subsequent reads (neither one-time nor expiring). A Read tool call has no
# command-prefix, so we use a CONSUMABLE marker file instead: create it to authorize
# the NEXT credential-file read within 120s; the guard consumes it (one read) and
# ignores a stale one.
#   touch ~/.claude/state/cred_read_ack   # then do the one Read
# ----------------------------------------
ACK_FILE="$STATE_DIR/cred_read_ack"
# Atomic one-shot claim: `mv` succeeds for exactly ONE racer, so two concurrent reads
# can never both consume the same marker (codex #19 race).
if [ -f "$ACK_FILE" ] && mv "$ACK_FILE" "$ACK_FILE.used.$$" 2>/dev/null; then
  ack_age=$(( $(date +%s) - $(stat -c %Y "$ACK_FILE.used.$$" 2>/dev/null || echo 0) ))
  rm -f "$ACK_FILE.used.$$" 2>/dev/null
  if [ "$ack_age" -le 120 ]; then
    echo "[credential_file_read_guard] BYPASS via cred_read_ack (consumed, age ${ack_age}s): $FILE_PATH" >> "$LOG_DIR/credential_file_read_guard.log"
    exit 0
  fi
fi

# ----------------------------------------
# Block + alternative action
# ----------------------------------------
echo "Read of $REASON refused: $FILE_PATH" >&2
echo "To check a key WITHOUT leaking its value: 'grep -c <KEY> $FILE_PATH' (count only) or 'cut -d= -f1 $FILE_PATH' (key names). For real use, 'sops exec-env <file> <cmd>'. NEVER 'grep -n <KEY>' — grep prints the whole matching line, which leaks the value (gh #15)." >&2
echo "For Edit: Bash grep first → know line numbers → Edit with surrounding context (no Read needed)." >&2
echo "Archeology bypass (ONE read, 120s expiry, incident risk 自覚): touch ~/.claude/state/cred_read_ack  then re-Read." >&2
echo "Past leak: docs/runbooks/CREDENTIAL_ROTATION.md (TBD) for emergency rotation." >&2
exit 2
