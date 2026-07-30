# slice ② — free-range reviewer (4 体目)

- epic: [REVIEW_FLOW_PORT.md](../REVIEW_FLOW_PORT.md) (gh #233) / status: **v0.7** / 2026-07-30
- state dir (本 doc の review 用): `docs/designs/REVIEW_FLOW_PORT/.dual-magi/04-freerange/`
- changelog: v0.7 = v0.11.0 activation 実走で free-range 自身が検出した label 自己矛盾、引用 drift、
  budget 算術、disputed blocker の撤去 bypass を修正。SKILL.md v0.11.1 と acceptance evidence を反映。
- changelog: v0.5 = R5 8-fix micro-reroll, no new mechanisms; design-done checkpoint。本 doc 該当は r5-8 (撤去 count を round 非依存の
  semantic key の distinct に変更) / r5-3 (verdict 書戻しは harvester 統合なので §7-1 の配線条件を再定義)。
- changelog: v0.4 = **convergence gate 依存の撤回** (r4-workflow-1 REJECT / codex#4)。gate は Claude-native campaign に構造的に
  適用できないので P12 と「decision 不変」受入条件を破棄し、severity 制御の所在を SKILL.md 散文 gate に確定。budget 表は
  **SKILL.md 散文予算**として retitle (r4-ops-10 / r4-workflow-10)、撤去判定 SQL を distinct + coverage 条件に (r4-ops-5 /
  r4-workflow-12)、④→② の installer 依存辺を削除 (r4-workflow-6)。
- 依存: 撤去判定が [01-sedimentation.md](01-sedimentation.md) の `parent_verdict` / `campaign_id` を使う。
  **activation rail は不要** — `~/.claude/skills/dual-magi-review` は既に repo への symlink として実在し、本 slice は
  SKILL.md の version bump のみ (③④ の完了を待たずに着手できる)。

## 1. 形

- **dual-magi-review skill (Claude-orchestrated) 内のみ**。magi campaign の fanout CLI / persona set / canonical template には
  **一切触れない**。
- 3 perspective の**置換でなく追加 4 体目** (round 2 以降で任意起動、`--freerange` flag)。出力 file は
  `round_<N>_freerange.json`。SKILL.md の **Custom perspective label 予約語禁止**では
  `freerange` を user-supplied label に使えないが、built-in `--freerange` がその literal を
  所有する明示的例外。
- brief は 1 行「この doc の問題点を自由に探せ。checklist はない。」+ 出力契約 (Finding schema + file 返り) のみ。

## 2. severity の取扱い — 1 文で確定する

> **freerange の CRITICAL / HIGH は、他 reviewer と同様に plateau を止める。「数えない」のは *必要 reviewer 集合* であって
> severity gate ではない。**

- plateau gate の構造 (3 perspective + cross-family が毎 round 走る) は不変。freerange が走らなくても plateau 判定は成立する
  = gate に穴を開けない。
- 逆に freerange が open な CRITICAL/HIGH を出したら campaign は続く。**これは freerange の cost であり、§5 の撤去判定は
  この cost を勘定に入れる。**
- この severity 拘束を**実際に効かせているのは §3 の散文 gate**であって、機械 gate ではない (次節)。

## 3. 停止判定の所在 — 機械 gate は Claude-native state を読まない (r4-workflow-1 / codex#4)

v0.3 は停止判断が `plugins/harness-magi-codex/scripts/magi_design_convergence_gate.py` に委ねられていると仮定していた。
**引用した literal は正しいが推論が偽**である。実測:

| 実測 | 帰結 |
|---|---|
| gate の `PERSONAS = ('melchior','balthasar','caspar')` は module 定数、fanout phase は `round_<N>_<persona>.json` を 3 本決め打ちで組み立て、1 本でも欠ければ `UnsafeInput('fanout output set is incomplete')` を raise | Claude-native の per-reviewer file は **定義上 PERSONAS と一致し得ない** |
| dual-magi SKILL.md **Custom perspective label 予約語禁止**は `melchior` / `balthasar` / `caspar` 等を user-supplied perspective label として**使用禁止** (companion campaign の完了判定と衝突するため) | 上の一致不能はこの禁止が原因。回避すると別 campaign の状態を壊す |
| dual-magi SKILL.md **Adapters / design convergence**: 「Claude-native workflow の state だけを evaluator に渡してはならない」 | gate を Claude-native state で走らせること自体が**明示的に禁止** |
| `PHASE_WEIGHT` は `fanout:3` / `targeted:1` / `xfamily:1` のみ。`freerange` を参照する code は 0 hit | freerange は機械予算にも計上されない |

> **機械 gate は Claude-native state を読まない (SKILL.md の design convergence 節が渡すことを禁じている)。freerange の severity 拘束は、
> 3 perspective の severity を拘束しているのと同じ散文規則 — dual-magi SKILL.md Step 6 の stop criterion 5 (severity-gated
> terminal) — が担う。**

- したがって §2 の拘束は「gate に 4 本目を認識させる」ことでは達成されない。SKILL.md v0.11.0 の**本文**に、
  freerange の CRITICAL/HIGH も stop criterion 5 の対象であることを 1 文で書く。
- **companion (harness-magi-codex) campaign 側には freerange を持ち込まない** (非目標、§8)。持ち込むには fanout CLI・
  persona set・protocol snapshot・synthesis validator・gate/test の同時更新が要り、本 slice の scope を超える。

## 4. round budget — SKILL.md 散文予算であって wired enforcement ではない (r4-ops-10 / r4-workflow-10)

ultramagi SKILL.md の **Runaway guard** に「12 weighted model launches / campaign」
「3 full pairs / 12 weighted launches」が実在する。単位は **pair (fan-out + cross-family)**。

| 構成 | campaign 累計 |
|---|---|
| round 1: 3 perspective + cross-family | 4 |
| round 2: 3 + cross-family + **freerange** | +5 = **9** (残り 3 は retry) |

- v0.3 にあった「3 perspective のみ = 3 launch / 4 round」の行は**削除**した。plateau 条件 3 (現 revision に対して cross-family
  round が走っていること) により、cross-family 抜きの構成は plateau を名乗れず**到達可能な運用構成ではない**。
- **これは discipline target であって強制ではない**。round 1 を必ず通常構成で走らせるため、
  12 launch 内で full freerange round は 1 回だけ。残り 3 は retry に回す
  (cross-family 単独 round には使わない)。
- したがって**任意起動の根拠を budget 算術に置かない**。根拠は §2 のコスト論 — freerange の open な CRITICAL/HIGH が campaign を
  延長するので、統括が round 2 以降で明示的に付ける時だけ回す。

## 5. 撤去条件

### 5.1 計算経路

判定材料は `personal.magi_findings` で閉じる。完全な selection / coverage /
`HOLD_DISPUTE` query の SoT は dual-magi-review SKILL.md
**Operational procedure: freerange retirement**。以下は semantic blocker count の核だけ:

```sql
SELECT count(DISTINCT (title_norm, location_norm, severity_norm)) FROM personal.magi_findings
WHERE reviewer = 'freerange' AND severity_norm IN ('REJECT','CRITICAL','HIGH')
  AND parent_verdict = 'verified' AND campaign_id = :campaign;
```

- **数える単位は round 非依存の semantic key = campaign 内の `(title_norm, location_norm, severity_norm)` distinct (r5-8)**。
  v0.4 の `count(DISTINCT content_hash)` は誤り — `content_hash` の入力に含まれる `artifact_key` が **round を含む** ので
  (01 §3.2/§3.5)、同じ指摘の round 2/3/4 再掲は 3 つの異なる content_hash になり、本文が避けると宣言した生行 count と同じ方向へ
  歪む。生の `count(*)` も `count(DISTINCT content_hash)` も **撤去判定を常に「残す」側へ**倒すので使わない。
- 束縛は `doc_slug` でなく **`campaign_id`** (01 §3.2)。1 doc に複数 campaign が付き、epic 分割下では 1 campaign dir に sub-doc が
  複数ぶら下がる (本 campaign が実例)。**sub-doc 単位の判定が要る時は `source_path` から sub-doc を引く** (01 §3.4)。
- どの round の verdict を採るか: **いずれかの round で `verified` が付けば 1 と数える**。ただし text が変わった行は
  `parent_verdict` が NULL に戻る (01 §3.5) ので、判定は常に**現在の text に対する verdict**で行われる。
- `parent_verdict` は 01 §6 の `verification.json` 経路が入れる。**これが無いと撤去判定は計算不能**。
  **適用の保証は harvester 側** (01 §6 / §5.1、r5-3) — sidecar が harvest より先に書かれても、次の harvest run が
  upsert 後に同一 run 内で適用するので late row にも verdict が付く。
  **v0.6 訂正**: v0.5 までは適用者を `magi_findings_mark_verdict.py` という独立 script として書いていたが、
  slice ③ の実装 (hippocampus-mcp PR #265、
  `scripts/magi_findings_daily.py::apply_verifications`) では **harvester に統合**され、
  その名の script は存在しない。
  **doc も SKILL.md も script 名に依存させない** — 契約は「sidecar を書けば次回 harvest が適用する」であり、
  適用がどの entrypoint に住むかは実装の自由。

### 5.2 判定規則

1. **起動下限 (floor)**: freerange を起動した campaign が 3 本たまるまで判定しない。
2. **撤去候補 (非有効)**: 3 campaign 連続で verified blocker count が 0、disputed blocker
   count も 0、**かつ その 3 campaign の `verdict_coverage` が 100%**なら `RETIRE` 候補。
   zero-finding campaign は coverage complete とする。
   **被覆率が 100% 未満の間は判定を保留**し telemetry を上げる (r4-workflow-12: writer が動いていないだけの欠測を
   「freerange は無効」と読ませない)。`HOLD_COVERAGE` が 7 日を超えたら measurement-path
   incident として escalation、修復まで判定凍結。
3. **撤去 (非使用)**: 6 ヶ月経っても 3 campaign たまらなければ「使われていない」を理由に撤去。これで「任意起動が続かず判定が
   永久に発火しない」という反証不能を閉じる。

`severity` は freerange 自身が書く field なので単独では判定材料にしない。**外部信号 = `parent_verdict = 'verified'`** が必ず
AND で入る。HIGH+ の `disputed` は `HOLD_DISPUTE` で撤去不可。ただし外部信号の強度は
01 §10 の未強制 convention に等しい (`verification.json` は campaign dir 内にある)。
`RETIRE` は自動撤去権限でなく operator が PR/review 履歴と 3 campaign を照合する advisory。

## 6. probe

| # | 対象 | 確認事項 | 状態 |
|---|---|---|---|
| P13 | `magi_validate_findings.py` | reviewer label `freerange` が validation を通ること | **green** (`test_dual_magi_freerange.py`; validator に allowlist はなく compatibility probe としては trivial) |

v0.3 の P12 (`magi_design_convergence_gate.py` dry-run で decision 不変を確認) は **ill-posed なので破棄**した — gate は
Claude-native の per-reviewer file 集合が揃わない時点で例外を投げるので、4 本目を置く以前に decision は観測されない (§3)。

## 7. activation checklist (epic §5 に加えて)

1. dual-magi-review SKILL.md v0.10.1 → **v0.11.0 以上**に bump し、(i) `--freerange` の起動規約 (ii) §2 の severity 1 文
   (iii) **Step 4 (Synthesize) に `verification.json` の書出し 1 行** (01 §6) を入れる。
   **v0.6 訂正**: v0.5 は「書出し + `magi_findings_mark_verdict.py` 実行の 2 行」としていたが、統括裁定 (2026-07-28、
   req-20a6f6f5) により **skill は sidecar を書くだけ**、適用は次回 harvest に委ねる。理由: 即時実行は review という
   read-only 行為に DB credential と PG 到達性を要求し scope を広げる。SKILL.md には
   **「verdict の反映は非同期 (次回 harvest 時)」を明記**し、**script 名は書かない**
2. `readlink -f ~/.claude/skills/dual-magi-review` が repo path を指す (**実測: 今日すでに真**。新規 installer は要らない)
3. **新 session** で (i) skill 一覧に出現 (ii) 明示呼出しで load (iii) version 行一致
4. P13 が green
5. freerange campaign 1 本を実走し、`magi_findings` に `reviewer='freerange'` 行が入ること + 同 campaign で
   `parent_verdict` が **SKILL.md が書いた `verification.json` 起点で自動的に**入ること (③ の手動実行でなく配線の検証)。
   **Step 4 の即時実行が 0-match でも、その後の harvest run で verdict が付くところまでを 1 本の fixture で確認する**
   (空 DB → sidecar 先行 → harvest → `parent_verdict` populate、01 §9-7 と同じ経路。r5-3)
6. **retirement count fixture: 同一 finding を round 2/3/4 に再掲した campaign で §5.1 の count が 1 になること** (r5-8)

### 7.1 activation evidence (2026-07-30)

| item | evidence | state |
|---|---|---|
| 1 | PR #251 / commit `ec59ab0`; activation review fix は SKILL.md v0.11.1 | done |
| 2 | `readlink -f ~/.claude/skills/dual-magi-review` → repo の `plugins/harness-magi/skills/dual-magi-review` | done |
| 3 | new Claude session で明示 `/dual-magi-review ... --round 2 --freerange`; v0.11.0 load receipt | done |
| 4 | `python3 plugins/harness-magi/tests/test_dual_magi_freerange.py` | green |
| 5 | v0.11.0 run: `round_2_freerange.json` 10 findings + `verification.json` 10 freerange entries。hippocampus harvester run_id `7`: `findings_new=40`, `verification_applied=40`, `verification_nomatch=0`, exit 0。DB の freerange 行 `10/10` に `parent_verdict` (`verified=3`, `unreviewed=7`) | done |
| 6 | SQLite fixture: round 2/3/4 の同一 verified HIGH → distinct semantic blocker count `1` | green |

## 8. やらないこと

- 3 perspective の置換 (常に追加 4 体目) / 全 round での常時起動 (§4 より round 2 以降の任意起動)
- magi campaign の fanout CLI / persona set / canonical template / fingerprint への変更
- **companion (harness-magi-codex) campaign への freerange 持ち込み** (§3)。機械 gate / PHASE_WEIGHT / synthesis validator の
  同時更新が要るので v2
- `magi_design_convergence_gate.py` の変更 (本 slice は gate を読まない設計に変わったので、変更の必要自体が無い)
- freerange 自身が書く field (severity / dup_flag) だけによる撤去判定
- **未強制の convention**: 「統括が freerange を起動しすぎない」は運用規律であって、budget を強制する機構は無い
  (§4)
- **未強制の convention**: §2 の severity 拘束は SKILL.md 散文であり、script が freerange の CRITICAL を数える経路は無い
