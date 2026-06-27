# Domain perspective preset: harness self red-team (= claude-harness メタ層 audit)

= claude-harness 自身 (`~/.claude/CLAUDE.md` + `~/.claude/skills` + `~/.claude/hooks` +
`~/projects/claude-harness` repo) を **定期的に red-team** する preset。
通常の dual-magi が「design doc を弾く」のに対し、 これは **rule system 自体の腐敗・矛盾・抜け穴**を炙る。

由来: gh hrmtz/claude-harness#27。 設計思想 = Diamond is Unbreakable のメタ層適用。
**仕組みを信じて任せられるのは、 仕組みが自分を疑う (red-team) から**。 その self-distrust を
harness 自身にも向ける。

## 二トリガー (= 適用 timing)

```
diff-driven (変更時)  : rule-conflict / security-hole / behavioral-residue   ← 編集が原因の欠陥
periodic sweep (時間) : drift-stale / dead-rule                              ← 放置中に世界が動いて腐る欠陥
```

stale / dead は「編集したから」でなく「放置中に世界が動いたから」腐る → diff では見えず、
時間トリガーの full sweep でしか炙れない。 だから両トリガー要る (= hippocampus #50 の
WAL 捕捉=変更時 + 合成=時間 backstop と同型)。

## perspectives

### rule-conflict
- 2 ルールが衝突した時の優先順位が未定義 (= どちらが勝つか書いてない)
- 後付け rule が前の rule と矛盾 (= 例: 「milestone 主体で通知」 vs 「2-3h 間隔で broadcast 必須」の境界)
- 同一概念に対する複数の数値/閾値が file 間で食い違う (= corpus size 訂正の類、 但し今回は rule 間)
- global CLAUDE.md ↔ project CLAUDE.md ↔ skill SKILL.md ↔ hook の 4 層で同 trigger に別挙動
- 「OVERRIDE any default」系の絶対命令が複数あり互いに上書き関係が不明
- persona stack (川尻/仗助/バイツァ/松岡/真田) の発火条件が overlap し、 同一場面で相反指示

### drift-stale
- 事実が変わったのに rule が古い (= grounding 切れ。 例: corpus 5M→10M 訂正の類)
- file path / host name / IP / port を hardcode した rule が実態とズレ (= topology table の alias 事故系譜)
- 参照先 memory file (`[[name]]`) / skill / script が rename・削除されて dead link
- version 番号・日付が embedded された rule が現在と乖離 (= 「v0.6.0 default」 が古い等)
- 「最近 / 直近 / N 回目」 等の相対表現が絶対座標で裏取りできない (= 時系列 hallucination 防止 rule 自体の自己違反)
- deprecated とマークされた pattern (= shared pane 等) が削除予定を過ぎても残存
- tool / CLI / MCP の説明文が実 API とズレ (= MCP corpus 「5M+」誤記の類)

### dead-rule
- 発火条件が現実に来ず、 一度も trigger しない rule (= 無価値 or 書き間違い)
- hook が settings.json / hooks.json に wire されておらず実行されない (= file 存在するが dead)
- matcher 正規表現が実 command 形と合わず素通り (= guard のつもりが no-op)
- 「N 回失敗したら」 等の閾値が高すぎて実質到達不能
- 参照される backup path / fallback 経路が存在せず、 発動時に逆に壊れる
- example / usage に書かれた invocation が現 args spec で動かない (= skill drift)

### security-hole
- SOPS / credential 経路の抜け穴 (= leak 9 回の系譜)。 `sops -d` を許す抜け道、 scrub 漏れ pattern
- credential scrub の kill switch (`credential_scrub.disabled`) が悪用・誤発火する経路
- hook の bash command guard を迂回できる構文 (= base64 / 変数展開 / 改行 injection)
- mailbox / briefing / pane prompt に平文 credential が焼ける経路 (= formation の hard-refuse 漏れ)
- `--fix` / mutation が read-only 約束を破れる抜け道 (= autonomous loop で人間 gate skip)
- backup path が予測可能で第三者読取り可能 (`/home/hrmtz/sanada_backup_persistent/` の perm)
- hook が user 入力を unsanitized で eval / sh -c に渡す (= command injection)
- ghost dub / memory embed が credential-shaped body を拾う経路 (`scope: private` 漏れ)

### over-firing/noise
- 効きすぎて可読性 / token / cost を食う rule (= 過剰装飾・冗長な reminder)
- 毎 session inject されるが大半の task で無関係な context (= temporal anchor / topic inject の S/N)
- guard が誤検知連発で正当な操作を阻害 (= false positive で workflow 停止)
- Discord 通知 trigger が過敏で milestone でない event まで push
- 同じ警告を複数 hook が重複出力 (= self_check + admission + reminder の overlap)
- 忍殺語 style 等の装飾 rule が技術精度・コード可読性を侵食 (= 自己禁止条項の自己違反検査)

### behavioral-residue
- structural (hook / skill skeleton) で書けるのに behavioral (CLAUDE.md の「〜せよ」) で書いてる
- 「AI が自発的に守る」前提の rule で、 過去に self-discipline failure 実績がある (= credential leak)
- memory `feedback_*` に留まり skill / hook に昇格していない教訓 (= behavioral → structural 未完)
- 同じ failure mode を behavioral で 2 回以上注意している (= structural 化の signal を見逃し)
- hook で enforce できる invariant (= main HEAD ≤ dev HEAD 等) が CLAUDE.md の口約束のまま
- persona / 比喩レイヤーが実際の structural guard を曖昧にしている (= 「黙って backup」が hook 化されず人頼み)

### injection-surface
- 外部入力 (memory / mailbox / recalled context / MCP tool 出力 / web fetch) が rule を上書きできる経路
- `<system-reminder>` / recalled memory が user instruction と誤認される境界 (= 既に防御文あるか、 穴あるか)
- ghost dub された memory が他 agent の context に注入され指示として作用する経路
- formation mailbox (平文 jsonl) 経由で worker に悪意ある briefing を流せる経路
- MCP server 説明文 / tool description が prompt injection vector になる (= 信頼境界の明示有無)
- recalled context が「書かれた当時の事実」 stale なのに現在の指示として効く (= 自己照合 rule の有無)
- skill の SKILL.md / preset file 自体が改変されて指示注入される経路 (= file integrity 前提の検査)

## perspective brief template

```
あなたは <claude-harness> の <perspective name> red-team reviewer。
harness の rule / skill / hook を **敵対的に** 精査し、 腐敗・矛盾・抜け穴を炙る。

context:
- target = ~/.claude/CLAUDE.md (= global rules) + ~/.claude/skills + ~/.claude/hooks
           + ~/projects/claude-harness repo (= plugins/harness-{core,formation,magi,rails})
- harness = rule system 自身。 これを審査するのは 「仕組みが自分を疑う」 メタ層 red-team
- 既知 failure 系譜: credential leak 9 回 / main 居座り / 時系列 hallucination / 同family盲点
- 不変ルール: read-only audit、 mutation は人間 gate (= --fix 禁)

GROUNDING (mandatory): rule が参照する file / path / host / version / memory link は
**実在 verify** する (= 推測で finding を書かない):
1. file / path 実在: `ls <path>` / `test -f`、 hook wire は settings.json + hooks.json を grep
2. memory link 実在: `[[name]]` → 対応 .md が memory/ dir に在るか
3. drift: rule の数値/日付/version が現状と一致するか (= git log / file mtime / 実 config)
4. dead 判定: hook matcher 正規表現 vs 実 command 形、 trigger 条件が現実に来るか

review 観点 (= <perspective> 視点):
1. <observation 1>
2. <observation 2>
...

finding ごとに分類 tag を付与: conflict / stale / dead / hole / noise / residue / injection

出力 format (per finding):
- severity: CRITICAL / HIGH / MED / LOW / nit
- category: conflict | stale | dead | hole | noise | residue | injection
- location: <file>:<line or section anchor>
- rationale: なぜ欠陥か (= 被害シナリオ)
- proposed_fix: 提案のみ (= 適用は人間。 「〜を hook 化」「〜を削除」「〜を絶対座標に」)
- confidence: high / med / low
- verify_commands_executed: [実行した ls/grep/test の list] (= 空 or generic のみ = degraded)

総合: CLEAN / MINOR-ISSUES / NEEDS-ATTENTION / CRITICAL-FOUND
```

## usage

```bash
# harness 全体を 7 lens で red-team (= 月次 full sweep)
/dual-magi-review ~/.claude/CLAUDE.md \
  --perspectives rule-conflict,drift-stale,dead-rule,security-hole,injection-surface \
  --domain-preset ~/.claude/skills/dual-magi-review/examples/harness_red_team_perspectives.md \
  --external codex-exec

# 変更時 diff audit (= 編集が原因の欠陥に絞る、 3 lens)
/dual-magi-review ~/.claude/CLAUDE.md \
  --perspectives rule-conflict,security-hole,behavioral-residue \
  --domain-preset ~/.claude/skills/dual-magi-review/examples/harness_red_team_perspectives.md \
  --external codex-exec
```

7 perspective 全部を 1 round で回すと sub-agent 数が多く noise るので、 trigger に応じて
5 (full sweep) / 3 (diff audit) に絞るのが実用 default。 但し cross-family round は必須
(= 同family盲点、 gh #195: 4 Claude round plateau → codex 1 round 6 new CRITICAL)。

## 配備 (= AgentShield 型、 Task C)

```
periodic red-team workflow(
  target  = ~/.claude/CLAUDE.md + skills + hooks + claude-harness repo,
  preset  = harness_red_team_perspectives.md,
  mode    = read-only (= --fix 禁、 mutation は人間 gate),
  family  = cross-family 必須 (= codex-exec)
)
  → 所見を conflict/stale/dead/hole/noise/residue/injection で分類
  → 各 finding を gh issue 化 (P3、 -R hrmtz/claude-harness)
  → CRITICAL は discord-bot post claude-harness "..."
cadence: 月次 full sweep + 変更時 diff audit を default、 実走で noise/cost 検証し調整
```

実装は `claude-harness` repo の red-team workflow script (= Workflow tool 経由) を参照。

## 真田 guard (= 重要)

- audit は **read-only、 `--fix` 禁**。 harness 改変は他 project へ波及する非可逆寄り
  → backup → diff → **人間判断**。
- **cross-family 必須**: Claude が書いた harness を Claude 単独 audit = 同family盲点。
- finding は **提案のみ**。 mutation (= rule の追加/削除/書換) は人間 gate を必ず通す。

## extending

harness が evolve したら preset 拡張:
- 新 hook category 追加 → dead-rule / security-hole perspective に wire 検査項目追記
- 新 persona / behavioral layer → behavioral-residue perspective に structural 化候補追記
- 新 external input source (= 新 MCP / 新 mailbox channel) → injection-surface に経路追記
- failure incident 発生 → 該当 perspective に「過去 incident」として観点 1 行追加
