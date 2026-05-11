#!/bin/bash
# PreToolUse Read hook: block full-file Read of plain credential SoT files.
#
# 背景: 2026-05-11 incident #21 で Read tool が .env 全 32 行を会話ログに dump、
# 7 key 漏洩 (TURSO_TOKEN / CLOUDFLARE_TUNNEL_TOKEN / R2 secret / etc)。
# scrub hook (= PostToolUse credential_value_scrub) は 4 key 未登録で覆えなかった。
#
# 防御方針: そもそも credential SoT を Read tool に流さない。 構造把握は
# `grep -n <KEY> .env` (= Bash 経由、 outer-pipe で値露出しない) に置換。
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
# ack bypass (= 意識的 1 回 limit)
# ----------------------------------------
if [ "${HRMTZ_ACK_CRED_READ:-}" = "1" ]; then
  # log but allow
  echo "[credential_file_read_guard] BYPASS via HRMTZ_ACK_CRED_READ=1: $FILE_PATH" >> "$LOG_DIR/credential_file_read_guard.log"
  exit 0
fi

# ----------------------------------------
# Block + alternative action
# ----------------------------------------
echo "Read of $REASON refused: $FILE_PATH" >&2
echo "Use 'grep -n <KEY> $FILE_PATH' via Bash to locate specific lines without dumping values to chat log." >&2
echo "For Edit: Bash grep first → know line numbers → Edit with surrounding context (no Read needed)." >&2
echo "Archeology bypass: set HRMTZ_ACK_CRED_READ=1 env (= 1 回 limit、 incident risk 自覚)." >&2
echo "Past leak: docs/runbooks/CREDENTIAL_ROTATION.md (TBD) for emergency rotation." >&2
exit 2
