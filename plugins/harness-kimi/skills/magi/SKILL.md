---
name: magi
version: 0.1.0-kimi
description: |
  高リスク変更前の 3 視点 pre-flight レビュー。技術（MELCHIOR）、運用（BALTHASAR）、商業（CASPAR）の 3 人のレビューアを並列で起動し、実行前に計画の盲点を洗い出す。
  walltime ≥ 2h、大規模 DML、不可逆な変更、新しいインフラレイヤー、確実な金銭消費、長時間の sleep/poll ループなどの高リスクタスクで発動。
type: prompt
whenToUse: |
  高リスクな変更を kick する前。特に walltime ≥ 2h、≥ 100M 行 DML、不可逆な cutover、新しいパイプライン/サービスレイヤー、≥ $10 確実消費、1h 以上の sleep/poll を含むスクリプト、など。
arguments:
  - brief
disableModelInvocation: false
---

# magi — three-perspective preflight review (Kimi 移植版)

Claude harness の `magi` skill を Kimi 用に移植したもの。Claude/Codex の過剰な guardrail で議論しにくい高リスク変更を、Kimi で事前に検討するのに使う。

## 目的

高リスクな変更を始める前に、**技術・運用・商業**の 3 つの直交する視点から計画を審査する。実行者本人が見落としがちな silent failure、回復コスト、代替案、ROI を事前に浮き彫りにする。

## 発動条件

以下のいずれかに該当する変更を kick する前に発動する：

- walltime ≥ 2h
- ≥ 100M 行の DML
- 不可逆 / 6h 以上かかる rollback
- 新しいレイヤー / パイプライン / サービス
- ≥ $10 の確実な消費
- 1h 以上 sleep / poll ループを含む単一スクリプト

以下はスキップ：小さな修正、1 行 edit、ad-hoc クエリ、doc/memory 編集、既に同じプロトコルで審査済みの変更。

## 手順

### 1. 変更 brief を用意する

ユーザーから brief が与えられていない場合は、まずユーザーに聞くか、簡潔な brief を自分で作成する。brief は以下を含める：

- 何をするか（1 段落）
- なぜやるか（driver / deadline / dependency）
- 推定 walltime、コスト、peak（disk / memory / CPU / network）
- 可逆性（rollback 経路と推定コスト）
- 同時に走る可能性のある他タスクとの衝突

brief は 200 行以内に収める。書きたい場合は `docs/magi/<YYYYMMDD>_<change-slug>_brief.md` に保存してもよい。

### 2. 3 人のレビューアを並列起動する

`AgentSwarm` を使って、次の 3 つの sub-agent を同時に起動する：

- `melchior` — technical
- `balthasar` — operational
- `caspar` — commercial

各 sub-agent へのプロンプトは以下のテンプレートで構成する：

```
You are the {{item}} reviewer in a Magi pre-flight review.
Read the persona template at ~/.kimi-code/skills/magi/templates/{{item}}_prompt.md.
Then review the following change brief and produce the output requested by that template.

Stay strictly in your lane. Do not cover the other reviewers' perspectives.

Change brief:
---
<BRIEF>
---

Return your review in the exact output format specified in the template.
```

`<BRIEF>` にはステップ 1 で用意した full brief をそのまま挿入する。

### 3. 結果を統合する

3 人の出力を受け取ったら、以下の構造で synthesis を作成する：

```markdown
# Magi pre-flight: <change name>

## Trigger that fired
- <どの閾値に該当したか>

## Persona summaries

### MELCHIOR (technical)
<要約>

### BALTHASAR (operational)
<要約>

### CASPAR (commercial)
<要約>

## Synthesis

**Convergent** (2 人以上が指摘した高信頼度の懸念):
- ...

**Divergent** (1 人だけの指摘 — ペルソナバイアスの可能性を考慮):
- ...

## Verdict
**PROCEED** / **PIVOT** / **ABORT** — <1 行の理由>

- PROCEED: 大きな懸念なし、実行可能
- PIVOT: より小さく / 安く / 速い path がある場合はそちらを提案
- ABORT: 今はどの形でも価値がない、延期 or 中止

## Next action
<具体的な次のステップ>
```

必要なら `docs/magi/<YYYYMMDD>_<change-slug>.md` に保存する。

## Anti-patterns

- 1 人の agent に 3 視点を全部やらせない。独立性が肝。
- 変更を始めてから Magi を回さない。沈没コストが synthesis を歪める。
- 些細な修正で発動しない。コストに見合わない。
- どの persona も見ていない領域は「未審査」として明示する。
