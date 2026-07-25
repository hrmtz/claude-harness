#!/usr/bin/env bash
# Canonical template identity regression: a mistaken MAGI_CANONICAL_SKILLS_DIR override that
# resolves to schema-valid but weak pre-flight templates must refuse (exit 64) before any
# provider launch, and canonical template drift must be detected by fingerprint.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FANOUT="$HERE/../scripts/magi_fanout_codex.sh"
VERIFY="$HERE/../scripts/magi_verify_canonical_templates.py"
CANON="$HERE/../../harness-magi/skills"
CODEX_SKILLS="$HERE/../skills"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
DOC="$TMP/design.md"; printf '%s\n' 'a test design' > "$DOC"

pass=0; fail=0
ok()  { echo "  ok   - $1"; pass=$((pass+1)); }
bad() { echo "  FAIL - $1"; fail=$((fail+1)); }

python3 "$VERIFY" "$CANON" magi >/dev/null 2>&1 \
  && ok "verifier accepts the canonical magi templates" \
  || bad "verifier rejected the canonical magi templates"
python3 "$VERIFY" "$CANON" bug-hunt >/dev/null 2>&1 \
  && ok "verifier accepts the canonical bug-hunt templates" \
  || bad "verifier rejected the canonical bug-hunt templates"

python3 "$VERIFY" "$CODEX_SKILLS" magi >/dev/null 2>&1
[ $? -eq 64 ] && ok "verifier rejects the codex pre-flight skill root (no marker)" \
              || bad "verifier accepted the codex pre-flight skill root"

# Drifted copy: edit one canonical template without regenerating the marker.
cp -R "$CANON" "$TMP/drifted"
printf 'weakened prompt\n' >> "$TMP/drifted/magi/templates/melchior_prompt.md"
python3 "$VERIFY" "$TMP/drifted" magi >/dev/null 2>&1
[ $? -eq 64 ] && ok "verifier detects canonical template drift" \
              || bad "verifier accepted a drifted canonical template"
python3 "$VERIFY" --write "$TMP/drifted" >/dev/null 2>&1 \
  && python3 "$VERIFY" "$TMP/drifted" magi >/dev/null 2>&1 \
  && ok "--write regenerates the marker after an intentional template edit" \
  || bad "--write did not restore verification after a template edit"

# A stub codex that records every launch: the override refusal must happen BEFORE it.
mkdir -p "$TMP/bin"
cat > "$TMP/bin/codex" <<'STUB'
#!/usr/bin/env bash
if [ "${1:-}" = "exec" ] && [ "${2:-}" = "--help" ]; then
  printf '%s\n' '--output-schema --output-last-message --ephemeral'
  exit 0
fi
echo launched >> "${STUB_LAUNCH_LOG:?}"
out=""
schema=""
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then out="$2"; shift 2
  elif [ "$1" = "--output-schema" ]; then schema="$2"; shift 2
  else shift
  fi
done
[ -n "$out" ] || exit 64
prompt="$(cat)"
artifact_id="$(printf '%s\n' "$prompt" | sed -n 's/^ARTIFACT ID: //p' | head -n 1)"
artifact_sha="$(printf '%s\n' "$prompt" | sed -n 's/^ARTIFACT SHA256: //p' | head -n 1)"
reviewer="$(printf '%s\n' "$prompt" | sed -n 's/^You are the \([^ ]*\) reviewer.*/\1/p' | head -n 1 | tr '[:lower:]' '[:upper:]')"
printf '{"reviewer":"%s","round":1,"artifact_id":"%s","artifact_sha":"%s","verdict":"GO","schema_grounding_verdict":"PASS","verify_commands_executed":["stub"],"source_artifacts":[],"dispositions":[],"findings":[]}\n' \
  "$reviewer" "$artifact_id" "$artifact_sha" > "$out"
STUB
chmod +x "$TMP/bin/codex"

STUB_LAUNCH_LOG="$TMP/launches" PATH="$TMP/bin:$PATH" \
  MAGI_CANONICAL_SKILLS_DIR="$CODEX_SKILLS" \
  "$FANOUT" "$DOC" 1 "$TMP/out-override" >"$TMP/override.log" 2>&1
rc=$?
[ $rc -eq 64 ] && ok "codex skill root override exits 64" \
              || bad "codex skill root override returned rc=$rc"
[ ! -e "$TMP/launches" ] && ok "no provider launched for an incompatible override" \
                        || bad "provider launched despite an incompatible override"
[ ! -e "$TMP/out-override" ] && ok "incompatible override left no output directory" \
                            || bad "incompatible override created output state"

# The original collision shape: a layout that still HAS <set>/templates/ (so the existence
# check passes) but carries no canonical fingerprint. The identity check must name itself.
mkdir -p "$TMP/lookalike/magi/templates"
cp "$CANON/magi/templates/"*.md "$TMP/lookalike/magi/templates/"
STUB_LAUNCH_LOG="$TMP/launches-lookalike" PATH="$TMP/bin:$PATH" \
  MAGI_CANONICAL_SKILLS_DIR="$TMP/lookalike" \
  "$FANOUT" "$DOC" 1 "$TMP/out-lookalike" >"$TMP/lookalike.log" 2>&1
rc=$?
[ $rc -eq 64 ] && ok "template lookalike without fingerprint exits 64" \
              || bad "template lookalike without fingerprint returned rc=$rc"
grep -q 'canonical template identity check failed' "$TMP/lookalike.log" \
  && ok "lookalike refusal names the identity check" \
  || bad "lookalike refusal did not name the identity check"
[ ! -e "$TMP/launches-lookalike" ] && ok "no provider launched for a fingerprint-less lookalike" \
                                  || bad "provider launched for a fingerprint-less lookalike"

# The identity check must not break the canonical happy path.
STUB_LAUNCH_LOG="$TMP/launches-ok" PATH="$TMP/bin:$PATH" \
  "$FANOUT" "$DOC" 1 "$TMP/out-canonical" >"$TMP/canonical.log" 2>&1
rc=$?
[ $rc -eq 0 ] && ok "canonical template root passes fan-out identity check" \
              || {
                sed -n '1,160p' "$TMP/canonical.log" >&2
                sed -n '1,120p' "$TMP/out-canonical/round_1_fanout."*.FAILED.json >&2 2>/dev/null || true
                bad "canonical template root failed fan-out (rc=$rc)"
              }

echo "test_canonical_templates: $pass passed, $fail failed"
exit $((fail > 0))
