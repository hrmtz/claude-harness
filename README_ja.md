# claude-harness

[Claude Code](https://claude.com/claude-code) を半年運用した結果蒸留した、operational harness (防御 hook + workflow guardrail + persona-based behavioral rail)。

> **なぜ「ハーネス」？** LLM agent は patterned に失敗する — credential leak / recovery loop / premature script generation。`CLAUDE.md` に書く behavioral rule は何度書いても同じ事故を繰り返す。本 marketplace は **structural fix** (hook / guard / 反射的 context 注入) を ship する。「気をつける」に頼らず、agent の判断を待たずに発火する layer。

## 同梱 plugin

| Plugin | 内容 | trigger |
|---|---|---|
| **harness-core** | 3 hook: credential 値 scrub (PostToolUse) + 危険 bash command guard (PreToolUse) + admission keyword reminder (UserPromptSubmit) | 全 Bash 呼び出し + 全 user prompt |
| **harness-magi** | 3 視点 preflight review skill (MELCHIOR/BALTHASAR/CASPAR persona、parallel `Task` spawn)。大型 change 実行前に技術 / 運用 / 商業の盲点を front-load、走らせてから「もっといい方法あった」発見 (post-hoc 最適化 tax) を planning phase に押し出す | walltime ≥ 2h / ≥ 100M row DML / 非可逆 cutover / 新 layer build / ≥ $10 確実消費 / >1h sleep-loop script |
| **harness-rails** | 長時間 op の operational safety rail: pre-flight algorithm fitness CLI (working set vs RAM ceiling 計算)、in-flight heartbeat + cron watcher (stale + ETA overrun 検知)、Discord + gh issue auto-emit。auto-kill / auto-revert は禁止 (人間介入必須)。23h sunk-cost incident で「memory に書いた philosophy が rail 化されてないと fire しない」教訓から構造化 | walltime > 1h の長時間 op、watcher は cron `*/1 * * * *` |

姉妹 repo: [**njslyr7**](https://github.com/hrmtz/njslyr7) — tmux pane で長時間 peer worker を spawn する `formation` skill + CLI。別 install (`install.sh`)。

将来予定: CLAUDE.md persona template、repo-init skeleton。状況は [GitHub issues](https://github.com/hrmtz/claude-harness/issues) 参照。

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

- ✅ `harness-core` — production で運用中
- ✅ `harness-magi` — pure prompt skill、即 ship 可
- ✅ `harness-rails` — 165M-row HNSW build で実 production 検証済 ([docs/INCIDENT_23H_HNSW.md](./docs/INCIDENT_23H_HNSW.md) 参照)
- 🔗 `formation` skill — [hrmtz/njslyr7](https://github.com/hrmtz/njslyr7) で配布 (別 repo 別 install)
- ⏳ `harness-claude-md-template` — paste 可能な CLAUDE.md skeleton

## 既知 issue

- [#1](https://github.com/hrmtz/claude-harness/issues/1) — false positive: `-m` / `--body` / `--title` 引数に禁止 pattern の literal mention があると guard が trip した (commit `fdd01f6` で fix 済)

## License

MIT — `LICENSE` 参照。

## English README

See [README.md](./README.md) for the English version.
