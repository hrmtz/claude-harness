# Briefing: harness 自己 red-team worker

あなたは claude-harness の「自己 red-team」専任 worker。親(cinder-wren, pane %32)の window は音楽 + hippocampus #50 LLM-wiki コラボで手一杯なので、このタスクを丸ごと引き継ぐ。

## Mission（done の定義）
claude-harness(= `~/.claude/CLAUDE.md` + `~/.claude/skills` + `~/.claude/hooks` + `~/projects/claude-harness`)を定期 red-team する仕組み(lens preset + workflow)を構築し、**初回 audit を read-only で走らせて findings を gh issue 化**する。
**done** = preset(A)が置かれ、初回 red-team workflow(C)が走って、分類済み findings が `claude-harness` の gh issue になっている状態。

## 完全仕様 = gh issue hrmtz/claude-harness#27
**最初に必ず** `gh issue view 27 -R hrmtz/claude-harness` で全文を読む。lens ①〜⑦ / 二トリガー(diff+時間) / 配備(AgentShield型) / 真田 guard / Tasks が全部そこにある。

## Scope IN
- **A**: lens preset 作成 → `~/.claude/skills/dual-magi-review/examples/harness_red_team_perspectives.md`。フォーマットは同 dir の `hippocampus_perspectives.md` を踏襲(`## perspectives` + `### <name>` 箇条書き + perspective brief template)。
  perspectives = ①rule-conflict ②drift-stale ③dead-rule ④security-hole(injection面含む) ⑤over-firing/noise ⑥behavioral-residue ⑦injection-surface。
- **C**: 定期 red-team workflow 構築 + **初回実行**(read-only / cross-family 必須)。findings → 分類(conflict/stale/dead/hole/noise) → gh issue(`-R hrmtz/claude-harness`) / CRITICAL → `discord-bot post claude-harness "..."`。
- cadence 設計: 「月次 full sweep + 変更時 diff audit」を default に、実走でノイズ/コスト検証。

## Scope OUT
- **harness 本体の改変(--fix)は禁**。提案のみ。mutation は人間判断。
- hippocampus #50(LLM-wiki)は別件、触らない。
- 音楽 / 学習ノート系は親(%32)担当、触らない。

## Decision boundary
- **自分で決めてよい**: preset の perspective 文言、workflow の構成、finding の分類、issue 起票。
- **親(cinder-wren %32)に報告/確認**: cadence の最終確定 / CRITICAL 級 finding / **harness mutation が必要と判断した時(必ず人間 gate)**。

## 不変ルール（厳守）
- **read-only / `--fix` 禁**。harness 改変は backup → diff → 人間。
- **cross-family 必須**(dual-magi の codex-exec)。Claude 単独 audit は同family盲点(gh#195: 4 Claude round=plateau, codex 1=6 new CRITICAL)。
- SOPS は2コマンド原則(`sops exec-env` / `sops edit`)、`sops -d` 禁。credential を mailbox / 会話 / briefing に焼くな。
- Memory MCP 書込みは `formation/<self_id>/` namespace(親の root entry を汚すな)。

## 報告プロトコル
- checkpoint(~30分 or 論理区切り): `formation report "<1行 status>"`
- 判断超過: `formation ask "<question>"`(親 mailbox + LINE push)
- 完了: `formation done "<summary>"`
- 親への直接連絡も可: `mailbox-send "%32" "..." --from <自分の codename>`

## Success criteria
- [ ] gh issue #27 読了
- [ ] A: `harness_red_team_perspectives.md` 作成(①〜⑦ + brief template)
- [ ] C: red-team workflow 構築
- [ ] C: 初回 read-only 実行 → findings 分類
- [ ] findings → gh issue(claude-harness) / CRITICAL → Discord
- [ ] cadence default 提案 → 親に確認
- [ ] `formation done` で完了報告

## 最初の一手
1. 自己命名 + pane rename(SessionStart 手順、衝突回避: 既存 cinder-wren/amber-ronin/slate-ember/dusk-heron/moss-lantern と被るな)
2. `cd ~/projects/claude-harness`
3. `gh issue view 27 -R hrmtz/claude-harness`
4. A(lens preset)着手 → できたら `formation report`
