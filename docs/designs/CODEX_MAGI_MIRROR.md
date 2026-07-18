# CODEX_MAGI_MIRROR — Codex を主にした dual-magi / ultramagi 移植設計

status: v0.7 (v0.5 plateau後、v0.6 G8/G9、v0.7 explicit Grok fallbackを追加。各revisionはexact SHAで再gateする)
author: claude-ember-crane
date: 2026-07-08
scope: `plugins/harness-magi-codex/` 新設 + 原典へのone-line G1-G9 gate pointer（原典S1b-S3修理はbacklog）
related: gh #195
review state: review artifacts under `.dual-magi/*` が記録。current SHAは変更ごとにClaude gateを再実行する

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
| **v0.7** | **`magi_xfamily.sh --reviewer claude|grok`、provider別provenance/G2/G6/G9、Grok read-only tool allowlist + sandbox fallback** | Grok provider E2E round 1 |

> **注**: 旧campaign markerはround 10で除去した。現行gateは
> `<doc-dir>/.dual-magi/PLATEAU.<doc-id>.<artifact-sha-prefix>`だけをcanonicalとし、同docの
> 旧revision markerをgrant/denyのたびにrevokeする。docを1 byteでも編集したら再gateが必要。

---

## 0. SCOPE + 脅威モデル + 不変条件

**やること**: dual-magi-review / ultramagi を、**Codex CLIがorchestrator、Claudeがpreferred cross-family、Grokが明示fallback**という構成で`~/.codex/skills/`に移植する。Codex側はfail-closed。原典Claude側の完全なS1移行は§7のbacklogであり、本releaseの原典変更はone-line gate pointerだけとする。

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
| **INV-1** | cross-family round が走ったことを artifact から**機械的に照合**できる | `*.meta.json` に provider + `{artifact_sha, output_sha, model_id, requested_model, session_id, num_turns, transcript_path, transcript_sha}` を保存。gate が G1-G9 を検査 (§4.3) | T1 のみ |
| **INV-2** | cross-family record 無しに `plateau` を宣言できない | **`magi_plateau_gate.sh` だけが plateau marker を書く**。model は書けない | T1 |
| **INV-3** | 同族 3 視点は互いの findings を見ない | fan-out script が唯一の prompt 作者。出力読取前に 3 process 全起動。sibling 出力既存なら exit 非 0 | T1 |
| **INV-4** | grounding self-reportだけでplateauさせない | gateがcommand list非空かつprovider transcriptのtool use非零を独立assert (§5.1/G9) | T1（明白な未grounded/自己矛盾のみ）。command包含や内容真偽は検出しない |
| **INV-5** | credential content を prompt argv / findings に載せない | allowlist に **DB tool を入れない**、Claudeはstdin・Grokは`--prompt-file`の0600 temp（argvはpathのみ）、単一cleanup trap、credential形状を書込前 scrub (§4.2) | T1 |
| **INV-6** | review round は read-only | Claude=built-in surfaceを`Read,Grep,Glob`へ構造限定（write/edit/shell/Agentは`not enabled in this context`でsurface不在、`--safe-mode --strict-mcp-config`でambient MCP/custom agentもロードしない）+ `dontAsk`/write deny を多層防御に併置。Grok=read-only built-ins allowlist + `search_tool,use_tool,Agent`除去 + `MCPTool` deny + OS read-only sandbox（provider別live probe、§4.5/P-c,P-h） | T1 |
| **INV-7** | cross-family の中で cross-family を呼ばない | **`flock(2)`** + Claude=`--tools Read,Grep,Glob`/Agent・Task・Bash deny/safe-mode、Grok=`--no-subagents`/Agent除去 (§4.4) | T1 |

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

### 1.2 v0.3 が依存する実測（2026-07-08 snapshot。件数/versionはcurrent truthではない）

```
codex --version                                → codex-cli 0.142.5
codex features list                            → enable_fanout=false / multi_agent=stable,true
claude --help (2026-07-18)                     → --json-schema / --disallowedTools / --permission-mode{...,dontAsk,...} / --safe-mode / --strict-mcp-config / --tools <name,name>
  ↳ --tools は comma 連結 1 値で built-in surface を限定 (`""`=全 disable)。--allowedTools/--disallowedTools は space 区切り。この convention 混在は実測済で意図的 (P-h が構造 restrict を live 確認)
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
| P-c | 同上を `--permission-mode dontAsk --disallowedTools 'Edit' 'Write' 'NotebookEdit'` で | **未作成**、model は `BLOCKED` 応答 | permission-layer rail (v0.5)。現行は P-h の構造 restrict で置換 |
| P-d | `claude -p --output-format json` の envelope | `modelUsage` (exact model id で keyed)、`session_id`、`num_turns`、`permission_denials` | INV-1 の材料。ただし T1 用 |
| P-e | Grok adapter railでrepository配下にfile作成を指示 | **未作成**。`--tools` allowlistにshell/edit/write toolなし + OS read-only | Grok INV-6 の二重rail |
| P-f | real `codex exec -o <mkfifo>` + output schema (2026-07-18 live) | **structured JSONがFIFOを通過** | FIFO interfaceを実測。scrub-before-durableはstub regressionで別assert |
| P-g | Grok exact adapter railで`search_tool`/`use_tool`呼出を指示 (2026-07-18 live) | **BLOCKED応答 + transcript tool_calls 0** | MCP meta-toolの非呼出を実測。offered-tool metadata不在のためsurface不在そのものは証明しない |
| P-h | Claude exact adapter rail (`--safe-mode --strict-mcp-config --tools 'Read,Grep,Glob'`) でWrite/Agent/Bash呼出を指示 (2026-07-18 live) | **file未作成 + tool_resultは`No such tool available: Write. Write exists but is not enabled in this context` + `permission_denials`空** | write/edit/shellはsurface不在（構造 restrict）であり、`dontAsk`のpermission denialに依存しないことを実測。error文字列がenabled toolset由来なので不在を反証可能 |
| P-i | real opus adapter round で `--model claude-opus-4-8` 指定、transcript `message.model` と envelope `modelUsage` を突合 (2026-07-18 live, r13-r15) | **transcript全assistant turnの`message.model`=`claude-opus-4-8`。`modelUsage`は`{haiku, opus}`両方をbill (haikuはutility turn)** | `message.model`は per-response の model field で、billing集合とも`--model`文字列echoとも異なる独立記録 = G6(ii)の非循環前提を happy-path で支持。**限界**: served≠requested の server-side downgrade は強制不能で未実測。よってG6(ii)は「transcriptが記録した served model」までを検査対象とclaimし、それ以上(真の served の暗号的束縛)はT2 scope外 |

### 1.3 非対称

| 軸 | Codex | Claude | Grok fallback |
|---|---|---|---|
| 構造化出力 | `--output-schema <file>` | `--json-schema '<inline JSON>'` | inline schema → `structuredOutput` |
| 並列 subagent | shell fan-out | `Task` | adapterは`--no-subagents`固定 |
| read-only rail | reviewer prompt/tool allowlist | `--tools Read,Grep,Glob`で構造restrict + `--safe-mode --strict-mcp-config` (MCP/agent不在) + `dontAsk`/write denyを多層防御 (§4.5/P-h) | read-only allowlist + `search_tool,use_tool,Agent`除去 + `MCPTool` deny + OS sandbox (§4.5) |
| provenance | Codex rollout JSONL | Claude projects JSONL + envelope | Grok sessions `chat_history.jsonl` + envelope |

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
        scripts/magi_xfamily.sh --reviewer claude|grok
          flock(2) / prompt は file/stdin / schema は inline 展開
          Claude: claude -p --json-schema "$(cat …)" --permission-mode dontAsk
          Grok: grok --tools read_file,grep,list_dir --sandbox read-only
                 --disallowed-tools search_tool,use_tool,Agent --deny MCPTool
                 --no-subagents --no-memory --disable-web-search
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
│   ├── magi_xfamily.sh              # provider-selectable cross-family adapter
│   ├── magi_xfamily_claude.sh       # backward-compatible Claude wrapper
│   ├── magi_plateau_gate.sh         # INV-2 の構造 rail
│   ├── magi_lock.sh                 # flock(2) helper (獲得 / exit 3 / 単一 cleanup。§4.1)
│   └── magi_scrub.py                # DSN/secret 形状 scrub (INV-5)
├── tests/
│   ├── test_docs_match_scripts.py   # anti-drift 契約テスト
│   ├── test_fanout_scrub.sh         # FIFO pre-write scrub + 3 persona / sibling rail
│   ├── test_inv6_readonly.sh        # provider選択live read-only + inline schema/@file回帰
│   ├── test_inv7_lock.sh            # 未取得/保持中 + 並行 + SIGKILL 自動解放 + 子孫拒否
│   ├── test_plateau_gate.sh         # 偽装 / stale / UNPARSEABLE / G9 branchesを弾く
│   ├── test_claude_provider.sh      # Claude default route + 構造rail argv / family取り違え / transcript drift
│   ├── test_grok_provider.sh        # Grok provenance / family取り違え / transcript drift
│   └── test_stale_round_failclosed.sh # failed rerun後のstale successを弾く
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
DOC_REAL="$(realpath "$DOC_PATH")"
DOC_LOCK_ID="$(printf '%s' "$DOC_REAL" | sha256sum | cut -c1-16)"
LOCK_FILE="$(dirname "$DOC_REAL")/.dual-magi/.xfamily.${DOC_LOCK_ID}.lock"
exec 9>"$LOCK_FILE"
flock -n 9 || exit 3      # 保持者が生存 = 再帰 or 同一 doc の並行 review

PROMPT_FILE="$(mktemp)"                                   # 0600
build_prompt > "$PROMPT_FILE"

case "$REVIEWER" in
claude)
timeout "${MAGI_XFAMILY_TIMEOUT_S:-900}" \
  claude -p \
    --output-format json \
    --json-schema "$SCHEMA_JSON" \
    --model "${MAGI_XFAMILY_CLAUDE_MODEL:-${MAGI_XFAMILY_MODEL:-claude-fable-5}}" \
    --safe-mode --strict-mcp-config --tools 'Read,Grep,Glob' \
    --permission-mode dontAsk \
    --allowedTools 'Read' 'Grep' 'Glob' \
    --disallowedTools 'Agent' 'Task' 'Edit' 'Write' 'NotebookEdit' 'Bash' \
    < "$PROMPT_FILE"                                      # ← stdin。argv に doc を載せない
  ;;

# Claude quota/capacity fallback (explicit; never automatic):
grok)
timeout "${MAGI_XFAMILY_TIMEOUT_S:-900}" \
  grok --prompt-file "$PROMPT_FILE" --model "${MAGI_XFAMILY_GROK_MODEL:-grok-4.5}" \
       --cwd "$PWD" --effort high --max-turns 40 \
       --tools 'read_file,grep,list_dir' --sandbox read-only --no-subagents --no-memory \
       --disallowed-tools 'search_tool,use_tool,Agent' --deny 'MCPTool' \
       --disable-web-search --output-format json --json-schema "$SCHEMA_JSON"
  ;;
esac
```

`flock` が消す欠陥 (全て実測):

| v0.3 の欠陥 | flock ではどうなるか |
|---|---|
| `[ -e f ] && exit; touch f` の TOCTOU | 原子。`flock -n` の獲得は不可分 |
| SIGKILL 後の stale lock | **kernel が fd close 時に自動解放** (実測: holder を `kill -9` 後に即再獲得できた) |
| stale 回収の rm -rf 競合 / pid 再利用 | stale 回収機構が**存在しない**ので競合しない |
| `rmdir` が pid file のせいで失敗し lock が漏れる | lock file を消さない (flock は file の存在ではなく fd の lock 状態が本体) |
| 並行 review が 2 本とも通る | raw `flock -n`は1、adapter contractへmapしてexit 3で拒否 |
| 再帰した子が lock をすり抜ける | 実測: 自分の fd を開いた子孫は拒否された |

`--json-schema` は **inline JSON のみ**。`@file` は `Unrecognized token '@'` で exit 1 (実測)。よって schema は `$(cat ...)` で展開して渡す。

> 既知の穴 (明記): 子 process は **fd 9 を継承する**ため、継承した fd に対して `flock` すれば通ってしまう。reviewer が `Bash(codex:*)` を持たない (§4.4) ことが実質の rail。`flock --close` / `FD_CLOEXEC` の適用は実装時に検討する。

### 4.2 INV-5 — round 1 で発見された実在の漏洩経路

v0.1 は allowlist に `Bash(psql:*)` を置き、かつ「実行 command を逐語で記録せよ」と要求していた。合わせると `psql 'postgres://user:PASSWORD@host/db'` が findings json に永続化され、**その findings が次 round の prompt に混ぜられて別 vendor の API に再送信される** (r1-balthasar-3)。credential leak 9 件の環境で、INV-5 自身が漏洩経路を作っていた。

1. **allowlist に DB tool を一切入れない。** v0.3 は `Bash(magi-pg-ro:*)` wrapper を opt-in で許すとしたが、round 3 の指摘どおり wrapper 自身の argv / 継承環境 / core dump / stderr が未規定であり、「INV-5 を満たす」と称せる状態に無い (r3-xfamily-3)。**規定できない機構を INV-5 の根拠に数えない。** v1 では **DB grounding を持つ doc の cross-family review は scope 外**とし、SKILL.md にそう書く。wrapper は仕様が固まってから別 PR で足す (§10)。
2. `magi_scrub.py` が書込前に `://[^@]*@` / `PGPASSWORD=` / api-key 形状を伏字化。**findings と meta の両方**。DB tool が無くても、doc 本文や reviewer の引用に credential 形状が紛れる経路は残るため、scrub は維持する。
3. prior findings を次 round prompt に組む直前に再 scrub (多層防御)。
4. prompt contentはargvに載せない。Claudeはstdin、Grokは`--prompt-file`へ0600の`mktemp` pathを渡す。Grok argvに載るのはpathだけで、contentは載らない。

### 4.3 INV-1 / INV-2 — 「照合」であって「偽造耐性」ではない

adapter は envelope 全体 + 以下を `round_<N>_xfamily.meta.json` に束ねて保存する:

`{reviewer_family, session_id, model_id, requested_model, model_usage_keys, num_turns, artifact_sha, output_sha, transcript_path, transcript_sha, started_at, finished_at}`

`magi_plateau_gate.sh` は marker を書く前に、**全て機械的に検査可能な**以下を assert:

| # | assert | 何を弾くか |
|---|---|---|
| G1 | `round_<N>_xfamily.json` 実在、`verdict ∈ {GO, GO-WITH-REVISE, REVISE, REJECT}` | cross-family round の欠落。`UNPARSEABLE` は enum 外なので通らない |
| G2 | `--reviewer-family` と `meta.reviewer_family` が一致し、model_id/model keysがClaudeまたはGrokの選択familyに一致 | 同族roundまたはprovider label偽装 (T1) |
| G3 | `meta.artifact_sha` == **今 plateau 宣言しようとしている doc の実 sha** | **stale round の使い回し** (別 version / 別 doc の xfamily 結果を流用) |
| G4 | `meta.output_sha` == findings json の実 sha | round 後の findings 差し替え |
| G5 | `meta.num_turns >= 1`、かつ `num_turns <= 1` なのに `verify_commands_executed` 非空 なら FAIL | 走っていない round の自己矛盾 |
| G6 | session_id が選択providerの transcript (`~/.claude/projects/...` または `~/.grok/sessions/.../chat_history.jsonl`) に一意解決し、provider/model/SHAが一致。**model検査は両provider対称で2段**: transcript modelを (Claude=`message.model` / Grok=assistant `model_id`) から読み、(i) `meta.model_id`がtranscriptと整合する **consistency check** (model_idがresolved transcriptに不在なもの=hand-edit/stale/cross-round reuse を弾く。model_id自身がtranscriptのlast `message.model`由来なので、wrong-but-**present**なadapter mis-derivationは弾けない=derivationではなくreuseのpolice。双方向substringで managed prefix/alias 許容)、(ii) **transcriptとは独立にrecordした`meta.requested_model`が実走modelに含まれる (方向性: `requested ⊆ served`。alias/managed prefix 展開は許すが、`claude-opus-4` for `claude-opus-4-8` の truncation downgrade は弾く=r15-xfamily-1)**。(ii)はrequestedがtranscript由来でないので循環しない。ただし保証範囲は「transcriptが記録した served model」に scoped: `message.model`=API response の model field で、`--model` echo でなく response model を追跡することを実測 (§1.2 P-i)。echo する CLI では無効化される T1 signal であり T2 attestation ではない | adapterを通さないmeta、provider取り違え、silent same-family substitution / truncation downgrade (superstring variant は residual gap、上記note)、完了後transcript drift |
| G7 | verdict が REJECT **または REVISE** なら marker を書かない | 「REJECT だったが ship した」「REVISE のまま plateau を名乗った」 |
| G8 | findings に severity REJECT / CRITICAL / HIGH が 1 つでもあれば書かない | **`GO-WITH-REVISE` がblocking findingを抱えたまま plateau になる**。severity calibrationでinvariant driftを通さない |
| G9 | (1) grounding FAIL、(2) command list空、(3) command報告ありだがprovider transcriptのtool useゼロ、の各々を拒否 | 明白に未groundedなround / 自己矛盾（command単位包含はclaimしない） |

> G8 / G9 は **実装後の bug-hunt が発見した欠陥への対応** (= design v0.5 時点では G1-G7 のみだった)。
> gate が verdict field しか見ていなかったため、CRITICAL を抱えた `REVISE` に plateau を与えていた。
> G9 の transcript 照合は「command を報告したのに tool を 1 つも使っていない」という **非 flaky な不整合**
> だけを見る。command 単位の完全一致は paraphrase で誤検知するので採らない (= §5.1 の立場そのまま)。

> G6 の model provenance (両provider対称、r13-r16 で反復強化) が **正確に catch する範囲**: (a) wholesale
> substitution (requested が served id の部分文字列でない。opus→haiku) と (b) truncation downgrade
> (served ⊊ requested。`claude-opus-4` for `claude-opus-4-8`、g6e/grok trunc test で test-backed)。
> **residual T1 gap (正直に)**: served が requested の **superstring** となる cheaper variant
> (`claude-opus-4-8-lite`、`grok-4-fast`) は `requested ⊆ served` を満たし通過する。dated/patch snapshot
> (`…-20260101`) と文字列上区別できないため substring 比較では原理的に弾けない (g6f が behavior を pin)。
> 緩和は方向で異なる: full model id の pin は **truncation 方向のみ** を締める (superstring 方向では
> 例の operator は既に full id を pin 済でも `-lite` が通る=r17-xfamily-1)。superstring 方向には
> 純 string の緩和が無く、provider が requested と異なる cheaper variant を silently serve しない
> という T1 信頼に依る。真の served の暗号的束縛は T2 で scope 外 (§0.1)。

失敗 sentinel は `*_xfamily.FAILED.json` という**別名**に書く (成功 path の名前に失敗を書くと、「file が在る」を見る下流 check が fail-closed を fail-open に戻す — r1-balthasar-9)。

marker を書けるのは gate script だけ。model は書けない。markerもcampaign dirではなく
`<doc-dir>/.dual-magi/PLATEAU.<doc-id>.<artifact-sha-prefix>`へ集約し、grant/deny時に
同docの全旧revision markerをrevokeする。これで INV-2 が behavioral から構造 rail に上がる。

**prompt SHA を transcript と照合するgateは作らない** (r3-xfamily-1)。実 transcript にprompt hash fieldはなく、記録textの再hashにはcanonicalization規約が必要である。代わりにadapter完了時のtranscript bytesを両providerで`transcript_sha`へ束縛し、値がある場合はG6で後続driftを検出する（Grokは必須、Claudeもadapterが常に記録）。Claude reviewer sessionをresumeして追記する前にgateする。G1-G9はT1だけを対象とする。

**この照合が防ぐのは T1 だけである** (§0.1)。同 UID の敵対 process は全 artifact を書ける。`~/.claude/projects/` は同 UID 所有の普通のディレクトリであり、暗号学的束縛は無い。**README・SKILL.md に「forgery-resistant」と書かない。** T2 を防ぐなら orchestrator の書込権限外にある署名 attestation が必要で、本 PR の scope 外。

### 4.4 INV-7 — recursion + 並行

env は使えない (§1.2)。使うのは:
1. **`flock(2)`** (§4.1 が唯一の規範。`exec 9>"$LOCK_FILE"; flock -n 9 || exit 3`)。pid file も `kill -0` も stale 回収も**書かない** —— kernel が fd close 時に解放するので、それらは不要かつ有害 (v0.3 で racy と判定された)。
2. Claudeはbuilt-in surfaceを`Read,Grep,Glob`へ限定し、`Agent,Task,Bash`を明示denyする。`--safe-mode --strict-mcp-config`でcustom agent/ambient MCPもロードしない。Grokは`--no-subagents`+Agent除去。recursive adapter/子CLI起動はshell自体が無いため、fd 9継承穴へのrailにもなる。
3. exit 3 の意味を明示: 「cross-family record ではない」。plateau は blocked のまま。exit 3 を「意図的 skip、続行してよい」と解釈すると guard 自体が INV-2 の迂回路になる。
4. lock は doc 単位。**同一 doc の並行 review は 2 本目が exit 3** で止まる (これは仕様。別 doc は別 lock なので並行可)。

### 4.5 INV-6 — read-only

providerごとにrailを分ける。Claudeは`--safe-mode --strict-mcp-config`、built-in toolを
`Read,Grep,Glob`だけに限定し、`Agent,Task,Edit,Write,NotebookEdit,Bash`を明示denyする。
`--permission-mode dontAsk`を併用し、ambient MCP/custom agents/shellをsurfaceから外す。
`acceptEdits`は使わない。
Grokは`--tools read_file,grep,list_dir`を明示し、headless modeのdefault tool injectionを
停止する。さらに`--disallowed-tools search_tool,use_tool,Agent`と`--deny MCPTool`で
globally configured MCP serverへのmeta-tool経路を閉じる。shell/edit/write/MCP toolを
モデルへ渡さないことが第一railで、OS
`--sandbox read-only`を第二railに固定する。`--always-approve`は使わない。
`--no-subagents --no-memory --disable-web-search`も併用する。stub testはflag driftを、
`MAGI_TEST_REVIEWER=grok MAGI_TEST_LIVE=1 test_inv6_readonly.sh`はwrite拒否とMCP meta-tool
非呼出（指示にBLOCKED応答、transcript tool_calls 0）を実測する。offered-tool surfaceの
metadataはCLI出力に無いため、不在そのものを証明するとはclaimしない。
adapter自身がstate dirへfindings/metaを書くのはprotocol outputであり、reviewer processの
workspace mutationとは分離する。prior-round全fileの前後hash比較は未実装なのでrailとしてclaimしない。

---

## 5. finding schema — 単一 SSOT

`schemas/finding.schema.json` が唯一の定義。SKILL.md の prose は再掲せず参照する (再掲は drift 源)。codex には `--output-schema <file>`、claude には**中身を inline 展開**して渡す。

原典 prose schema との既知差分: severity に `CRITICAL` 追加、per-finding `verdict` 廃止 (round 単位に集約)。よって v0.1 の「完全同一なので `.dual-magi/` を共有でき真の双方向」は撤回 (r1-caspar-1)。原典との統一は §7。

### 5.1 INV-4 — grounding はself-report単独では通さない

P-a (constrained decoding が required field を捏造する) により、`verify_commands_executed` の self-report は「走らせていない reviewer がもっともらしい command 文字列を吐く」ことを構造的に許す。

v0.2 は「transcript から導出すれば解決」と書いた。**これは穴を塞がず移動させただけ** (r2-xfamily-2): transcript 照合が証明するのは「その command が session 中に発生した」ことだけで、`rg` を 1 回撃って findings 20 個を捏造した reviewer は通過する。

v0.3 はそこで「finding 毎に `tool_event_id` を引用させ、load-bearing claim の被覆を gate する」と書いた。round 3 はこれを **2 つとも実装不能**と判定し、正しい:

- **`tool_event_id` を reviewer に書かせられない** (r3-xfamily-2)。transcript には `tool_use.id` / `tool_result.tool_use_id` があり **事後の相関は取れる**が、reviewer が自分の server 採番 id を `structured_output` の中に再現できる保証は無い。`@file` と同じ「未検証インタフェースへの依存」。
- **「load-bearing claim の被覆」を機械判定する手順が無い** (r2-xfamily-2)。claim を doc から抽出するのが model なら、self-report 問題に戻るだけ。v0.3 §10 が自分でそう認めていた。

**v0.4: 削る。** reviewer に id を書かせない。gate に被覆判定を入れない。

- reviewer は従来どおり `verify_commands_executed` をself-reportする
- gateはそのlistが非空であること、provider transcriptにtool useが1件以上あること、`num_turns<=1`との自己矛盾がないことだけを独立に確認する
- **command文字列の包含関係は検査しない**。CLI間のtool event表現とparaphrase差を安全にcanonicalizeする仕様がないためである
- **明示する**: これは明白な欠落と自己矛盾だけを検出し、意味的真偽も「報告した個々のcommandを本当に実行したか」も保証しない。`rg`を1回撃ってfindingsを捏造するreviewerは通過する

`evidence` の per-finding 束縛は **v2 以降の課題**として §10 に残す。実装できる形 (adapter が事後に紐付け、reviewer は `claim_id` と command 文字列だけ書く) の PoC が取れてから schema に入れる。**取れる前に schema に書かない。**

---

## 6. fail-open vs fail-closed

原典 `codex-exec` adapter は「retry 1 → 失敗継続なら fail-open (internal-only 継続)」。本移植は **fail-closed**。

- 原典の fail-open は cross-family が opt-in だった v0.5 以前の名残。v0.6.0 で cross-family は plateau の**必要条件**になった。必要条件が落ちたのに処理継続するのは INV-2 違反。
- gh #195 の root cause は「AI が flag を忘れて skip」= **skip が静かにできる構造**。fail-open はそれを温存する。
- Claudeが落ちている場合は自動fallbackせず、callerが`--reviewer grok`を明示する。選択providerも失敗すれば停止する。

`--no-cross-family <reason>` の明示 opt-out は残すが、**gate は marker を書かない**ので opt-out した review は plateau を名乗れない。

---

## 7. 原典の修理 — S1b migration backlog

r2-xfamily-5: 原典 `plugins/harness-magi/skills/dual-magi-review/SKILL.md` は「cross-family は mandatory」と宣言しながら、Error handling / Failure modes / Anti-patterns節には **external-failedで継続、external-skipped、adapter opt-in** が混在する。つまり **現に使われている canonical な向き (Claude 主) が、この doc が enforce すると謳う不変条件を破れる**。鏡像だけ厳格にして原典を放置するのは筋が通らない。

さらに codex 側 provenance は**実在する** (`~/.codex/sessions/**/rollout-*.jsonl` に `session_meta` + `turn_context.model`、380 files 実測)。「future work」とした v0.2 の判断は技術的に不当。

本releaseの原典変更は本pluginのG1-G9 gateへのone-line pointerだけである。G1-G9表や
call siteは原典に実装していない。以下のS1b-S3移行は未実装backlogであり、
原典のoptional/opt-in/external-skipped経路を修理済みとはclaimしない:

1. 原典にはS1方針の一部proseと、残存optional/opt-in/external-skipped経路が混在している
2. 原典から本pluginのgateをone-line参照するが、call siteとCodex rollout provenance adapter導入はS2/S3に残す
3. Codex-orchestrated本pluginは選択provider失敗時にfail-closedを実装済み

### 7.1 移行手順 (r2-xfamily-5: 「今日 fail-open な plugin を即 fail-closed にすると既存 workflow が止まる」)

原典を無条件に fail-closed 化すると、`codex` 未 install / 未 login の環境で既存の review が **その場で止まる**。段階を切る:

| stage | 挙動 | plateau marker |
|---|---|---|
| **S0 (historical)** | cross-family 失敗 → fail-open、internal-only で継続 | model が prose で宣言 (= 実質無制限) |
| **S1a (原典current、混在)** | behavioral checklistとplateau禁止proseは存在するが、旧optional/external-skipped経路が残る。原典proseは旧語彙で単に「S1」と自称 | behavioral禁止のみ。structural marker連携なし |
| **S1b (backlog)** | startup prereq probeを自動化し、旧skip経路を統一。欠けていれば警告して継続 | gate markerを書かないことをcall siteで構造化 |
| **S2 (次 release、bounded grace)** | S1 に加え、cross-family 失敗を **warning から error に格上げ**。`--no-cross-family <reason>` を明示した場合のみ継続 | 同上 (opt-out でも marker は出ない) |
| **S3** | cross-family 失敗 = **fail-closed** (exit 非 0) | 同上 |

要点: **「止める」より先に「plateau を名乗らせない」**。marker を出さないだけなら既存 workflow は動き続け、しかし「cross-family 済」と誤認されることは無くなる。行動を壊さずに不変条件を回復できる。原典S1bの既存 `.dual-magi/` state file / 旧 invocation upgrade testは未実装backlogであり、本releaseで検証済みとはclaimしない。

| 項目 | 原典 (現状) | 本 plugin | 解消 |
|---|---|---|---|
| adapter 失敗時 | S1a proseと旧optional/external-skipped経路が混在 | fail-closed | 原典S1b-S3はbacklog |
| severity enum | CRITICAL 無し | 有り | `schemas/finding.schema.json` を両 plugin の SSOT に昇格 |
| plateau 宣言 | S1a behavioral禁止proseと旧経路が混在、structural marker未導入 | gate scriptのみ | 原典S1b以降で構造化 |
| persona template | canonical | 参照 (複製しない) | 本 PR |
| harness-kimi の複製 template | drift 済 | — | follow-up issue |

---

## 8. 検証 — 常設testとmanual/backlogを分離する

v0.1/v0.2 の P1-P5 は「一度実走して確かめる」手動 probe で、release 後に何も残らなかった。repo は `b37ded8` で doc-vs-code 契約テストを入れたばかりであり、新 plugin がその rail の外で生まれるのは許容しない (r1-caspar-3)。

| phase | 常設テスト |
|---|---|
| P1 | `test_fanout_scrub.sh` = 3 persona fan-outのdurable JSON schema再検証/scrub-before-durable/sibling拒否。`MAGI_TEST_LIVE=1`でreal `codex -o FIFO` interfaceもprobe (INV-3/INV-5) |
| P2 | `MAGI_TEST_REVIEWER=claude|grok MAGI_TEST_LIVE=1 test_inv6_readonly.sh` = provider別に**exact adapter rail**でwrite/shell/subagentを指示し、(1) file side-effect不在、(2) transcript上forbidden toolがsuccess resultを返さないこと（error=構造rail作動は許容、非errorが違反）を実測 + inline schema/@file回帰。claim範囲はfalsifiableなside-effect/transcriptに限定（offered-tool surfaceは証明しない、P-h/P-g） |
| P3 | `test_inv6_readonly.sh`内で両providerのinline schema positiveと`@file` negativeを検証 |
| P4 | `test_inv7_lock.sh` = 未取得→続行 / 保持中→exit 3 / **同doc別campaign排他・別doc独立** / SIGKILL 自動解放 / 子孫拒否 / provider失敗時にEXIT trapがprompt/raw tempを消す |
| P5 | `test_plateau_gate.sh` = G1-G9 を個別に落とす。`test_grok_provider.sh`はGrok transcript/model/SHA provenance、provider取り違え、same-family偽装を弾く。`test_stale_round_failclosed.sh`はfailed rerun後のstale successを弾く |
| P6 | `test_docs_match_scripts.py` = SKILL.md/README/設計を読込み、env (`MAGI_XFAMILY_{CLAUDE_MODEL,GROK_MODEL,TIMEOUT_S}` + legacy MODEL)・exit code (2=fail-closed, 3=lock)・G1-G9表記を実装と照合。reviewer flag pinningは`test_claude_provider.sh`（Claude=safe-mode/strict-mcp-config/Read,Grep,Glob/write・agent・shell deny、default route）と`test_grok_provider.sh`のadapter argv fixture（docsとの自動照合ではない）、両testがdispatch/provenance/family mismatchも検証 |

P7 install/uninstall検証とP8 e2e自己適用は現時点ではmanual実走であり、常設test化はbacklog。

---

## 9. 残存リスク (正直に)

| risk | 影響 | 立場 |
|---|---|---|
| **reviewer が `rg` を 1 回撃って findings を捏造する** | verdict の意味が失われる | **検出しない**。§5.1 が検出するのは欠落と不整合だけ。**本設計最大の残存リスク**であり、これを塞ぐ機構は現状の CLI インタフェースでは実装できない (§10-1) |
| **同 UID 敵対 process による artifact 偽造 (T2)** | INV-1/INV-2 が完全に破れる | **防がない** (§0.1)。防ぐには orchestrator 書込権限外の署名 attestation |
| 子 process が fd 9 を継承し flock をすり抜ける | INV-7 が破れる | 実質の rail は allowlist に `Bash(codex:*)` が無いこと (§4.4)。`flock --close` / `FD_CLOEXEC` は実装時検討 |
| `codex exec` の hook は trust 状態で発火が変わる | trust 永続化した途端 hook が reviewer probe を拒否または遅延 | hook有無matrixは未実装backlog。P1 defaultはstub、optional liveはFIFO interfaceのみ |
| Claude/Grok envelope / transcript path 規約が将来変わる | G6 が死ぬ | fail-closed なので **静かには壊れない** (gate が exit 非 0)。P5 でprovider別keyをassert |
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
