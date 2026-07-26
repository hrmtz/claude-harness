#!/bin/bash
# redact.sh - credential pattern detection for mailbox writes.
#
# Agents routinely leak tokens into peer messages and those jsonl entries live
# forever as observable plain text. `is_credential_like` is called by
# mailbox writers to hard-refuse bodies that match common secret shapes. Agents
# MUST reference SOPS-encrypted files instead, e.g.
#   sops exec-env config/secrets.enc.yaml 'use "$openai_key"'
# and never paste the decrypted value into a mailbox message.

is_credential_like() {
  local body="$1"
  # Return 0 (true) if body looks like it contains a credential.
  #
  # Catalog (keep in sync with the transcript scrubber):
  #   - provider key shapes: sk-*, ghp_/gho_/ghs_/github_pat_*, xai-*, AIza*, AKIA/ASIA*
  #   - named KEY=VALUE for known providers (ANTHROPIC.. etc)
  #   - postgres(ql)://user:pass@ DSNs  (project primary DB secret)
  #   - general KEYWORD=VALUE families: *_PASSWORD= *_TOKEN= *_SECRET= *_SECRET_ACCESS_KEY=
  #     (covers POSTGRES_PASSWORD, TURSO_AUTH_TOKEN, R2_SECRET_ACCESS_KEY, ...)
  #   - PEM private keys, long JWTs
  # Case-sensitive shapes (provider prefixes / DSN / PEM / JWT) — these encode
  # case in the secret itself, so do not lower-case them.
  if echo "$body" | grep -Eq \
'(sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|ghs_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xai-[A-Za-z0-9]{20,}|AIza[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16}|ASIA[A-Z0-9]{16}|(ANTHROPIC|OPENAI|GEMINI|XAI|DEEPSEEK|LINE|CF|CLOUDFLARE|WP|ZOTERO|TAILSCALE)_(API_)?(KEY|TOKEN|SECRET|PASSWORD)[[:space:]]*=[[:space:]]*[^ ]+|postgres(ql)?://[^:@/[:space:]]+:[^@[:space:]]+@|-----BEGIN [A-Z ]*PRIVATE KEY-----|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,})'; then
    return 0
  fi
  # KEYWORD = VALUE families. Case-INsensitive so lowercase keys match
  # (db_password=...), tolerant of an optional 'export ' prefix and of
  # whitespace around '=' (KEY = value). The key may carry a provider prefix
  # (ANTHROPIC_API_KEY) or be the bare keyword (token=...).
  if echo "$body" | grep -Eiq \
'(^|[^a-z0-9_])(export[[:space:]]+)?[a-z0-9_]*(password|passwd|secret|secret_access_key|token|api[_-]?key)[[:space:]]*=[[:space:]]*[^[:space:]]+'; then
    return 0
  fi
  return 1
}

# Refusal logging is metadata-only: never copy the credential-shaped body into
# the log. All Formation entrypoints call this same function so direct
# mailbox-send invocations cannot bypass the audit trail.
refuse_credential() {
  local channel="$1" from="$2" context="$3"
  local formation_home="${FORMATION_HOME:-$HOME/.formation}"
  local refuse_log="${FORMATION_REFUSE_LOG:-$formation_home/mailbox/refuse.log}"
  local ts
  channel="$(printf '%s' "$channel" | tr -d '\000-\037\177')"
  from="$(printf '%s' "$from" | tr -d '\000-\037\177')"
  context="$(printf '%s' "$context" | tr -d '\000-\037\177')"
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  mkdir -p "$(dirname "$refuse_log")"
  printf '%s\t%s\tfrom=%s\t%s\n' "$ts" "$channel" "$from" "$context" \
    >> "$refuse_log"
  echo "formation: refusing — body matches credential pattern." >&2
  echo "formation: reference a SOPS-encrypted file instead, e.g." >&2
  echo "           sops exec-env config/secrets.enc.yaml '<cmd that uses \$key>'" >&2
  echo "formation: attempted send logged at $refuse_log" >&2
}
