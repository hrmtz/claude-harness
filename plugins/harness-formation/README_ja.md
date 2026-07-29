# harness-formation

[English](README.md) | 日本語

兄弟 tmux pane に常駐させた AI agent worker (claude / codex) を束ねるための最小オーケストレーション層。`formation` CLI と同名の agent skill として出荷する。

## なぜ作ったか

旧 v6 prototype は育ちすぎた。8 体固定編成、YAML タスクキュー、guardian スクリプト群、CLI 別 instructions の多重化、二次 dashboard ── 動いてはいたが、日常タスクには儀式が重すぎた。

切り替えのきっかけは 2026 年 4 月の長時間作業セッション。その場で即興した「共有 mailbox ファイル + bash ヘルパー数本」だけの 3 pane ミニシステムが、数時間タスクを綺麗に片付けた。同じ形の問題に v6 の重装備を持ち出すのは過剰、と気付かされた。

`harness-formation` はその蒸留。**残すべきプロトコル (観測性 / peer メッセージング / human-in-the-loop)** だけを残し、**公式 Claude Code primitives (`Task` / `TaskCreate` / `ScheduleWakeup` / `Memory`) が既に提供している機能** は全部捨てた。

## 中身

- `bin/formation` ── worker lifecycle / mailbox / durable request を束ねる CLI:
  `spawn | msg | status | inbox | reap | report | done | ask | ack | resolve | remote-check`
- `bin/formation-mail-nudge` ── 無視された badge 用の任意 one-shot / watcher。自動起動しない
- `bin/formation-stall-watch` ── mailbox 沈黙と pane 安定性を組み合わせた structural stall observer
- `bin/install-formation-mail-nudge-service` ── 任意 watcher の明示的 systemd user service install / uninstall
- `bin/formation-window-status` ── journal 付き tmux window list の明示的 apply / status / revert
- `lib/mailbox.sh` ── jsonl append-only の pane 間メッセージバス。recipient 毎カーソル、flock で書き込みガード
- `lib/mailbox_delivery.sh` ── `formation msg` / `mailbox-send` 共通の宛先・送信者解決、relay 委譲、exclusive inject policy
- `lib/mailbox_notify.sh` / `lib/mailbox_relay.sh` ── prompt に触れない signal primitive と worker 毎の relay daemon
- `lib/requests.sh` ── transport と分離した durable ASK / ACK / resolve state
- `lib/wake.sh` ── exceptional inject 専用の単一 `tmux_send_submit` primitive
- `lib/redact.sh` ── credential パターン検知と metadata-only refusal audit (送信全パスで hard-refuse)
- `skills/formation/SKILL.md` ── agent skill (claude + codex)。発火条件と実行フロー
- `skills/formation/templates/briefing.md` ── lead と worker の契約書テンプレ

## いつ使うか

worker 起動のコストは「fresh な AI agent プロセス (claude or codex) 1 個 + pane 分割 + 数秒の bootstrap」。これを払う価値があるのは **数分から数時間レンジ** のタスクで、かつ以下のいずれかが欲しい場合:

- 生観測 (pane を tail してリアルタイムで見たい)
- 途中で方針変更 (`formation msg worker-1 "approach B に切り替え"`)
- human-in-the-loop ── worker が `formation ask` で durable な request id を作り、parent が `formation ack` / `formation resolve` で明示的に閉じる

これより短い作業は built-in `Task` tool を使え。

## インストール

```bash
# Claude Code 内
/plugin marketplace add github:hrmtz/claude-harness
/plugin install harness-core@claude-harness
/plugin install harness-formation@claude-harness

# CLI を PATH に置く
ln -sfn ~/.claude/plugins/harness-formation/bin/formation ~/.local/bin/formation
```

`harness-core` は全 Formation worker 起動で使う cross-CLI identity guard を提供する。
両 plugin を install すること。guard が見つからない場合、Formation は fail closed する。

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

### 任意の ignored-badge escalation / window status

mailbox は既定で badge-only、prompt keystroke はゼロ。`formation-mail-nudge`
は `--exclusive-input` で spawn した worker に限る例外で、最新 registry と
live pane の両方の exclusive 宣言、古い badge、一定時間不変の pane snapshot
をすべて要求する。送るのは短い `formation inbox` pull 指示だけで、mailbox
本文ではない。同じ seq の child 注入は一度だけで `receipt unconfirmed`。
成功根拠は badge の clear/advance または canonical worker からの新しい durable
row だけで、pane repaint は成功扱いしない。検証時間後もこの効果がなければ、
spawn 時に記録した parent へ固定 metadata の durable alert を一度 append し、parent prompt には触れず
zero-keystroke signal する。legacy / 不一致 parent route は可視化し、推測しない。
pending seq が child 注入条件を一度も満たさない場合も、
`FORMATION_MAIL_NUDGE_NO_ATTEMPT_ALERT`（既定300秒）の上限で parent へ一度だけ
durable alert を送り、理由を `idle-never-stable` / `nonexclusive` /
`registry-route-invalid` として記録する。この経路は child / parent prompt の
keystroke をゼロに保ち、alert を再送しない。

```bash
formation-mail-nudge --dry-run
formation-mail-nudge                 # one-shot
formation-mail-nudge --watch         # foreground
install-formation-mail-nudge-service --dry-run install
install-formation-mail-nudge-service install
install-formation-mail-nudge-service uninstall
```

plugin install / `formation spawn` は watcher を起動しない。常駐は明示的な
systemd user service 選択で、installer は disposable caller worktree ではなく
canonical checkout を解決する。

`formation-window-status` も自動実行しない。`apply` は server-global format の
正確な preimage を journal し、`--arrange` は別 opt-in。`revert` は同じ tmux
server の journal を復元し、`status` は read-only:

```bash
formation-window-status status
formation-window-status apply --lead "$TMUX_PANE" --task "review"
formation-window-status apply --arrange --dry-run
formation-window-status revert
```

### 3. worker 側 (worker の agent が Bash tool から叩く — claude/codex 共通)

```bash
formation report "phase 1 完了、phase 2 着手"
formation ask "schema migration vs dual-write どっち？"
formation done "PR #42 出した、tests green"
```

`formation ask` は `WAITING_PARENT` を durable に保持する。後続の report
では消えず、parent は `formation ack <request-id> [summary]` または
`formation resolve <request-id> <summary>` を実行する。
spawn は semantic parent identity と parent pane route を分離して worker
へ渡す。`report` / `done` / `ask` と、返送する `ack` / `resolve` はすべて
append を先に確定し、同じ zero-keystroke の relay-or-direct signal policy
を通る。relay が死んでいても badge の direct fallback が働き、本文を
prompt へ注入しない。
parent pane は `TMUX_PANE` を信用せず process ancestry から解決し、wrapper
で root PID chain が切れる場合だけ caller の controlling TTY と
`pane_tty` の一意一致を安全な fallback として使う。stale/inherited な
sibling id と mutable window name は無視し、valid な locked/legacy identity
を持つ実 parent pane も明示 `FORMATION_SELF` も証明できなければ、返信不能
worker を作らないよう spawn を fail-closed にする。
`formation status` は欠落/不正 route を registry を変更せず
`parent=UNROUTABLE` と表示する。意図した parent pane 内の operator は
`formation repair-parent <worker_id>` で一つの曖昧でない legacy row だけを
修復できる。parent は現在 pane と locked identity から導出し、target child
pane が live で worker の locked/legacy identity を保持することも検証する。
child pane の parent option 2個と registry row を registry lock 下で同期し、
変更前に registry/target-row/pane-option の preimage を
`~/sanada_backup_persistent/`（`FORMATION_PARENT_REPAIR_BACKUP_ROOT` で変更可）
へ保存して recovery path を表示する。set-option / registry 失敗時は pane
option を rollback し、closed/recycled child pane と異なる non-null route は
拒否する。pane と row が既に一致する再実行は byte-for-byte no-op になる。
既知 pane の signal に失敗した場合、row/state は durable のまま exit `4`
を返す。この code で `report` / `done` を自動 retry すると重複 row を作る
ため再送しない。pane route が欠落または検証不能なら pull-only の exit `0`
とし、stderr に `signal=unavailable` を出す。

### スマホ介入

Claude worker の `[ASK]` を確認して直接返す場合:

```
/remote-control formation-refactor-1
```

worker の session に attach される。手で補足を返せるが、durable ASK state
自体は parent pane から `formation ack` / `formation resolve` で閉じる。

Codex worker は `formation msg <worker_id> "..."` または tmux pane へ attach
して補足できる。ASK は別途 `formation ack` / `formation resolve` で閉じる。
現行 Codex に experimental な `codex remote-control` が存在する場合も、
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
