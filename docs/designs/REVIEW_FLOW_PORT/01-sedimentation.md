# slice ③ — magi findings 沈殿 loop

- epic: [REVIEW_FLOW_PORT.md](../REVIEW_FLOW_PORT.md) (gh #233) / status: **v0.6** / 2026-07-28
- changelog: v0.6 = **統括裁定 (2026-07-28、sed-impl 実測)**。recurrence 定義を dedup key から分離 — grouping key = `title_norm` /
  カウント単位 = distinct `artifact_key` / `canonical_repo` を key に含めない、(a) cross-artifact = 昇格候補 と (b) within-campaign
  round 跨ぎ = churn signal の 2 tier に分離 (§5)。`dup_flag` の recurrence 利用を却下 (epic §7 DEFERRED)。fail-closed の絶対 floor を
  canonical basis 実測 45 へ再校正 + 95% 閾値 test 追加 (§5.2)。**新機構の追加なし。**
- state dir (本 doc の review 用): `docs/designs/REVIEW_FLOW_PORT/.dual-magi/01-sedimentation/`
- changelog: v0.5 = R5 8-fix micro-reroll, no new mechanisms; design-done checkpoint。本 doc 該当は r5-1 (doc_slug parent-dir
  fallback 削除 + 再計測 mark) / r5-2 (sighting_key に artifact digest) / r5-3 (verdict 適用を harvester 統合) / r5-4 (sidecar join
  key を repo 境界込みで一意化)。
- changelog: v0.4 = **data 層 identity の再設計**。列挙基準を subdir 再帰へ (r4-ops-1) / doc_slug 導出規則を新設 (r4-ops-2) /
  PK を **sighting_key** に分離し content_hash を非 PK 化 (codex#1 / r4-adversarial-4/5) / artifact_key を worktree root 基準に (codex#2) /
  campaign_id + 構造化 parent verification (codex#3 / r4-workflow-12) / observed_at を best-effort へ降格 (r4-ops-4) / fail-closed を
  canonical dir 基準 + 絶対 floor に (r4-ops-3 / r4-adversarial-12)。

## 1. 目的

繰り返し出る指摘を機械列挙し、hook / lint / guard / rail への昇格候補として提示する。昇格判断は人間 + 統括 (**自動昇格しない**)。
副産物として ①②④ の効果測定 data 面を供給する。

## 2. corpus の実測 (設計の前提)

全て 2026-07-28 実測。**行ごとに列挙基準を明記する** (基準を混ぜた表が v0.3 REJECT の一因 = r4-adversarial-15)。

| 量 | 値 | 基準 / 出典 |
|---|---|---|
| `.dual-magi*` dir | **232** (maxdepth 7 で飽和。d2:3/d3:16/d4:192/d5:19/d6:1/d7:1)、入れ子 3 | P4 |
| canonical / worktree replica | **67 / 165 = 71%** (`git worktree list` 18 entry) | r4-ops |
| **採用する file 列挙基準** | 最外 `.dual-magi*` dir **配下の再帰** = json **9,270**。うち **4,432 は subdir にしか無い** (per-doc campaign layout) | r4-ops-1 |
| 棄却した基準 (v0.3 直下限定) | 4,838 (P4 時点 4,874)。**findings の 50.0% = 15,275 件**を無警報で落とし、**本 campaign 自身の artifact も落ちる** | r4-ops-1 |
| findings-bearing / dir layout | direct 3,309 file / 15,252 findings + subdir 3,757 / 15,275 = **7,066 file / 30,527 findings**。dir 内訳 (入れ子 3 除く 229) は direct のみ 159 / direct+sub 41 / **sub のみ 12** / 空 17 | r4-ops-1 |
| doc_slug 導出可否 | 可 **8,130 json** / **不能 723** (plain `.dual-magi/` 直下)。dir basename distinct 16、素の `.dual-magi` が 70 dir | r4-ops-2 — **旧規則 (parent-dir fallback 込み) の値。§3.4 の fallback 削除で「可」は減り「不能」は 723 以上になる = dryrun で再測 (r5-1)** |
| recurrence 実測 (旧定義) | 再帰 basis で distinct doc_slug **81 / recurring 2 件**。直下限定 basis なら **3 / 0 件** | r4-ops-2 — **旧 recurrence 定義 (content_hash × doc_slug) の値。§5 v0.6 で定義自体を破棄したので基準に使わない** |
| recurrence 実測 (v0.6 定義) | (a) cross-artifact = **12 group / 35 sightings** / (b) within-campaign round 跨ぎ = **7 group / 18 sightings**。`content_hash` group の recurring は **0** (= dedup key なので当然) | sed-impl 実測 2026-07-28 (§5)。§9-2 の dryrun で採用列挙基準の値に再測 |
| 時刻 field 保有率 | findings-bearing 6,899 file 中 **53 = 0.77%** | r4-ops-4 |
| 欠損 | title 欠落 **590** / severity 欠落 48 / reviewer 系 body field 不在 258 file | r4-ops |
| role 内訳 / adapter 数 | per-persona 40.9% / xfamily 14.6% / xfamily-meta 13.6% / per-persona-xfam 13.1% / bare-round 7.7% / ledger 5.5% / 他 4.6%。harvest 3 role なら adapter は **`$.findings` 1 本**、全 role で 21 | P1 — **直下 4,874 基準。再帰基準は dryrun で再測** |
| `artifact_sha` populate | 46.8% | **r3 の再帰 basis。採用基準での再測は dryrun 課題** |
| severity 生値 | 7 種 (HIGH/MED/LOW/CRITICAL/nit/REJECT/**MEDIUM**) | r3-ops |

role を 3 種に絞る理由は「44% を捨てる」でなく「**adapter 面を 21 → 1 に落とす**」。7,066 と 6,899 は別 pass の計数で **167 件差**があり dryrun で 1 本の counter に統一する。① の memo が無い先頭 slice なので **着手日に本表を再実行**する (§9-1)。

## 3. 収穫 pipeline (原則: 「file の列挙」でなく「**canonical finding の同定**」)

### 3.1 dir 発見と file 列挙 (r4-ops-1)

- 発見 = `find ~/projects -maxdepth 7 -type d -name '.dual-magi*'` (実測 232/232)。
- **artifact root = 最外の `.dual-magi*` dir のみ**。内側に現れた `.dual-magi*` dir は root として採らず、**外側 root の走査からも subtree
  ごと除外**する (= 境界。内側は同じ `find` が別 root として拾う)。実測 3 例で、再帰列挙の二重計上 39 file の唯一の実体。
- file 列挙は **root 配下の再帰** (`.dual-magi/<DOC_SLUG>/…` を含む)。symlink は追わない (`find` default)。v0.3 の「各 dir 直下のみ」は
  棄却 — §2 の 50.0% 欠損と、**本 campaign 自身の artifact (`docs/designs/.dual-magi/REVIEW_FLOW_PORT/`) が読まれない**ため。

### 3.2 repo / worktree / artifact_key / campaign_id (codex#2, codex#3)

`git rev-parse --show-toplevel` + `git worktree list` で canonical checkout に正規化。worktree 配下の file を **replica と扱って skip
できるのは、canonical に同 relpath が在り、かつ `artifact_digest` (= 当該 json の raw bytes の sha256) が一致する時だけ** (r5-2)。
同 relpath でも digest が違えば **独立 artifact** なので skip せず別 sighting として harvest する — 実測で canonical と
`_formation_wt/issue107` の `docs/designs/.dual-magi/round_1_{melchior,balthasar,caspar}.json` など 5 組が「同 relpath・異 digest」。
skip 自体は **任意の cost 最適化**のままで、dedup の正しさは §3.5 の key が担う。`dirs_skipped_worktree` と **`dirs_canonical` (= 発見 − replica)** を
telemetry に出し、§5.2 の閾値は生の `dirs_found` でなく後者に掛ける (実測 71% が replica)。

**artifact_key** — v0.3 の「canonical_repo からの相対 path」は worktree で計算できない (実測: campaign dir が `~/projects/_formation_wt/…`
に実在し `relative_to` が `NOT_RELATIVE_TO_CANONICAL`)。導出順: (1) campaign dir から `git rev-parse --show-toplevel` で **その dir を含む
worktree root** を解決 → (2) worktree root からの relpath (canonical でも worktree でも同形) → (3) `artifact_key = "path:" +
<canonical repo identity> + ":" + <relpath> + "#r" + <round>`。**常に path surrogate**で、`artifact_sha` は列として保持し**同一 doc 内の
reroll 識別にのみ**使う (r4-ops-6 の 2 解釈を 1 つに確定)。`round` NULL は `#r-` と表記 (実測 0.6%、drop しない)。

**campaign_id** = campaign ledger dir 名 (= per-doc subdir 名、無ければ `.dual-magi*` dir 名)。1 doc に複数 campaign が付くので doc_slug
では campaign を同定できない。§6 の verdict 書戻しと 04 の撤去判定はこの列で join する。

### 3.3 role 分類 = 2 段 (r3-ops-2)

- **stage 1 = 内容判定**: top-level に `findings[]` (list of dict) があれば harvest 候補。無ければ `no_findings`。
  **stage 2 = 属性抽出**: `(round, reviewer)` は **JSON 本体 field が第一情報源**、file 名は fallback のみ。
- 除外: `_xfamily.meta.json` は **`_xfamily.json` matching より先に** `\.meta\.json$` で落とす (662 file / 13.6%、findings を持たない run
  metadata) / merged・synthesis は **file 名でなく body key** (`reviewer_files` / `dispositions` / `reviewers[]`) で判定 / ledger は
  stage 1 で自動除外。
- telemetry: `unparsed` = findings[] を持つのに分類できなかった file 数、`no_findings` = findings 不在 file 数 (**両者を混ぜない**)、
  `reduced_shape` = `location` / `finding_id` を欠く縮約 shape の findings 数。
- **log は path と件数のみ。file 本文を出さない** (診断 log は §3.7 の redaction path を通らない = r4-adversarial-16)。抜粋が要る時は
  `credential_redact_text()` を通し長さを cap する。

### 3.4 doc_slug の導出 (r4-ops-2、NEW)

v0.3 は doc_slug を NOT NULL + recurrence の唯一の分母にしながら導出規則を書いていなかった。導出は **§3.1 の列挙規則とセット**で決まる
(subdir 名が slug になる layout を捨てる選択と NOT NULL は両立しない)。

1. **per-doc subdir 名** `.dual-magi/<slug>/…` (採用基準ではこれが最頻) → 2. **dir 名 suffix** `.dual-magi-<slug>` → 3. body が
**exact artifact identity** を持つ場合のみ その `doc` / `artifact` field → 4. どれでも解けない → **`doc_slug` NULL のまま行は保存**し
**`doc_slug_underivable`** counter に計上する。**§5 v0.6 以降、doc_slug NULL 行も recurrence 集計に残る** (grouping は `title_norm` × distinct `artifact_key` で、`artifact_key` は必ず在る)。

**parent-dir fallback は削除する (r5-1)**。v0.4 は plain `.dual-magi/` 直下の file に `.dual-magi*` dir の親 dir 相対 path を当てて
いたが、これは **同一 root 配下の異なる doc を 1 bucket に潰す** (read-only probe: body に `doc`/`artifact` を持たない direct findings
file が 20 root / 98 file、同一 root 最大 18 file)。r4-ops-2 自身も parent-dir basis を distinct doc_slug=3 / recurring=0 と実測して
棄却しており、v0.4 本文の「723 は導出不能」とも自己矛盾していた。**plain `.dual-magi/` 直下 file は body の exact artifact identity が
無い限り `doc_slug=NULL`**、`doc_slug_underivable` に計上する (「黙って別 doc に混ぜる」より「不明と数える」)。doc 帰属は失うが、§5 v0.6 の recurrence は `artifact_key` で取るので行自体は集計に残る。

→ `doc_slug` は **NULL 許容** (NOT NULL で run を abort させない。v0.3 は 723 行で落ちる設計だった)。**NULL の実件数は 723 以上に
増える** — 正確な値と、それが recurrence 分母に与える影響は §9-2 の dryrun で再測して §2/§5 に貼り直す。粒度は **campaign dir 単位**で、
epic 分割下では 1 campaign dir に sub-doc 複数が付くので sub-doc 単位が要る指標は `source_path` から sub-doc を引く (02 §5 / 04 §5.1)。
導出規則は content_hash の構成要素なので **規則変更 = 再 harvest による全行再構築**。

### 3.5 identity の 2 分割 — sighting_key (PK) と content_hash (codex#1 / r4-adversarial-4/5)

v0.3 は content_hash を PK にした。しかし `title_norm` / `location_norm` / `severity_norm` は hash 入力なので、**訂正 harvest も
redaction pattern 追加も新 hash → 新行**となり汚染された旧行が永久に残る (一方向 ratchet)。

```python
J = lambda v: json.dumps(v, ensure_ascii=False, separators=(",", ":")).encode()   # 固定順 JSON 配列
# (1) sighting identity — 「どの campaign の どの artifact の どの finding か」。redaction でも訂正でも動かない
artifact_digest = sha256(<source json の raw bytes>).hexdigest()          # exact artifact identity (r5-2)
sighting_key = sha256(J([canonical_repo, campaign_id, source_relpath, artifact_digest,
                         reviewer, finding_id or f"#{ordinal}"])).hexdigest()
# (2) content identity — 「何を言っているか」。redaction 後の正準化 text で計算
content_hash = sha256(J([canonical_repo, doc_slug or "", artifact_key, reviewer,
                         title_norm, location_norm, severity_norm])).hexdigest()
```

- `source_relpath` = §3.2 の worktree root 解決後の repo 相対 path。`canonical_repo` を先頭に置くのは同じ relpath
  (`docs/designs/.dual-magi/<slug>/round_1_adversarial.json`) が複数 repo に実在するため。`ordinal` = file 内 `findings[]` の 0-origin 序数。
- **`campaign_id` + `artifact_digest` を key に入れる理由 (r5-2)**: v0.4 key は「canonical repo + worktree-root 相対 path + reviewer +
  finding_id」だったので、**同 relpath・異内容の独立 worktree artifact が同一 sighting に潰れ**、走査順で text / content_hash /
  parent_verdict が上書きされていた (実測 5 組)。digest を入れると同一 file を再 harvest しても key は不変なので、**§3.5 の
  「訂正・redaction pattern 追加は同じ行を書き換える」性質 (一方向 ratchet の回避) は保たれる** — digest は redaction 前の
  source bytes に対して取り、text 列の再計算とは独立。artifact json は campaign の write-once 成果物なので、digest 変化 =
  「同じ finding の訂正」ではなく「別 artifact」と読む。
- **PK = `sighting_key`**。`content_hash` は **非 PK の index 付き列**で recurrence の grouping にのみ使う。**upsert は
  `ON CONFLICT (sighting_key) DO UPDATE`** が `content_hash` + 可変 text 列を更新する → 訂正も新 redaction pattern も**同じ行を書き換える**。
- 素の `||` 連結は禁止 (field 境界が保存されず `title='AB'+loc='C'` と `title='A'+loc='BC'` が衝突)。NULL は明示的に空文字へ落とす。
  `severity_norm` 写像: `MEDIUM|MED-HIGH → MED` / `NIT|INFO → NIT` / `REJECT` 独立維持 / 他 upper-case。`*_norm` = 前後空白除去 + 全角
  半角統一 + 連続空白 1 個化。
- **可変列のいずれかが `IS DISTINCT FROM` の時だけ UPDATE** (無変更行を毎日触らない)。text が実際に変わったら **`parent_verdict` を NULL
  に戻し `text_rewritten` を +1** — verdict は旧 text に下されたもので新 text には移らない (r4-adversarial-4)。`first_seen_at` は保持。
- `source_path` の勝者規則: **canonical_repo 配下を優先、同順なら辞書順最小** (worktree skip を任意にしたまま provenance を決定的に
  する = r4-ops-8)。**recurrence は content_hash では取らない** — grouping は `title_norm`、カウントは distinct `artifact_key` (§5 v0.6)。生の行数はどの指標にも使わない。

### 3.6 時間軸 — first_seen_at を主軸に (r4-ops-4)

実測: findings-bearing 6,899 file 中、時刻 field を持つのは **53 = 0.77%**。slug → doc path の写像も 95 slug 中 **6 slug** しか
`<slug>.md` に解決しない。v0.3 の「窓は observed_at 基準」は母集団がほぼ空で、欠測が「安全側」でなく「達成側」に効く fail-open だった。

**`observed_at` は best-effort 列**に降格。導出は (1) file 内時刻 field → (2) campaign dir 直下最古 json の mtime → NULL。**成功指標にも
窓集計にも使わない** (`observed_at_null` は残すが閾値は紐付けない = 充足率を見るだけの計器)。**窓 (30 / 60 日) は `first_seen_at`
(= harvest 時刻) 基準**とし、**backfill が履歴を 1 点に圧縮する**ので指標は **backfill 以後の期間のみ**を対象とする (§7)。

### 3.7 credential hygiene (r3-adversarial-10 / r4-adversarial-6)

- `title` / `rationale` / `required_fix` は **INSERT 前に既存 redact/scrub path** (`plugins/harness-core/hooks/credential_patterns.sh` を
  単一 pattern source、実測 source 可 / 21 pattern) を通す。hash は **redaction 後の text** で計算する (§3.5)。redaction 後もなお match
  する finding は truncate でなく **drop** し `dropped_credential` に計上、drop 時も `sighting_key` / `source_path` / `severity` /
  `dropped=true` の **stub 行は残す** (corpus に無言の穴を作らない)。
- **kill switch は fail-closed**: flag は `~/.claude/hooks/magi_findings_harvest.disabled` (`credential_scrub.disabled` とは **別 path**、
  blast radius を結合しない)。flag があるとき harvest は **text 列の永続化を行わず非 0 exit** する (§5.1 の Discord が鳴る) —
  transcript scrubber の fail-open idiom を永続 store に持ち込まない。
- `personal.magi_findings` は **credential-bearing surface** と DDL comment に明記。ghost dub は allowlist gate (default-deny)、
  hippocampus の検索面は table 固有なので現状この table は露出しない — **これは現行 tool set の性質であって本設計の機構ではない**
  (§10 に未強制 convention として再掲)。

## 4. schema (migration 046)

成果物 3 点 (hippocampus-mcp 規約、045 が最新なので 046 は空き): (1) `046_magi_findings.sql` = `BEGIN`/`COMMIT` + 列コメント +
`COMMENT ON TABLE` (由来 = claude-harness#233、書き手 = `scripts/magi_findings_harvest.py`)、(2) `046_magi_findings_down.sql` =
`DROP TABLE IF EXISTS` × 2 (corpus から冪等再計算可 = loss-tolerant の理由を file 頭に書く。実測 52 up / 33 down、欠落は全て ≤037 で 038
以降は例外なく対を持つ)、(3) `manifest.yaml` に 1 entry — **manifest に無い migration は `hippocampus migrate` が実行しない**。

```sql
CREATE TABLE IF NOT EXISTS personal.magi_findings (
    sighting_key   TEXT PRIMARY KEY,   -- §3.5 (repo, campaign_id, source_relpath, artifact_digest, reviewer, finding_id|ordinal)
    artifact_digest TEXT NOT NULL,     -- §3.2/§3.5 source json raw bytes の sha256 = exact artifact identity
    content_hash   TEXT NOT NULL,      -- §3.5 redaction 後の内容 identity。重複計上防止 (dedup) 用であって recurrence の grouping 鍵ではない (§5)
    first_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(), last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 窓集計は前者 (§3.6)
    observed_at    TIMESTAMPTZ,        -- best-effort (§3.6)。指標に使わない
    canonical_repo TEXT NOT NULL, campaign_id TEXT NOT NULL,  -- §3.2、verdict 書戻し / 撤去判定の join key
    doc_slug       TEXT,               -- §3.4、NULL 可 (実測 723 json)。提示時の文脈表示 (§5 v0.6)
    artifact_key   TEXT NOT NULL,      -- §3.2、worktree root 基準の path surrogate + round
    artifact_sha   TEXT, round INTEGER, reviewer TEXT NOT NULL, finding_id TEXT,
    severity_raw   TEXT NOT NULL, severity_norm TEXT NOT NULL, shape TEXT NOT NULL,  -- full|reduced (§3.3)
    title TEXT NOT NULL, title_norm TEXT NOT NULL, location TEXT, location_norm TEXT,  -- title 欠落 590 は入口で除外
    rationale      TEXT, required_fix TEXT,        -- §3.7 redact 済
    parent_verdict TEXT, verdict_round INTEGER,    -- §6 が書く。text 変更で NULL 復帰
    dropped        BOOLEAN NOT NULL DEFAULT false, -- §3.7 の stub 行
    source_path    TEXT NOT NULL, source_mtime TIMESTAMPTZ,   -- 勝った file (§3.5 の決定的規則)
    first_run_id   BIGINT              -- FK 無し = harvest_runs 刈り込みを許すため (列 comment に明記)
);
CREATE INDEX IF NOT EXISTS magi_findings_content_hash_idx ON personal.magi_findings (content_hash);
CREATE INDEX IF NOT EXISTS magi_findings_campaign_idx     ON personal.magi_findings (campaign_id);

CREATE TABLE IF NOT EXISTS personal.magi_harvest_runs (
    run_id     BIGSERIAL PRIMARY KEY,  -- run_at 単独 PK は同一 tx で衝突する
    started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), finished_at TIMESTAMPTZ, exit_code INTEGER,
    mode       TEXT NOT NULL CHECK (mode IN ('backfill','incremental')),  -- dryrun は DB に触らない
    dirs_found INTEGER, dirs_skipped_worktree INTEGER, dirs_canonical INTEGER,
    files_seen INTEGER, files_parsed INTEGER, files_findings_bearing INTEGER,
    findings_seen INTEGER, findings_new INTEGER, text_rewritten INTEGER, unparsed INTEGER,
    skipped_no_title INTEGER, no_findings INTEGER, reduced_shape INTEGER,
    doc_slug_underivable INTEGER, observed_at_null INTEGER, dropped_credential INTEGER,
    verdict_coverage NUMERIC,          -- §6 parent_verdict 付与率
    verification_applied INTEGER, verification_nomatch INTEGER, verification_underspecified INTEGER,  -- §6 sidecar 適用 (r5-3/r5-4)
    recurring_candidates INTEGER       -- §5 候補件数 (指標死亡の検出用)
);
```

**tx 境界** (r4-ops-11): run 行 INSERT は**独立 tx で commit** (以降の失敗でも記録が残る) → findings upsert は batch tx → run 行 UPDATE
は最後に独立 tx。exit 3 経路でも UPDATE は走る。増分窓は「前回 `exit_code=0` かつ**同一 mode** の run の `started_at` 以降」。

**dryrun は mode 列に無い** (r4-ops-7): `--mode dryrun` は **DB に一切触れず** (harvest_runs にも書かない)、集計を **stdout に JSON** で
出す — (1) 候補件数 (2) 上位 20 の `(title_norm, distinct artifact_key 数)` (3) 全 counter。§5 に貼る数値は**この出力そのもの**で、table 不在
でも走るので epic §4 の受入順序 (dryrun → migration) が成立する。

## 5. recurrence と提示 (v0.6 = 統括裁定 2026-07-28、sed-impl 実測に基づく)

**v0.5 の「recurring = 同一 `content_hash` group が ≥2 distinct `doc_slug`」は破棄する。** 原因は **dedup key と recurrence key の
混同**: `sighting_key` / `content_hash` は `artifact_key` + reviewer + location + severity を含む**重複計上防止の key** であり、
同じ指摘が別 artifact に再出現したことを捉える key ではない。sed-impl 実測で `content_hash` recurring = **0** はこの定義の当然の
帰結であって、corpus に再発が無いことを意味しない (同 corpus で `title_norm` 完全一致の反復は実在する)。

- **recurrence grouping key = `title_norm`**。カウント単位は **distinct `artifact_key`** (同一 artifact 内の複数 sighting は 1)。
- **`canonical_repo` は key に含めない** — repo を跨いで再発する指摘こそ昇格価値が最も高いので、repo で割ると狙いの信号を消す。
- **2 tier に分け、混ぜない**。分割条件は **`campaign_id`** — `artifact_key` は round を含む (§3.2) ので
  「distinct `artifact_key` ≥2」だけでは **同一 campaign の round 再掲が (a) に混入する**。したがって (a) は
  **distinct `campaign_id` ≥2 を AND 条件にする**:

  | tier | 定義 | 実測 (2026-07-28、sed-impl 再測) | 扱い |
  |---|---|---|---|
  | (a) cross-campaign recurrence | 同一 `title_norm` group が **distinct `artifact_key` ≥2 かつ distinct `campaign_id` ≥2** | **19 group / 49 sightings** | **昇格候補**として提示 |
  | (b) within-campaign round 跨ぎ | 同一 campaign 内でのみ distinct `artifact_key` ≥2 (= round 再掲) | **2 group / 4 sightings** | **churn signal** (revision churn rule の可視化)。**昇格候補ではない** |
  | (参考) 合算 | distinct `artifact_key` ≥2 のみ (tier 未分離) | 21 group / 53 sightings | 提示に使わない (2 tier を混ぜた値) |

  **実測の基準 (2026-07-28 再測)**: campaign-dir 基準に修正した `artifact_key`、`title_norm` は literal 一致、**`doc_slug` NULL 行を含む**。
  v0.6 初版に載せた 12 group / 35 sightings は **旧 doc_slug derived-only 集計**の値なので置換した。

- **`dup_flag` は recurrence signal に使わない**。実測: present かつ非 new の 797 行のうち positive な dup marker は **47** のみで、
  explicit nondup 298 / free-text 452 に汚染されており、signal として成立しない。→ 「採らなかった選択肢」として epic §7 DEFERRED に記録。
- `artifact_sha` も使わない (実測: `artifact_sha ≥2` を満たす title_norm は corpus 全体で 3 件、全て空白差で分裂した退化断片)。
- `doc_slug` は **提示時の文脈表示**に降格する (どの doc で出たかを人が読むため)。`doc_slug IS NULL` 行も (a)(b) の集計対象に残る
  — 帰属先 doc が不明でも `artifact_key` は必ず在るので、旧定義のような無警報の脱落は起きない。
- **backfill dry-run は実装着手の precondition** のまま。stdout JSON を本節に貼ってから §7 の数値目標を確定する。この順序を飛ばすと
  「migration + script + cron を作った後に出力が空だった」を確実に踏む。dryrun の上位 20 は
  **`(title_norm, distinct artifact_key 数)`** で出す (§4 の記述もこの key に揃える)。
- **「指標が死んでいる」判定は tier (a) の group 数で行う**。旧定義の recurring 2 件は key の取り違えによる値なので基準に使わない。
  `recurring_candidates` を毎 run 記録し、tier (a) が継続して 0-3 件なら昇格候補提示という目的自体を再設計する (可視化と検出は別物)。

### 5.1 cron

成果物 **`hippocampus-mcp/scripts/cron_magi_findings.sh`** — `cron_token_levers.sh` を idiom として `flock -w 1800` (ln25) /
`SOPS_AGE_KEY_FILE` 可読性 pre-flight で **exit 2** (ln33-37) / `sops exec-env <hippocampus.enc.yaml> '<python> <script>'` (ln40) / 非 0
exit で `discord-bot post hippocampus-mcp` (ln46) / log `~/.local/log/magi_findings.log` (ln16) の 5 点を踏襲 (全て実在を実測)。
**Discord には log path のみ、log 抜粋を載せない** (§3.3)。**この日次 run が §6 の `verification.json` 適用も担う** (upsert 後、同一 run
内。r5-3) ので、sidecar だけが先に書かれた campaign も翌朝の run で verdict が入る。crontab 行は
`57 5 * * * bash $HOME/projects/hippocampus-mcp/scripts/cron_magi_findings.sh # magi_findings_daily`
(実測 41 本、5-6 時台は red_team `0 5 * * 1` / token_levers `47 5` / scan_session_creds `30 6` / pt_backfill `0 6 1 1,4,7,10` のみで
`57 5` は空き。本設計が 42 本目)。

### 5.2 fail-closed (r4-ops-3 / r4-adversarial-12)

閾値は **`dirs_canonical`** に掛ける。生の `dirs_found` には掛けない (実測 71% が worktree replica で、worktree を数本畳むだけで 80% を割り正常運用で毎朝鳴る)。

| # | 条件 | 動作 | 評価時点 |
|---|---|---|---|
| a | `dirs_canonical = 0` | exit 3 (無条件) | 発見直後 |
| b | `dirs_canonical < 直近成功 run (同一 mode) × 0.80` **または** `< 絶対 floor × 0.95` | exit 3 | 発見直後 |
| c | `files_findings_bearing < 直近成功 backfill × 0.50` (**backfill のみ**) | exit 3 | 全走査後 |
| d | `files_seen > 0 AND files_parsed = 0` | exit 3 | 全走査後 |

- **絶対 floor** は dry-run 実測の canonical dir 数で seed し (**2026-07-28 再校正: canonical basis 実測 = 45**。v0.5 の 67 は
  旧計数)、`--accept-baseline` を明示した run でのみ引き上げる。**(b) の `< 絶対 floor × 0.95` 閾値には test を 1 本置く**
  (44 で exit 3 / 45 以上で通過)。相対比
  だけでは 0.8 倍の緩やかな侵食が永久に発火しない (232 → 190 → 156 … が無警報で進む)。(c) を **backfill 限定**にするのは、incremental
  の対象 file 数が前日の campaign 活動量そのもので campaign が走らない日 (max-round 分布の median は 1) に必ず発火するため —
  incremental 側は `files_seen > 0 かつ parse 成功率 < 90%` を WARN とする。
- **初回 run は (b)(c) を評価しない** (dry-run 実測値が絶対 floor の初期値)。比較先は**同一 mode** に限る。`unparsed > 0` は WARN +
  例示 log (path のみ)。activation では**陽性対照と陰性対照の両方**を回す (§9-4)。

## 6. parent_verdict の書込み経路 (codex#3 / r4-workflow-12)

v0.3 は merged `round_<N>.json` の `parent_verification_notes` を読む設計だった。実測するとこの field は **自由文で `content_hash` も
per-finding status も持たず**、magi script 側に producer contract も無い。自由文解析をやめ **親が構造化 sidecar を書く**形にする
(campaign dir 直下の **`verification.json`**):

```json
{"canonical_repo": "/home/hrmtz/projects/claude-harness", "campaign_id": "REVIEW_FLOW_PORT", "round": 4,
 "written_by": "parent-synthesis", "verifications": [
  {"source_relpath": "docs/designs/.dual-magi/REVIEW_FLOW_PORT/round_4_adversarial.json",
   "reviewer": "adversarial", "finding_id": "r4-adversarial-3", "status": "verified", "note": "実測で確認"},
  {"sighting_key": "…", "status": "disputed", "note": "前提が古い"}]}
```

- `status` enum = `verified` / `disputed` / `unreviewed`。
- **join key は repo 境界込みで一意でなければならない (r5-4)**。v0.4 の `(campaign_id, reviewer, finding_id)` は `campaign_id` が dir
  basename でしかないため repo-wide で非一意 — 実測でこの tuple は 3,643 種中 **64 種が複数 canonical repo に衝突**し、
  `('.dual-magi','operative-x (= codex-exec, gpt-5.5)','r2-codex-1')` は 3 repo / 11 file に当たる。よって join は
  **`sighting_key` 直指定**、または **`canonical_repo` + canonical `source_relpath` + `reviewer` + `finding_id`** の完全形のみを
  受け付ける。`canonical_repo` / `source_relpath` を欠く entry は適用せず `verification_underspecified` に計上する。
- **0 件 match は counter に載せて次へ。複数 match は「どれも更新せず」 + 非 0 exit** (誤更新を counter で追認しない)。
  `canonical_repo` は sidecar の値と campaign path から解決した repo が一致することを確認してから使う (不一致は非 0 exit)。
- 消費側 = `scripts/magi_findings_mark_verdict.py --campaign <dir> --round <N>` で自由文は読まない。**`verdict_coverage`**
  (= parent_verdict NOT NULL 行 / 対象 campaign 行) を毎 run 記録し、04 の撤去判定はこの被覆率を見る (欠測を「無効」と読ませない)。

**適用順序 = harvester 統合 (r5-3)**。v0.4 は Step 4 synthesis 直後に sidecar を書いて `mark_verdict` を即実行する形だったが、
finding row を INSERT するのは後から走る日次 harvester なので、**sidecar 実行時点では対象行が存在せず 0-match counter が回るだけで
永久に verdict が付かなかった**。修正: **harvest の各 run が、upsert 完了後に同一 run / 同一 transaction 内で、走査した campaign dir の
`verification.json` を読んで適用する** (= `mark_verdict` の適用 logic を harvester から呼ぶ)。sidecar は campaign dir に残り続けるので、
**後から入った行にも次回以降の run で必ず当たる** — pending verification の永続 store は作らない (新 store を足さない側を採る)。
Step 4 からの即時実行は「早く反映されるだけ」の任意経路として残す (0-match でも失敗ではない)。

**起動者 (NAME THE INVOKER)**: `dual-magi-review` SKILL.md の **Step 4 (Synthesize)** に「`verification.json` を書く」+
「`magi_findings_mark_verdict.py` を実行する」の 2 行を足す。**verdict が付くことの保証は harvester 側**にあり、Step 4 の実行は
前倒しにすぎない。

> **この SKILL.md 改訂は slice ② の deliverable**。③ は script を出荷し ② が invocation を配線する (② は元々 dual-magi SKILL.md を
> v0.11.0 に bump する slice なので同じ bump に相乗りする)。**③ 単独では script は書かれても呼ばれない**ので ③ の checklist (§9-5) は
> 手動実行で verdict 行が入るところまでを確認する。epic §6 の「magi campaign 本体への変更一切」は fanout CLI / persona set / canonical
> template / fingerprint を指し、dual-magi-review SKILL.md の散文追記は含まない (epic §2-3)。

## 7. 成功指標

- **backfill dry-run の分布を貼るまで数値目標を書かない** (§5)。全指標は **`first_seen_at` 基準 = post-backfill era only** —
  backfill 分は全行が同一日に刻印されるので backfill 日を含む窓は指標に使わない (§3.6)。
- 昇格の記録 = gh issue に `promoted-from-findings` label (**label 未作成、実装 slice で作る**)。昇格した content_hash group の
  **新しい `artifact_key` での再出現**が昇格後 30 日 (first_seen_at 基準) で 0。**sunset**: 導入 60 日で昇格 0 件なら cron を retire
  (成功しているが誰も読んでいない状態を正常と区別できないため、非使用も撤去理由にする)。

## 8. probe

| # | 対象 | 確認事項 | 状態 |
|---|---|---|---|
| P1 / P4 | role 別 shape 分布 / `find` の maxdepth 曲線 | adapter が `$.findings` 1 本 / meta 除外順 / 縮約 shape 率、飽和深さ 7 と入れ子二重計上 39 file | **済** (§2、直下基準) |
| P7 | `hippocampus migrate` の未 manifest 挙動 | 046 が manifest 無しで適用されないこと | 未 |
| P8 | `credential_patterns.sh` の呼出し面 | source 可 / `credential_redact_text()` の適用範囲 / ALLOWLIST_REGEX が `credential_shape_patterns()` で適用されないことの確認 | 未 |
| P14 | worktree fixture | canonical と worktree の同一 campaign が同一 `artifact_key` に落ちること (§3.2)。**加えて: 同 relpath でも `artifact_digest` が異なる 2 file は別 sighting になること / digest 一致時のみ replica skip (§3.2/§3.5、r5-2)** | 未 |

## 9. activation checklist (epic §5 に加えて)

1. **§2 の実測表を着手日に再実行**し値が動いていないことを確認 (memo 不在 slice の代替 pre-flight)
2. `--mode dryrun` を DB 無しで走らせ stdout JSON (候補件数 / 上位 20 / 全 counter) を §5 に貼付。**§3.4 の fallback 削除後の
   distinct `artifact_key` / tier (a)(b) の group 数 / `doc_slug_underivable` で §2 と §5 の旧値を置換すること (r5-1 + v0.6 裁定)**
3. `hippocampus migrate` を**新環境で**通し 046 が適用されること (P7) / `crontab -l` に §5.1 の行が 1 本だけ存在
4. fail-closed の**陽性対照** (1 条件を人工発火 → Discord 着弾) と**陰性対照** (worktree 削除 / 静穏日で発火しないこと) の両方
5. campaign 1 本で `magi_findings_mark_verdict.py` を**手動実行**し `parent_verdict` NOT NULL 行 + `verdict_coverage` 記録を確認 (SKILL.md への配線は ② の deliverable)
6. **P14 fixture: 同 relpath / 異 `artifact_digest` の 2 file → 2 sighting (digest 一致時のみ replica skip)** (§3.2/§3.5、r5-2)
7. **順序 fixture: 空 DB → `verification.json` を先に置く → harvest 1 回 → 当該行の `parent_verdict` が populate される** (§6、r5-3)
8. **join fixture: 同一 `(campaign_id, reviewer, finding_id)` が 2 repo に在る sidecar → `canonical_repo` + `source_relpath` 完全形なら
   片方だけ更新 / 完全形を欠く entry は複数 match で無更新 + 非 0 exit** (§6、r5-4)
9. `gh label create promoted-from-findings`

## 10. やらないこと

- 自動昇格 (recurring → hook)。常に人間 + 統括の判断 / embedding・Jaccard 類似 dedup (v1 は正準化 hash + `title_norm` 完全一致)
- merged / synthesis / ledger の findings 取込み (親判定は §6 の書戻しで得る) / `.meta.json` の provenance 列取込み (**v2**)
- 既存行を pattern catalog 更新時に走査する `--mode scrub` (v2)。v1 は §3.5 の sighting_key upsert で **再 harvest が同じ行を書き換える**ことで足りるが、**source file 自体が消えた行には届かない** (epic §7)
- **未強制の convention** 3 本: (i) 「`~/projects` 配下の `.dual-magi*` に書ける主体は全て信頼される」は v1 の前提で guard 無し
  (混乱した agent は防ぐが敵対的な書き手は防がない) / (ii) §3.7 の「ghost dub・shared-search から除外」は DDL comment であって機構
  ではない / (iii) §6 の `verification.json` は reviewer subagent も書ける campaign dir に置き、親が書いたことを検証する機構は無い
  (04 §5.1 の撤去判定はこの強度に等しい)
