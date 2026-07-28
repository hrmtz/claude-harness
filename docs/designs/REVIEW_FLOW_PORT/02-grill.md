# slice ① — grill: ultramagi の premise 実在確認 phase

- epic: [REVIEW_FLOW_PORT.md](../REVIEW_FLOW_PORT.md) (gh #233) / status: **v0.5** / 2026-07-28
- state dir (本 doc の review 用): `docs/designs/REVIEW_FLOW_PORT/.dual-magi/02-grill/`
- changelog: v0.5 = R5 8-fix micro-reroll, no new mechanisms; design-done checkpoint。本 doc に R5 finding の該当なし (01 §3.4 の
  doc_slug 導出変更に伴い、効果指標の sub-doc 粒度は引き続き `source_path` 引き当てで取る)。
- changelog: v0.4 = **挿入位置の確定形を撤回** (r4-workflow-2)。`[0] SCOPE` と `[1]` は section でなく **単一 fenced code block 内の
  隣接行**であることが実測で判明したので、位置は **P9 の結果で決める**ことにし、確定形の記述を削除。効果指標に premise_id と
  slice 粒度を追加 (r4-ops-9 / codex#3)。
- 依存: 効果指標が [01-sedimentation.md](01-sedimentation.md) の `personal.magi_findings` を使う。

## 1. 目的

doc 起草前に premise の実在確認と user への最小質問を済ませ、schema-drift 系 CRITICAL を前倒しで消す。単独 skill 化しない —
**ultramagi SKILL.md への節追記**。

## 2. 置き場所と名前 (r4-workflow-2 / P9 待ち)

**実測**: ultramagi SKILL.md の `## Epic admission gate (before [0])` は **ln89-120 の独立 section** で ln120 に終わる。次の section は
**ln122 の `## The loop`** で、`[0] SCOPE` (ln125) から `[6] NEXT` (ln149) までは **ln124-150 の単一 fenced code block 内の行**である。

→ v0.3 が書いた「`[0] SCOPE` と `[1]` の隙間」という挿入位置は **section として存在しない** (code block を割ることになる)。同じ文が
「Epic admission gate の後」= ln120/122 の間 (= `[0]` より前) も同時に指しており、互いに排他な 2 位置を指していた。**この確定形は削除**する。

- **P9 完了 (2026-07-28、実測 = 上記 line map)**: 推奨形は否定されず、**(a) 独立 section + code block 行追加**を採用。
  確定した挿入位置 (改訂前 SKILL.md v0.4.0 = 317 行の行番号):
  - **`## Phase 0 (premise grill) — before [1] PLAN` を ln120 (Epic admission gate 末尾) と ln122 (`## The loop`) の間に独立 section として挿入**。
  - loop 側は `[1] PLAN` (ln127-128) の説明に「Requires Phase 0 (premise grill) on this slice first」を **1 行追加**するのみ
    (fenced block ln124-150 は行追加だけで構造不変)。
  - `## The loop` 見出し直後に「`[0]`–`[6]` は code block 内の行であって section ではない」注記を 1 段落追加 (§2 末尾の再発防止条項)。
- `[0]` は **code block 内の行であって節ではない** — この事実を SKILL.md 改訂時の注記として残す (同じ誤りの再発防止)。
- **節名は本 doc 全体で「Phase 0 (premise grill)」に統一**する。素の「Phase 0」と `[0]` を混在させない (ultramagi 既存の
  `[0] SCOPE` と番号が衝突するため)。
- epic の場合は **slice ごとに 1 回**回す (umbrella doc には掛けない)。
- SoT path: `plugins/harness-magi/skills/ultramagi/SKILL.md` の 1 本のみ。harness-kimi / harness-magi-codex 側への複製は scope 外。

## 3. 手順

1. **入力**: gh issue# or 口頭 task。`gh issue view` で目的・背景を要約。
2. **先制調査** (Explore subagent、read-only): 対象 code path / **schema-as-code** (`migrations/` の DDL file) / feature flag /
   既存テスト / 類似実装。
   - **live DB `\d` は使わない**。Codex 側 reviewer には psql 禁止 rail があり再検証できないため、`migrations/` を
     **全 family が grep で検証可能な共通 evidence 面**として採る (r2-codex-2)。
3. **質問最小化**: 調査で答えが出ない事項だけ AskUserQuestion、1 turn ≤3 問、推奨回答付き。
4. **refinement memo**: `docs/designs/<slug>_REFINEMENT.md`。premise ごとに **`premise_id` (連番) + evidence receipt**
   (実行 command + 出力抜粋、`path:line` 形式)。後続 reviewer は receipt の command を再実行して検証する。

## 4. LIVE-DB-UNVERIFIED の運搬 (r3-workflow-8)

live DB でしか確認できない premise (populate 率等) は memo に `LIVE-DB-UNVERIFIED` と明記する。**「検証を義務付ける」と書いただけ
では義務にならない**ので、structural な置き場所を 3 点用意する:

1. epic §5-5 の **slice 着手時 pre-flight** の第 1 項 = 「対応 memo の `LIVE-DB-UNVERIFIED` 行を全解消 (解消 = 実行 command +
   出力抜粋を memo に追記)」。
2. 残件 > 0 の slice は **PR 本文に件数を書き、0 でなければ merge しない**。
3. 1 件以上残る時点で **gh issue を 1 本立て、slice の PR から参照する** (TODO は頭でなく tracker)。

**memo が存在しない slice** (① 導入前に着手する ③ が該当) は、代替として当該 sub-doc §2 の実測表を着手日に再実行する
(01 §9-1。r4-workflow-8)。

## 5. 効果の検証 — 直接指標を採る (r3-ops-4 / r4-ops-9 / codex#3)

**v0.2 の「導入後 3 campaign の round 1 REJECT+CRITICAL 件数 vs 歴史平均で 30% 減」は破棄する。** 実測 baseline
(canonical row の round=1 かつ REJECT/CRITICAL = 266 行 / 57 campaign) の分布は min 1 / median 2 / mean 3.4 / p90 6 / max 18 の
強い右裾で、

- 「3 campaign の中央値 ≤1」は **効果ゼロでも 26% の確率で達成**される。比較先を mean 3.4 に取ると **64%** に跳ね上がる
- doc は post 側を中央値・歴史側を平均と書いており、そもそも同じ統計量を比べていない
- `artifact_sha` NULL の 74 行が単一 bucket に融合し歴史 mean を 3.4 → 4.59 (+35%) に膨らませる

n=3 の件数比較はコイン投げなので **採用しない**。代わりに Phase 0 の作用機序に直接対応する指標を使う:

> **premise 再指摘率** = Phase 0 の refinement memo に記録された premise のうち、後続 round で **schema / premise 系 finding として
> 再指摘されたもの**の割合。

- **分母 = memo の `premise_id` 数** (slice ごとに確定、人が数えられる小さい数)。
- **分子 = 再指摘された premise の distinct 数** (finding の行数ではない)。1 premise に複数 finding が付いても 1 と数える
  — 行数で数えると比が 100% を超える (codex#3)。
- **粒度を揃える** (r4-ops-9): memo は **slice 単位**、`magi_findings` の campaign 識別子は `campaign_id` (campaign dir 単位)。
  epic 分割下では 1 campaign dir に sub-doc が複数ぶら下がるので、分子は **当該 sub-doc を `location` / `source_path` に持つ
  findings** に絞る (01 §3.4 の sub-doc 引き当て)。
- 判定は `severity_norm` を使う (生 severity は REJECT 75 と CRITICAL 3,219 を混ぜ MEDIUM 57 を落とす)。
- **n=3 でも意味を持つ** — campaign 間の難易度差でなく、同一 campaign 内の memo と findings の対応を見るため。
- 目標値は最初の 3 campaign を実走してから確定する (件数目標を先に書かない)。

`artifact_sha` NULL 行に依存しない (対応付けは campaign 内で閉じる) ので、r3-ops-4(b) の NULL bucket 汚染も構造的に回避される。

## 6. probe

| # | 対象 | 確認事項 | 状態 |
|---|---|---|---|
| P9 | ultramagi SKILL.md の実構造 | section 境界の行番号 map + Phase 0 を (a) 独立 section にするか (b) loop 表に 1 句足すかの選択。結果を §2 に行番号で貼ってから SKILL.md を書く | **済 (2026-07-28)** — §2 に line map 反映、(a) 独立 section + code block 行追加を採用 |
| P10 | premise ↔ findings の対応付け | §5 の分子を人が数える**手順** (どの列を見て何を判断するか) を 1 campaign 分実際に書き下せること + 所要時間 | 未 (01-sedimentation の `magi_findings` 着地待ち。SKILL.md 側は目標値を書かない形で先行済) |

## 7. activation checklist (epic §5 に加えて)

1. **P9 完了** — 挿入位置を行番号で §2 に確定させる (未 probe の位置に対して SKILL.md を書かない)
2. ultramagi SKILL.md の version bump (frontmatter 実測 v0.4.0 → v0.5.0)
3. `readlink -f ~/.claude/skills/ultramagi` が repo path を指す
4. **新 session** で `/ultramagi` を明示呼出しし Phase 0 節が load されること + version 行一致
5. 実走 1 campaign で REFINEMENT memo が `premise_id` + evidence receipt 付きで生成されること

## 8. やらないこと

- 単独 skill 化 (ultramagi SKILL.md への節追記のみ)
- grill 内での live DB 検証 (schema-as-code に限定、`LIVE-DB-UNVERIFIED` で slice pre-flight に委譲)
- schema-drift の自動分類 column (判定時に title/rationale を人間が読む)
- harness-kimi / harness-magi-codex 側 SKILL.md への複製 (v1 は Claude flow のみ)
- **未強制の convention**: §4 の pre-flight は checklist であって hook ではない。memo を開かずに実装を始める経路は塞がっていない
- **未強制の convention**: §5 の分子は人手判定であり、機械的に premise と finding を突き合わせる column は作らない
