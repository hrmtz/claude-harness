# Briefing (phase 2): harness 自己 red-team — FIX フェーズ

slate-falcon、続投。user(中村さん)が **human gate を開けた**: 君が #27 audit で出した findings(#28 umbrella / #29-#41)を、この流れで **実際に fix** する。read-only は解除、ただし harness 改変は blast-radius 大なので 下記規律を厳守。

## 不変規律（FIX 中ずっと有効）
1. **per-finding flow**: 真田 backup(`~/sanada_backup_persistent/`、黙って) → fix → `git diff` → 関連 hook/test で verify → **dev branch に commit**(finding# を ref)。**main 直 commit 禁**。1 commit = 1 finding 原則。
2. **dev 限定**。commit 後に `git branch --show-current` で dev 確認。
3. **security 系の fix は cross-family review 必須**(dual-magi の codex-exec で fix 内容を叩いてから commit)。同family 盲点で穴を塞ぎ損ねるのを防ぐ(gh#195)。
4. **structural-consolidation 系(#30/#40)は auto-fix 禁**。SoT 選択を要する設計判断 → 案を作って `formation ask` で user 判断を仰ぐ。判断後に execute。
5. SOPS 2コマンド原則・credential 焼付け禁・Memory は `formation/<self>/` namespace。

## 優先順位とグルーピング

### A. security 系（最優先・cross-family review 必須）
- **#41 / #33**: mars PG autorotate に人間 gate 挿入(攻撃者制御 tool 出力中の DSN で無gate発火を塞ぐ)。
  ⚠️ **重大ハザード**: この self-DoS は audit 中に **実発火した**(報告 text の scrub 誤発火→autorotate#26 再trigger)。**fix/test 中に autorotate を再発火させるな** — DSN 風文字列を scrub→autorotate 経路に通すテストをしない、dry-run / 経路遮断で検証。
- **#39** HMAC credential scrubber fail-open(大出力/長run)
- **#34** mailbox hard-refuse が primary secrets(PG DSN, POSTGRES/TURSO/R2)を取りこぼす
- **#36** Bash credential-file guard が relative .env / 非列挙 reader を見逃す
- **#38** formation が credential-shape screening だけで任意 briefing から bypass-perm agent を spawn
- **#37** formation inbox が peer mailbox body を command 出力として render(data/instruction 境界なし)
- **#32** formation skill 群(6 files)が禁止 SOPS decrypt-pipe idiom を教えてる → `sops exec-env` に全置換

### B. dead / residue（mechanical 寄り、A の後）
- **#29** temporal_anchor.sh が live で wired nowhere → wire するか CLAUDE.md の記述を実態に合わせる
- **#31** branch_policy_guard.sh が live で未配線 → wire
- **#35** 真田 silent-backup が behavioral residue → **PreToolUse backup hook 化(structural)**。※これは fix というより新規 hook build = 一段重い。設計を `formation report` で共有してから着手

### C. structural-consolidation（auto-fix 禁・`formation ask` で user 判断）
- **#40** formation runtime が njslyr7 + harness-plugin に二重・配線split → **SoT を1本化**(どちらを正とし他を retire、symlink+CLI 整合)。案+推奨を作って ask
- **#30** plugins が deployed artifact でない / hooks.json ↔ settings.json 双方向 drift → **SoT 1本化**(repo plugin を正にするか live を正にするか) + orphan/dormant を CI diff で検出。案+推奨を作って ask

## 報告
- batch ごとに `formation report "<1行>"`、判断超過/structural は `formation ask`、完了で `formation done`。
- 親(cinder-wren %32)が user に diff/進捗を中継する。詰まったら遠慮なく ask。

## 最初の一手
1. `gh issue view 28 -R hrmtz/claude-harness`(umbrella で全 finding 俯瞰)
2. A 群の #41/#33(security 最優先・ハザード注意)から、規律フローで着手
3. 1件 fix → backup/diff/verify/dev-commit → report
