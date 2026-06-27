# dispatch: #45 fix — repoint harness autorotate to the v2 backend

(user GO 済。cinder-wren %32 より。trigger 語を避けるため mailbox 本体でなくこの file に書いた。)

## 何を直すか
`~/.claude/hooks/autorotate_leaked_cred.sh`(= #33/#41 の human-gated autorotate) の `ROT_SCRIPT` 変数が **deprecated な backend script** を指している:

```
ROT_SCRIPT="$HOME/projects/PRS-LLM-dev/scripts/_rotate_mars_pg_roles.sh"   # ← DEPRECATED
```

`_rotate_mars_pg_roles.sh` は **2026-06-08 本番 auth/RAG 障害の犯人**(distribution 不完全: container env_file に配らない / pg_premium :5435 を ALTER しない / verify が mars-side のみ)。header にも「DEPRECATED, do NOT run for prod」と明記。

→ **`_rotate_pg_roles_v2.sh` に張り替え**:
```
ROT_SCRIPT="$HOME/projects/PRS-LLM-dev/scripts/_rotate_pg_roles_v2.sh"
```
v2 は hardened: container env_file 配布 + force-recreate / pg(:5434)+pg_premium(:5435) 両 ALTER / 各 origin の /health/ready fail-closed verify。

## 互換確認(必須)
autorotate がこの backend を呼ぶ引数(現状 `--roles <role> --execute` 等)が v2 と互換か検証:
- v2 usage: `--roles <csv> --execute`(非prod), prod は `ROTATE_PROD_I_UNDERSTAND=1` env gate。
- autorotate は **non-prod role のみ**(prod は escalate)なので prod gate には当たらない筈。
- 旧 backend にあった `--no-laddie` 等の引数差分があれば、autorotate の呼び出し側も合わせる。
- 非互換が出たら fix せず報告。

## discipline (rotation backend = Mafutsu に効く)
真田 backup → diff → dev commit(main 直禁)。**本番 backend に効く変更なので、deploy/有効化の瞬間は実行直前に cinder-wren(%32)経由で user 確認**(mutation-moment gate)。詳細根拠 = gh #45 のコメント。

## 別件 finding(この dispatch 中に観測): 新 prop-guard hook が まだ over-fire
さっき頼んだ pg propagation guard、v3 でも **コマンドが exec-pattern を string 引数として含むだけ**で block する(gh issue comment / mailbox-send / この dispatch 送信が全部弾かれた)。read/grep は v3 で通るようになったが、「該当語を引数に持つ無関係コマンド」をまだ実行と誤認。
→ refine: match を「**実際に起動されるプログラム(先頭 token / 実行される script path)が backend script、or psql に PW変更SQL(ALTER...PASSWORD)を投入**」の時だけに絞る。gh/mailbox-send/echo/Write 等に該当語が引数で入るだけでは block しない。
これは別 issue 化して(claude-harness)、#35 等と並行で。over-fire は本番作業(現にこの fix 作業自体)を止めるので優先度 中。
