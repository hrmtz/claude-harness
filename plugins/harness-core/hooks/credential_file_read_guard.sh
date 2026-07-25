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
# Issue #108: read acknowledgement is not disclosure authorization. A legacy
# ~/.claude/state/cred_read_ack marker never permits tool/model-visible output.
#
# coverage: .env / .env.<suffix> / rclone.conf / .netrc / .aws/credentials /
#           .cloudflared/*.json / *.pem / *.key / *.p12

source "$(dirname "$0")/lib.sh"

HOOK_INPUT=$(cat)
export HOOK_INPUT

FILE_PATH=$(parse_tool_file_path)
[ -z "$FILE_PATH" ] && exit 0

# MCP filesystem tools commonly use file:// URIs. Decode only local file URIs;
# non-file schemes are remote resources and outside this local-file guard.
case "$FILE_PATH" in
  file://*)
    FILE_PATH=$(python3 - "$FILE_PATH" <<'PY' 2>/dev/null || true
import sys
from urllib.parse import unquote, urlparse
u = urlparse(sys.argv[1])
if u.scheme == "file" and u.netloc in {"", "localhost"}:
    print(unquote(u.path))
PY
    )
    [ -z "$FILE_PATH" ] && exit 0
    ;;
  *://*) exit 0 ;;
esac

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
  .env | */.env)
    BLOCK=1; REASON=".env (= plain credential SoT)"
    ;;
  .env.* | */.env.*)
    BLOCK=1; REASON=".env.<suffix> (= environment-specific credentials)"
    ;;
  # rclone / aws / netrc
  rclone.conf | */rclone.conf | *.config/rclone/rclone.conf)
    BLOCK=1; REASON="rclone.conf (= R2/S3 secret access key)"
    ;;
  .aws/credentials | */.aws/credentials)
    BLOCK=1; REASON="AWS credentials"
    ;;
  .netrc | */.netrc)
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
# Block + alternative action
# ----------------------------------------
if [ -e "$STATE_DIR/cred_read_ack" ]; then
  hook_log "credential_file_read_guard" \
    "legacy read marker ignored for classified path (READ_ACK_DOES_NOT_AUTHORIZE_OUTPUT)"
fi
MSG="READ_ACK_DOES_NOT_AUTHORIZE_OUTPUT — Read of $REASON refused: $FILE_PATH
To check a key without exposing its value: use a count-only check or list key names only. For real use, inject with 'sops exec-env <file> <repo-baked-consumer>' and keep the consumer from printing the value. Never use a matching-line grep because it prints the value.
For Edit: locate line numbers without reading values, then Edit with bounded surrounding context.
Destination-bound delivery is not part of this slice; handle emergency access outside agent tool output."
emit_deny "$MSG"
