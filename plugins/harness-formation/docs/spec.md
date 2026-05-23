# njslyr7 — 蒸留版スペック

> Status: v0.1 shipped (`bin/formation` + `skills/formation/`). 独立リポに昇格済み。
> 起源: 2026-04-13 PRS-LLM-dev で即興した mini agent system が機能した経験を v6.x 鍛造方針に逆輸入した。`multi-agent-njslyr` (v6) の ceremony を落とし、公式 Claude Code primitives に乗る最小レイヤだけを残した。

## 設計原則

公式 Claude Code (Task ツール / ScheduleWakeup / TaskCreate / Memory) に **乗っかる前提**。njslyr v7 は「公式ツールで足りない部分の最小レイヤ」だけを提供する。

### keep (v6 から継承)
- `docs/philosophy.md` の 5 原則 (Autonomous Formation / Parallelization / Research First / Continuous Learning / Triangulation)
- `docs/protocols/cross_machine.md` の SSH/ntfy fallback パターン (cross-machine 通信時のみ)

### drop (v6 から削除)
- 8 体ヤクザ編成 (Smith/Tajiba/Yamahiro/Kusuba/Yakuza1-6/Soukaiya 等の固定編成)
- ヤクザペルソナ・忍殺スラング強制
- YAML タスクスキーマ (`queue/tasks/{agent_id}_{task_id}.yaml`) — 公式 TaskCreate に委譲
- guardian script 群 (tortoise/crane) — 公式 ScheduleWakeup に委譲
- darkninja → smith → yakuza の 3-4 階層命名 — 任意の N 階層を runtime 決定
- dashboard.md 二次データ
- generated/codex-* 等の per-CLI instructions 多重化

### add (今日の蒸留から)
- mailbox 抽象 (`MAILBOX_HOME` env + jsonl append-only)
- role tag (read-only / destructive / verifier) で permission gating
- 観測者特権 (default 平文ログ、user が tail で peek 可能)
- 常駐プロトコル (Sanada / Matsuoka) を CLAUDE.md 数十行で表明

---

## アーキテクチャ

```
┌─────────────────────────────────────────────────┐
│ Claude Code (公式)                              │
│  ├─ Task ツール (subagent_type で specialization)│
│  ├─ TaskCreate / TaskUpdate (タスク管理)         │
│  ├─ ScheduleWakeup (自律ループ)                  │
│  ├─ Memory (永続学習)                           │
│  └─ tmux send-keys (pane wake)                  │
├─────────────────────────────────────────────────┤
│ njslyr v7 蒸留レイヤ (~150-300 行)              │
│  ├─ mailbox.sh (send / inbox / mark_read)       │
│  ├─ wake.sh (tmux + ssh fallback)               │
│  ├─ role.yaml (per-pane permissions)            │
│  └─ CLAUDE.md (Sanada / Matsuoka / lead 規則)    │
└─────────────────────────────────────────────────┘
```

### コアコンポーネント

#### 1. `lib/mailbox.sh` (~50 行想定)

```bash
# Usage:
#   PRS_MSG_SELF=pane8 mailbox send pane9 "message body"
#   PRS_MSG_SELF=pane8 mailbox inbox
#   PRS_MSG_SELF=pane8 mailbox mark_read

MAILBOX_HOME="${MAILBOX_HOME:-$HOME/.njslyr/mailbox}"
```

- jsonl append-only (`$MAILBOX_HOME/log.jsonl`)
- per-recipient cursor (`$MAILBOX_HOME/cursor/<recipient>.txt`)
- send 時に `wake` も呼ぶ (tmux send-keys → 失敗時 ssh fallback)
- 暗号化オプション: `[CONFIDENTIAL]` プレフィックス時のみ recipient 公開鍵で encrypt (オプション、デフォルト平文)

#### 2. `lib/wake.sh` (~30 行)

```bash
# Usage:
#   wake pane9              # local tmux
#   wake host:pane9         # ssh + remote tmux
```

- `tmux send-keys -t <session>:<pane>` → exit code で失敗検知
- 失敗時 ssh 経由で remote tmux にリトライ
- ntfy fallback は cross_machine.md の既存パターン継承

#### 3. `config/role.yaml` (~10 行 per agent)

```yaml
agents:
  pane8:
    role: lead
    permissions: [read, destructive]
    wake_path: tmux:dev:pane8
    mailbox_id: pane8
  pane9:
    role: ingest_verifier
    permissions: [read, destructive]
    wake_path: tmux:dev:pane9
  pane10:
    role: read_only_test
    permissions: [read]
    wake_path: tmux:dev:pane10
```

- `permissions: [read]` のみの role は destructive API/コマンド禁止 (CLAUDE.md で表明)
- N 階層自由 (今日は 3 体だったが、複雑タスクなら 5-7 体構成も可)

#### 4. `CLAUDE.md` (~50 行)

```markdown
# Operating Protocols

## Sanada (事前バックアップ)
destructive 操作前に必ず Sanada backup を取る。
- chichibu (WSL2): /home/<user>/sanada_backup_persistent/
- それ以外: /tmp/sanada_backup/
- 24h 経過で自動削除

## Matsuoka (撤退禁止)
- 「やめましょう」「手動でお願いします」禁止
- 失敗 → 別アプローチ提示で前進
- ABORT 提案禁止 (ユーザー明示指示時のみ撤退可)

## Lead 判断
- destructive 操作は lead pane の明示 ack 後
- read-only pane は事実観測専任
- 異論あれば即 msg、最終判断は lead

## 観測者特権
- mailbox は default 平文
- user は `~/.njslyr/mailbox/log.jsonl` を tail で全 pane peek 可
- creds は `[CONFIDENTIAL]` プレフィックスで明示
```

---

## 想定 LoC

| Component | v6.1 (推定) | v7 (目標) |
|---|---|---|
| core scripts | 数千行 | ~200 行 |
| instructions | 数十ファイル | 1-2 ファイル |
| YAML schema | 多重 | なし |
| guardian script | 複数 | 0 (公式 ScheduleWakeup) |
| Total | ~5000+ | ~500 |

---

## 移行戦略

1. v7 を `branches/v7-distilled/` で独立鍛造、v6.1 は legacy 維持
2. PRS-LLM-dev で `~/.local/bin/prs_msg.sh` を置換実装に差し替え (互換 API)
3. content-forge で v7 の cross-project mailbox 試験
4. cross-machine (Kyoto ↔ NeoSaitama) は v6 cross_machine.md 流用、後で v7 統合

---

## オープン課題

- TODO.md の `PRS_MSG_HOME 抽象化 + cross-project agent 連携の鍵管理` を v7 設計に取り込む
- 暗号化のデフォルト方針 (常に平文 / オプション暗号 / 重要メッセージのみ暗号)
- v6 user (もしいれば) の移行パス
