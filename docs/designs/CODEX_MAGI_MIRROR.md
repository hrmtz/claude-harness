# CODEX_MAGI_MIRROR — Codex を主にした dual-magi / ultramagi 移植設計

status: v0.5 (round 1 = Claude ×3 / round 2,3,4 = codex REJECT、いずれも適用済。round 4 は **new findings 0**、唯一の CRITICAL = doc 内矛盾 → v0.5 で解消)
author: claude-ember-crane
date: 2026-07-08
scope: `plugins/harness-magi-codex/` 新設 + **原典 `plugins/harness-magi/` の fail-open 修理** (round 2 で scope に編入)
related: gh #195
review state: `.dual-magi/round_1.json` (25 findings / 6 CRITICAL) → `round_2_xfamily.json` (**REJECT / 5 NEW CRITICAL**) → `round_3_xfamily.json` (**REJECT / 3 NEW**)

> **本 doc 自身が dual-magi の実証例**。Claude 同族 3 視点は 25 findings を出したが、cross-family 1 round が **同族が 1 つも触れなかった CRITICAL を 5 個**出して REJECT した。gh #195 の pattern が family を入れ替えても再現している。
>
> **v0.4 の主題は「削る」こと**。round 3 の残存 CRITICAL は全て同じ形をしていた —— 「v0.3 は実装できない機構を仕様に書いた」(reviewer が自分の tool_event_id を引用する / transcript 内の prompt hash と照合する / `magi-pg-ro` が credential を argv から締め出す)。これは `@file` 欠陥と同じ **未検証インタフェース依存**の再発である。v0.3 は「gate を厳しくする」方向に膨らんだ。v0.4 は逆に、**宣言した脅威モデル (T1) に必要な最小限まで機構を削り、実装できない主張を仕様から消す**。

## 変更履歴

| ver | 主変更 | 駆動した round |
|---|---|---|
| v0.1 | 初稿 | — |
| v0.2 | CLI 実挙動 5 件の誤りを訂正、INV を構造 rail 化 | round 1 (Claude ×3、25 findings) |
| v0.3 | 脅威モデル (T1/T2) を導入、原典修理を scope に編入 | round 2 (codex REJECT、5 CRITICAL) |
| v0.4 | **flock 採用で lock 問題を一掃 / 実装不能な gate 条件を削除 / DB grounding を scope 外へ / 移行手順を明記** | round 3 (codex REJECT、3 new) |
| v0.5 | **削除した機構への dangling reference を除去** (§2 図 / §3 tree / §4.4 が `mkdir` lock と `nonce` を規範として残していた = doc 内矛盾) | round 4 (codex REJECT、**new 0**、CRITICAL 1 = 自己矛盾) |
| — | **round 5 = codex `GO` / findings 0 / grounding PASS → PLATEAU** (`.dual-magi/state.json`) | round 5 |
| **v0.6** | **G8 / G9 を追加** (= 実装後 bug-hunt が発見: gate が verdict field しか見ず、CRITICAL を抱えた `REVISE` に plateau を与えていた。grounding FAIL も素通ししていた) | 実装 bug-hunt (design round ではない) |

> **注**: plateau marker は **v0.5 の `artifact_sha` に対して**発行された。v0.6 の編集で doc の sha は変わったため、
> 私自身の G3 assert により **その marker は現在の doc を certify しない**。これは仕様どおりの挙動であり、
> 「design が実装後に変わったなら、plateau は取り直せ」という不変条件が自分自身に適用された記録として残す。
> v0.6 の変更は gate を **厳しくする**方向 (= 追加 assert) のみで、緩めていない。

---

## 0. SCOPE + 脅威モデル + 不変条件

**やること**: dual-magi-review / ultramagi を、**Codex CLI が orchestrator、Claude が cross-family reviewer** という鏡像構成で `~/.codex/skills/` に移植する。**加えて**、原典 Claude 側 plugin の cross-family fail-open を修理する (§7、round 2 r2-xfamily-5)。

### 0.1 脅威モデル (v0.2 に欠けていた節。ここの混同が INV-1 を壊していた)

| | T1: 不注意 / bug | T2: 敵対的 same-user process |
|---|---|---|
| 例 | AI が flag を渡し忘れる (= gh #195 の実際の root cause)。script が cross-family を silently skip する。stale artifact を新 round と誤認する | orchestrator と同 UID の process が偽 transcript / 偽 envelope を書き、cross-family が走ったと gate を騙す |
| **本設計が防ぐか** | **防ぐ** (これが目的) | **防がない** |
| 理由 | 検知に必要な全 artifact を gate が機械的に照合できる | gate・transcript・findings は **全て同 UID が書ける ordinary file**。同 UID の攻撃者に対する暗号学的束縛は無い |

**v0.2 の誤り**: 「codex process は claude を実際に走らせずに transcript を捏造できない」と書いた。**偽**。`~/.claude/projects/<slug>/` は同 UID 所有の普通のディレクトリで、jsonl は普通のファイルである (r2-xfamily-1)。

**v0.3 の立場**: 本 gate は **accidental-skip detection** であり、**adversarial forgery resistance ではない**。T2 を防ぐには orchestrator の書込権限の外にある署名 attestation が要る。本 PR の scope 外とし、doc に明記する。**「forgery-resistant」という語を使わない。**

T1 に対する強度は上げられる。gate は単なる存在確認ではなく、**envelope 内の値どうしの整合**を検査する (§4.3): stale / 使い回し / 別 doc の artifact を再利用した round は T1 として弾ける。

### 0.2 不変条件

「enforced by」が behavioral (= SKILL.md の文) なら rail ではない (`docs/PHILOSOPHY_RAIL_LEVELS.md`)。

| INV | 内容 | enforced by | 防げる脅威 |
|---|---|---|---|
| **INV-1** | cross-family round が走ったことを artifact から**機械的に照合**できる | `*.meta.json` に envelope 全体 + `{artifact_sha, output_sha, model_id, session_id, num_turns}` を保存。gate が G1-G7 を検査 (§4.3) | T1 のみ |
| **INV-2** | cross-family record 無しに `plateau` を宣言できない | **`magi_plateau_gate.sh` だけが plateau marker を書く**。model は書けない | T1 |
| **INV-3** | 同族 3 視点は互いの findings を見ない | fan-out script が唯一の prompt 作者。出力読取前に 3 process 全起動。sibling 出力既存なら exit 非 0 | T1 |
| **INV-4** | reviewer の grounding を self-report のまま信用しない | **adapter が transcript から実行 command を導出**し、self-report との包含関係を検査 (§5.1)。reviewer の協力を要求しない | T1 (**欠落と不整合のみ**)。内容の真偽は検出しない |
| **INV-5** | credential を prompt / findings / argv に載せない | allowlist に **DB tool を入れない**、prompt は **stdin** (argv 不使用)、`mktemp` 0600 + 単一 cleanup trap、DSN 形状を書込前 scrub (§4.2) | T1 |
| **INV-6** | review round は read-only | `--permission-mode dontAsk` + 明示 `--disallowedTools Edit Write NotebookEdit` (§1.2 P-c で実測) | T1 |
| **INV-7** | cross-family の中で cross-family を呼ばない | **`flock(2)`** (原子獲得、SIGKILL で kernel が自動解放、stale 無し)。allowlist から `Bash(codex:*)`/`Bash(claude:*)` を排除し named + tested に (§4.4) | T1 |

---

## 1. Grounding (実測。prose ではない)

### 1.1 これまでの round で反証された自分の主張

**設計を実測に従わせた記録。削らない。** v0.1 で 5 件、v0.2 で更に 5 件が反証された。

| 主張 | 実測 | 反証 round |
|---|---|---|
| 「Claude に `--output-schema` 相当は無い」 | **偽**。`--json-schema` が実在し `structured_output` を返す | r1-melchior-2 (再現確認済) |
| 「`acceptEdits` + allowlist に Edit 無し = mutation 不可」 | **偽**。ファイルが作成された | r1-melchior-1 (再現確認済) |
| 「tool 実行到達に permission-mode が要る」 | **偽**。allowlist のみで Bash が走る | r1-melchior-4 |
| 「env export で子に depth が届く」 | **半分偽**。codex には届く、**`claude -p` の Bash tool shell には届かない** | r1-balthasar-5 |
| 「並列 subagent は無い」 | cherry-pick。`multi_agent stable true` が同 output にある | r1-melchior-7 |
| 「codex は claude transcript を捏造できない」 | **偽**。同 UID 所有の普通のファイル | r2-xfamily-1 |
| 「transcript 導出で捏造リスクが緩和される」 | **偽**。command の発生を証明するだけで、finding の真実性は証明しない | r2-xfamily-2 |
| 「`[ -e f ] && exit; touch f; trap rm EXIT` で確実削除」 | **偽**。TOCTOU + **2 個目の `trap ... EXIT` が 1 個目を置換** (再現確認済) | r2-xfamily-3 |
| 「`--json-schema "@schemas/finding.schema.json"`」 | **偽**。`Error: --json-schema is not valid JSON: Unrecognized token '@'`、exit 1 (再現確認済) | r2-xfamily-4 |
| 「codex 側 provenance は未調査、future work」 | **不当**。`~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl` (380 files) に `session_meta` + `turn_context.model` が実在 | r2-xfamily-5 |

### 1.2 v0.3 が依存する実測

```
codex --version                                → codex-cli 0.142.5
codex features list                            → enable_fanout=false / multi_agent=stable,true
claude --help                                  → --json-schema / --disallowedTools / --permission-mode{...,dontAsk,...}
claude -p --json-schema "@file"                → exit 1, "not valid JSON: Unrecognized token '@'"   ← inline 必須
bash: trap A EXIT; trap B EXIT; trap -p EXIT   → B のみ残る                                        ← 単一 cleanup 必須
ls -la ~/.codex/skills/                        → formation は symlink (Codex は symlink skill を解決)
ls ~/.claude/projects/-home-hrmtz-projects-claude-harness/*.jsonl | wc -l  → 80、全て <uuid>.jsonl
  ↳ 実 probe の session_id 7279f67e-… が実在 = 命名規則は成立。ただし同 UID が書ける
find ~/.codex/sessions -type f | wc -l         → 380 (rollout jsonl、session_meta + turn_context.model)
diff harness-magi/…/bug-hunt/templates/ harness-kimi/…/bug-hunt/templates/ → hornet/wasp は既に drift 済
```

決定的 probe:

| # | probe | 観測 | 帰結 |
|---|---|---|---|
| P-a | `codex exec --output-schema` に required 省略 + enum 違反を指示 | exit 0、schema 完全準拠。required は **捏造値で充填** | constrained decoding。**schema 準拠は内容の真実性を何も保証しない** |
| P-b | `claude -p --permission-mode acceptEdits --allowedTools 'Read' 'Grep'` に file 作成指示 | **作成された** | v0.1 の INV-6 は 0 rail |
| P-c | 同上を `--permission-mode dontAsk --disallowedTools 'Edit' 'Write' 'NotebookEdit'` で | **未作成**、model は `BLOCKED` 応答 | **INV-6 の正しい形** |
| P-d | `claude -p --output-format json` の envelope | `modelUsage` (exact model id で keyed)、`session_id`、`num_turns`、`permission_denials` | INV-1 の材料。ただし T1 用 |

### 1.3 非対称

| 軸 | Codex | Claude |
|---|---|---|
| 構造化出力 | `--output-schema <file>` | `--json-schema '<inline JSON>'` (**file 参照不可**) |
| 並列 subagent | exec からは無し → shell fan-out (process 分離で INV-3 が構造的に満たされる) | `Task` |
| env の子 shell 到達 | 届く | **届かない** |
| provenance | `~/.codex/sessions/**/rollout-*.jsonl` (`session_meta`, `turn_context.model`) | envelope の `session_id` / `modelUsage` |

**両 CLI 共通の限界**: schema 準拠 ≠ 内容の真実性 (P-a)。

---

## 2. アーキテクチャ

```
        ┌──────────────────────────────────┐
        │ Codex CLI session (orchestrator) │
        └──────────────┬───────────────────┘
                       │ 1) fan-out (INV-3: script が唯一の prompt 作者、3 process 同時起動)
      ┌────────────────┼────────────────┐
      ▼                ▼                ▼
 codex exec        codex exec       codex exec        -s read-only / --output-schema
 (MELCHIOR)        (BALTHASAR)      (CASPAR)
      └────────────────┼────────────────┘
                       ▼
        .dual-magi/round_<N>_codex.json
                       │  2) cross-family (INV-1) ─ skip 不能
                       ▼
        scripts/magi_xfamily_claude.sh
          flock(2) / prompt は stdin / schema は inline 展開
          claude -p --json-schema "$(cat …)" --permission-mode dontAsk
                 --disallowedTools Edit Write NotebookEdit
          ├─ round_<N+1>_xfamily.json          findings
          ├─ round_<N+1>_xfamily.meta.json     envelope + {artifact_sha,output_sha,model_id,session_id,num_turns}
          └─ round_<N+1>_xfamily.FAILED.json   失敗時 (成功 path とは別名)
                       │  3) plateau は model が宣言しない
                       ▼
        scripts/magi_plateau_gate.sh   ← 唯一の marker 作者 (INV-2)
```

state dir = `${doc_dir}/.dual-magi/` (`.gitignore` に `docs/**/.dual-magi/` 既存)。
round file は family で名前空間を分ける (`_codex` / `_xfamily`)。v0.1 の「schema 同一なので共有可」は撤回 (§5)。

---

## 3. 成果物

```
plugins/harness-magi-codex/
├── README.md                        # version 0.1.0-codex / install / uninstall / cost / 脅威モデル
├── install-codex-skills.sh          # symlink (formation 先例)。--copy で rsync fallback
├── uninstall-codex-skills.sh
├── schemas/finding.schema.json      # SSOT。codex は --output-schema <file>、claude は中身を inline 展開
├── scripts/
│   ├── magi_fanout_codex.sh         # 同族 N persona を codex exec で並列起動
│   ├── magi_xfamily_claude.sh       # cross-family adapter
│   ├── magi_plateau_gate.sh         # INV-2 の構造 rail
│   ├── magi_lock.sh                 # flock(2) helper (獲得 / exit 3 / 単一 cleanup。§4.1)
│   └── magi_scrub.py                # DSN/secret 形状 scrub (INV-5)
├── tests/
│   ├── test_docs_match_scripts.py   # anti-drift 契約テスト
│   ├── test_inv6_readonly.sh        # P-c 再現
│   ├── test_inv7_lock.sh            # 未取得/保持中 + 並行 + SIGKILL 自動解放 + 子孫拒否
│   ├── test_json_schema_inline.sh   # r2-xfamily-4 回帰: @file は失敗し inline は成功する
│   └── test_plateau_gate.sh         # 偽装 / stale / UNPARSEABLE を弾く
└── skills/
    ├── dual-magi-review/SKILL.md
    └── ultramagi/SKILL.md
```

- **`magi` / `bug-hunt` の独立 SKILL.md は作らない** (r1-caspar-6)。`magi_fanout_codex.sh --persona-set {magi,bug-hunt}` の引数で足りる。
- **persona template を複製しない** (r1-caspar-2)。canonical な `plugins/harness-magi/skills/{magi,bug-hunt}/templates/*.md` を参照。harness-kimi の複製は既に drift 済という実証がある。
- **install は symlink** (r1-caspar-7)。`~/.codex/skills/formation` が live symlink である実測に従う。

### 3.1 SKILL.md 書換 delta

| 原典 | Codex 版 |
|---|---|
| `allowed-tools: [Task, …]` | frontmatter から削除 (Codex は name + description のみ required) |
| 「3 つの `Task` tool call」 | `scripts/magi_fanout_codex.sh <doc> <round> --persona-set magi` |
| `--external` default = `codex-exec` | **`claude-headless`** ← INV-1 の核心 |
| plateau 判定を model が prose で行う | **「plateau は `magi_plateau_gate.sh` が書く。あなた (model) は宣言できない」** |
| ultramagi gate [4] = Workflow `parallel()` | `magi_fanout_codex.sh --persona-set bug-hunt` |
| `codex-mailbox` adapter | 削除 |

### 3.2 fan-out script の契約 (INV-3)

prompt を組むのは script だけ (model が場当たりに組むと逐次汚染が静かに起きる)。出力を 1 つも読む前に 3 process を全起動。起動時に同 round の sibling 出力が既存なら exit 非 0。

---

## 4. cross-family adapter

### 4.1 呼出 (round 2/3 適用済)

v0.3 は `mkdir` + pid file + stale 回収を手書きし、round 3 に「まだ racy」と正しく指摘された。**手書き lock protocol を捨て、`flock(2)` に委譲する。**

```bash
set -euo pipefail
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # schema を script 位置基準で解決 (CWD 非依存)
SCHEMA_JSON="$(cat "$SELF_DIR/../schemas/finding.schema.json")"

# --- 単一 cleanup / 単一 EXIT trap (2 個目の trap は 1 個目を置換する。実測済) ---
PROMPT_FILE=""
_cleanup() { [ -n "$PROMPT_FILE" ] && rm -f "$PROMPT_FILE"; }
trap _cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM      # signal は EXIT trap を経由するので cleanup は一度だけ走る

# --- INV-7: flock。fd 保持者の死 (SIGKILL 含む) で kernel が自動解放する ---
LOCK_FILE="${STATE_DIR}/.xfamily.lock"
exec 9>"$LOCK_FILE"
flock -n 9 || exit 3      # 保持者が生存 = 再帰 or 同一 doc の並行 review

PROMPT_FILE="$(mktemp)"                                   # 0600
build_prompt > "$PROMPT_FILE"

timeout "${MAGI_XFAMILY_TIMEOUT_S:-900}" \
  claude -p \
    --output-format json \
    --json-schema "$SCHEMA_JSON" \
    --model "${MAGI_XFAMILY_MODEL:-claude-fable-5}" \
    --permission-mode dontAsk \
    --allowedTools 'Read' 'Grep' 'Glob' 'Bash(grep:*)' 'Bash(rg:*)' 'Bash(git log:*)' 'Bash(git show:*)' \
    --disallowedTools 'Edit' 'Write' 'NotebookEdit' \
    < "$PROMPT_FILE"                                      # ← stdin。argv に doc を載せない
```

`flock` が消す欠陥 (全て実測):

| v0.3 の欠陥 | flock ではどうなるか |
|---|---|
| `[ -e f ] && exit; touch f` の TOCTOU | 原子。`flock -n` の獲得は不可分 |
| SIGKILL 後の stale lock | **kernel が fd close 時に自動解放** (実測: holder を `kill -9` 後に即再獲得できた) |
| stale 回収の rm -rf 競合 / pid 再利用 | stale 回収機構が**存在しない**ので競合しない |
| `rmdir` が pid file のせいで失敗し lock が漏れる | lock file を消さない (flock は file の存在ではなく fd の lock 状態が本体) |
| 並行 review が 2 本とも通る | 実測: contender は exit 1 で拒否された |
| 再帰した子が lock をすり抜ける | 実測: 自分の fd を開いた子孫は拒否された |

`--json-schema` は **inline JSON のみ**。`@file` は `Unrecognized token '@'` で exit 1 (実測)。よって schema は `$(cat ...)` で展開して渡す。

> 既知の穴 (明記): 子 process は **fd 9 を継承する**ため、継承した fd に対して `flock` すれば通ってしまう。reviewer が `Bash(codex:*)` を持たない (§4.4) ことが実質の rail。`flock --close` / `FD_CLOEXEC` の適用は実装時に検討する。

### 4.2 INV-5 — round 1 で発見された実在の漏洩経路

v0.1 は allowlist に `Bash(psql:*)` を置き、かつ「実行 command を逐語で記録せよ」と要求していた。合わせると `psql 'postgres://user:PASSWORD@host/db'` が findings json に永続化され、**その findings が次 round の prompt に混ぜられて別 vendor の API に再送信される** (r1-balthasar-3)。credential leak 9 件の環境で、INV-5 自身が漏洩経路を作っていた。

1. **allowlist に DB tool を一切入れない。** v0.3 は `Bash(magi-pg-ro:*)` wrapper を opt-in で許すとしたが、round 3 の指摘どおり wrapper 自身の argv / 継承環境 / core dump / stderr が未規定であり、「INV-5 を満たす」と称せる状態に無い (r3-xfamily-3)。**規定できない機構を INV-5 の根拠に数えない。** v1 では **DB grounding を持つ doc の cross-family review は scope 外**とし、SKILL.md にそう書く。wrapper は仕様が固まってから別 PR で足す (§10)。
2. `magi_scrub.py` が書込前に `://[^@]*@` / `PGPASSWORD=` / api-key 形状を伏字化。**findings と meta の両方**。DB tool が無くても、doc 本文や reviewer の引用に credential 形状が紛れる経路は残るため、scrub は維持する。
3. prior findings を次 round prompt に組む直前に再 scrub (多層防御)。
4. prompt は stdin。argv に載せると `/proc/<pid>/cmdline` から最大 900 秒間 全 local process に読める。

### 4.3 INV-1 / INV-2 — 「照合」であって「偽造耐性」ではない

adapter は envelope 全体 + 以下を `round_<N>_xfamily.meta.json` に束ねて保存する:

`{session_id, model_id (modelUsage key), num_turns, artifact_sha, output_sha, prompt_sha, started_at, finished_at}`

`magi_plateau_gate.sh` は marker を書く前に、**全て機械的に検査可能な**以下を assert:

| # | assert | 何を弾くか |
|---|---|---|
| G1 | `round_<N>_xfamily.json` 実在、`verdict ∈ {GO, GO-WITH-REVISE, REVISE, REJECT}` | cross-family round の欠落。`UNPARSEABLE` は enum 外なので通らない |
| G2 | `meta.model_id` が `^claude-` に match し、orchestrator family と異なる | 同族 round を cross-family と偽ること (T1) |
| G3 | `meta.artifact_sha` == **今 plateau 宣言しようとしている doc の実 sha** | **stale round の使い回し** (別 version / 別 doc の xfamily 結果を流用) |
| G4 | `meta.output_sha` == findings json の実 sha | round 後の findings 差し替え |
| G5 | `meta.num_turns >= 1`、かつ `num_turns <= 1` なのに `verify_commands_executed` 非空 なら FAIL | 走っていない round の自己矛盾 |
| G6 | `meta.session_id` が `~/.claude/projects/<cwd-slug>/<session_id>.jsonl` に実在 | adapter を通さず手書きされた meta |
| G7 | verdict が REJECT **または REVISE** なら marker を書かない | 「REJECT だったが ship した」「REVISE のまま plateau を名乗った」 |
| G8 | findings に severity REJECT / CRITICAL が 1 つでもあれば書かない | **`GO-WITH-REVISE` が CRITICAL を抱えたまま plateau になる** (= 実装 bug-hunt で発見。verdict field だけを見るのでは不足) |
| G9 | `schema_grounding_verdict == FAIL` なら書かない。加えて command を報告しているのに transcript に tool_use が 1 件も無ければ書かない | 「prose だけ読んだ round」「command を捏造した round」 |

> G8 / G9 は **実装後の bug-hunt が発見した欠陥への対応** (= design v0.5 時点では G1-G7 のみだった)。
> gate が verdict field しか見ていなかったため、CRITICAL を抱えた `REVISE` に plateau を与えていた。
> G9 の transcript 照合は「command を報告したのに tool を 1 つも使っていない」という **非 flaky な不整合**
> だけを見る。command 単位の完全一致は paraphrase で誤検知するので採らない (= §5.1 の立場そのまま)。

失敗 sentinel は `*_xfamily.FAILED.json` という**別名**に書く (成功 path の名前に失敗を書くと、「file が在る」を見る下流 check が fail-closed を fail-open に戻す — r1-balthasar-9)。

marker を書けるのは gate script だけ。model は書けない。これで INV-2 が behavioral から構造 rail に上がる。

**`prompt_sha` は meta に記録するが gate 条件にしない** (r3-xfamily-1)。v0.3 は「transcript 内の prompt hash と照合する」と書いたが、**実 transcript にそんな field は無い** (実測: line type は `queue-operation / attachment / user / assistant / last-prompt`、key は `promptId` / `lastPrompt` / `message`。prompt の *text* はあるが *hash* は無い)。記録済 text を再 hash する形に落とすには canonicalization 規約と「stdin bytes と byte 同一」の証明が要り、どちらも今は無い。**証明できない照合を gate に入れない。** audit 用に保存するだけとし、G1-G7 だけで T1 を検出する。

**この照合が防ぐのは T1 だけである** (§0.1)。同 UID の敵対 process は全 artifact を書ける。`~/.claude/projects/` は同 UID 所有の普通のディレクトリであり、暗号学的束縛は無い。**README・SKILL.md に「forgery-resistant」と書かない。** T2 を防ぐなら orchestrator の書込権限外にある署名 attestation が必要で、本 PR の scope 外。

### 4.4 INV-7 — recursion + 並行

env は使えない (§1.2)。使うのは:
1. **`flock(2)`** (§4.1 が唯一の規範。`exec 9>"$LOCK_FILE"; flock -n 9 || exit 3`)。pid file も `kill -0` も stale 回収も**書かない** —— kernel が fd close 時に解放するので、それらは不要かつ有害 (v0.3 で racy と判定された)。
2. reviewer の allowlist に `Bash(codex:*)` / `Bash(claude:*)` を入れない。v0.1 では偶然の rail だったので **名前を付けてテストする** (子が codex 起動を試み、envelope の `permission_denials` が非空)。これは fd 9 継承の穴 (§4.1 末尾) に対する実質の rail でもある。
3. exit 3 の意味を明示: 「cross-family record ではない」。plateau は blocked のまま。exit 3 を「意図的 skip、続行してよい」と解釈すると guard 自体が INV-2 の迂回路になる。
4. lock は doc 単位。**同一 doc の並行 review は 2 本目が exit 3** で止まる (これは仕様。別 doc は別 lock なので並行可)。

### 4.5 INV-6 — read-only

`--permission-mode dontAsk` + 明示 `--disallowedTools Edit Write NotebookEdit` (P-c 実測)。`acceptEdits` は使わない (P-b: allowlist を上書きして書き込む)。
backstop: 子 process 前後で `.dual-magi/` 配下 round json の hash 比較 (gitignore された state dir は `git status` に映らず、prior round の verdict 改竄を検出できない — r1-balthasar-6)。

---

## 5. finding schema — 単一 SSOT

`schemas/finding.schema.json` が唯一の定義。SKILL.md の prose は再掲せず参照する (再掲は drift 源)。codex には `--output-schema <file>`、claude には**中身を inline 展開**して渡す。

原典 prose schema との既知差分: severity に `CRITICAL` 追加、per-finding `verdict` 廃止 (round 単位に集約)。よって v0.1 の「完全同一なので `.dual-magi/` を共有でき真の双方向」は撤回 (r1-caspar-1)。原典との統一は §7。

### 5.1 INV-4 — grounding は adapter が transcript から導出する

P-a (constrained decoding が required field を捏造する) により、`verify_commands_executed` の self-report は「走らせていない reviewer がもっともらしい command 文字列を吐く」ことを構造的に許す。

v0.2 は「transcript から導出すれば解決」と書いた。**これは穴を塞がず移動させただけ** (r2-xfamily-2): transcript 照合が証明するのは「その command が session 中に発生した」ことだけで、`rg` を 1 回撃って findings 20 個を捏造した reviewer は通過する。

v0.3 はそこで「finding 毎に `tool_event_id` を引用させ、load-bearing claim の被覆を gate する」と書いた。round 3 はこれを **2 つとも実装不能**と判定し、正しい:

- **`tool_event_id` を reviewer に書かせられない** (r3-xfamily-2)。transcript には `tool_use.id` / `tool_result.tool_use_id` があり **事後の相関は取れる**が、reviewer が自分の server 採番 id を `structured_output` の中に再現できる保証は無い。`@file` と同じ「未検証インタフェースへの依存」。
- **「load-bearing claim の被覆」を機械判定する手順が無い** (r2-xfamily-2)。claim を doc から抽出するのが model なら、self-report 問題に戻るだけ。v0.3 §10 が自分でそう認めていた。

**v0.4: 削る。** reviewer に id を書かせない。gate に被覆判定を入れない。

- reviewer が emit するのは従来どおり `verify_commands_executed` (command 文字列) のみ
- **adapter が run 後に transcript から** `tool_use` event を抽出し、実行 command 集合を**自分で**構成する (reviewer の協力不要)
- 判定は 2 つだけ、どちらも決定的:
  - `verify_commands_executed ⊄ transcript 由来集合` (= 走らせていない command を報告した) → `schema_grounding_verdict: FAIL`、round degraded
  - `num_turns <= 1` かつ `verify_commands_executed` 非空 → 自己矛盾 → FAIL
- **明示する**: これが検出するのは **欠落と不整合**だけであって、**意味的な真偽ではない**。「`rg` を 1 回撃って findings を捏造した reviewer」は**通過する**。それは本設計では検出できない (§9 の最大残存リスク)。判断の正しさは人間 or 次 round の責務。

`evidence` の per-finding 束縛は **v2 以降の課題**として §10 に残す。実装できる形 (adapter が事後に紐付け、reviewer は `claim_id` と command 文字列だけ書く) の PoC が取れてから schema に入れる。**取れる前に schema に書かない。**

---

## 6. fail-open vs fail-closed

原典 `codex-exec` adapter は「retry 1 → 失敗継続なら fail-open (internal-only 継続)」。本移植は **fail-closed**。

- 原典の fail-open は cross-family が opt-in だった v0.5 以前の名残。v0.6.0 で cross-family は plateau の**必要条件**になった。必要条件が落ちたのに処理継続するのは INV-2 違反。
- gh #195 の root cause は「AI が flag を忘れて skip」= **skip が静かにできる構造**。fail-open はそれを温存する。
- コスト = 「claude が落ちていると review が止まる」。許容 (原典 § When NOT to invoke: 「time-critical hotfix → this takes hours」)。

`--no-cross-family <reason>` の明示 opt-out は残すが、**gate は marker を書かない**ので opt-out した review は plateau を名乗れない。

---

## 7. 原典の修理 — round 2 で scope に編入

r2-xfamily-5: 原典 `plugins/harness-magi/skills/dual-magi-review/SKILL.md` は「cross-family は mandatory」と宣言しながら、その adapter は **exit 非 0 で fail-open、timeout で fail-open、parse 失敗は warning 保存**、と書いてある (lines 397-399, 550)。つまり **現に使われている canonical な向き (Claude 主) が、この doc が enforce すると謳う不変条件を破れる**。鏡像だけ厳格にして原典を放置するのは筋が通らない。

さらに codex 側 provenance は**実在する** (`~/.codex/sessions/**/rollout-*.jsonl` に `session_meta` + `turn_context.model`、380 files 実測)。「future work」とした v0.2 の判断は技術的に不当。

したがって本 PR の scope に以下を編入する:

1. 原典 `codex-exec` adapter の error handling を **fail-closed** に変更 (fail-open 3 経路)
2. 原典にも `magi_plateau_gate.sh` 相当を導入し、codex rollout provenance (`session_meta.id` / `turn_context.model`) を artifact / output に束ねて照合
3. それまでの間、原典の plateau 宣言能力に「provable cross-family round 無し」の警告を明記

### 7.1 移行手順 (r2-xfamily-5: 「今日 fail-open な plugin を即 fail-closed にすると既存 workflow が止まる」)

原典を無条件に fail-closed 化すると、`codex` 未 install / 未 login の環境で既存の review が **その場で止まる**。段階を切る:

| stage | 挙動 | plateau marker |
|---|---|---|
| **S0 (現状)** | cross-family 失敗 → fail-open、internal-only で継続 | model が prose で宣言 (= 実質無制限) |
| **S1 (本 PR)** | 起動時に prereq を検査 (`command -v codex`、`codex` の auth 状態)。欠けていれば **早期に警告して継続** | **gate script が marker を書かない**。その review は plateau を名乗れない。既存 workflow は止まらない |
| **S2 (次 release、bounded grace)** | S1 に加え、cross-family 失敗を **warning から error に格上げ**。`--no-cross-family <reason>` を明示した場合のみ継続 | 同上 (opt-out でも marker は出ない) |
| **S3** | cross-family 失敗 = **fail-closed** (exit 非 0) | 同上 |

要点: **「止める」より先に「plateau を名乗らせない」**。marker を出さないだけなら既存 workflow は動き続け、しかし「cross-family 済」と誤認されることは無くなる。行動を壊さずに不変条件を回復できる。既存 `.dual-magi/` state file と旧 invocation 形式に対する upgrade test を P9 として追加する。

| 項目 | 原典 (現状) | 本 plugin | 解消 |
|---|---|---|---|
| adapter 失敗時 | fail-open | fail-closed | **本 PR で原典も fail-closed** |
| severity enum | CRITICAL 無し | 有り | `schemas/finding.schema.json` を両 plugin の SSOT に昇格 |
| plateau 宣言 | model が prose 判断 | gate script | **本 PR で原典にも gate** |
| persona template | canonical | 参照 (複製しない) | 本 PR |
| harness-kimi の複製 template | drift 済 | — | follow-up issue |

---

## 8. 検証 — 全て commit された test にする

v0.1/v0.2 の P1-P5 は「一度実走して確かめる」手動 probe で、release 後に何も残らなかった。repo は `b37ded8` で doc-vs-code 契約テストを入れたばかりであり、新 plugin がその rail の外で生まれるのは許容しない (r1-caspar-3)。

| phase | 常設テスト |
|---|---|
| P1 | 本 doc を対象に 3 persona fan-out → 3 json が schema 通り。sibling 出力既存なら exit 非 0 (INV-3) |
| P2 | `test_inv6_readonly.sh` = P-c 再現 (write 拒否)。scrub の DSN 伏字化 unit test |
| P3 | `test_json_schema_inline.sh` = **回帰**: `@file` 形式は exit 非 0、inline 展開は `structured_output` を返す |
| P4 | `test_inv7_lock.sh` = 未取得→続行 / 保持中→exit 3 / **並行 2 本で 1 本だけ通る** / SIGKILL された holder の lock が自動解放される / 自分の fd を開いた子孫が拒否される / 正常終了で prompt file が消える |
| P5 | `test_plateau_gate.sh` = G1-G7 を個別に落とす: 同族 model_id、artifact_sha 不一致 (stale round 流用)、output_sha 不一致 (findings 差し替え)、`session_id` が transcript に無い、`UNPARSEABLE` verdict、REJECT verdict、をそれぞれ弾く |
| P6 | `test_docs_match_scripts.py` = SKILL.md/README が列挙する flag・env (`MAGI_XFAMILY_{MODEL,TIMEOUT_S}`)・exit code (2=fail-closed, 3=lock) が実装と一致 (`harness-formation/tests/test_docs_match_dispatch.py` の作法) |
| P7 | install → `~/.codex/skills/` に 2 skill symlink、冪等、uninstall で消える |
| P8 | e2e 自己適用: Codex session から本 doc を review させる |
| P9 | 移行 (§7.1): `codex` 不在環境で S1 が **止まらず、かつ marker を書かない**。既存 `.dual-magi/` state file を読める |

---

## 9. 残存リスク (正直に)

| risk | 影響 | 立場 |
|---|---|---|
| **reviewer が `rg` を 1 回撃って findings を捏造する** | verdict の意味が失われる | **検出しない**。§5.1 が検出するのは欠落と不整合だけ。**本設計最大の残存リスク**であり、これを塞ぐ機構は現状の CLI インタフェースでは実装できない (§10-1) |
| **同 UID 敵対 process による artifact 偽造 (T2)** | INV-1/INV-2 が完全に破れる | **防がない** (§0.1)。防ぐには orchestrator 書込権限外の署名 attestation |
| 子 process が fd 9 を継承し flock をすり抜ける | INV-7 が破れる | 実質の rail は allowlist に `Bash(codex:*)` が無いこと (§4.4)。`flock --close` / `FD_CLOEXEC` は実装時検討 |
| `codex exec` の hook は trust 状態で発火が変わる | trust 永続化した途端 `bash_command_guard` が reviewer probe を拒否、`sanada_autobackup` が Bash 毎に最大 12 秒 × 3 並列 | P1 で hook 有無の両方を assert |
| `claude -p` envelope / transcript path 規約が将来変わる | G6 が死ぬ | fail-closed なので **静かには壊れない** (gate が exit 非 0)。P5 で key 存在を assert |
| transcript path 規約が公開契約か実装詳細か不明 | 同上 | 実装詳細と仮定。破れたら gate が落ちる = 検知可能 |
| DB grounding を持つ doc は cross-family review できない | 適用範囲が狭い | v1 の意図的制約 (§4.2)。wrapper 仕様が固まるまで広げない |
| cross-family で doc 全文が別 vendor に渡る | 情報流出 | INV-5。対象 doc に secret が無いことは呼出側責任 |
| 3 process 並列 × doc 全文 | token 3 倍 | 原典と同等。README に cost 節 |

## 10. 未解決 (v2 以降)

1. **finding ↔ evidence の束縛** (§5.1)。実装できる形は「reviewer は `claim_id` + command 文字列だけ書き、adapter が run 後に transcript の `tool_use.id` と紐付ける」だと思われる。**PoC が取れてから schema に入れる。取れる前に書かない** (v0.3 の失敗の再発防止)。「load-bearing claim の被覆」を model 以外の何が抽出するのかは未解決のまま。
2. `magi-pg-ro` wrapper の完全仕様 (argv / 継承環境 / core dump 無効化 / stderr scrub / 失敗経路)。固まるまで DB grounding は scope 外 (§4.2)。
3. plateau gate が「orchestrator family と異なる」を判定するには orchestrator 自身の family を知る必要がある。T1 前提なら gate script の引数 / env で十分だが、誰が渡すのかを実装時に明示する。
4. lock を doc 単位にしたので、同一 doc の並行 review は 2 本目が exit 3。これは「再帰」と区別できない。exit 3 (recursion) と exit 4 (concurrent) を分けるべきか。
5. T2 (署名 attestation) を導入するとしたら、鍵はどこに置くのか。同 UID から読めない鍵は、この single-user box では OS 機構 (TPM / keyring + polkit) を要する。
