# harness-kimi

[Kimi Code CLI](https://moonshotai.github.io/kimi-code/) 向けの claude-harness 移植。

Kimi Code CLI >= 0.28 は **native hook API** を持つ (gh #54)。ペイロードは Claude と同じ snake_case (`tool_input.command` 等) で、`hookSpecificOutput.permissionDecision` / `additionalContext` の JSON contract もそのまま受理される (2026-07-21 実測)。したがって harness-core の hook スクリプトは **無改変で動き**、CLI 差分は `harness-core/hooks/lib.sh` が吸収する (PostToolUse の `.tool_output` 文字列、`wire.jsonl` のパス解決)。

防御は以下の 3 層:

1. **native hooks (preventive)** — `~/.kimi-code/config.toml` の `[[hooks]]` に配線。PreToolUse gate が Bash/Read/Write/Edit の実行前に block する。
2. **AGENTS.md (behavioral rail)** — プロジェクト単位でシステム指示に組み込まれる。
3. **session log scrubber (detective)** — `~/.kimi-code/sessions/*/session_*/agents/main/wire.jsonl` を cron で走査し、既知の credential 値を自動 redact。PostToolUse は observe-only (block 不可) なので、この事後層は残す。

## Install

### 1. native hooks を配線 (主層)

```bash
~/projects/claude-harness/install-kimi-hooks.sh
```

`plugins/cross_cli_hooks.json` の kimi セクション (hook セット) + 各 plugin の `hooks/hooks.json` (event/matcher/timeout の SSOT) から `[[hooks]]` を生成し、`config.toml` 内のマーカーブロックに冪等マージする。既存のユーザ設定には触れない。hook セットを変えたら overlay を編集して再実行。

### 2. AGENTS.md と tmux identity を管理する (wrapper)

`~/.local/bin/kimi` を wrapper に置き換えると、`kimi` 起動時にカレントディレクトリへ
`AGENTS.md` を自動配置する。tmux 内では pane の `@formation_id` を routing identity の
SSOT として扱い、standalone Kimi の表示名を整える。別 chassis の nested child は親 pane
を改名せず、Formation 管理 pane の identity も上書きしない。

```bash
~/projects/claude-harness/plugins/harness-kimi/install-kimi-wrapper.sh
```

その後、shell config に以下を追加して `~/.local/bin` が `~/.kimi-code/bin` より先に来るようにする:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

デフォルトでは `~/projects/` 以下でのみ `AGENTS.md` を自動作成する。任意のディレクトリで作成したい場合は `HARNESS_KIMI_ANYWHERE=1` を環境変数に設定する。

### 2'. 手動で AGENTS.md を置く

```bash
cd /path/to/your/project
~/projects/claude-harness/plugins/harness-kimi/install-kimi-agents.sh
```

### 3. Session scrubber を有効化

```bash
~/projects/claude-harness/plugins/harness-kimi/install-kimi-scrubber.sh
```

1 分ごとに Kimi の `wire.jsonl` をスキャンする cron entry が追加される。

## Verify

```bash
# deny が効くこと (block されて理由が context に返る)
kimi -p "Run exactly this shell command and nothing else: sops -d x.enc.yaml"

# overlay と live 配線の drift check
bash ~/projects/claude-harness/scripts/check_cross_cli_hooks.sh --live
```

## Disable / uninstall

```bash
# native hooks を外す (config.toml のマーカーブロックを除去、backup 付き)
~/projects/claude-harness/uninstall-kimi-hooks.sh

# scrubber 一時停止 / 再開
touch ~/.kimi-code/harness-scrub.disabled
rm ~/.kimi-code/harness-scrub.disabled

# scrubber cron 削除
~/projects/claude-harness/plugins/harness-kimi/uninstall-kimi-scrubber.sh
```

## Limitations

- Kimi の hook は **fail-open** (hook のエラー/timeout は allow)。「rail であって sandbox ではない」——高リスク操作は permission 承認と併用すること。
- PostToolUse / SessionStart / SubagentStop は **observe-only** (block 不可、戻り値は main flow に影響しない)。credential scrub は従来通り detective 層 + cron scrubber の併用。
- Stop/SubagentStop の gate (`sr_depth_gate`, `stall_autocontinue`) は Claude transcript 構造に依存するため **未移植**。
- hook は新規セッション開始 (または `/reload`) でロードされる。

## Legacy: BASH_ENV 傍受レイヤー (#52, deprecated)

Kimi に native hook API が無かった時代の防御層 (`guard-env.sh` / `guard-check.sh` / `guarded-bash.sh` / `install-kimi-bash-guard.sh`) と detective watcher (`kimi_wire_watcher.*` / `install-kimi-watcher.sh`) は **deprecated**。native PreToolUse は同じ gate を tool call 時点で発火させるため、`bash --posix` / `bash -i` / `sh -c` による既知バイパスごと不要になった。設計記録は [docs/kimi_hooks.md](../../docs/kimi_hooks.md) の legacy セクションを参照。

旧レイヤーが live なマシンの移行:

```bash
~/projects/claude-harness/plugins/harness-kimi/uninstall-kimi-watcher.sh   # watcher cron 除去
rm -rf ~/.kimi-code/bin/guarded-bash-dir                                    # BASH_ENV guard 撤去
~/projects/claude-harness/plugins/harness-kimi/install-kimi-wrapper.sh     # wrapper 更新 (BASH_ENV なし)
~/projects/claude-harness/install-kimi-hooks.sh                             # native hooks 配線
```

uninstall 経路を断たないため、deprecated スクリプト自体は repo に残してある。

## Files

- `AGENTS.md.template` — プロジェクトにコピーする behavioral rule テンプレート
- `kimi_session_scrub.py` — wire.jsonl スキャン・redact 本体
- `kimi_session_scrub.sh` — cron から呼ばれる wrapper
- `install-kimi-agents.sh` / `install-kimi-scrubber.sh` / `uninstall-kimi-scrubber.sh`
- `kimi-wrapper.sh` / `install-kimi-wrapper.sh` — `kimi` コマンドをラップし、AGENTS.md
  自動配置と tmux routing/display identity の保護を行う
- **skills port**: `install-kimi-skills.sh` + `skills/magi` / `skills/bug-hunt` — Kimi 側の magi / bug-hunt skill 移植
- deprecated: `guard-env.sh` / `guard-check.sh` / `guarded-bash.sh` / `install-kimi-bash-guard.sh` / `kimi_wire_watcher.*` / `install-kimi-watcher.sh` / `uninstall-kimi-watcher.sh`
