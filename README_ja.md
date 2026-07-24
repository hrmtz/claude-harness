# claude-harness

[Claude Code](https://claude.com/claude-code) の自前運用から蒸留した、operational harness (防御 hook + workflow guardrail + persona-based behavioral rail)。

> **なぜ「ハーネス」？** LLM agent は patterned に失敗する — credential leak / recovery loop / premature script generation。`CLAUDE.md` に書く behavioral rule は何度書いても同じ事故を繰り返す。本 marketplace は **structural fix** (hook / guard / 反射的 context 注入) を ship する。「気をつける」に頼らず、agent の判断を待たずに発火する layer。

## 同梱 plugin

| Plugin | 内容 | trigger |
|---|---|---|
| **harness-core** | defense-in-depth の hook 群 (`hooks/hooks.json` が authoritative)。看板の trio: credential 値 scrub (PostToolUse) + 危険 bash command guard (PreToolUse) + admission keyword reminder (UserPromptSubmit); 加えて branch-policy / rotation-propagation / 真田 auto-backup guard、session-context rail、Stop/SubagentStop の security-review 深度 gate | 全 Bash 呼び出し・全 user prompt・全 session stop |
| **harness-magi** | 多視点 preflight の Claude 側 contract mirror。Claude-native structural runner が ship するまでは fail-closed。MELCHIOR/BALTHASAR/CASPAR fan-out と cross-family review の実動系は `harness-magi-codex` companion を使う | walltime ≥ 2h / ≥ 100M row DML / 非可逆 cutover / 新 layer build / ≥ $10 確実消費 / >1h sleep-loop script |
| **harness-rails** | 長時間 op の operational safety rail: pre-flight algorithm fitness CLI (working set vs RAM ceiling 計算)、in-flight heartbeat + cron watcher (stale + ETA overrun 検知)、Discord 通知 + opt-in gh issue emit。auto-kill / auto-revert は禁止 (人間介入必須)。23h sunk-cost incident で「memory に書いた philosophy が rail 化されてないと fire しない」教訓から構造化 | walltime > 1h の長時間 op、watcher は cron `*/1 * * * *` |
| **harness-formation** | tmux pane で長時間 peer AI agent (claude or codex) を spawn + coordinate。append-only jsonl mailbox にセッション固有 ID を付与して同一コードネーム別セッションの identity drift を防止。auto-relay daemon + credential-safe body guard 内蔵 | 数時間単位の walltime が必要で live observability や mid-flight redirection が要るタスク |
| **harness-craft** | [obra/superpowers](https://github.com/obra/superpowers) (MIT) から蒸留した behavioral craft skill 集: `skill-tdd` (pressure test による RED-GREEN-REFACTOR skill 作成、structural-first 入口ゲート付き)、`atomized-briefing` (formation worker / subagent dispatch 向け 2-5 分粒度 context-free plan)、`root-cause-debugging` (Iron Law + 3 連続 fix 失敗で dual-magi-review へ escalation)。superpowers は教え、harness は強制する — 本 plugin は明示的 behavioral 別館で、強制側に配線済み | SKILL.md 作成時、formation worker / subagent plan の briefing 時、デバッグ全般 |
| **harness-kimi** | Kimi Code CLI 向けの harness-core 移植。Kimi >= 0.28 の **native hook API** に配線 (`install-kimi-hooks.sh` が `~/.kimi-code/config.toml` の `[[hooks]]` に `cross_cli_hooks.json` のセットを書き込む)。ペイロードは Claude 形状の snake_case で hookSpecificOutput JSON contract も受理されるため guard スクリプトは無改変で動き、差分は `lib.sh` が吸収 ([#54](https://github.com/hrmtz/claude-harness/issues/54), [docs/kimi_hooks.md](./docs/kimi_hooks.md))。+ プロジェクト単位の `AGENTS.md` (behavioral) + 定期 session log scrubber | Kimi セッション全般 (native PreToolUse gate) + 1 分毎の cron scrubber |
| **harness-grok** | Grok CLI の **native hook API** への harness-core/rails 移植。`install-grok-hooks.sh` が共有 overlay から `~/.grok/hooks/harness.json` を生成、`lib.sh` が Grok の camelCase payload (`toolInput`/`toolName`/`sessionId`) と `{"decision":"deny"}` 出力形式を吸収して同じ guard を発火させる ([docs/grok_hooks.md](./docs/grok_hooks.md))。Grok payload が guard を silently pass する fail-open を塞ぐ | Grok セッション全般; `bash install-grok-hooks.sh` + 二重発火回避に `[compat.claude] hooks = false` |

将来予定: CLAUDE.md persona template、repo-init skeleton。状況は [GitHub issues](https://github.com/hrmtz/claude-harness/issues) 参照。

## Install

```bash
# Claude Code 内
/plugin marketplace add github:hrmtz/claude-harness
/plugin install harness-core@claude-harness
```

install 後は `${CLAUDE_PLUGIN_ROOT}/hooks/hooks.json` で hook が auto-wire される。`~/.claude/settings.json` の手動編集不要。

Codex は `.agents/plugins/` のnative repository marketplaceを使う。install・
trust・旧global hook移行・update・enable/disable・uninstallは
[`docs/codex_plugins.md`](./docs/codex_plugins.md) を参照。

> ⚠️ 既に `~/.claude/settings.json` に同名 hook を手動配線している場合、plugin install 前に削除すること (二重発火回避)。

### 動作確認

```bash
# credential scrub の回帰確認: synthetic fixture を使う
python3 plugins/harness-core/tests/test_value_scrub_jwt.py

# bash guard の発火確認: 禁止 pattern を試す
sops -d secrets.enc.yaml
# 期待動作: PreToolUse で block + 安全な代替案表示
```

## plugin 経由しない手動 install

直接 file copy で導入したい場合:

```bash
git clone https://github.com/hrmtz/claude-harness
cp -r claude-harness/plugins/harness-core/hooks/* ~/.claude/hooks/
# その後 ~/.claude/settings.json に追記 — 詳細は plugins/harness-core/README.md
```

## 設計思想

本 marketplace は **harness 全体の半分**。残り半分は philosophy + memory 構造 + persona stack の解説 doc:

- **`docs/CLAUDE_HARNESS_DISTILLED.md`** — 設計根拠 (3-tier memory 構造、真田/松岡/仗助 persona stack、SOPS 2-command 原則、credential leak 8 incidents → structural fix への昇格 timeline)

install 前に上記 doc を読むことを強く推奨。**なぜ** これらの hook が必要なのかを理解せずに導入すると、自分の workflow に合わない部分を持て余す。

## Status

- ✅ `harness-core` — production で運用中
- ⚠️ `harness-magi` — Claude contract mirror。Claude-native structural runner が ship するまでは fail-closed。実動 protocol は `harness-magi-codex` を使う
- ✅ `harness-rails` — 165M-row HNSW build で実 production 検証済 ([docs/INCIDENT_23H_HNSW.md](./docs/INCIDENT_23H_HNSW.md) 参照)
- ✅ `harness-formation` — `formation` skill + CLI。claude + codex worker 対応、session-scoped mailbox identity
- ✅ `harness-kimi` — Kimi Code CLI >= 0.28 native hook 移植 (BASH_ENV 層は deprecated); 詳細は [plugins/harness-kimi/README.md](./plugins/harness-kimi/README.md)
- ✅ `harness-grok` — Grok CLI native-hook 移植; Phase 1 Grok Verifier sign-off 済 2026-07-03 ([docs/grok_hooks.md](./docs/grok_hooks.md) Verification record)
- ✅ `harness-craft` — obra/superpowers v6.x から蒸留した behavioral skill 3 本 ([#90](https://github.com/hrmtz/claude-harness/issues/90))
- ⏳ `harness-claude-md-template` — paste 可能な CLAUDE.md skeleton

## 既知 issue

- [#1](https://github.com/hrmtz/claude-harness/issues/1) — false positive: `-m` / `--body` / `--title` 引数に禁止 pattern の literal mention があると guard が trip した (commit `fdd01f6` で fix 済)

## License

MIT — `LICENSE` 参照。

## English README

See [README.md](./README.md) for the English version.
