---
name: harness-grok-port
version: 0.1.0
description: |
  claude-harness を Grok CLI 向けに移植する実装仕様。
  Implementer（Claude/Codex）が lib.sh 正規化・hook インストーラ・cross_cli overlay を実装し、
  Verifier（Grok）が E2E チェックリストで受け入れ判定する。
  Trigger: harness-grok 移植、Grok hook 対応、install-grok-hooks、cross_cli grok セクション。
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
  - Grep
  - Glob
---

# harness-grok — 移植仕様（Implementer 用）

> **このファイルを Claude/Codex に読ませて実装させる。** 完了後は Grok が §7 のチェックリストで検証する。

## 0. 役割分担

| 役割 | 担当 CLI | 責務 |
|------|----------|------|
| **Implementer** | Claude Code または Codex | 本 SKILL の §3–§6 を実装。PR/commit まで |
| **Verifier** | Grok | §7 チェックリストを実行。失敗項目を issue 化 |
| **Out of scope（Phase 2）** | 別タスク | `formation spawn --cli grok`、red-team Workflow の Skill 化 |

Implementer は **Grok セッションでの E2E は不要**。payload シミュレーション + 既存 pytest は Implementer 側で回す。Grok 実機検証は Verifier の仕事。

---

## 1. 背景と問題

Grok はデフォルトで `~/.claude/settings.json` の hook を読み込む（`[compat.claude] hooks = true`）。現状 29 本ロードされているが、**実効性に致命的な穴**がある。

### 1.1 確認済みの不具合（2026-07-03 再現）

```bash
# Claude 形式 → deny（正常）
echo '{"tool_input":{"command":"sops -d secrets.enc.yaml"}}' \
  | bash plugins/harness-core/hooks/bash_command_guard.sh

# Grok 形式 → 空出力・スルー（= 危険コマンドが通る）
echo '{"toolInput":{"command":"sops -d secrets.enc.yaml"}}' \
  | bash plugins/harness-core/hooks/bash_command_guard.sh
```

| ギャップ | harness 期待 | Grok 実際 |
|----------|--------------|-----------|
| ツール入力 | `tool_input` (snake) | `toolInput` (camel) |
| deny 出力 | `hookSpecificOutput.permissionDecision` | `{"decision":"deny","reason":"..."}` |
| セッション JSONL | `transcript_path` | なし。`GROK_SESSION_ID` + `GROK_WORKSPACE_ROOT` は hook env で注入 |
| ツール名 | `Bash`, `Read`, `Write` | `run_terminal_command`, `read_file`, `search_replace`（matcher エイリアスは Grok 側で解決済み） |

**最悪ケース**: hook は走るが silently pass → fail-open と組み合わさり無防備。

### 1.2 参照実装（既存パターン）

| CLI | ドキュメント | インストーラ | overlay |
|-----|-------------|-------------|---------|
| Codex | `docs/codex_hooks.md` | `install-codex-hooks.sh` | `cross_cli_hooks.json` → `codex` |
| Kimi | `docs/kimi_hooks.md` | `install-kimi-bash-guard.sh` 等 | `cross_cli_hooks.json` → `kimi` |
| **Grok** | `docs/grok_hooks.md`（新規） | `install-grok-hooks.sh`（新規） | `cross_cli_hooks.json` → `grok`（新規） |

Grok は **ネイティブ hook API あり** → Kimi の BASH_ENV ハックは不要。Codex 移植に近い。

---

## 2. 設計原則

1. **SSOT は変えない**: event/matcher/timeout は各 plugin の `hooks/hooks.json` のまま。`cross_cli_hooks.json` は「どの hook を Grok が使うか」だけ選ぶ（gh #55 パターン）。
2. **lib.sh で正規化**: 各 hook スクリプトを個別に直すのではなく、共有ヘルパーで Claude/Codex/Grok の差を吸収。
3. **二重発火禁止**: `install-grok-hooks.sh` は `~/.grok/hooks/harness.json` に書く。`[compat.claude] hooks` を Grok 利用時はオフにする手順を README に明記（または settings.json から harness hook 行を削除する migration note）。
4. **fail-closed on deny**: Grok はクラッシュ = fail-open。deny は必ず `emit_deny` が **両形式** を stdout に出す。
5. **段階的スコープ**: Phase 1 = Bash 系 gate + scrub + UserPromptSubmit。Read/Write guard は Phase 1.5（Grok ツールスキーマ調査後）。

---

## 3. 実装タスク（DAG）

依存関係順。並列可のものは `(parallel)` 表記。

```
PR-1  lib.sh 正規化ヘルパー
  ↓
PR-2  cross_cli_hooks.json grok セクション + check_cross_cli_hooks.sh 拡張
  ↓
PR-3  install-grok-hooks.sh + docs/grok_hooks.md
  ↓
PR-4  (parallel) 既存 hook の lib ヘルパー呼び出しへの置換（Bash 系優先）
  ↓
PR-5  credential scrub の Grok jsonl パス + テスト
  ↓
PR-6  README_ja.md / README.md に harness-grok 行追加
```

### PR-1: `lib.sh` 正規化（最重要）

**ファイル**: `plugins/harness-core/hooks/lib.sh`

追加する関数（名前は提案。実装時に既存スタイルに合わせてよい）:

```bash
# HOOK_INPUT または stdin から JSON を取得（既存パターン踏襲）

parse_tool_command() {
  # .tool_input.command // .toolInput.command
}

parse_tool_file_path() {
  # Read: .tool_input.file_path // .toolInput.path // .toolInput.file_path
  # Write/Edit: 同上
}

parse_tool_content() {
  # Write: .tool_input.content // .toolInput.content // .toolInput.new_string
  # Edit/search_replace: .tool_input.new_string // .toolInput.new_string
}

emit_deny() {
  # $1=reason text
  # stdout に BOTH:
  #   {"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":$msg}}
  #   {"decision":"deny","reason":$msg}
  # Claude/Codex は前者、Grok は後者（または両方）を解釈
}

emit_allow() {
  # Grok 用: {"decision":"allow"} を出す（Claude は空 exit 0 でよい場合は併記 optional）
}
```

`active_jsonl()` 拡張:

```bash
active_jsonl() {
  # 1. HOOK_INPUT の .transcript_path（Claude/Codex）
  # 2. HOOK_INPUT の .transcriptPath（将来互換）
  # 3. Grok: GROK_SESSION_ID + GROK_WORKSPACE_ROOT から組み立て
  #    候補: $HOME/.grok/sessions/<url-encoded-workspace>/<sessionId>/chat_history.jsonl
  #    workspace の encode: Python urllib.parse.quote(workspace, safe='') 等で検証すること
  # 4. fallback: ~/.claude/projects/*/*.jsonl（既存）
}
```

`recent_assistant_turns()` は Grok の `chat_history.jsonl` 形式に合わせて jq セレクタを追加:

```bash
# Grok chat_history: {"type":"assistant",...} — 既存 .type == "assistant" がそのまま効くか実ファイルで確認
```

**テスト**: `plugins/harness-core/tests/` に `test_lib_grok_compat.sh` または pytest を追加。上記 1.1 の2パターンが deny になること。

### PR-2: `cross_cli_hooks.json` + drift check

**ファイル**: `plugins/cross_cli_hooks.json`

`grok` セクションを追加。初期セットは **codex セクションと同じ**（Bash/PostToolUse/UserPromptSubmit のみ）:

```json
"grok": {
  "note": "Native Grok hooks via ~/.grok/hooks/harness.json. Bash-shaped PreToolUse/PostToolUse/UserPromptSubmit only; Read/Write matchers deferred to Phase 1.5.",
  "hooks": [
    "harness-core/hooks/sanada_autobackup.sh",
    "harness-core/hooks/bash_command_guard.sh",
    "harness-core/hooks/branch_policy_guard.sh",
    "harness-core/hooks/pg_rotation_propagation_guard.sh",
    "harness-rails/hooks/pipeline_preflight_gate.sh",
    "harness-rails/hooks/phase_review_gate.sh",
    "harness-core/hooks/long_task_advisor.sh",
    "harness-core/hooks/credential_value_scrub.sh",
    "harness-core/hooks/admission_reminder.sh"
  ],
  "external": []
}
```

**ファイル**: `scripts/check_cross_cli_hooks.sh`

- `jq` の ALL_HOOKS 抽出に `.grok.hooks[]` を追加
- `--live` 時: `~/.grok/hooks/harness.json` の command 一覧と overlay を diff

### PR-3: `install-grok-hooks.sh` + `docs/grok_hooks.md`

**新規**: `install-grok-hooks.sh`（`install-codex-hooks.sh` をテンプレに）

- 出力先: `~/.grok/hooks/harness.json`（グローバル、常に trusted）
- JSON 形式は Grok user guide `10-hooks.md` に準拠:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "bash /abs/path/to/hook.sh", "timeout": 5 }
        ]
      }
    ]
  }
}
```

- hook パスは **repo の絶対パス**（Codex と同様。content 変更は re-install 不要）
- event/matcher/timeout は owning plugin の `hooks.json` から lookup（Codex installer の Python ジェネレータを流用・共通化してよい）
- インストール後の手順:
  1. `grok /hooks` でロード確認
  2. **`~/.grok/config.toml` に `[compat.claude] hooks = false` を推奨**（二重発火回避）
  3. または `~/.claude/settings.json` から harness hook 行を削除する migration 節を doc に書く

**新規**: `docs/grok_hooks.md` — `codex_hooks.md` と同構造（背景・payload・lib.sh・setup・troubleshooting）

### PR-4: hook スクリプトの lib ヘルパー置換

**優先（Bash / deny 系）** — `parse_tool_command` + `emit_deny`:

- `bash_command_guard.sh`
- `branch_policy_guard.sh`
- `pg_rotation_propagation_guard.sh`
- `pipeline_preflight_gate.sh`（deny 出力がある場合）
- `phase_review_gate.sh`

**例（bash_command_guard.sh）**:

```bash
# 変更前
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

# 変更後
source lib.sh
HOOK_INPUT=$(cat); export HOOK_INPUT
CMD=$(parse_tool_command)

# deny ブロック
emit_deny "$MSG"   # hookSpecificOutput + decision の両方
```

**PostToolUse / 注入系**（動作確認のみ、変更少なめ）:

- `admission_reminder.sh` — 既に `parse_prompt` 経由で動く
- `credential_value_scrub.sh` — `parse_tool_command` + `active_jsonl` 依存
- `long_task_advisor.sh` — `parse_tool_command` 置換

**Phase 1.5（別 PR 可）**: Read/Write 系

- `credential_file_read_guard.sh`
- `check_zsh_reserved_vars.sh`
- `check_early_check_timer.sh`
- `ssh_fanout_canonical_check.sh`

Grok の `read_file` / `search_replace` の `toolInput` フィールド名を **実 hook payload で1回ログ取得**してから実装すること（skill に値をハードコードしない）。

### PR-5: credential scrub Grok jsonl

**ファイル**: `credential_value_scrub.sh`, `credential_scrub.sh`, `credential_scrub.py`

- `active_jsonl()` が Grok パスを返すこと
- redact 後の jsonl が `chat_history.jsonl` 形式を壊さないこと
- 既存 pytest (`test_value_scrub_*.py`) に Grok 形式 payload の fixture を追加

### PR-6: README 更新

`README.md` / `README_ja.md` の plugin 表に `harness-grok` 行を追加。Status: ✅ after Verifier sign-off.

---

## 4. 共通化の提案（任意だが推奨）

`install-codex-hooks.sh` と `install-grok-hooks.sh` の Python ジェネレータが重複する。時間があれば:

```
scripts/generate_hook_overlay.py <vendor>   # codex|grok
```

に抽出。必須ではない。

---

## 5. 受け入れ基準（Implementer 自己チェック）

Implementer は PR 作成前に最低限これを pass:

```bash
# overlay 整合
bash scripts/check_cross_cli_hooks.sh

# Grok 形式 deny（lib 導入後）
echo '{"toolInput":{"command":"sops -d secrets.enc.yaml"}}' \
  | bash plugins/harness-core/hooks/bash_command_guard.sh \
  | jq -e '.decision == "deny" or .hookSpecificOutput.permissionDecision == "deny"'

# 既存テスト退行なし
cd plugins/harness-core && python -m pytest tests/ -q --tb=no 2>/dev/null | tail -5
```

---

## 6. Verifier（Grok）向けチェックリスト

> Implementer が PR を出したら、**Grok セッション**で以下を実行。結果を PR コメントまたは `docs/grok_hooks.md` の Verification record に追記。

### 6.1 インストール

```bash
cd ~/projects/claude-harness
bash install-grok-hooks.sh

# 二重発火回避（いずれか）
# A) ~/.grok/config.toml
#    [compat.claude]
#    hooks = false
# B) ~/.claude/settings.json から harness hook ブロックをコメントアウト

grok /hooks    # harness.json が Plugin/Global に表示されること
```

### 6.2 PreToolUse block（実セッション）

Grok セッション内で以下を**実際に**試す（permission_mode が always-approve でも hook deny が勝つこと）:

| # | コマンド | 期待 |
|---|----------|------|
| 1 | `sops -d secrets.enc.yaml` | deny + 代替案テキスト |
| 2 | `git push origin main`（branch_policy 発火条件を満たす branch で） | deny または pass（branch 次第） |
| 3 | `echo hello` | 静かに pass（hook 出力なし） |

scrollback に hook annotation が出ること（Grok UI 既定）。

### 6.3 UserPromptSubmit 注入

プロンプトに `credential leak` を含めて submit → 次ターン context に admission reminder が載ること。

### 6.4 PostToolUse scrub

```bash
# fake key を stdout に出す bash を Grok に実行させる
echo 'sk-ant-api03-FAKE_KEY_FOR_TEST_xxxxxxxxxxxxxxxxxxxx'
```

期待: warning + `chat_history.jsonl` 内の該当文字列が redact される（`active_jsonl` が Grok パスを解決できていること）。

確認:

```bash
SID="$GROK_SESSION_ID"   # または grok /session-info から取得
WS="$HOME/.grok/sessions" # encoded path を explore
grep -c 'sk-ant-api03-FAKE' .../chat_history.jsonl   # → 0
```

### 6.5 drift check

```bash
bash scripts/check_cross_cli_hooks.sh --live
# → grok harness.json と overlay 一致
```

### 6.6 二重発火

`[compat.claude] hooks = true` のまま install した場合、同一 Bash で guard が **2回**走っていないか scrollback で確認。走っていたら doc の migration 手順を踏む。

### 6.7 記録テンプレ

```markdown
## Verification record (Grok vX.Y.Z, harness-grok PR #N, YYYY-MM-DD)

- [ ] install-grok-hooks.sh
- [ ] /hooks に harness.json 表示
- [ ] sops -d → deny
- [ ] benign echo → pass
- [ ] admission keyword → inject
- [ ] fake sk-ant → scrubbed in chat_history.jsonl
- [ ] check_cross_cli_hooks.sh --live
- [ ] 二重発火なし

Notes: ...
```

---

## 7. Phase 2（本 SKILL のスコープ外）

別 issue / 別 SKILL で追う:

| 項目 | 概要 |
|------|------|
| `formation spawn --cli grok` | `plugins/harness-formation/bin/formation` に grok 起動分岐 |
| Read/Write guard Phase 1.5 | Grok `toolInput` スキーマ確定後 |
| SessionStart 系 | `ghost_inject`, `temporal_anchor` の Grok 互換 |
| red-team Workflow | `harness_red_team.js` → Grok SKILL（7× spawn_subagent + codex headless） |
| `.grok-plugin/plugin.json` | marketplace 配布 |

---

## 8. 参照ファイル一覧

| パス | 用途 |
|------|------|
| `docs/codex_hooks.md` | Codex 移植の完成形 |
| `docs/kimi_hooks.md` | hook なし CLI 向け代替（Grok には不要） |
| `plugins/cross_cli_hooks.json` | overlay SSOT |
| `install-codex-hooks.sh` | インストーラ雛形 |
| `plugins/harness-core/hooks/lib.sh` | 正規化の主戦場 |
| `plugins/harness-kimi/guard-check.sh` | PreToolUse JSON 組み立ての参考 |
| `~/.grok/docs/user-guide/10-hooks.md` | Grok hook 公式仕様 |
| `docs/HOOK_OUTPUT_DESIGN.md` | deny メッセージの polarity ルール |

---

## 9. Implementer への一言

- **最小 diff で Phase 1 を閉じる**。Read/Write・formation・Workflow Skill は伸ばさない。
- deny 理由の文言は `HOOK_OUTPUT_DESIGN.md` 準拠（retreat-counter、blocked 禁止）。
- `transcript_path` の Grok 組み立ては **実際の `~/.grok/sessions/` を ls して encode 規則を確認**してから書く。推測でパスを決めない。