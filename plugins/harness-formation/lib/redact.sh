#!/bin/bash
# redact.sh - credential pattern detection for mailbox writes.
#
# Agents routinely leak tokens into peer messages and those jsonl entries live
# forever as observable plain text. `is_credential_like` is called by
# mailbox_send to hard-refuse bodies that match common secret shapes. Agents
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
  if echo "$body" | grep -Eq \
'(sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|ghs_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xai-[A-Za-z0-9]{20,}|AIza[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16}|ASIA[A-Z0-9]{16}|(ANTHROPIC|OPENAI|GEMINI|XAI|DEEPSEEK|LINE|CF|CLOUDFLARE|WP|ZOTERO|TAILSCALE)_(API_)?(KEY|TOKEN|SECRET|PASSWORD)=[^ ]+|postgres(ql)?://[^:@/[:space:]]+:[^@[:space:]]+@|[A-Za-z0-9_]*_(PASSWORD|TOKEN|SECRET|SECRET_ACCESS_KEY)=[^ ]+|-----BEGIN [A-Z ]*PRIVATE KEY-----|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,})'; then
    return 0
  fi
  return 1
}
