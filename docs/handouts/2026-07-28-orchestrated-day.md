# Handout — 2026-07-28、統合オーケストレーター体制の初日

Written by the coordinator (indigo-lantern) during the session, not from
recall: every number below was measured with the command named beside it.
Live machine state changes the moment anyone runs an installer — re-measure
before acting.

---

## この日に変わった一番大きいこと

**統括が実装をやめた。** user 指示 (「おまえが統合オーケストレーターとなって、
作業は基本的に下の人間にやらせて」) により、統括の仕事は routing / briefing /
merge 判断 / 測定契約の固定 / mailbox 応答に限定した。実装・review 実行は
worker に降ろす。記録 → memory `feedback_indigo_is_standing_coordinator`。

その結果、1 日で **worker 8 本** (claude 3 / codex 3 / kimi 2) を回し、
**PR 10 本 merge**、**issue 4 本 close**、**構造 issue 3 本起票**まで進んだ。
使い捨て前提で ~50 応答ごとに閉店 → resume packet → respawn、という回し方が
実地で機能した。

---

## 着地したもの

### claude-harness (`dev` = 6c1f7e2)

merge 済み PR (`git log --merges --since=2026-07-28`):

| PR | 中身 |
|---|---|
| #229 | dual-magi reviewer の **file 返り契約** (v0.10.0)。findings を per-reviewer JSON に Write させ、返り値は ≤200 words の receipt |
| #230 | **fat-output advisory hook**。`gh pr diff` / `gh issue list` / `git log` / `formation status` の絞りなし呼出しに絞り方を提案 |
| #237 | identity claim の **SessionStart timeout 死** 修正 (#236)。sentinel 走査を 1-pass 化、retention 30d→7d |
| #238 | **mailbox 未読件数の hook 注入** (#232)。UserPromptSubmit + SessionStart、fail-open、30 分 cooldown |
| #240 | ultramagi **Phase 0 (grill)** — 設計前に premise の実在を確認する段 (#233 slice ①) |
| #241 | AGENTS.md **superset 統合 + 節単位 drift checker** (#231) |
| #225 | magi_scrub が **age 秘密鍵 / PEM / JWT** を取りこぼしていた件 |
| #226 | mail-nudge `--quiet` (routine 出力で escalation が埋もれる件、#213 の前半のみ) |
| #227 | mailbox delivery outcome の命名が実際と逆だった件 |

close: **#236 / #232 / #231 / #212 / #214**。#213 は後半 (nonexclusive で nudge
経路が死んでいる件) が残るため **open 維持**。

### hippocampus-mcp (`dev` = 80925a4 時点、slice ③ は PR #265 で係争中)

| PR | 中身 |
|---|---|
| #263 | session analyzer が JSONL 行でなく **API response 単位**で数えるよう修正 |
| #264 | **`personal.token_levers_daily`** (migration 045) + harvest script + cron。束ね採用率と fat offender を日次計測 |

---

## 測定契約 (窓を開ける前に固定した、後から動かすな)

### genshijin 常時オンの効果 — 判定 8/4

- 採取器: `personal.token_levers_daily` (before/after 同一)
- metric: **output_tokens / responses** (日次 total は task volume 交絡で 4.5 倍振れる。
  per-response 正規化で 1.9 倍幅に収束するのを実測してから採用)
- baseline: **7/14-7/27 の 14 日、median 988.7 tok/resp**
- 閾値 (事前確定・自己正規化): **A ≤741 (0.75×) / C ≥890 (0.90×) / B は窓 1 週延長・閾値不変**
- **A の必要条件に「圧縮起因の訂正 incident 0 件」を含む** — token だけ減って正確性が落ちる測定にしないため
- 除外規則は **両窓とも除外なし**。当初 baseline から 7/26-27 (多体運転日) を除いていたが、
  worker からの指摘で実測したところ当該日の out/resp は 623.5 / 807.1 と**低い側**で、
  除外は A が出やすい向きに偏っていた。対称性を回復した (契約 v1.1、#218 comment)

### #235 観測窓 — 判定 8/10

hippocampus-mcp#235。**8/10 02:21 UTC まで作業ゼロ**。再測手順は #235 の comment に
cold start 可能な形で記録済み。起床は crontab one-shot (`235_measure_reminder_oneshot`、
8/10 11:33 JST に Discord push)。**pane を生かしておく必要はない** — この理由で
rust-crane は reap した。

両方とも crontab に one-shot reminder が入っている (`crontab -l | grep measure_reminder`)。

---

## #233 REVIEW_FLOW_PORT — 設計 5 round と実装の現在地

zenn 記事 (https://zenn.dev/kimuchan/articles/bc8e98682f8594) の運用要素 4 つの移植。
設計 doc は本日 commit した (それまで **untracked のまま campaign を回していた** — これ自体が
今日一番危なかった負債)。

campaign: **R1 (Claude×3) → R2 (codex) → R3 (Claude×3) → R4 (Claude×3 + codex 並列) →
R5 (codex)**、weighted launches **12/12 使い切り**、v0.1 → v0.5。全 round で REJECT が出続け、
R5 で REJECT-severity 0 / HIGH 6 (全て bounded) に到達したところで **altitude checkpoint**
を統括が承認して実装に降下した。zero-findings は Fable 級 reviewer では到達しないので待たない。

slice の現在地:

| slice | 状態 |
|---|---|
| ① grill | **merge 済み** (#240) |
| ③ 沈殿 loop | PR #265、CI green。**cross-family review で GO-WITH-REVISE / HIGH 3** → 修正中 |
| ④ babysit-pr | A-F 実装完了、live 配線の承認済み (順序条件付き)、実戦 1 回が残 |
| ② freerange | 実装中 |

### 設計を実測が上書きした 3 箇所 (すべて「literal は正しいが推論が偽」)

1. **corpus の実体**: `.dual-magi` は gitignore されていると書いていたが、hippocampus-mcp に
   276 件・PRS-LLM-dev に 53 件が **git tracked**。さらに worktree 複製で corpus の 71% が copy。
   dir も `.dual-magi-<slug>` variant を含めて 232 個、maxdepth 4 では 9.1% 取りこぼす (網羅は 7)
2. **GitHub review**: この account は **formal review を 1 件も作っていない**。全 PR で
   `reviews=0` / `reviewThreads=0` / `reviewDecision=""`。review 判定を reviewDecision に
   置く predicate は「不安定」ではなく**常に false**。issue comment + marker 契約
   (`^Independent review verdict: \*\*(BLOCK|PASS)\*\*`) に書き換えた
3. **PostToolUse の発火**: 「exit≠0 の Bash は error-wrapped shape で PostToolUse に届く」と
   推論していたが、実測では失敗する `gh pr create` は **PostToolUse を 1 回も発火させず**
   `PostToolUseFailure` 分岐に入る (Claude Code 2.1.220)

### recurrence が 0 件になった件 (裁定の実例)

slice ③ の dry-run で昇格候補が **0 件**。目的が数字上死んだので worker が ask してきた。
統括の裁定は「**まず positive control**」— 96% の findings が脱落していたので、
「recurrence が無い」でなく「pipeline が落としている」疑いを先に潰させた。結果:

- pipeline は正常 (この campaign 自身の artifact が output に生存、16/16・17/17・14/14)
- 原因は **dedup key と recurrence key の混同**。`content_hash` は artifact_key + reviewer +
  location + severity を含むので、doc を跨いだ再発を捉えられない
- 裁定: **recurrence key = `title_norm`、カウント単位 = distinct artifact_key**、
  tier (a) は `AND distinct campaign_id >= 2` (artifact_key が round を含むため)、
  tier (b) = within-campaign churn は混ぜない。**`dup_flag` は使わない** (実測: `present非new` 797 中
  positive marker は 47 のみで汚染)
- 結果 **(a) 19 group / 49 sightings、(b) 2 group / 4 sightings**

---

## 起票して残した構造欠陥

| issue | 中身 |
|---|---|
| **#239** | `formation ask` の resolve/ack が **nonexclusive worker に届かない**。worker は ASK 中 idle なので badge を読めず、親が手で nudge するまで両者が待つ。本日 3 回踏んだ |
| **#242** | 親の pull nudge が worker から **user 発話と区別できない**。実例: reap 判断の照会に対し worker が「user が使用中である証拠」として引用した発話が、実は私が送った nudge だった。観測汚染 + 権限混同の両方 |
| **#266** (hippocampus) | applied migration の literal を boundary gate がどう扱うか。045 の host comment に対し **2 つの gate** が compat 行を持つに至った。3 つ目が出たら gate 側か慣例側を直す |

---

## 運用の罠 (今日の実測、次に踏む人へ)

- **`.dual-magi/` 直下に merged file を書くと他 campaign の state を壊す**。同 dir を全 doc と
  magi campaign が共有している。dual-magi v0.10.1 で per-doc namespace
  (`${doc_dir}/.dual-magi/<doc-stem>/`) に分離した。実際に旧 campaign の
  `round_1/2.json` + `state.json` を上書きし、per-persona file から再構成する羽目になった
- **hash pin された snapshot が Git index 束縛だと、source を編集した時点で無関係な test 73 本が落ちる**。
  再生成は stage 後でないと効かない
- **`codex exec` を background で回すなら `< /dev/null` 必須**。無いと stdin 待ちで無音のまま止まる
- **`pkill -f "codex exec"` は自殺する** — 起動側 shell のコマンド行自体が pattern に一致する。pid 指定で殺せ
- **`gh pr checks --json` は gh 2.45.0 に無い**。`statusCheckRollup` を使う。
  `mergeable` は遅延計算で初回 UNKNOWN・2 回目 CLEAN、merged PR は永久 UNKNOWN →
  **state 分岐を mergeable 解釈より先に置く**
- **worker の formation msg 本文で backtick を使うと zsh が command substitution する**。
  今日 2 回、条件が欠落した状態で worker に届いた
- **applied migration は編集できない** (編集すると migrate が sha256 mismatch を永久に警告する。
  実例: `028_chassis_id_grok.sql`)。boundary 違反は bounded allowlist で回すしかない

---

## 片付けたもの

- **worktree 22 本除去** (claude-harness 23→11 / hippocampus 19→11)。条件は
  「origin/dev より先の commit ゼロ **かつ** 変更が AGENTS.md の一括 append のみ **かつ**
  稼働 worker 非使用」。branch ref は全保全、除去前に `worktree list` + `branch -vv` を
  `~/sanada_backup_persistent/worktree_cleanup_*` に保全
- **pane**: 完了した worker は即 reap。日終わりで稼働 3 本 (sed-impl / bp-impl / fr-impl)

## 次に読むもの

- `docs/designs/REVIEW_FLOW_PORT.md` (epic) と `REVIEW_FLOW_PORT/0[1-4]-*.md` (slice)
- #233 の design-done comment (round 経緯・launch 消費・DEFERRED 一覧)
- #218 の測定契約 v1.0 + v1.1 (baseline と閾値の確定値)
