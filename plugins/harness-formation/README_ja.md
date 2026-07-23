# harness-formation

[English](README.md) | 日本語

兄弟 tmux pane に常駐させた AI agent worker (claude / codex) を束ねるための最小オーケストレーション層。`formation` CLI と同名の agent skill として出荷する。

## なぜ作ったか

旧 v6 prototype は育ちすぎた。8 体固定編成、YAML タスクキュー、guardian スクリプト群、CLI 別 instructions の多重化、二次 dashboard ── 動いてはいたが、日常タスクには儀式が重すぎた。

切り替えのきっかけは 2026 年 4 月の長時間作業セッション。その場で即興した「共有 mailbox ファイル + bash ヘルパー数本」だけの 3 pane ミニシステムが、数時間タスクを綺麗に片付けた。同じ形の問題に v6 の重装備を持ち出すのは過剰、と気付かされた。

`harness-formation` はその蒸留。**残すべきプロトコル (観測性 / peer メッセージング / human-in-the-loop)** だけを残し、**公式 Claude Code primitives (`Task` / `TaskCreate` / `ScheduleWakeup` / `Memory`) が既に提供している機能** は全部捨てた。

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
- human-in-the-loop ── worker が `formation ask` で mailbox に問い合わせる。Claude worker は必要なら `/remote-control` (alias `/rc`)、Codex worker は tmux / `formation msg` で返信

これより短い作業は built-in `Task` tool を使え。

## インストール

```bash
# Claude Code 内
/plugin marketplace add github:hrmtz/claude-harness
/plugin install harness-formation@claude-harness

# CLI を PATH に置く
ln -sfn ~/.claude/plugins/harness-formation/bin/formation ~/.local/bin/formation
```

plugin install 後、hook は `hooks/hooks.json` 経由で Claude Code に配線される。CLI は `~/.local/bin/formation` など PATH 上に symlink して使う。ランタイム状態は `~/.formation/` (mailbox と registry、git 管理外) に作られる。既存の legacy runtime dir がある場合は自動検出する。

自動提案 hook はデフォルトで active。高確度の worker 起動意図を検出すると Formation skill のヒントを注入する。注入せずログだけ確認したい場合は `FORMATION_SUGGEST_MODE=shadow` を設定する。

update 後は plugin を更新し、必要なら symlink を張り直す。

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
formation ask "schema migration vs dual-write どっち？"
formation done "PR #42 出した、tests green"
```

### スマホ介入

Claude worker の `[ASK]` を確認して直接返す場合:

```
/remote-control formation-refactor-1
```

worker の session に attach される。そのまま手でタイプして返事すればいい。

Codex worker は `formation msg <worker_id> "..."` または tmux pane へ attach
する。現行 Codex に experimental な `codex remote-control` が存在する場合も、
これは別 app-server daemon の start/stop/pair 用で、Formation が起動済みの TUI
session には attach できない。installed CLI の capability は daemon を起動せず確認できる:

```bash
formation remote-check
```

## 設計不変条件

- **Memory MCP は lead と worker で共有**。worker は自分の entry を `formation/<worker_id>/` namespace 下に書き、親を汚染しないこと
- **CWD 継承**。worker は lead pane と同じ作業ディレクトリで起動する。cross-project spawn は v1 では未対応
- **観測者特権**。`~/.formation/mailbox/log.jsonl` は平文 jsonl。tail すれば全 formation の通信が生で見える。mailbox 自体は暗号化しない ── redaction フィルタ + SOPS 規律が機密を mailbox から遠ざける役割
- **Sanada (真田) / Matsuoka (松岡)** プロトコル (破壊操作前に黙って backup / 撤退禁止) はユーザーの global `~/.claude/CLAUDE.md` (Claude Code) または `~/AGENTS.md` (Codex) に常駐。formation は前提として動く、再掲しない

## クレデンシャル規律 (絶対)

**mailbox / msg / briefing のいずれにも、平文の credential を貼るな。** mailbox は平文 jsonl として永遠に残る。1 度漏れれば、誰が tail しても毎回見える。

- credential は SOPS 暗号化ファイル (`*.enc.yaml` / `*.enc.env`) で管理
- agent は値ではなく「パスと decrypt コマンド」で参照する:
  - ✗ `formation msg worker-1 "key は sk-abc123..."`
  - ✓ `formation msg worker-1 "sops exec-env config/secrets.enc.yaml '<openai を使う cmd>' で参照"`
- 送信時チェック: `formation msg / report / done / ask / spawn` はすべて body を `is_credential_like` に通す。マッチしたら exit 3 で hard-refuse、`~/.formation/mailbox/refuse.log` に試行を記録 (body 自体はログに残さない)
- 検知パターン: `sk-*`, `ghp_*`, `gho_*`, `AKIA*`, `*_API_KEY=...`, PEM private key, 長い JWT など

SOPS 未整備のプロジェクトでは `sops --encrypt` を先に走らせてから依頼せよ。平文フォールバックは無い。

## ステータス

v0.1 ── 動作確認済、実戦 dogfood 未実施。`wake.sh` の ssh fallback と lead 側の inbox 自動 poll は v2 回し。

設計の詳細と「v6 から意図的に落としたもの」は `docs/spec.md` 参照。
