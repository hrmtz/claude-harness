#!/bin/bash
# negative_claim_advisor.sh (claude-harness#281) の単体テスト。
# 既知陽性 (ADVISE) / 陰性対照 (SILENT) / patch mode / cooldown / 発火 log を検証。
# この test 自体が #281 の規約に従う: 検出器には既知陽性と陰性対照を両方通し、
# 「全部 SILENT」は検査器が壊れている兆候として扱う。
# Run: bash plugins/harness-core/tests/test_negative_claim_advisor.sh
set -u
HOOK="$(cd "$(dirname "$0")/../hooks" && pwd)/negative_claim_advisor.sh"
export XDG_RUNTIME_DIR="$(mktemp -d)"
export HOME="$(mktemp -d)"   # lib.sh の LOG_DIR (~/.claude/state/hook_logs) を隔離
trap 'rm -rf "$XDG_RUNTIME_DIR" "$HOME"' EXIT
LOG="$HOME/.claude/state/hook_logs/negative_claim_advisor.log"
PASS=0; FAIL=0; N=0

payload() { # $1=tool $2=file $3=content $4=session
  jq -n --arg t "$1" --arg f "$2" --arg c "$3" --arg s "$4" '
    {tool_name:$t, session_id:$s,
     tool_input:(if $t=="Edit" then {file_path:$f,old_string:"x",new_string:$c}
                 elif $t=="apply_patch" then {patch:$c}
                 else {file_path:$f,content:$c} end)}'
}
advises() { printf '%s' "$1" | bash "$HOOK" 2>/dev/null | grep -q 'additionalContext'; }
check() { # $1=ADVISE|SILENT $2=name $3=payload
  N=$((N+1))
  if advises "$3"; then r=ADVISE; else r=SILENT; fi
  if [ "$r" = "$1" ]; then PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m %s %s\n' "$r" "$2"
  else FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m expected %s got %s : %s\n' "$1" "$r" "$2"; fi; }

echo "== ADVISE: 既知陽性 — 母数なしの不在主張 =="
check ADVISE "JA 読み手ゼロ+どこからも" \
  "$(payload Write /tmp/a.md 'このレジスタは読み手ゼロ。どこからも読まれないので使ってはいけない。' s1)"
check ADVISE "EN never called + no references" \
  "$(payload Edit /tmp/b.md 'This helper is never called and has no references, so delete it.' s2)"
check ADVISE "bullet 内の主張 (bullet は patch 行ではない)" \
  "$(payload Write /tmp/c.md '調査結果:

- この関数は使われていない
- 削除して良い' s3)"
check ADVISE "dead store 引用" \
  "$(payload Write /tmp/d.md 'See SCHEDULER.md §5.2b for the dead store precedent.' s4)"
check ADVISE "apply_patch の追加行の主張" \
  "$(payload apply_patch '' '*** Begin Patch
*** Update File: docs/analysis.md
@@
+この I/O は参照が無いため dead store と判断する。
*** End Patch' s5)"

echo "== SILENT: 陰性対照 =="
check SILENT "同段落に母数 (解決/捨て)" \
  "$(payload Write /tmp/e.md '解決 44,625 / 捨て 44,092 (うち read 26,532)。この範囲では読み手ゼロだが捨てた分は未走査。' s6)"
check SILENT "n=/dropped 母数 (EN)" \
  "$(payload Edit /tmp/f.md 'no callers found in the scanned set (n=44625; 44092 dropped as unresolved).' s7)"
check SILENT "source code は対象外" \
  "$(payload Write /tmp/g.py '# this branch is never called and unreachable' s8)"
check SILENT "code fence 内のみ" \
  "$(payload Write /tmp/h.md 'ログ出力例:

```
warning: never called, no references
```

以上。' s9)"
check SILENT "無害な doc (over-warn しない)" \
  "$(payload Write /tmp/i.md 'デプロイ手順を 3 段階に分けて記述する。まず build、次に verify、最後に cutover。' s10)"
check SILENT "apply_patch で主張を削除するのは修正" \
  "$(payload apply_patch '' '*** Begin Patch
*** Update File: docs/analysis.md
@@
-このレジスタは読み手ゼロなので使ってはいけない。
+実際は 3 write / 6 read (解決 44,625 / 捨て 44,092)。
*** End Patch' s11)"

echo "== fail-open: broken payload =="
N=$((N+1))
out=$(printf 'not json' | bash "$HOOK" 2>/dev/null); rc=$?
if [ $rc -eq 0 ] && [ -z "$out" ]; then
  PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m broken payload exits 0 silently\n'
else
  FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m broken payload rc=%s out=%s\n' "$rc" "$out"; fi

echo "== cooldown: 同一 session 同一 file は 1 回、log には毎回残る =="
p=$(payload Write /tmp/cool.md 'この関数は呼ばれていない。' cool1)
N=$((N+1))
if advises "$p" && ! advises "$p"; then
  PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m 2nd hit same file+session suppressed\n'
else
  FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m cooldown not enforced\n'; fi
N=$((N+1))
logged=$(grep -c 'cool.md' "$LOG" 2>/dev/null); logged=${logged:-0}
if [ "$logged" -eq 2 ]; then
  PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m both hits logged (誤検出率を後で実測できる)\n'
else
  FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m log entries=%s expected 2\n' "$logged"; fi
N=$((N+1))
if advises "$(payload Write /tmp/cool.md 'この関数は呼ばれていない。' cool2)"; then
  PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m same file new session advises\n'
else
  FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m new session wrongly suppressed\n'; fi

echo
echo "RESULT: $PASS passed, $FAIL failed (母数: $N checks)"
[ "$FAIL" -eq 0 ]
