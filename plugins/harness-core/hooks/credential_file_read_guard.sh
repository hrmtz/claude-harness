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

if ! HOOK_INPUT=$(cat); then
  printf '%s\n' "credential read guard input acquisition failed; refusing tool execution" >&2
  exit 2
fi
if [ -z "$HOOK_INPUT" ] || ! printf '%s' "$HOOK_INPUT" \
    | jq -e -s 'length == 1 and (.[0] | type == "object")' >/dev/null 2>&1; then
  printf '%s\n' "credential read guard input validation failed; refusing tool execution" >&2
  exit 2
fi

if ! FILE_PATH=$(parse_tool_file_path); then
  printf '%s\n' "credential read guard path parsing failed; refusing tool execution" >&2
  exit 2
fi
[ -z "$FILE_PATH" ] && exit 0

# MCP filesystem tools commonly use file:// URIs. URI schemes and hostnames are
# case-insensitive. Decode only local file URIs; remote resources remain outside
# this local-file guard. Decoder failure is distinct from an intentional remote.
if [[ "$FILE_PATH" == *://* ]]; then
  if ! URI_RESULT=$(python3 - "$FILE_PATH" <<'PY' 2>/dev/null
import sys
from urllib.parse import unquote, urlparse
u = urlparse(sys.argv[1])
scheme = u.scheme.lower()
if scheme != "file":
    print("REMOTE")
elif u.netloc and (u.hostname or "").lower() != "localhost":
    print("REMOTE")
elif not u.path:
    raise ValueError("local file URI has no path")
else:
    decoded = unquote(u.path)
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in decoded):
        raise ValueError("local file URI contains unsupported control characters")
    print("LOCAL:" + decoded)
PY
  ); then
    printf '%s\n' "credential read guard URI decoding failed; refusing tool execution" >&2
    exit 2
  fi
  case "$URI_RESULT" in
    LOCAL:*) FILE_PATH="${URI_RESULT#LOCAL:}" ;;
    REMOTE) exit 0 ;;
    *)
      printf '%s\n' "credential read guard URI classification failed; refusing tool execution" >&2
      exit 2
      ;;
  esac
fi

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
