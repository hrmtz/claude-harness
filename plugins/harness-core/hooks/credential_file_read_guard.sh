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

classify_credential_path() {
  local path="$1"

  case "$path" in
    *.env.example | *.env.template | *.env.sample | *.env.dist | *.env.test | *.env.local-example)
      return 11
      ;;
  esac

  case "$path" in
    .env | */.env)
      REASON=".env (= plain credential SoT)"
      return 0
      ;;
    .env.* | */.env.*)
      REASON=".env.<suffix> (= environment-specific credentials)"
      return 0
      ;;
    credentials.* | */credentials.*)
      REASON="credentials.<suffix> (= credential artifact)"
      return 0
      ;;
    rclone.conf | */rclone.conf | *.config/rclone/rclone.conf)
      REASON="rclone.conf (= R2/S3 secret access key)"
      return 0
      ;;
    .aws/credentials | */.aws/credentials)
      REASON="AWS credentials"
      return 0
      ;;
    .netrc | */.netrc)
      REASON=".netrc (= HTTP basic auth credentials)"
      return 0
      ;;
    */.cloudflared/*.json)
      REASON="cloudflared tunnel credentials"
      return 0
      ;;
    *.pem | *.key | *.p12 | *.pfx)
      REASON="private key file"
      return 0
      ;;
  esac

  return 10
}

if [ "$#" -gt 0 ]; then
  if [ "$#" -ne 2 ] || [ "$1" != "--classify-path" ] || [ -z "$2" ]; then
    printf '%s\n' "CREDENTIAL_CLASSIFIER_INVALID_INVOCATION" >&2
    exit 64
  fi
  classify_credential_path "$2"
  exit $?
fi

source "$(dirname "$0")/lib.sh"

MAX_HOOK_INPUT_BYTES=4194304
if ! HOOK_INPUT=$(head -c $((MAX_HOOK_INPUT_BYTES + 1))); then
  printf '%s\n' "credential read guard input acquisition failed; refusing tool execution" >&2
  exit 2
fi
HOOK_INPUT_BYTES=$(LC_ALL=C printf '%s' "$HOOK_INPUT" | wc -c)
if [ "$HOOK_INPUT_BYTES" -gt "$MAX_HOOK_INPUT_BYTES" ]; then
  printf '%s\n' "credential read guard input exceeds size limit; refusing tool execution" >&2
  exit 2
fi
if [ -z "$HOOK_INPUT" ] || ! printf '%s' "$HOOK_INPUT" \
    | jq -e -s 'length == 1 and (.[0] | type == "object")' >/dev/null 2>&1; then
  printf '%s\n' "credential read guard input validation failed; refusing tool execution" >&2
  exit 2
fi

if ! FILE_PATH=$(parse_tool_file_path_strict); then
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

classify_credential_path "$FILE_PATH"
CLASSIFICATION=$?
case "$CLASSIFICATION" in
  0) ;;
  10|11) exit 0 ;;
  *)
    printf '%s\n' "credential read guard classification failed; refusing tool execution" >&2
    exit 2
    ;;
esac

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
