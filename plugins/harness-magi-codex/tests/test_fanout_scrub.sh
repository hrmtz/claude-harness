#!/usr/bin/env bash
# Fan-out regression: raw reviewer bytes travel through FIFOs and only scrubbed artifacts persist.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FANOUT="$HERE/../scripts/magi_fanout_codex.sh"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/out"
DOC="$TMP/design.md"; printf '%s\n' 'a test design' > "$DOC"

cat > "$TMP/bin/codex" <<'STUB'
#!/usr/bin/env bash
if [ -n "${STUB_TMUX_LOG:-}" ]; then
  if [ -z "${TMUX_PANE+x}" ]; then printf 'unset\n' >> "$STUB_TMUX_LOG"
  else printf 'inherited:%s\n' "$TMUX_PANE" >> "$STUB_TMUX_LOG"; fi
fi
out=""
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then out="$2"; shift 2; else shift; fi
done
[ -n "$out" ] || exit 64
if [ -n "${STUB_INVALID:-}" ]; then printf '{}\n' > "$out"; exit 0; fi
if [ -n "${STUB_HANG:-}" ]; then sleep 60; exit 1; fi
prompt="$(cat)"
artifact_id="$(printf '%s\n' "$prompt" | sed -n 's/^ARTIFACT ID: //p' | head -n 1)"
artifact_sha="$(printf '%s\n' "$prompt" | sed -n 's/^ARTIFACT SHA256: //p' | head -n 1)"
marker="Bearer "
marker="${marker}AAAAAAAAAAAA"
printf '{"reviewer":"STUB","round":1,"artifact_id":"%s","artifact_sha":"%s","verdict":"GO","schema_grounding_verdict":"PASS","verify_commands_executed":["%s"],"source_artifacts":[],"dispositions":[],"findings":[]}\n' \
  "$artifact_id" "$artifact_sha" "$marker" > "$out"
printf '%s\n' "$marker"
STUB
chmod +x "$TMP/bin/codex"

pass=0; fail=0
ok()  { echo "  ok   - $1"; pass=$((pass+1)); }
bad() { echo "  FAIL - $1"; fail=$((fail+1)); }

STUB_TMUX_LOG="$TMP/tmux-env" TMUX_PANE="%57" \
  PATH="$TMP/bin:$PATH" "$FANOUT" "$DOC" 1 "$TMP/out" >/dev/null 2>&1
rc=$?
[ $rc -eq 0 ] && ok "stub fan-out completes" || bad "stub fan-out rc=$rc"
if [ "$(sort -u "$TMP/tmux-env")" = "unset" ] && [ "$(wc -l < "$TMP/tmux-env")" -eq 3 ]; then
  ok "non-interactive reviewers do not inherit the parent TMUX_PANE"
else
  bad "non-interactive reviewers inherited the parent TMUX_PANE"
fi

json_count="$(find "$TMP/out" -maxdepth 1 -name 'round_1_*.json' -type f | wc -l)"
[ "$json_count" -eq 3 ] && ok "three persona JSON artifacts persist" \
                         || bad "expected 3 JSON artifacts, got $json_count"

marker="Bearer "; marker="${marker}AAAAAAAAAAAA"
if rg -F "$marker" "$TMP/out" >/dev/null 2>&1; then
  bad "unscrubbed reviewer bytes reached a durable artifact"
elif rg -F 'REDACTED' "$TMP/out" >/dev/null 2>&1; then
  ok "JSON and log artifacts are scrubbed before persistence"
else
  bad "scrub marker missing from durable artifacts"
fi

if find "$TMP/out" -maxdepth 1 \( -name '*.fifo' -o -name '*.safe.*' \) | grep -q .; then
  bad "fan-out left FIFO or safe-temp residue"
else
  ok "fan-out leaves no FIFO or safe-temp residue"
fi

PATH="$TMP/bin:$PATH" "$FANOUT" "$DOC" 1 "$TMP/out" >/dev/null 2>&1
[ $? -eq 5 ] && ok "existing sibling artifacts reject rerun" \
              || bad "existing sibling artifacts did not return exit 5"

mkdir -p "$TMP/invalid"
INVALID_DOC="$TMP/invalid-design.md"; printf '%s\n' 'another design' > "$INVALID_DOC"
STUB_INVALID=1 PATH="$TMP/bin:$PATH" "$FANOUT" "$INVALID_DOC" 1 "$TMP/invalid" >/dev/null 2>&1
if [ $? -ne 0 ] && ! find "$TMP/invalid" -name 'round_1_*.json' -type f | grep -q .; then
  ok "durable JSON is schema-validated and invalid partials are cleared"
else
  bad "schema-invalid durable output passed fan-out"
fi

mkdir -p "$TMP/hang"
HANG_DOC="$TMP/hang-design.md"; printf '%s\n' 'a hanging design' > "$HANG_DOC"
started="$(date +%s)"
STUB_HANG=1 MAGI_FANOUT_TIMEOUT_S=1 PATH="$TMP/bin:$PATH" \
    "$FANOUT" "$HANG_DOC" 1 "$TMP/hang" >/dev/null 2>&1
hang_rc=$?; elapsed=$(( $(date +%s) - started ))
if [ "$hang_rc" -eq 1 ] && [ "$elapsed" -lt 10 ]; then
  ok "hung providers hit the bounded fan-out deadline"
else
  bad "hung fan-out did not terminate promptly (rc=$hang_rc elapsed=${elapsed}s)"
fi
PATH="$TMP/bin:$PATH" "$FANOUT" "$HANG_DOC" 1 "$TMP/hang" >/dev/null 2>&1
[ $? -eq 0 ] && ok "timeout releases the document lock and leaves a retryable failed claim" \
              || bad "fan-out could not recover after timeout"

# Optional real-CLI interface probe: codex -o must support a FIFO sink. The stub above keeps the
# regression deterministic; this arm measures the external CLI assumption when explicitly enabled.
if [ -n "${MAGI_TEST_LIVE:-}" ]; then
  LIVE_FIFO="$TMP/live.raw.fifo"; LIVE_SAFE="$TMP/live.safe.json"
  mkfifo "$LIVE_FIFO"; exec 9<>"$LIVE_FIFO"
  ( exec 9>&-; python3 "$HERE/../scripts/magi_scrub.py" < "$LIVE_FIFO" > "$LIVE_SAFE" ) & live_scrub_pid=$!
  printf '%s\n' 'Return a GO verdict, reviewer LIVE, round 1, grounding FAIL, no operations, no findings.' \
    | timeout 180 env -u TMUX_PANE codex exec --skip-git-repo-check -s read-only --ephemeral \
        -C "$HERE/../../.." --output-schema "$HERE/../schemas/finding.schema.json" \
        -o "$LIVE_FIFO" - >/dev/null 2>&1
  live_rc=$?; exec 9>&-; wait "$live_scrub_pid" || live_rc=1
  if [ "$live_rc" -eq 0 ] && python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$LIVE_SAFE" 2>/dev/null; then
    ok "real codex -o writes structured output through FIFO"
  else
    bad "real codex -o FIFO interface failed"
  fi
else
  echo "  skip - set MAGI_TEST_LIVE=1 for real codex -o FIFO probe"
fi

echo "test_fanout_scrub: $pass passed, $fail failed"
exit $((fail > 0))
