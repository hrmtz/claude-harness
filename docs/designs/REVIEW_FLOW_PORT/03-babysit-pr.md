# slice ④ — babysit-pr skill

- epic: [REVIEW_FLOW_PORT.md](../REVIEW_FLOW_PORT.md) (gh #233) / status: **v0.5** / 2026-07-28
- state dir (本 doc の review 用): `docs/designs/REVIEW_FLOW_PORT/.dual-magi/03-babysit-pr/`
- changelog: v0.5 = R5 8-fix micro-reroll, no new mechanisms; design-done checkpoint。本 doc 該当は r5-5 (PASS marker に reviewed head
  OID を必須化) / r5-6 (receipt lineage を creation anchor + in-run push list に縮約、永続 chain は DEFERRED) / r5-7 (registry guard を
  exact path + active row に確定)。
- changelog: v0.4 = **probe P2/P3/P5 の結果で §3 を全面書換え**。この環境では formal review が 1 件も存在せず
  `reviewDecision` / `latestReviews` / `reviewThreads` が恒常的に空なので、green predicate の review 節を **issue comment 上の
  契約 marker** に置換 (P2/P3/P5)。CI matcher の自己封鎖を修正 (codex#5 = r4-adversarial-3)、receipt hook を harness-formation へ移し
  配線 step を追加 (codex#6 = r4-adversarial-2 = r4-workflow-4)、session provenance 主張を撤回 (codex#6)。
- 依存: activation rail (`install-claude-skills.sh`) は**本 slice 専用**。slice ② は既存 symlink 上の version bump なので
  この rail に依存しない (r4-workflow-6 で v0.3 の依存辺を削除)。

## 1. 目的

PR open 後の CI 監視・修正・review 対応を自動化し統括の注意力を解放する。

## 2. 配置 (r4-adversarial-1 で v0.3 の根拠を撤回)

`plugins/harness-formation/skills/babysit-pr/SKILL.md` + `~/.claude/skills/` symlink。

**v0.3 の「harness-core は PUBLIC だから harness-formation に置く」という安全論は誤り**。実測: `.claude-plugin/marketplace.json`
は 5 plugin (harness-core / harness-magi / harness-rails / harness-formation / harness-craft) を publish しており、
**harness-formation も同じ public marketplace に載り、`skills/` 自動 discovery も同様に効く** (既に `skills/formation` を同梱)。
配置は第三者露出の境界にならない。

採る根拠は 2 つに置き換える:

1. **co-location**: `bin/formation-integration-audit` が同居し §3 の checks predicate をそこから抽出する。PR は formation worker の
   成果物なので、skill と対象が同じ plugin に載る。
2. **inert by default (r5-7 で条件を分離)**: **skill 側は「receipt が無ければ無条件 no-op」** — registry の有無は skill の判定に
   入れない (v0.4 は §2 が AND、§5 が receipt 単独と書いており自己矛盾していた)。**registry を見るのは hook 側だけ**で、
   `~/.formation/formation/registry.jsonl` (実在 path = `plugins/harness-formation/bin/formation:33`) に **当該 repo / session の
   active row が在る時だけ receipt を mint** する。file が無い / 空 / 壊れている / directory でしかない / active row が無い、は
   すべて **no-op で exit 0**。両 guard を SKILL.md 冒頭と hook 冒頭にそれぞれ書く (§5)。

marketplace description が実態と乖離する点は残るので、**harness-formation の description 更新を本 slice の deliverable に含める**
(現 description は formation skill + njslyr7 CLI + UserPromptSubmit hook しか書いていない)。

## 3. green predicate — 実測に基づく再設計 (probe P2/P3/P5)

### 3.0 この環境には formal review が存在しない (最重要の実測)

probe P2/P3/P5 (gh 2.45.0、`hrmtz/claude-harness` #225/#226/#227/#237/#238 と `hrmtz/hippocampus-mcp` #247):

- `reviews.totalCount = 0` / `reviewThreads.totalCount = 0` / `reviewDecision = ""` (null でなく空文字) が **全 PR で成立**。
- 理由は構造的: **単一の共有 account が自分の PR に formal review を submit できない** (GitHub が author からの
  `REQUEST_CHANGES` / `APPROVE` を拒否する)。reviewer は issue comment で代替している。
- 実例: PR #247 は `reviews.totalCount = 0` / `comments.totalCount = 3` で、その 3 件が cross-family BLOCK・再 review PASS・author 返信
  — §3 が babysit したい traffic そのもの。
- 既存 `formation-integration-audit` を live で走らせると `REVIEW_UNKNOWN` が **62 件 = 全 PR 1 件ずつ** (260 findings 中の最頻)。
  これは何かを検出しているのでなく、**探している信号がこの環境に存在しない**ことを報告している。

→ `reviewDecision` / `latestReviews` / `reviewThreads` を条件に置く predicate は「不安定」ではなく **恒常的に false**。

### 3.1 review 節 = issue comment + 契約 marker

- 読む先は **`pullRequest.comments` (issue comment)**。`reviewThreads` も併読するが **空であることが期待値**と doc に明記する
  (将来 formal review が使えるようになった時に壊れないようにするだけの保険)。
- **contractual reviewer marker の文法を宣言する** (既存の非公式慣行を契約に昇格):

  ```
  ^Independent review verdict: \*\*(BLOCK|PASS)\*\* @ ([0-9a-f]{40})$
  ```

  行頭 match、`BLOCK` / `PASS` の 2 値、**末尾に「その verdict が下された reviewed head の 40-hex OID」を必須とする (r5-5)**。
  reviewer 側 (dual-magi / cross-family) はこの行を comment 先頭に置く。OID を欠く行 (v0.4 文法) は marker と認めず
  `REVIEW_UNRECORDED` 扱い。
- **「reviewed」= marker 付き comment が 1 件以上存在すること**。marker の無い author chatter は review と数えない。
- **green の review 節 = 「最新の PASS marker の OID が現在の `headRefOid` と一致し、かつそれより新しい BLOCK が無い」**。
  `headRefOid` は `gh pr view --json headRefOid` で実在する (実測: `formation-integration-audit` が既に読んでいる field)。
  **PASS 後に新しい commit を push したら、その head に対する PASS marker が無い以上 not green** — v0.4 は marker が
  reviewed head に束縛されず comment 時刻しか見ていなかったので、未 review commit を古い PASS で green にできた。
- **BLOCK / PASS の順序は「comment timestamp + OID」で定義する**: 同一 OID に対する marker 群は timestamp 最新が勝ち、
  現 head と異なる OID の marker は (PASS / BLOCK いずれも) 現 head の判定に使わない。marker が 1 件も無い PR、および現 head の
  OID を持つ marker が 1 件も無い PR は `REVIEW_UNRECORDED` として **green と呼ばず統括に返す** (§3.3)。
- marker 文法は SKILL.md と reviewer 側 doc の両方に書く。**未強制の convention** — comment を書く側を強制する機構は無い (§10)。

### 3.2 checks 節 = `statusCheckRollup` のみ (P2/P5)

- **`gh pr checks` は使わない**。gh 2.45.0 に `--json` が無く (`unknown flag: --json`、利用可能 flag は `--fail-fast` /
  `--interval` / `--required` / `--watch` / `--web`)、出力は bare TSV、**exit status は sampled 全 PR で 0** なので green 信号にならない。
- 使うのは `gh pr view --json statusCheckRollup`。entry は `__typename` / `name` / `status` / `conclusion` / `startedAt` /
  `completedAt` / `detailsUrl` / `workflowName` を持つ。
- audit の **2 軸 logic をそのまま写す**: `conclusion ∉ {SUCCESS, NEUTRAL, SKIPPED}` → bad、conclusion 不在かつ
  `status ∈ {PENDING, QUEUED, IN_PROGRESS, WAITING, EXPECTED}` → pending。`bad or unknown → ACTION` / `pending → WARN` / else PASS。
  **conclusion と status を別軸で扱う**のが要点 (素朴な `conclusion == "SUCCESS"` は進行中 check を失敗と誤分類する)。
- **これは import でなく EXTRACTION refactor**: `formation-integration-audit` (347 行) の判定は `audit_findings()` 内の
  **loop body (概ね ln205-245)** で、enclosing list に append する closure `add(...)` を呼ぶ形。**`is_green(pr) -> bool` は存在しない**。
  logic 自体は `pr` dict にしか依存しないので抽出は素直だが、見積りは「関数を抽出して 2 箇所から呼べるようにする」で取る。

### 3.3 mergeable 節 = state を先に見る (P2)

- **`state` / `mergedAt` で分岐してから** `mergeable` / `mergeStateStatus` を読む。merged PR は**恒久的に `UNKNOWN`** を返す
  (PR #247 で毎回再現)。
- open PR の初回読みは `UNKNOWN` を返し、2 回目で `CLEAN` / `MERGEABLE` になる (#227 で決定的に再現。GitHub が on-demand で
  計算するため)。→ **`UNKNOWN` は 1 回だけ backoff して再読**する。単発読みで `UNKNOWN` を not-green と扱うと新鮮な PR で flap する。
- **空 check-set = NOT green** (audit の `CHECKS_UNKNOWN` 意味論)。checks 0 件 / marker 0 件 はどちらも **terminal hand-back**。

### 3.4 claude-harness の CI は paths-filtered (r4-adversarial-14 で数値訂正)

`.github/workflows/harness-formation.yml` が唯一の workflow で、`pull_request` / `push` 両 trigger に **21 entry** の `paths:`
allowlist がある (v0.3 の「20」は誤り)。**この 21 entry には `plugins/harness-formation/**` が含まれる** ので、
**本 skill 自身の PR は CI を発火させる** (v0.3 の「skill だけの PR は空 check-set になる」は §2 の移設後は偽)。

→ validation PR の要件は「**非空の check-set を伴う PR であること**」。skill 自身の PR でもこれは満たされる。
`docs/**` や大半の `scripts/**` だけの PR は allowlist 外なので選ばない。

## 4. fix commit の path 制限 (codex#5 = r4-adversarial-3)

散文の deny-list は「列挙可能な集合」でないので、**matcher を skill と同 PR で commit する**。ただし v0.3 の機械導出は
**`paths:` trigger entry を deny 集合に入れており、これが自己封鎖を起こす** — 実測 21 entry には `plugins/harness-formation/**`
(= 本 skill と formation code そのもの) と harness-core の 15 file、`scripts/lib/chassis_stamp.py`、`scripts/check_cross_cli_hooks.sh`、
`install-*-hooks.sh` が入るため、**CI が観測する file はほぼ全部編集禁止**になり、loop は自 repo で hand-back 機械になる。
根本の誤りは「**CI を発火させる file**」と「**CI を設定する file**」の混同。

**deny 集合 (機械導出)** — `paths:` trigger entry は**入れない**:

1. workflow file 自身 (`.github/workflows/*.yml`)
2. test file (`tests/**`、`test_*`)
3. local action (`uses: ./…` の参照先)
4. **`run:` から実行される CI 依存 script** (workflow が名指しする実行対象)
5. 静的追加: `migrations/**` / `creds-migration/**` / `*.enc.*`

**repairable surface (allowlist)**: plugin 配下の source file (`plugins/**` のうち上記 deny に当たらないもの)。

**fixture (deliverable)**: 失敗している formation source file が **編集可能**であり、workflow file / test file が **編集不可**である
ことを 1 本の test で示す。この fixture が無いうちは matcher を安全根拠に使わない。

deny-list だけでは足りないので **性質としての不変条件**を 2 本置く:

1. fix commit は **PR で報告される check の集合を縮小してはならない**。
2. fix commit は **PR が元々変更した file を revert / 無効化してはならない** (doc-vs-code contract test を SKILL.md 側の編集で
   黙らせる経路が deny-list を踏まずに通る)。

各 push の前後で check 集合と changed-file 集合を比較し、どちらかが縮んだら hand-back。完了報告には **cumulative diffstat +
before/after の check 集合差分**を必ず載せる。

## 5. ownership receipt — 帳簿であって権限境界ではない (codex#6 / r4-workflow-4)

**writer = `gh pr create` を捕捉する PostToolUse hook**。v0.3 は読み手だけ設計され書き手が無かった。

- **配置は `plugins/harness-formation/hooks/`** (skill と同一 plugin。harness-formation には `hooks/hooks.json` が既に実在)。
  v0.3 の harness-core 配置は、`Three defense-in-depth hooks` と公称する security plugin に **4 本目の未記載 hook** を足し、
  第三者環境で `~/.claude/pr_receipts/*.json` を勝手に作る形だった。
- **registry guard (r5-7、exact contract)**: hook が receipt を mint するのは **`~/.formation/formation/registry.jsonl` という
  exact file が存在し、その中に当該 repo / session の active row が在る時だけ**。以下は全て **何も書かずに exit 0**:
  file 不在 / 空 file / JSON として壊れた行 / 同 path が directory / active row 無し (stale entry のみ)。
  **skill 側は receipt の有無だけで判定し、registry を読まない** (§2)。この 2 つを混ぜない。
  ※ §2 のとおり harness-formation も public なので、「public でない場所に置いたから安全」とは主張しない。
- 保存先: `~/.claude/pr_receipts/<repo_sanitized>_<pr>.json` (repo / PR# / head OID / 生成 nonce)。**filename sanitize**:
  `/` → `__`、`[A-Za-z0-9._-]` 以外を拒否。
- **mint 条件**: hook は `tool_input.command` を **argv parse して `gh pr create` であることを確認**してから、
  **その invocation 自身の stdout** から PR identity を取る (substring 検索で PR URL を拾わない = r4-adversarial-9)。
- **P11 実測 (2026-07-28、本 repo の draft PR #240 作成時)**:
  - `gh pr create --draft ...` の **stdout は PR URL 1 行のみ** (`https://github.com/<owner>/<repo>/pull/<n>`)。
    → repo と PR# はこの 1 行から取れる。**headRefOid は stdout に一切現れない**ので、v0.4/v0.5 §5 の「stdout から
    PR# / headRefOid を取る」は **前半のみ成立**。creation anchor OID は hook 側で **別途導出**する
    (`git -C <cwd> rev-parse HEAD`、または PR# 確定後の `gh pr view --json headRefOid`)。前者は remote round-trip 無しだが
    hook の cwd 依存、後者は 1 回 API を叩く。**どちらを採るかは実装時に fixture 付きで確定**する。
  - PostToolUse payload の取り出し口は **`.tool_response.stdout`** (実在 idiom: `plugins/harness-rails/hooks/vastai_create_followup_check.sh:32`)。
  - **失敗 invocation の扱い (v0.6 で訂正、実測 2026-07-28)**: v0.4/v0.5 は「exit≠0 の Bash は error-wrapped な別 shape で
    PostToolUse に届く」(`plugins/harness-core/hooks/lib.sh:216-223` の注記から推論) と書いていたが、**実測は異なる**。
    Claude Code 2.1.220 の isolated probe で、失敗する `gh pr create` は **PostToolUse hook を 1 回も発火させず**、
    debug 上は `outcome=error` の後 **`PostToolUseFailure` 分岐**に入る。したがって:
    - mint は `PostToolUse` に登録する (成功時のみ発火するので安全側)
    - `PostToolUseFailure` にも **no-op 防御**として登録し、「失敗 invocation で receipt が mint されない」を assert する test を置く
    - 「error-wrapped shape を parse する」実装は**不要** (そもそも届かない)
    引用した lib.sh の literal 自体は正しいが、**そこから PostToolUse の発火有無を推論したのが誤り**だった
    (epic §2-5「未実測の field を設計に書かない」の再発。probe が設計を上書きした形)。
- **remote marker**: PR 作成時に **per-PR random nonce** を body marker として remote 側にも書き、local receipt と一致して初めて
  自 PR と見なす。session id は publish しない (transcript path や mailbox log に現れる内部識別子)。
  **marker の書き手は「PR を作る agent が `--body` に含める」= skill 側の規約**とし、hook は remote write をしない
  (PostToolUse は argv を書き換えられず、hook からの `gh pr edit` は §7 の PUBLIC gate を迂回する remote write になるため)。
  → **marker の無い PR は恒久的に scope 外** (fail-closed)。この帰結を SKILL.md に明記する。
- **OID 規則 (r5-6 で主張を縮約)**: receipt が持つ単一 OID は **creation anchor** — 「この skill が PR を作った時点の head」以上の
  意味を持たない。規則は **「anchor が現 head の ancestor であること」だけ**。anchor から現 head までの間に積まれた commit のうち、
  **本 invocation 中に skill 自身が push した OID 以外は attribution 不能**として扱い、1 つでも在れば **hand-back**。
  skill は自分が push した OID を **in-run の list (process memory) にのみ**持ち、file には落とさない。
- **永続 append-only receipt chain は作らない (明記)**。v0.4 本文の「receipt lineage に無い commit があれば hand-back」は、lineage を
  追記する writer / ack record / schema がどこにも無く、**最初の fix commit で自己失格する**主張だった。v0.5 はこれを上記の縮約形へ
  置換する。**durable な ownership / ack 機構 (append-only chain + 第三者 commit の恒久排除) は v1 では構築しない = DEFERRED**
  (epic §7)。したがって「別 agent が積んだ commit」と「再起動後の自分が積んだ commit」は区別できず、どちらも hand-back になる。
  これは安全側 (false hand-back) に倒れる縮約であって、attribution の主張ではない。
- v0.3 の「その間の全 commit が本 session の author か acknowledged であること」は **撤回済**のまま — GitHub の author identity は
  login 単位で、全 formation / worktree session が同一 `hrmtz` identity を使い、**commit に session provenance を持たせる durable な
  機構が repo に存在しない** (codex#6 の実測)。
- **receipt 不在 = fail-closed (触らない)**。lifecycle: merge / close で delete、無条件 expiry (14 日)。
- **trust model (明記)**: これは **混乱した agent を防ぐ機構であって、敵対的な書き手は防がない**。home directory 上の file に
  それ以上の保証は与えられない。

### 5.1 hook の activation (codex#6)

skill の symlink 手順とは別に、**hook は publish 経路を持たないと live に反映されない**。deliverable に含める:

1. `plugins/harness-formation/hooks/pr_receipt.sh` + **`hooks/hooks.json` への entry 登録**
2. **`scripts/sync_hooks_to_live.py`** で `~/.claude/hooks/` に反映し settings を再構築
3. **`scripts/check_hook_wiring_drift.py` が沈黙する**こと (drift 検出 0)
4. **fixture**: 1 回の `gh pr create` が **local receipt と remote marker の対**を生むこと (両者の一致を test で確認)
5. epic §5-3 準拠の **入力 fixture + 期待 exit code の test** を `tests/` に 1 本

## 6. loop の終了条件

**success = checks green AND 現 `headRefOid` に対する PASS marker が在りそれより新しい BLOCK が無い (§3.1、r5-5) AND 全 open thread /
marker 付き指摘に sha を引用した返信が付いている**。これは loop の許された action だけで到達できる。**fix commit を push した時点で
PASS は失効する** (新 head の OID に対する marker が無くなるため) ので、push 後は必ず再 review 待ちの hand-back になる。

- **`unresolved thread 0` は human 側の merge gate であって loop の終了条件ではない** (reply-only と「unresolved 0 を green の必要
  条件にする」を同時に置くと terminal condition が禁止 action の向こう側に来る)。
- **open thread / 未応答 marker に当たったら SUCCESS-with-handback**。ここは harness の 松岡プロトコル (撤退禁止) と正面から
  擦れるので、SKILL.md 本文に **「hand-back は撤退でなく成功終了である」と明記**する。書かなければ runtime に自分で解決される。
- 返信 clause には**検査可能な bar** を置く (r4-adversarial-11): 引用 sha は **現 head の ancestor** であり、**PR base ではなく**、
  **その diff が当該指摘の対象 file に触れている**こと。満たさない返信は未応答扱い。完了報告に
  **thread/marker → 引用 sha → 触れた file** の対応表を載せる。**返信 0 件の hand-back も等しく成功**と書き、安い経路に優位を作らない。
- hardening (任意、v1 では未実装でも可): GraphQL resolve mutation / `gh pr merge` / `gh pr ready` を、
  `HARNESS_ACK_MAIN_COMMIT` を模した明示 ack env 無しでは deny する gh guard。

## 7. その他の境界

- **reply-only**: review thread / marker comment の自動 resolve はしない。対応 commit を積み「Fixed in `<sha>` — 1 行説明」を
  返すのみ。resolve は reviewer の仕事。`gh pr ready` (draft→open) もしない。
- **PUBLIC repo gate**: repo visibility を実行時に確認し、PUBLIC なら自動返信せず draft を提示して ack を待つ
  (claude-harness = PUBLIC / hippocampus-mcp = PRIVATE、実測済)。返信本文は **job 名 / step 名 / 失敗 test id / sha のみ**を引用し
  **raw log 本文を貼らない**。投稿前に `credential_patterns.sh` を通す。
- **待機上限**: fix loop ≤5 周 **かつ** 総 walltime ≤90 分 **かつ** poll 間隔 ≥60s。達したら状況報告 + 代替案 +
  **積んだ fix commit の cumulative diffstat と revert 手順**で停止。
- **guard 前提を主張しない**: 既存 hook (`branch_policy_guard` / `bash_command_guard`) は `git commit|push` と command shape にのみ
  効く。`gh` write の安全は本 skill の境界が担う。

## 8. probe

| # | 対象 | 確認事項 | 状態 |
|---|---|---|---|
| P2 | `gh pr view --json` / `gh pr checks` | field 実名と型、`reviewDecision=""`、`mergeable` の初回 UNKNOWN、gh 2.45.0 に `pr checks --json` 無し | **済** (§3) |
| P3 | GraphQL `reviewThreads` | query は動くが `totalCount` は全 PR で 0。review traffic は issue comment 側 | **済** (§3) |
| P5 | `formation-integration-audit` | checks predicate は再利用可 (要抽出、ln205-245 の loop body)、review predicate は `REVIEW_UNKNOWN` × 62 で恒常 false | **済** (§3) |
| P11 | `gh pr create` の出力 shape | PostToolUse hook が argv parse + 当該 stdout から PR# / headRefOid を取れるか (§5 writer の成立条件) | **一部済 (2026-07-28)** — PR# は取れる / **headRefOid は取れない** (下記)。残り = 失敗 invocation の payload shape 実測 |
| P15 | CI matcher fixture | 失敗中の formation source file が編集可、workflow / test file が編集不可 (§4) | 未 |

## 9. activation checklist (epic §5 に加えて)

1. **`install-claude-skills.sh` を新規作成** (`plugins/harness-magi-codex/install-codex-skills.sh` を雛形、実在を実測) +
   `tests/test_skill_install.sh` 相当の test
2. `readlink -f ~/.claude/skills/babysit-pr` が repo path を指す
3. **新 session** で (i) skill 一覧に出現 (ii) 明示呼出しで load (iii) version 行一致
4. **receipt hook** の作成 + `hooks/hooks.json` 登録 + `sync_hooks_to_live.py` 反映 + `check_hook_wiring_drift.py` 沈黙 (§5.1-1..3)
5. **receipt fixture**: 1 回の `gh pr create` が local receipt と remote marker の対を生む (§5.1-4) + hook の exit code test (§5.1-5)
6. **CI matcher fixture** (§4 / P15) が green
7. harness-formation の marketplace / plugin description を本 PR で更新 (§2)
8. **非空 check-set を伴う PR** で実戦 1 回 → 完了報告に cumulative diffstat + check 集合差分 + 返信対応表 (§6)
9. **marker fixture: PASS marker (OID 付き) の後に commit を push → not-green** / OID 無し marker は `REVIEW_UNRECORDED` (§3.1、r5-5)
10. **anchor fixture: skill が本 run で push していない commit が anchor..head に 1 つでも在れば hand-back** (§5、r5-6)
11. **registry guard fixture: `~/.formation/formation/registry.jsonl` が 不在 / 空 / malformed / directory / active row 無し の
    5 ケースで hook が mint せず exit 0、かつ receipt 不在で skill が無条件 no-op** (§2/§5、r5-7)

## 10. やらないこと

- auto-resolve / auto-merge / `gh pr ready` (draft→open) — **gh #233 item 3 の記述とは意図的に異なる** (epic §6 参照)
- CI 設定 / test file / local action / `run:` 実行対象 script / `migrations/**` / `creds-migration/**` / `*.enc.*` の編集
- PUBLIC repo への無人返信、返信本文への raw log 転記
- **commit の session 帰属推定** (§5)。anchor..head に自分が push していない commit が在れば attribution を試みず hand-back
- **永続 append-only receipt chain / durable ownership・ack 機構** (§5、r5-6)。v1 は creation anchor + in-run push list のみ (epic §7)
- hook からの remote write (`gh pr edit` 等)。marker は PR 作成側が `--body` に入れる
- **`/loop` や recurring scheduler からの駆動**。cap は per-invocation で再起動すると 0 に戻る。
  **未強制の convention**: 同梱の `loop` skill は「keep running /babysit-prs」を canonical example として挙げており、
  この合成は user の 1 文で成立する。per-(repo, PR) 永続 counter は v2 (epic §7)
- Codex/Kimi への公開 (harness-formation manifest への skills capability 追加は v2)
- **未強制の convention**: §4 の path 制限と不変条件は、PreToolUse matcher が出荷されるまで**強制されない convention** である
- **未強制の convention**: §3.1 の marker 文法は reviewer 側の書式規約であり、marker を書かせる機構は無い
