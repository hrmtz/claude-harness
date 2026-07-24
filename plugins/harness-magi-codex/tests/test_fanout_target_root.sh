#!/usr/bin/env bash
# gh #57 regression: invoking the fan-out from repo A against a RELATIVE doc path in
# repo B must ground reviewers in repo B -- canonicalized paths in the prompt, the
# target worktree as the reviewer cwd, and artifacts published to the canonical
# (repo B) out-dir.
set -uo pipefail
unset TMUX
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FANOUT="$HERE/../scripts/magi_fanout_codex.sh"
TMP="$(realpath "$(mktemp -d)")"; trap 'rm -rf "$TMP"' EXIT

pass=0; fail=0
ok()  { echo "  ok   - $1"; pass=$((pass+1)); }
bad() { echo "  FAIL - $1"; fail=$((fail+1)); }

# Repo A (caller cwd) and repo B (document owner) as sibling git worktrees.
mkdir -p "$TMP/bin" "$TMP/repoA" "$TMP/repoB/docs" "$TMP/repoB/state"
git init -q "$TMP/repoA"
git init -q "$TMP/repoB"
printf '%s\n' 'a design owned by repo B' > "$TMP/repoB/docs/design.md"
printf '%s\n' 'supporting implementation notes' > "$TMP/repoB/docs/supporting.md"

# Stub codex: record cwd and repo-B visibility per invocation, then emit valid JSON
# (same contract as test_fanout_scrub.sh).
cat > "$TMP/bin/codex" <<'STUB'
#!/usr/bin/env bash
if [ "${1:-}" = "exec" ] && [ "${2:-}" = "--help" ]; then
  printf '%s\n' '--output-schema --output-last-message --ephemeral'
  exit 0
fi
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -C) cd "$2" || exit 64; shift 2 ;;
    *) shift ;;
  esac
done
printf '%s\n' "$PWD" >> "$STUB_CWD_LOG"
if [ -f docs/supporting.md ]; then
  printf 'visible\n' >> "$STUB_VIS_LOG"
else
  printf 'missing\n' >> "$STUB_VIS_LOG"
fi
[ -n "$out" ] || exit 64
prompt="$(cat)"
printf '%s\n' "$prompt" | sed -n 's/^TARGET DOC: //p' | head -n 1 >> "$STUB_DOC_LOG"
artifact_id="$(printf '%s\n' "$prompt" | sed -n 's/^ARTIFACT ID: //p' | head -n 1)"
artifact_sha="$(printf '%s\n' "$prompt" | sed -n 's/^ARTIFACT SHA256: //p' | head -n 1)"
reviewer="$(printf '%s\n' "$prompt" | sed -n 's/^You are the \([^ ]*\) reviewer.*/\1/p' | head -n 1 | tr '[:lower:]' '[:upper:]')"
printf '{"reviewer":"%s","round":1,"artifact_id":"%s","artifact_sha":"%s","verdict":"GO","schema_grounding_verdict":"PASS","verify_commands_executed":["pwd"],"source_artifacts":[],"dispositions":[],"findings":[]}\n' \
  "$reviewer" "$artifact_id" "$artifact_sha" > "$out"
STUB
chmod +x "$TMP/bin/codex"

STUB_CWD_LOG="$TMP/cwd.log" STUB_VIS_LOG="$TMP/vis.log" STUB_DOC_LOG="$TMP/doc.log" \
  PATH="$TMP/bin:$PATH" \
  bash -c 'cd "$1" && shift && "$@"' _ "$TMP/repoA" \
  "$FANOUT" ../repoB/docs/design.md 1 ../repoB/state >/dev/null 2>&1
rc=$?
[ $rc -eq 0 ] && ok "relative cross-repo fan-out completes" || bad "fan-out rc=$rc"

if [ "$(sort -u "$TMP/cwd.log")" = "$TMP/repoB" ] && [ "$(wc -l < "$TMP/cwd.log")" -eq 3 ]; then
  ok "all reviewers launched with the repo B worktree as cwd"
else
  bad "reviewer cwd is not the repo B root: $(sort -u "$TMP/cwd.log" | tr '\n' ' ')"
fi

if [ "$(sort -u "$TMP/vis.log")" = "visible" ]; then
  ok "reviewers can see repo B supporting files"
else
  bad "repo B supporting files not visible from reviewer cwd"
fi

if [ "$(sort -u "$TMP/doc.log")" = "$TMP/repoB/docs/design.md" ]; then
  ok "prompt carries the canonicalized absolute TARGET DOC"
else
  bad "TARGET DOC was not canonicalized: $(sort -u "$TMP/doc.log" | tr '\n' ' ')"
fi

json_count="$(find "$TMP/repoB/state" -maxdepth 1 -name 'round_1_*.json' -type f | wc -l)"
[ "$json_count" -eq 3 ] && ok "artifacts published to the canonical repo B out-dir" \
                         || bad "expected 3 JSON artifacts in repo B, got $json_count"

# An inherited GIT_DIR/GIT_WORK_TREE must not defeat repo discovery for the target doc.
printf '%s\n' 'a second design owned by repo B' > "$TMP/repoB/docs/design2.md"
: > "$TMP/cwd.log"
STUB_CWD_LOG="$TMP/cwd.log" STUB_VIS_LOG="$TMP/vis.log.3" STUB_DOC_LOG="$TMP/doc.log.3" \
  PATH="$TMP/bin:$PATH" GIT_DIR="$TMP/repoA/.git" GIT_WORK_TREE="$TMP/repoA" \
  bash -c 'cd "$1" && shift && "$@"' _ "$TMP/repoA" \
  "$FANOUT" ../repoB/docs/design2.md 1 ../repoB/state2 >/dev/null 2>&1
rc=$?
if [ $rc -eq 0 ] && [ "$(sort -u "$TMP/cwd.log")" = "$TMP/repoB" ]; then
  ok "inherited GIT_DIR/GIT_WORK_TREE does not re-narrow grounding"
else
  bad "GIT_DIR leak broke grounding (rc=$rc cwd=$(sort -u "$TMP/cwd.log" | tr '\n' ' '))"
fi

# A document outside any git worktree still grounds reviewers in its own directory.
mkdir -p "$TMP/nogit" "$TMP/nogit-state"
printf '%s\n' 'an unversioned design' > "$TMP/nogit/design.md"
: > "$TMP/cwd.log"
STUB_CWD_LOG="$TMP/cwd.log" STUB_VIS_LOG="$TMP/vis.log.2" STUB_DOC_LOG="$TMP/doc.log.2" \
  PATH="$TMP/bin:$PATH" \
  bash -c 'cd "$1" && shift && "$@"' _ "$TMP/repoA" \
  "$FANOUT" ../nogit/design.md 1 ../nogit-state >/dev/null 2>&1
rc=$?
if [ $rc -eq 0 ] && [ "$(sort -u "$TMP/cwd.log")" = "$TMP/nogit" ]; then
  ok "non-git target falls back to the document directory as cwd"
else
  bad "non-git fallback broken (rc=$rc cwd=$(sort -u "$TMP/cwd.log" | tr '\n' ' '))"
fi

echo "test_fanout_target_root: $pass passed, $fail failed"
exit $((fail > 0))
