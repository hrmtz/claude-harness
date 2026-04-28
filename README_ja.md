# claude-harness

[Claude Code](https://claude.com/claude-code) を半年運用した結果蒸留した、operational harness (防御 hook + workflow guardrail + persona-based behavioral rail)。

> **なぜ「ハーネス」？** LLM agent は patterned に失敗する — credential leak / recovery loop / premature script generation。`CLAUDE.md` に書く behavioral rule は何度書いても同じ事故を繰り返す。本 marketplace は **structural fix** (hook / guard / 反射的 context 注入) を ship する。「気をつける」に頼らず、agent の判断を待たずに発火する layer。

## 同梱 plugin

| Plugin | 内容 | trigger |
|---|---|---|
| **harness-core** | 3 hook: credential 値 scrub (PostToolUse) + 危険 bash command guard (PreToolUse) + admission keyword reminder (UserPromptSubmit) | 全 Bash 呼び出し + 全 user prompt |

将来予定: formation skill (tmux pane の長時間 worker) / CLAUDE.md persona template。`docs/ROADMAP.md` (TBD) 参照。

## Install

```bash
# Claude Code 内
/plugin marketplace add github:hrmtz/claude-harness
/plugin install harness-core@claude-harness
```

install 後は `${CLAUDE_PLUGIN_ROOT}/hooks/hooks.json` で hook が auto-wire される。`~/.claude/settings.json` の手動編集不要。

> ⚠️ 既に `~/.claude/settings.json` に同名 hook を手動配線している場合、plugin install 前に削除すること (二重発火回避)。

### 動作確認

```bash
# credential scrub の発火確認: fake key を bash 出力に流す
echo 'sk-ant-api03-FAKE_KEY_FOR_TEST_xxxxxxxxxxxxxxxxxxxx'
# 期待動作: hook が pattern 検出、active session jsonl を sanitize、warning を emit

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

- ✅ `harness-core` (本 commit) — production で運用中
- ⏳ `harness-formation` — `njslyr7` (tmux pane peer-worker daemon) の public 化待ち
- ⏳ `harness-claude-md-template` — paste 可能な CLAUDE.md skeleton

## 既知 issue

- [#1](https://github.com/hrmtz/claude-harness/issues/1) — false positive: `-m` / `--body` / `--title` 引数に禁止 pattern の literal mention があると guard が trip した (commit `fdd01f6` で fix 済)

## License

MIT — `LICENSE` 参照。

## English README

See [README.md](./README.md) for the English version.
