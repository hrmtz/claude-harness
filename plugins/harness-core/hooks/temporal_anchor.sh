#!/bin/bash
# SessionStart hook: temporal anchor output (= anti-hallucination structural rail).
#
# Injects absolute time coordinates (= today / project age / recent activity)
# at session start so temporal claims are grounded in git/file metadata instead
# of model memory. Optional memory-file context can be enabled with
# HARNESS_TEMPORAL_MEMORY_DIR.
#
# Output is emitted once via SessionStart additionalContext.

set -uo pipefail

TODAY=$(date '+%Y-%m-%d')
TODAY_TS=$(date +%s)

HOOK_INPUT=$(cat)
CWD=$(printf '%s' "$HOOK_INPUT" | jq -r '.cwd // .workspace.current_dir // empty' 2>/dev/null)
[ -z "$CWD" ] && CWD="$PWD"

PROJ_ROOT=$(cd "$CWD" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null || true)

if [ -z "$PROJ_ROOT" ]; then
    # Not in a git repo; avoid noisy context for generic sessions.
    exit 0
fi

FIRST_COMMIT=$(cd "$PROJ_ROOT" && git log --reverse --format=%ai 2>/dev/null | head -1 | awk '{print $1}')
LAST_COMMIT=$(cd "$PROJ_ROOT" && git log -1 --format=%ai 2>/dev/null | awk '{print $1}')

# age 日数
AGE_DAYS=""
if [ -n "$FIRST_COMMIT" ]; then
    FIRST_TS=$(date -d "$FIRST_COMMIT" +%s 2>/dev/null || echo 0)
    if [ "$FIRST_TS" -gt 0 ]; then
        AGE_DAYS=$(( (TODAY_TS - FIRST_TS) / 86400 ))
    fi
fi

RECENT_COMMITS=$(cd "$PROJ_ROOT" && git log --since "1 week ago" --oneline 2>/dev/null | wc -l)

# Optional memory file mtimes (= extra grounding for "recently" claims).
MEM_DIR="${HARNESS_TEMPORAL_MEMORY_DIR:-}"
RECENT_MEM=""
if [ -n "$MEM_DIR" ] && [ -d "$MEM_DIR" ]; then
    RECENT_MEM=$(ls -t "$MEM_DIR"/feedback_*.md 2>/dev/null | head -3 | while read f; do
        basename=$(basename "$f" .md)
        mtime=$(stat -c %y "$f" 2>/dev/null | awk '{print $1}')
        echo "  - ${basename} (${mtime})"
    done)
fi
[ -z "$RECENT_MEM" ] && RECENT_MEM="  - (none configured; set HARNESS_TEMPORAL_MEMORY_DIR to include memory mtimes)"

# output JSON for SessionStart additionalContext
CTX=$(cat <<EOF
## 📅 temporal anchor — hallucination 防止 structural rail

- **TODAY**: ${TODAY}
- **project root**: ${PROJ_ROOT}
- **project age**: ${AGE_DAYS:-?} days (= first commit ${FIRST_COMMIT:-?})
- **last commit**: ${LAST_COMMIT:-?}
- **last 7d activity**: ${RECENT_COMMITS} commits
- **recent memory files**:
${RECENT_MEM}

**時系列 claim (= 「N 日前」 「半年来」 「1 年前」 「最近」 等) は必ず ground-truth (= git log / memory mtime / file ctime / 上記 anchor) で verify、 感覚 fabrication 禁止 (= memory feedback_temporal_claims_require_grounding)**

禁止語彙 default (= verification 通過しない限り使うな):
- 「半年来」「半年がかり」「1 年来」「久しぶり」「ここ最近」 等の漠然 expression
- 「N 日前」 等の具体数字も memory / git log での裏付け必須
EOF
)

jq -n --arg ctx "$CTX" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: $ctx
  }
}'
