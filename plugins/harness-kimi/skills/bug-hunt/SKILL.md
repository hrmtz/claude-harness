---
name: bug-hunt
version: 0.1.0-kimi
description: |
  非自明な diff に対する adversarial 事後レビュー。race/concurrency (HORNET)、edge-case/null-empty (GNAT)、error-swallow/silent-failure (WASP) の 3 視点で並列にバグを狩る。
type: prompt
whenToUse: |
  50 行以上 or 3 ファイル以上の diff が landed した後、production deploy 後、リファクタ後、またはユーザーが "bug-hunt" / "adversarial review" / "find what I missed" と言った時。
arguments:
  - diff_scope
disableModelInvocation: false
---

# bug-hunt — adversarial post-change review (Kimi 移植版)

Claude harness の `bug-hunt` skill を Kimi 用に移植したもの。Claude/Codex の guardrail で議論しにくい脆弱性や並列バグを、Kimi で adversarial に洗い出すのに使う。

## 目的

変更後の diff を、**3 つの敵対的視点**でレビューして、線形自己レビューでは見落とすバグを引きずり出す。

- **HORNET** — race / concurrency / lock order / idempotency
- **GNAT** — null / empty / boundary / unicode / type coercion
- **WASP** — error swallow / silent failure / log hygiene / fallback corruption

## 発動条件

以下のいずれかで発動：

- 非自明な diff が landed（> 50 changed lines or > 3 files）
- production deploy 完了 → 次の deploy 前
- ユーザーが "bug-hunt" / "adversarial" / "find what I missed" と言った
- multi-file refactor 後

以下はスキップ：doc-only diff、1 行 config 変更、既に同じプロトコルでレビュー済み、WIP コミット。

## 手順

### 1. diff を固定する

`git diff HEAD~1 HEAD` または `git diff main...HEAD`、あるいはユーザー指定の scope から diff を取得する。

```bash
DIFF=$(git diff <scope>)
echo "$DIFF" | wc -l
```

5000 行を超える場合は論理チャンク（1 ファイル or 1 機能）に分割する。巨大 diff は signal を埋める。

### 2. 3 人のハンターを並列起動する

`AgentSwarm` を使って、次の 3 つの sub-agent を同時に起動する：

- `hornet` — race / concurrency
- `gnat` — edge-case / null-empty
- `wasp` — error-swallow / silent failure

各 sub-agent へのプロンプトは以下のテンプレートで構成する：

```
You are the {{item}} hunter in a bug-hunt review.
Read the persona template at ~/.kimi-code/skills/bug-hunt/templates/{{item}}_prompt.md.
Then analyze the following diff and produce findings in the exact format requested by that template.

Stay strictly in your lane. Do not cover the other hunters' bug classes.

Diff:
---
<DIFF>
---

Return your findings in the exact output format specified in the template.
Quality over quantity — 3 sharp findings beat 10 fuzzy ones.
```

`<DIFF>` にはステップ 1 で取得した diff をそのまま挿入する。

### 3. 集計・トリアージする

3 人の出力を受け取ったら、以下の構造でレポートを作成する：

```markdown
# Bug-hunt: <diff scope>

## Diff summary
- <N files changed, M lines>
- <one-line theme>

## Findings

### HIGH (n)
1. **<convergent | hornet | gnat | wasp>** file:line — <summary>
   <details> · fix: <action>

### MEDIUM (n)
...

### LOW (n)
...

### Dismissed (false positives)
- <finding> — <why not actually broken>

## Actions
- Fixed in this session: <list>
- Filed as gh issue: <list with #s>
- Re-verified with: <smoke / test command>
```

集計ルール：

- 重複は (file, line, theme) で判断
- 2 人以上が同じ箇所を指摘 → **convergent**（真陽性率が高い）
- 1 人だけ → single-hunter（見る価値ありだが優先度低）
- 各 finding に対して判断：
  - **Fix now** — HIGH severity は同セッションで修正し、smoke/test を再実行
  - **gh issue** — MEDIUM は issue 化
  - **Dismiss with reason** — false positive は理由を書く（次の hunt で同じ誤検出を繰り返さないため）

### 4. 再検証する

修正後は関連する smoke / curl / unit test を再実行する。ユーザー可視の動作に触れた場合は happy path を手動確認する。

## Anti-patterns

- 1 人の agent に 3 視点を全部やらせない。分離が signal の源泉。
- ハンター同士の結果を見せない。独立させて初めて convergent が意味を持つ。
- 巨大 diff を 1 回で hunt しない。
- "fix later, mental note" にしない。fix するか issue 化するか明示。
- 再検証なしで hunt 完了と言わない。
