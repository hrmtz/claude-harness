#!/usr/bin/env bash
# negative_claim_advisor.sh — PreToolUse Write/Edit/apply_patch hook (advisory only)
#
# Warns when a markdown / analysis document asserts ABSENCE ("読み手ゼロ",
# "never called", "no references", "どこからも読まれない") without stating, in
# the same paragraph, the denominator of the search that backs the claim
# (how many sites were scanned, how many were dropped/unresolved).
#
# Root incident: nakamura-fdr#46 (2026-08-05). The static analyzer sh2a.py
# silently dropped 49.7% of accesses (resolved 44,625 / dropped 44,092, of
# which 26,532 reads). Its R=0 became "write 1 / readers zero — must not be
# used" in KNOCK_CHANNEL_MAP.md §4, four workers concurred, and other docs
# cited it as a dead-store precedent. Ground truth: 3 writes / 6 reads.
# Headcount was no cross-check — everyone read the same broken tool.
#
# The same shape recurs everywhere: "grep found nothing so it doesn't exist"
# (dynamic dispatch unseen), "no callers so safe to delete" (reflection
# unseen), "no errors in the log so healthy" (dropped at log level).
# A negative claim is always overstated by exactly what the search missed.
#
# Scope: documents only (.md/.markdown/.rst/.adoc/.txt). Source code is
# deliberately out of scope — a guard that misfires on code comments gets
# disabled, and a disabled guard is worse than none (false reassurance).
#
# Behavior: WARN via additionalContext, never deny. Precedent for why:
# bash_command_guard's over-deny was misdiagnosed as a guard bug and an
# orchestrator burned an hour on it. Every firing is appended to
# ~/.claude/state/hook_logs/negative_claim_advisor.log so the false-positive
# rate can be measured before any promotion to deny.
#
# Suppression: a paragraph that quantifies its search (母数 / 捨て / n= /
# unresolved / dropped / N/M / percentages ...) passes untouched.
# Cooldown: one advisory per file per session (log records every hit anyway).
# Fail-open: anything unparseable exits 0 silently.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh" 2>/dev/null || exit 0

HOOK_INPUT="$(head -c 262144 || true)"
export HOOK_INPUT
[ -n "$HOOK_INPUT" ] || exit 0

tool=$(parse_tool_name)
case "$tool" in
    Write|Edit|apply_patch|write_file|search_replace) : ;;
    *) exit 0 ;;
esac

file_path=$(parse_tool_file_path)
content=$(parse_tool_content)
[ -n "$content" ] || exit 0

DOC_EXT='\.(md|markdown|rst|adoc|txt)$'
if [ -n "$file_path" ]; then
    echo "$file_path" | grep -qiE "$DOC_EXT" || exit 0
else
    # apply_patch with no explicit path: only proceed if the patch itself
    # targets a document file.
    echo "$content" | grep -qiE '^\*\*\* (Add|Update) File: .*\.(md|markdown|rst|adoc|txt)[[:space:]]*$' || exit 0
fi

# Patch payloads (apply_patch) need +/- line handling; Write/Edit content is
# literal document text where a leading '-' is a markdown bullet, not a
# removed line. Distinguish by tool, not by sniffing the content.
is_patch=0
[ "$tool" = "apply_patch" ] && is_patch=1

findings=$(CONTENT="$content" IS_PATCH="$is_patch" python3 - <<'PY' 2>/dev/null || true
import os, re

src = os.environ.get("CONTENT", "")
is_patch = os.environ.get("IS_PATCH", "0") == "1"

# Negative-absence claims, JA + EN. Each pattern is a claim that some search
# returned zero — the class of statement nakamura-fdr#46 proved unsafe to
# make without a denominator.
NEG = [
    ("読み手ゼロ",       re.compile(r"読み手ゼロ")),
    ("参照なし",         re.compile(r"参照(?:は|が|も)?\s*(?:無い|ない|ゼロ|存在しない|見つからない)")),
    ("使われていない",   re.compile(r"(?:使わ|使用さ|利用さ)れてい?(?:ない|ません)")),
    ("呼ばれていない",   re.compile(r"(?:呼ば|呼び出さ)れてい?(?:ない|ません)")),
    ("読まれていない",   re.compile(r"読まれてい?(?:ない|ません)")),
    ("1箇所もない",      re.compile(r"[1一]\s*[箇ヶカ]所も\s*(?:無い|ない)")),
    ("どこからも",       re.compile(r"どこからも")),
    ("到達しない",       re.compile(r"到達(?:しない|できない|不能)")),
    ("存在しない",       re.compile(r"存在しない")),
    ("dead store/code",  re.compile(r"\bdead\s+(?:store|code)\b", re.I)),
    ("never called/used", re.compile(r"\bnever\s+(?:called|used|read|referenced|reached|executed)\b", re.I)),
    ("no references",    re.compile(r"\bno\s+(?:references|refs|callers|readers|usages|uses|reads|writes)\b", re.I)),
    ("not used anywhere", re.compile(r"\bnot\s+(?:used|referenced|called|read|reached)\s+anywhere\b", re.I)),
    ("zero references",  re.compile(r"\bzero\s+(?:references|callers|readers|reads|usages)\b", re.I)),
    ("unreachable",      re.compile(r"\bunreachable\b", re.I)),
]

# Denominator / coverage vocabulary: evidence the writer knows how much the
# search saw AND how much it missed. Any hit in the same paragraph passes it.
DENOM = re.compile(
    r"母数|捨て|取りこぼ|未解決|件中|のうち|うち\s*(?:read|write|\d)"
    r"|全\s*\d+\s*件|解決\s*[\d,]+|n\s*=\s*\d"
    r"|\bunresolved\b|\bdropped\b|\bdiscarded\b|\bout\s+of\b|\bof\s+[\d,]+"
    r"|[\d,]+\s*/\s*[\d,]+|\d+(?:\.\d+)?\s*%|走査(?:範囲|対象|済)|検索(?:範囲|対象)|網羅",
    re.I,
)

# Strip fenced code blocks — code samples inside docs are out of scope by the
# same misfire logic that excludes source files. In patch mode only, keep
# added lines (minus the '+') and blank removed lines: a deleted negative
# claim is the fix, it must not fire. Outside patch mode a leading '-' is a
# markdown bullet and a leading '+' can be one too — leave both intact.
lines, in_fence, cleaned = src.splitlines(), False, []
for ln in lines:
    body = ln
    if is_patch:
        if ln[:1] == "+":
            body = ln[1:]
        elif ln[:1] == "-" and not ln.startswith("---"):
            cleaned.append("")
            continue
        elif ln.startswith(("*** ", "@@")):
            cleaned.append("")
            continue
    if re.match(r"^\s*(```|~~~)", body):
        in_fence = not in_fence
        cleaned.append("")
        continue
    if in_fence:
        cleaned.append("")
        continue
    cleaned.append(body)

out = []
para, start = [], 1
def flush(para, start):
    text = "\n".join(para).strip()
    if not text:
        return
    hits = [name for name, rx in NEG if rx.search(text)]
    if hits and not DENOM.search(text):
        snip = " ".join(text.split())[:80]
        out.append(f"L{start}\t{','.join(hits)}\t{snip}")

for i, ln in enumerate(cleaned, 1):
    if ln.strip():
        if not para:
            start = i
        para.append(ln)
    else:
        flush(para, start)
        para = []
flush(para, start)

print("\n".join(out))
PY
)

[ -n "${findings// }" ] || exit 0

# Every firing goes to the measurement log (pre-cooldown), so the
# false-positive rate is computable from real sessions before any deny talk.
adv_log="$LOG_DIR/negative_claim_advisor.log"
while IFS= read -r f; do
    [ -n "$f" ] && echo "[$(date +%F_%T)] file=${file_path:-<patch>} $f" >> "$adv_log" 2>/dev/null
done <<< "$findings"

# Cooldown: one advisory per file per session — repeated edits to the same doc
# must not train the operator to ignore the advisory (or disable the hook).
session=$(printf '%s' "$HOOK_INPUT" | jq -r '.session_id // .sessionId // ""' 2>/dev/null || echo "")
[ -n "$session" ] || session="ppid-$PPID"
state_dir="${XDG_RUNTIME_DIR:-/tmp}/claude-negclaim-advisor"
state_file="$state_dir/${session//[^a-zA-Z0-9_-]/_}"
cool_key="${file_path:-<patch>}"
mkdir -p "$state_dir" 2>/dev/null || exit 0
grep -qxF "$cool_key" "$state_file" 2>/dev/null && exit 0
echo "$cool_key" >> "$state_file" 2>/dev/null || true

matched=$(echo "$findings" | head -3 | sed 's/\t/ /g' | sed 's/^/  /')
msg="📉 negative-claim advisory: この文書は母数なしで「不在」を主張している:
${matched}
負の主張 (読み手ゼロ / never called / 参照なし / 到達しない) は、検索が取りこぼした件数を併記できないなら書くな。検索は取りこぼした分だけ常に「不在」を過大評価する。
実例 nakamura-fdr#46: 解析ツールが access の 49.7% (44,092 件) を黙って捨て、その R=0 を根拠に doc へ「読み手ゼロ・使用禁止」と書かれ worker 4 人が同意した。実際は 3 write / 6 read。
同じ段落に母数 (解決 N / 捨て M、n=、件中、N/M 等) を併記すれば発火しない。(advisory only、このまま書いても良い)"

jq -n --arg msg "$msg" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    additionalContext: $msg
  }
}' 2>/dev/null || exit 0
