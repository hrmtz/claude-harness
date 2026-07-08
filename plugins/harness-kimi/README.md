# harness-kimi

[Kimi Code CLI](https://moonshotai.github.io/kimi-code/) 向けの claude-harness 移植。

Kimi には Claude/Codex のような `PreToolUse`/`PostToolUse` hook がないため、本 plugin は以下の 2 層で防御する：

1. **AGENTS.md による behavioral rail** — プロジェクト単位でシステム指示に組み込まれる。
2. **定期的な session log scrubber** — `~/.kimi-code/sessions/*/session_*/agents/main/wire.jsonl` をスキャンし、既知の credential 値を自動 redact。

## Install

### 1. AGENTS.md を自動で置く（wrapper）

`~/.local/bin/kimi` を wrapper に置き換えると、`kimi` 起動時にカレントディレクトリ（および親）に `AGENTS.md` が無ければ自動コピーする。

```bash
~/projects/claude-harness/plugins/harness-kimi/install-kimi-wrapper.sh
```

その後、shell config に以下を追加して `~/.local/bin` が `~/.kimi-code/bin` より先に来るようにする：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

デフォルトでは `~/projects/` 以下でのみ `AGENTS.md` を自動作成する。任意のディレクトリで作成したい場合は `HARNESS_KIMI_ANYWHERE=1` を環境変数に設定する。

### 1'. 手動で AGENTS.md を置く

```bash
cd /path/to/your/project
~/projects/claude-harness/plugins/harness-kimi/install-kimi-agents.sh
```

### 2. Session scrubber を有効化

```bash
~/projects/claude-harness/plugins/harness-kimi/install-kimi-scrubber.sh
```

1 分ごとに Kimi の `wire.jsonl` をスキャンする cron entry が追加される。

## Disable / uninstall

```bash
# 一時停止
touch ~/.kimi-code/harness-scrub.disabled

# 再開
rm ~/.kimi-code/harness-scrub.disabled

# cron entry 削除
~/projects/claude-harness/plugins/harness-kimi/uninstall-kimi-scrubber.sh
```

## Limitations

- Kimi に native hook API は無いが、`BASH_ENV` + `$BASH_EXECUTION_STRING` の
  interception で **Bash の実行前 preventive block を実現している** (opt-in:
  `install-kimi-bash-guard.sh` + `HARNESS_KIMI_BASH_GUARD=1`)。絶対パスの
  `/bin/bash -c` も捕捉する。仕組みの詳細 → [docs/kimi_hooks.md](../../docs/kimi_hooks.md)。
  AGENTS.md (behavioral) はその上位の遵守 rail。
- 実行前 block の対象は **Bash 経由に限定**。Read/Edit/Write など非 Bash の操作は
  Kimi 側で interception できない (native hook API 不在)。
- Scrubber は事後処理 (detective)。`kimi_wire_watcher` が毎分 cron で wire.jsonl を
  再走査する 2nd wall だが、漏洩検知後は必ず該当 credential を rotate すること。

## Files

- `AGENTS.md.template` — プロジェクトにコピーする behavioral rule テンプレート
- `kimi_session_scrub.py` — wire.jsonl スキャン・redact 本体
- `kimi_session_scrub.sh` — cron から呼ばれる wrapper
- `install-kimi-agents.sh` / `install-kimi-scrubber.sh` / `uninstall-kimi-scrubber.sh`
- `kimi-wrapper.sh` / `install-kimi-wrapper.sh` — `kimi` コマンドをラップして AGENTS.md 自動配置
- **BASH_ENV preventive guard**: `guard-env.sh` (BASH_ENV entrypoint) / `guard-check.sh`
  (判定) / `guarded-bash.sh` / `install-kimi-bash-guard.sh` — Bash 実行前 block (1st wall)
- **detective 2nd wall**: `kimi_wire_watcher.py` / `kimi_wire_watcher.sh` /
  `install-kimi-watcher.sh` / `uninstall-kimi-watcher.sh` — 毎分 cron で wire.jsonl を
  再走査し bash_command_guard を後追い適用、gap を discord-bot に alert
- **skills port**: `install-kimi-skills.sh` + `skills/magi` / `skills/bug-hunt` — Kimi 側の
  magi / bug-hunt skill 移植
