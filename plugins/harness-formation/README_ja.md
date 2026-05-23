# njslyr7

[English](README.md) | 日本語

兄弟 tmux pane に常駐させた AI agent worker (claude / codex) を束ねるための最小オーケストレーション層。`formation` CLI と同名の agent skill として出荷する。

## なぜ作ったか

`multi-agent-njslyr` (v6) は育ちすぎた。8 体固定編成のヤクザ、YAML タスクキュー、guardian スクリプト群、CLI 別 instructions の多重化、二次 dashboard ── 動いてはいたが、日常タスクには儀式が重すぎた。

切り替えのきっかけは 2026 年 4 月の PRS-LLM-dev セッション。その場で即興した「共有 mailbox ファイル + bash ヘルパー数本」だけの 3 pane ミニシステムが、数時間タスクを綺麗に片付けた。同じ形の問題に v6 の重装備を持ち出すのは過剰、と気付かされた。

`njslyr7` はその蒸留。**残すべきプロトコル (観測性 / peer メッセージング / スマホ経由の human-in-the-loop)** だけを残し、**公式 Claude Code primitives (`Task` / `TaskCreate` / `ScheduleWakeup` / `Memory`) が既に提供している機能** は全部捨てた。

## 中身

- `bin/formation` ── サブコマンド 7 つの 1 本 CLI:
  `spawn | msg | status | inbox | reap | report | done | ask`
- `lib/mailbox.sh` ── jsonl append-only の pane 間メッセージバス。recipient 毎カーソル、flock で書き込みガード
- `lib/wake.sh` ── `tmux send-keys` と `paste-buffer` のヘルパー
- `lib/redact.sh` ── credential パターン検知 (送信全パスで hard-refuse)
- `skills/formation/SKILL.md` ── agent skill (claude + codex)。発火条件と実行フロー
- `skills/formation/templates/briefing.md` ── lead と worker の契約書テンプレ

全部で約 300 行。

## いつ使うか

worker 起動のコストは「fresh な AI agent プロセス (claude or codex) 1 個 + pane 分割 + 数秒の bootstrap」。これを払う価値があるのは **数分から数時間レンジ** のタスクで、かつ以下のいずれかが欲しい場合:

- 生観測 (pane を tail してリアルタイムで見たい)
- 途中で方針変更 (`formation msg worker-1 "approach B に切り替え"`)
- スマホ経由の human-in-the-loop ── worker が LINE で問い合わせ、モバイル Claude の `/remote-control` (alias `/rc`) で返信

これより短い作業は built-in `Task` tool を使え。

## インストール

```bash
git clone https://github.com/hrmtz/njslyr7.git ~/projects/njslyr7
cd ~/projects/njslyr7
bash install.sh
```

`install.sh` は `bin/formation` を `~/.local/bin` に、`skills/formation` を `~/.claude/skills` (Claude Code) と `~/.codex/skills` (Codex) の両方に symlink し、ランタイム状態用 dir `~/.njslyr7/` (mailbox と registry、git 管理外) を作る。

update 後は `install.sh` を再実行。冪等。

## 使い方

### 1. Claude Code / Codex 経由 (推奨)

tmux 内の AI agent セッションに一言:

> 「○○を別 pane で formation 走らせて、数時間かかる」

skill が自動発火し、briefing を詰めた上で spawn してくれる。

### 2. 手動 CLI

```bash
# briefing を書く
cp ~/.claude/skills/formation/templates/briefing.md ./briefing.md
$EDITOR ./briefing.md

# spawn — claude worker (デフォルト)
formation spawn ./briefing.md refactor-1

# spawn — codex worker
formation spawn --cli codex --model gpt-4.1-mini ./briefing.md refactor-1

# 監視
formation status              # 全 worker と最新 pane 行
formation inbox               # worker からの未読報告

# 途中指示
formation msg refactor-1 "approach B に切り替えて"

# 畳む
formation reap refactor-1
```

### 3. worker 側 (worker の agent が Bash tool から叩く — claude/codex 共通)

```bash
formation report "phase 1 完了、phase 2 着手"
formation ask "schema migration vs dual-write どっち？"   # LINE 通知飛ぶ
formation done "PR #42 出した、tests green"              # LINE 通知飛ぶ
```

### スマホ介入

`[ASK]` が LINE に来たら、スマホの Claude から:

```
/remote-control formation-refactor-1
```

worker の session に attach される。そのまま手でタイプして返事すればいい。

## 設計不変条件

- **Memory MCP は lead と worker で共有**。worker は自分の entry を `formation/<worker_id>/` namespace 下に書き、親を汚染しないこと
- **CWD 継承**。worker は lead pane と同じ作業ディレクトリで起動する。cross-project spawn は v1 では未対応
- **観測者特権**。`~/.njslyr7/mailbox/log.jsonl` は平文 jsonl。tail すれば全 formation の通信が生で見える。mailbox 自体は暗号化しない ── redaction フィルタ + SOPS 規律が機密を mailbox から遠ざける役割
- **Sanada (真田) / Matsuoka (松岡)** プロトコル (破壊操作前に黙って backup / 撤退禁止) はユーザーの global `~/.claude/CLAUDE.md` (Claude Code) または `~/AGENTS.md` (Codex) に常駐。njslyr7 は前提として動く、再掲しない

## クレデンシャル規律 (絶対)

**mailbox / msg / briefing のいずれにも、平文の credential を貼るな。** mailbox は平文 jsonl として永遠に残る。1 度漏れれば、誰が tail しても毎回見える。

- credential は SOPS 暗号化ファイル (`*.enc.yaml` / `*.enc.env`) で管理
- agent は値ではなく「パスと decrypt コマンド」で参照する:
  - ✗ `formation msg worker-1 "key は sk-abc123..."`
  - ✓ `formation msg worker-1 "sops -d config/secrets.enc.yaml | jq -r .openai で decrypt"`
- 送信時チェック: `formation msg / report / done / ask / spawn` はすべて body を `is_credential_like` に通す。マッチしたら exit 3 で hard-refuse、`~/.njslyr7/mailbox/refuse.log` に試行を記録 (body 自体はログに残さない)
- 検知パターン: `sk-*`, `ghp_*`, `gho_*`, `AKIA*`, `*_API_KEY=...`, PEM private key, 長い JWT など

SOPS 未整備のプロジェクトでは `sops --encrypt` を先に走らせてから依頼せよ。平文フォールバックは無い。

## ステータス

v0.1 ── 動作確認済、実戦 dogfood 未実施。`wake.sh` の ssh fallback と lead 側の inbox 自動 poll は v2 回し。

設計の詳細と「v6 から意図的に落としたもの」は `docs/spec.md` 参照。
