# 2026-07-27 昼 引き継ぎ — 統括 claude-indigo-lantern (2 代目、全体リセット版)

前 session の handout (`2026-07-27-overnight.md`) の続き。JST 12:55 に統括復帰、14:10 に owner 指示で**フリート全体を閉店**した。この handout は 3 代目の統括が最初に読むもの。

## 0. 次の統括が最初にやること

1. 本ファイルを読む (いま読んでいる)
2. `~/.formation/resume/` を見る — **worker 8 体分の resume packet + 再 spawn 用 briefing が揃っている**。worker を再開するときは `formation spawn ~/.formation/resume/briefings/<id>-briefing.md <id>` するだけで前回状態を自動で読み込む
3. `gh issue list` — claude-harness #212/#213/#214/#215/#218/#220/#221、hippocampus #252/#256/#111/#222/#262 が実装待ち
4. owner 判断待ち (owner は「明日やる」と明言済み): backend#6 rotate / backend#13 / backend#14 / zetith-site#60・61・63 / zetith-site#67 / `feat/agy-migration` push 可否 (bundle 保全済み、repo PUBLIC のため owner 判断)

## 1. この session で merge されたもの (全て cross-family review PASS → dev)

| PR | repo | 中身 | merge sha |
|---|---|---|---|
| #219 | claude-harness | #216 parent_id null fix | `b62f5f07` |
| #223 | claude-harness | #219 の hotfix (tmux quoting) | `4f3fef54` |
| #224 | claude-harness | #222 Kimi 偽 REFUSED fix | `00110a90` |
| #258 | hippocampus | tally 経路 symlink 穴 (#253) | `63cfe1ed` |
| #259 | hippocampus | test/installer の worktree pinning (#254/#257) | `4ef3a8e` |
| #261 | hippocampus | #218 トークン計測 analyzer | `b40f73bf` |

close: claude-harness #211/#216/#222、hippocampus #253/#254/#257。#218 は計測完了・**梃子実装が未着手のまま open**。

## 2. 一番の成果 — escalation の silent drop が live で直った

parent_id:null だった 5 体を repair-parent で修復、全て ROUTABLE。「黙った worker についての通知だけが消える」穴が塞がった。

**経緯が教訓**: PR #219 は review PASS だったのに、%209 から live 実行したら 5/5 fail-closed rollback。原因は **tmux show-options が `%` を含む値を quote して返す** (`"%209"` vs `%209`) — sandbox の mock が素の値を返す test gap。診断 → hotfix #223 → 「旧 binary で新 regression が赤い」ことを reviewer が確認してから merge → 再実行成功。**review PASS ≠ live で動く。live 実行を close gate に置いたから捕まえられた。**

## 3. フリート閉店の状態 (JST 14:10)

- **10 体全て DONE → reap 済み**。未解決 ASK ゼロ。registry 空
- resume packet 8 本: `~/.formation/resume/{hc-orch,hx-orch,zs-orch,pl-orch,mz-orch,zc-coord,zc-review,zc-review2}.md`
- 再 spawn briefing 8 本: `~/.formation/resume/briefings/` (verifier/verifier2 は stateless なので packet 不要、新規 spawn でよい)
- **rust-crane (%330) だけ残してある** — #235 活性化飢餓の観測窓 (2026-08-10 02:21 UTC まで)。owner が直接立てた peer。idle pane はトークンを消費しない
- 保全 2 件: zetith-site の local-only commit `628674d` → `~/sanada_backup_persistent/zetith_organize_628674d_20260727/` (bundle、complete history 確認済み。park 元の /tmp は揮発なので bundle が正)。`feat/agy-migration` → 前日の bundle のまま owner 判断待ち

## 4. この session の新規 issue (worker の発見を拾ったもの)

| issue | 中身 | 出所 |
|---|---|---|
| claude-harness#220 | watcher を「mailbox 無発信 ∧ pane hash 無変化」の 2 信号に | zc-coord の設計 + 実測 |
| claude-harness#221 | verdict copy 義務化 + request-id 追跡 (「読了したが着手していない」検出)。**行動不要 ack が escalation を発火させる誤報 3 回 → 応答不要 flag も入れる** | zc-coord の運用 + 私の実測 |
| claude-harness#222 | Kimi pane 偽 REFUSED (同日 fix→close) | zc-coord の解析 |
| hippocampus#262 | **統括 handout/resume packet を hippocampus に ingest、統括 spawn 時に注入** | owner 発案。file path 依存から検索依存へ |

## 5. トークン削減 (#218) の現在地

- 計測フェーズ完了 (PR #261 merge)。実 session で 888/888 tool message 単発 = 束ね採用率 0% を機械確認、戻り 49.4 万文字
- 梃子 3 つ (束ね / 出力を発生源で絞る / session 境界) は**実装未着手**
- 今回の全体閉店自体が最大の削減: 811M cache_read は「統括が長生きするコスト」だった。**resume packet 方式で「覚えることを pane でなくディスクにやらせる」型が確立**
- 次の一手候補: analyzer に単価推移を出させて「session をいつ切るか」を数字で出す

## 6. この session の私のミス

1. **stale な督促** — hc-orch に「#211 を push しろ」と督促したが、前 session 中に PR #217 として merge 済みだった。handout の 03:12 時点の状態を現在と誤認。hc-orch が証跡付きで訂正
2. **C-u プローブを本物の可能性がある入力枠に撃った** — zc-coord の入力枠の正体不明テキストに、owner の「俺のじゃない」を得る**前に**判別を検討し、得た後に実行した。結果は無害 (正体は Claude Code の auto follow-up 提案 = draft でもゴーストでもない第 3 の見た目、#215 に記録) だが、owner 確認前に触っていたら実入力を壊し得た

## 7. 運用知見 (次の統括へ)

- **wake は `lib/wake.sh` の `tmux_send_submit` 一本** (raw send-keys 禁止)。nonexclusive の worker には formation msg だけでは届かない — idle なら wake が要る (#213 が直るまで)
- **verdict には必ず ack を返す**。reviewer への割当は統括が一元管理、orchestrator は「review 依頼あり」とだけ上げる
- **merge 許可は統括、close は実測で欠陥が消えてから**。live 実行を close gate に置く (§2 の教訓)
- 行動不要の ack を送ると 15 分後に escalation 誤報が返ってくる (#221 で対処予定)。それまでは既知 benign として扱う
- pane 文字列判定は脆い (#220)。stall 判定は pane を見てから、mailbox の emission を先に信じる
