---
name: dual-magi-review
version: 0.6.0
description: |
  Independent multi-perspective peer review for large design docs.
  Spawn 3 same-family sub-agent reviewers AND a cross-family reviewer
  (= different model family、 default: codex-exec) to cancel shared
  training-data bias. Cross-family is **default mandatory** since v0.6.0
  (= gh #195 incident: 4 Claude same-family rounds reached plateau CONFIRM,
  Codex 1 round = REJECT with 6 NEW CRITICAL all missed by Claude).
  Synthesize REJECT/REVISE findings into a structured report. Default mode
  is review-only: mutation (apply, commit, push) is opt-in.

  NOT for: small code diffs (use /simplify), single-function checks
  (use Task directly), or any artifact under ~200 lines (overhead exceeds value).
allowed-tools:
  - Task
  - Read
  - Edit
  - Write
  - Bash
  - AskUserQuestion
---

# dual-magi-review — independent multi-perspective design doc peer review

A "Magi" is a parallel set of independent sub-agent reviewers, each examining a
doc from a distinct perspective. "Dual-Magi" pairs same-family Magi with a
**cross-family reviewer** (different model family via external adapter) to
subtract shared training-data bias.

Inspired by Evangelion's 三賢者 system + academic peer review compression.

## Pattern essence (= core invariant、 unchanging)

```
1. independent multi-perspective critique (= N sub-agents、 perspectives 直交)
2. cross-family bias cancellation (= 異 model、 optional adapter)
3. iterative reroll (= round 毎に prior findings 反映 + 新 catch)
4. synthesis-driven convergence (= duplicate rate + severity で stop)
```

Variant detail (= adjustable):
- perspective count N (= 3 default、 2-5 OK)
- round count (= 2-4 typical、 domain で flex)
- cross-family vs same-family-only
- external transport (= mailbox / API webhook / shared file / etc; see § Adapters)

## When to invoke

- Authoring or reviewing a **design doc ≥ 500 lines** (= architecture, ADR,
  migration plan, schema, retrieval framework, identity strategy)
- The doc is **production-critical** (= migration / deploy / data ingest /
  scoring algorithm / public-facing system)
- **Single-model bias risk is high** (= you wrote it, you're reviewing it,
  attachment bias expected)
- Triggers: "peer review this doc", "review iteratively", "independent review",
  "magi にかけて", "ブラッシュアップ", "production-grade review"

## When NOT to invoke

- Code review of a PR diff → `/simplify`
- Single-function correctness → `Task` tool directly
- Doc < 200 lines → manual review faster than skill overhead
- Time-critical hotfix → this takes hours

## Modes (= default review-only)

| mode | flag | side effects | use case |
|---|---|---|---|
| **review-only** (default) | (none) | none — outputs report only | initial validation、 mutation 別工程 |
| apply-local | `--apply-local` | edits doc in working tree、 no commit | reroll inline + manual review of diff |
| commit-push | `--commit-push` | apply-local + commit on current branch + push | autonomous loop (= use with extreme caution) |

Mutation modes are **opt-in**. The skill never commits, pushes, or modifies
files by default. This is to preserve user control over git state and the
branch policy invariant (see § Prerequisites).

## Args

```
/dual-magi-review <doc-path> \
  [--rounds N]                        # default 3
  [--perspectives p1,p2,p3]           # default: algorithm,adversarial,business
  [--external <adapter-id>]           # default: codex-exec (= v0.6.0 mandate)
  [--no-cross-family <reason>]        # explicit opt-out, must justify
  [--domain-preset <path>]            # extra perspective definitions
  [--apply-local | --commit-push]     # opt-in mutation
  [--round N]                         # resume at specific round (= 2-phase pattern)
```

- `<doc-path>`: absolute path required
- `--rounds N`: max rounds 1-4 (= per-round invocation pattern recommended、 see § Loop strategy)
- `--perspectives`: 3 comma-separated keywords (= preset or custom)
- `--external`: see § Adapters。 **default `codex-exec`** (= v0.6.0、 see § Cross-family mandate)
- `--no-cross-family <reason>`: opt-out cross-family、 reason string required (= telemetry + audit)。 valid reasons: `quick-iteration` (= rapid doc skeleton iteration、 followed by mandatory final round) / `non-design-artifact` (= not a design doc、 e.g., status report)
- `--domain-preset`: load additional perspective briefs from external file
- `--apply-local` / `--commit-push`: enable mutation (= mutually exclusive)
- `--round N`: explicit round number for resuming (= e.g., `--round 2` after externall reply received)

## Cross-family mandate (= v0.6.0、 gh #195 lesson)

**Plateau CONFIRM definition** updated:
- Claude same-family 3 round CONFIRM **is NOT plateau**
- plateau requires 1+ **cross-family round** (= codex exec or mailbox adapter) with REJECT/REVISE not blocking
- skill **MUST refuse `plateau` declaration** until cross-family round recorded

**Why** (= 5/22 incident):
- gh #195: citation pipeline refactor v0.5、 4 Claude same-family round plateau CONFIRM 4.3/4.5/4.8 → Codex 1 round REJECT 1/5 + 6 NEW CRITICAL (= Claude 全 round で全 miss)
- root cause: same-family bias で integration / production-shape verify を skip、 doc internal coherence のみ確認
- structural fix: v0.5.0 schema-grounding mandate と同 pattern で、 cross-family round を skill default に baked-in

**Invocation behavior** (= v0.6.0):
1. `--external` 未指定 → **`codex-exec` adapter で自動 final round 走らせる**
2. `--no-cross-family <reason>` 指定 → log + warn、 「plateau CONFIRM」 を report に書けない
3. cross-family round で REJECT / 重大 CRITICAL → final verdict は cross-family verdict 優先 (= Claude 3 round CONFIRM があっても overrule)

## Prerequisites

1. doc exists at `<doc-path>` (= absolute path)
2. git working tree clean OR `--apply-local`/`--commit-push` not set
3. current branch is **NOT main** (= per project branch policy)
4. `Task` tool available (= Anthropic CLI)
5. for `--external <adapter>`: adapter-specific prerequisites (= see § Adapters)
6. for `--commit-push`: project's pre-commit hook + branch protection respected

## Protocol (= per-round = single invocation)

**Key**: 1 invocation = 1 round. Loop is **user-driven** (= re-invoke for next
round). This avoids context exhaustion + makes state explicit in user transcript.

### Step 1: Read doc + state setup

- Read `<doc-path>` (= full content)
- Compute artifact_sha = sha256 of doc content (= for correlation)
- Determine round number (= from `--round` flag、 default 1)
- Load prior-round findings if round > 1 (= from `${doc_dir}/.dual-magi/round_<N-1>.json`)

### Step 2: Spawn 3 sub-agents in parallel

Single message with 3 `Task` tool calls (= parallel execution):
- Each receives full doc as context
- Each receives one perspective brief
- Each must use structured finding schema (see § Finding schema)
- Each must end with: 「総合: GO / GO-WITH-REVISE / REJECT」

#### Schema-grounding mandate (= v0.5.0、 5/22 ADR-TPN v0.1 incident 学習)

doc に table 名 / column 名 / SQL 例 / 既存 code 挙動 premise が含まれる場合、 各 sub-agent
prompt template に下記を **必ず inject** (= AI 自起草 doc の 妄想 column / hallucinated schema を
front-load catch):

```
SCHEMA GROUNDING (mandatory): The doc you are reviewing may reference real
tables, columns, SQL queries, or claims about how existing code (e.g.,
"filter X is applied to retrieve Y") behaves. For every such reference:

1. Verify table/column existence:
   - `psql "$POSTGRES_URL" -c '\d <table>'` for live DB schema
   - `grep -rn '<column_name>' migrations/` for schema-as-code
   - `grep -rn '<column_name>' core/ api/ scripts/` for actual usage
2. Verify populate state for filter premises:
   - `SELECT COUNT(*) FILTER (WHERE <col> IS NULL), COUNT(*) FROM <table>`
   - if a filter assumes "this column is populated", surface NULL coverage
3. Verify existing-code-behavior premises:
   - "ENV_FLAG_X gates Y" claim → `grep -rn 'ENV_FLAG_X' core/ api/` and
     trace the actual code path, surface drift between doc narrative and
     real behavior

Any drift found = CRITICAL finding (= schema-reality drift breaks impl).
Do NOT trust doc-internal SQL examples; the author may have written them
from memory or imagination without `\d`-verifying.
```

これにより design doc 起草の systematic hallucination bias (= AI is biased toward
narrative coherence and skips literal-existence verification) を structural rail で catch。
memory `feedback_design_doc_schema_grounding_required` の structural 上位対応 (=
behavioral rule の skill skeleton 焼付け)。

#### Reviewer artifact MUST-emit (= v0.5.1、 efficacy anecdotal → measurable)

Each sub-agent's output JSON MUST include `verify_commands_executed` field listing
the actual psql / grep / Read commands run during the review:

```json
{
  "reviewer": "MELCHIOR",
  "round": 3,
  "verdict": "GO-WITH-REVISE",
  "verify_commands_executed": [
    "psql ... -c '\\d papers'",
    "psql ... -c '\\d medical_facts'",
    "grep -rn 'recall_flag' /home/.../migrations/",
    "grep -rn 'PRO_DOMAIN_FILTER' /home/.../core/ /home/.../api/",
    "Read core/retriever_pg.py:370-390",
    "..."
  ],
  "schema_grounding_verdict": "PASS | PARTIAL | FAIL",
  ...
}
```

Rationale: v0.5.0 hook's claim "schema-grounding mandate baked-in" is otherwise
self-reported. The explicit command list:

1. **Measures hook efficacy** — comparing rounds shows whether reviewers actually
   verify vs hand-wave; rounds with empty `verify_commands_executed` are degraded
   regardless of stated verdict
2. **Audit trail** — when a v3 round passes but post-ship reveals schema drift,
   the command list shows whether the gap was un-verified or actively missed
3. **Force-functions the verify behavior** — knowing the list will be emitted
   creates checklist-style discipline at review time

If `verify_commands_executed` is empty or only contains generic `Read` commands
without targeted psql/grep verification:
- mark reviewer-side `schema_grounding_verdict: FAIL`
- mark the whole round as "schema-grounding degraded" in synthesis
- consider re-running that reviewer with explicit instruction

This is the 「N+1 回目を待たない」 (don't wait for the next failure) structural
rail — every round emits its own audit, no round is graded only on outcome.

例 (= 2026-05-22 PRS-LLM ADR-TPN v0.1 dual-magi v2 で実際に catch):
- doc: `medical_facts.layer LIKE '1c%'` → 実: `layer` は `book_catalog.layer`、 JOIN 必要、 v0.1 SQL silently fail
- doc: `papers.recall_flag = TRUE` → 実: column 不在、 hallucinated safety rail
- doc: `paper_chunks.updated_at` → 実: column 不在、 pre-flight gate hard-error stub
- doc: 「PRO_DOMAIN_FILTER は paper retrieve も絞る」 → 実: textbook retrieve only、 narrative completely false

dual-magi v1 → v2 の 同 doc round で 1 round 単独で 5 CRITICAL schema-drift を catch、
single-model self-review では同 doc 起草者 (= AI) が verify を skip するため miss 確実。

### Step 3: Wait for all 3 outputs (= foreground block)

`Task` tool returns when sub-agent completes. All 3 block in parallel.

Failure handling:
- If N<3 sub-agents return: log gap, proceed with what's available, mark report degraded
- If any sub-agent times out: continue with remainders, retry budget = 1

### Step 4: Synthesize

Output **structured finding matrix** to user (Markdown table):

| field | description |
|---|---|
| finding_id | unique per round (= round-perspective-N) |
| severity | REJECT / HIGH / MED / LOW / nit |
| verdict | GO / REVISE / REJECT |
| title | short summary (= ≤ 80 chars) |
| location | doc line range or section anchor |
| rationale | why this is a finding |
| required_fix | specific change recommended |
| confidence | high / med / low |
| dup_flag | new / dup-from-round-N (= compared to prior rounds) |
| missed_angle | what previous rounds / single-model would miss |

Then aggregate:
- count per severity
- cross-perspective agreement (= 2+ perspectives flag same issue = high-confidence cluster)
- new-vs-duplicate ratio (= if > 80% dup → ship signal)

Save to `${doc_dir}/.dual-magi/round_<N>.json` for next-round reference.

### Step 5 (optional): External reviewer round

If `--external <adapter>` set:
- Invoke adapter (see § Adapters)
- Adapter publishes review request via its transport (= mailbox / webhook / API)
- Skill returns immediately (= non-blocking)
- User must re-invoke with `--round N --resume` after external reply received

For non-async adapters (= synchronous API): adapter returns reviews inline.

### Step 6: Convergence evaluation

Output to user:
- `total findings: REJECT N, HIGH N, MED N, LOW N`
- `new vs duplicate from prior round: <ratio>`
- `recommendation: continue → next round / converge → ship`

Stop criteria (= user judges OR explicit flag for auto):
1. ≥ 80% findings duplicate from prior round
2. All new findings LOW severity / nit
3. Cumulative rounds = `--rounds N` max
4. User says "ship" / "stop"

### Step 7: Mutation (= only if --apply-local or --commit-push)

If mutation flag set:
- Read doc again (= check artifact_sha unchanged, fail if changed)
- Apply REJECT + HIGH REVISE findings via Edit
- Update doc's changelog section with version note
- If `--commit-push`:
  - Verify branch policy (= current != main)
  - `git pull --ff-only origin <current-branch>` (= reject if non-FF)
  - `git add <doc-path>` + `git commit` with structured message
  - `git push origin <current-branch>`
  - Verify pre-commit hook passed (= no `--no-verify`)

If mutation flag NOT set (= default review-only):
- Output: 「No mutation applied. Run with --apply-local to integrate findings.」

## Finding schema (= standardized)

```json
{
  "finding_id": "r1-algorithm-3",
  "severity": "HIGH",
  "verdict": "REVISE",
  "title": "PageRank convergence criteria not auditable",
  "location": "§3.3.1 lines 186-203",
  "rationale": "L1 1e-8 threshold over 77.6M nodes is too strict; max-iter 100 has no fail behavior",
  "required_fix": "tolerance 1e-6×N + top-k Jaccard + Kendall tau + explicit fail diagnostic + Discord alert",
  "confidence": "high",
  "dup_flag": "new",
  "missed_angle": "single-model review treats convergence as solved; numerical rigor gap"
}
```

## Perspective presets

### Generic (= any design doc、 default)

- `algorithm`: algorithm + statistical/numerical rigor, convergence, scale, memory, alternatives
- `adversarial`: security, abuse, supply chain, gaming, public-launch incentive, governance
- `business`: GTM, buyer segments, brand, competitor counter, moat, timeline realism

### Custom

Any 3 keywords accepted. Skill generates a brief from keyword (= 5-10 observation points, output format expectation).

### Domain presets

For project-specific perspectives (= not in generic), use `--domain-preset <path>`:

```
/dual-magi-review docs/designs/foo.md \
  --perspectives medical-informatics,production-retrieval,graph-theory \
  --domain-preset ~/.claude/skills/dual-magi-review/examples/medical_rag_perspectives.md
```

Example presets:
- `examples/medical_rag_perspectives.md` (= PRS-LLM / mafutsu)
- `examples/web_app_perspectives.md` (= future)
- `examples/data_pipeline_perspectives.md` (= future)
- `examples/ml_training_perspectives.md` (= future)
- `examples/legal_document_perspectives.md` (= future)

## Adapters (= cross-family transport)

Cross-family reviewer integration is implemented via adapter modules in
`adapters/<adapter-id>.md`. Each adapter defines:
- transport mechanism
- request schema (= sender → reviewer)
- response schema (= reviewer → sender)
- correlation fields
- ACK / timeout / fallback policy
- prerequisites (= mailbox file / API key / pane id / etc)

Built-in adapters (= skeleton, implement per-project):

### `codex-exec` (= default since v0.6.0、 non-interactive)

**why default**: zero-config、 synchronous、 no tmux pane / mailbox setup 不要、 5-10 min walltime で 1 round 完結。 gh #195 lesson (= 4 Claude same-family round CONFIRM → Codex 1 round REJECT 1/5 with 6 NEW CRITICAL) を構造 default に焼く。

#### invocation

```bash
codex exec --skip-git-repo-check - < /tmp/codex_review_prompt.md
```

skill 内動作:
1. 通常 Claude 3 視点 round (= MELCHIOR / BALTHASAR / CASPAR) 完了後
2. `codex exec` を **final round として自動呼出**、 prompt は v0.6.0 template 経由構築:
   - target doc full read
   - prior round findings 同梱
   - **schema-grounding mandate explicit** (= 「real grep/psql verification, NOT speculation」)
   - **integration drift focus** (= production wire dict shape ↔ impl 入力 assumption)
   - output format: REJECT/REVISE/GO-WITH-REVISE/APPROVED + CRITICAL/HIGH/MED findings
3. Codex output を round N+1 として記録、 final verdict は Codex verdict 優先
4. Codex REJECT または CRITICAL ≥ 1 → 「plateau」 declaration block、 round 続行

#### options

- `--codex-model <model>`: default Codex CLI default (= GPT-5.5 系)
- `--codex-timeout-s <sec>`: default 600 (= 10 min)
- `--codex-prompt-file <path>`: prompt template override、 default skill 自動生成

#### prerequisites

- `codex` CLI installed (= `which codex` で確認)
- `codex login` 通過済 (= `codex doctor` で auth status)
- 対象 repo 内に対象 doc 存在

#### error handling (= v0.6.1、 S1 stage。 詳細 `docs/designs/CODEX_MAGI_MIRROR.md` §7.1)

> **不変条件**: cross-family round は plateau の **必要条件** (= v0.6.0)。 それが満たされないまま
> 「plateau」 を名乗ることはできない。 fail-open は cross-family が opt-in だった v0.5 以前の名残であり、
> 「skip が静かにできてしまう構造」 = gh #195 の root cause そのもの。

以下は **既存 workflow を止めないための移行段階 (= S1)**。 継続するか否かの挙動は当面 fail-open のままだが、
**plateau を名乗る資格は失われる**:

- `codex exec` exit non-zero → log + retry 1 回、 失敗継続なら continue、 但し round に **`external-failed` を mark**
- timeout → kill + retry なし、 continue、 但し **`external-failed`**
- output parse 失敗 → **raw output を round N+1 entry に保存** (= 次 round の reviewer が現物を見られる位置。 v0.6.0 の挙動を維持) + warning、 **`external-failed`**

**`external-failed` が付いた round を含む review は、 `plateau` / 「plateau CONFIRM」 を report に書いてはならない。**
「internal-only で継続できた」 ことと 「cross-family round が走った」 ことは別物である。

移行段階: **S1 (= 現在)** = 止めない、 但し plateau 不可 → S2 = `--no-cross-family <reason>` 明示時のみ継続 →
S3 = fail-closed (= exit 非 0)。 「止める」 より先に 「plateau を名乗らせない」。 行動を壊さずに不変条件を回復する。

構造 rail (= 文ではなく script) は `plugins/harness-magi-codex/scripts/magi_plateau_gate.sh` に実装済 (= G1-G7)。
原典側にも同等の gate を入れるのが S2/S3 の作業 (= 原典は現在 provenance を一切記録していない)。
Codex 側 provenance は実在する: `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl` の
`session_meta` + `turn_context.model` (= 実測、 380 files)。

### `codex-mailbox` (= njslyr7 mailbox-based、 v0.3.0: per-project channel + spawn options)

#### options

- `--codex-pane <tmux-target>`: target pane (= e.g., `0:2`). default `auto-detect` (= search active codex pane)
- `--mailbox-path <path>`: mailbox jsonl path. default `~/.njslyr7/mailbox/<project-slug>.jsonl`
- `--spawn-via <method>`: `manual` (= require pre-existing pane) / `formation` (= auto-spawn via formation skill) / `none` (= fail if not running). default `manual`
- `--codex-briefing <text>`: briefing if spawning, e.g., "Magi reviewer for <project>". default skill-generated

#### isolation pattern (= recommended, β)

各 project に **専用 codex pane + 専用 mailbox channel** で isolate:

```bash
# hippocampus session で初回 bootstrap (= 1 度 manual)
tmux new-window -n codex-hippocampus
codex  # = 新 process、 hippocampus session 専用

# 同 session 内で mailbox 別 channel 用意
touch ~/.njslyr7/mailbox/hippocampus.jsonl

# Codex CLI に briefing inject
# (= Codex 起動後 prompt で 「you are reviewer for hippocampus、 listen to ~/.njslyr7/mailbox/hippocampus.jsonl」)
```

以降 hippocampus session で:

```bash
/dual-magi-review ~/projects/hippocampus-mcp/docs/foo.md \
  --external codex-mailbox \
  --codex-pane 0:3 \
  --mailbox-path ~/.njslyr7/mailbox/hippocampus.jsonl
```

= **seq stream / context / state 完全分離**、 PRS-LLM session ↔ hippocampus session 干渉ゼロ。

#### ephemeral spawn pattern (= 都度起動、 γ)

長期 use 不要なら formation skill で 都度 spawn → task 完了で kill:

```bash
/dual-magi-review ~/projects/foo/design.md \
  --external codex-mailbox \
  --spawn-via formation \
  --codex-briefing "Magi reviewer, single doc, single round"
```

skill 内動作:
1. `formation spawn codex --briefing "<briefing>" --mailbox <unique-mailbox>` invoke
2. spawn 完了 ack 受領 → review request 送付
3. review 完了後 `formation stop <worker-id>` で kill
4. mailbox 該当 entries archive

= **task scope 明確、 起動忘れ防止、 resource 効率**、 short-lived review に最適。

#### shared pane pattern (= v0.2.0 default、 deprecated for v0.3.0)

複数 project が **同一 Codex pane + 同一 mailbox** 共有:
- seq stream 混在、 subject prefix で識別
- ~~推奨しない (= context bleed risk)~~

v0.3.0 で β / γ を canonical default、 shared pattern は v0.4.0 で削除候補。

#### request schema (= v0.3.0、 correlation fields 拡張)

```json
{
  "mailbox_seq": <int>,
  "request_id": "<UUID v4>",
  "project_slug": "<prs-llm|hippocampus|...>",
  "round": <int>,
  "artifact_path": "<absolute>",
  "artifact_sha": "<sha256>",
  "response_kind": "review",
  "expected_count": 4,
  "reviewer_id": "<codex-pane-id>",
  "perspective": "<keyword>",
  "ts": "<ISO 8601 UTC>",
  "from": "<sender>",
  "to": "<codex pane>",
  "subject": "[<project_slug>] dual-magi-review request",
  "body": "<request brief>"
}
```

`project_slug` で hippocampus / prs-llm / etc を 明示識別、 Codex 側 context 切替 自動化。

#### ACK / timeout / fallback

- ACK: subject prefix `[ACK]` で response 内に echo、 5 min 以内
- response timeout: 60 min for first、 30 min between subsequent
- fallback: internal Magi only、 report に `external-skipped` mark
- tmux notify: best-effort (= 既)、 mailbox + ACK が source of truth

#### Codex side responsibility (= NOT Claude skill scope)

- Codex CLI 起動 + briefing 受領
- 指定 mailbox channel polling / tail
- request 受信時 sub-agent spawn + review output
- response の schema compliance (= request_id echo + structured findings)

Codex 側 implementation は別 skill / command (= 「codex-side dual-magi-reviewer」 等) を Codex repository で 維持、 Claude skill は schema + adapter contract only。

### `webhook` (= future)

POST request to URL, sync response with timeout.

### `shared-file` (= future)

write request to shared file, poll for response.

## Loop strategy (= multi-round)

**Recommended pattern**: 1 skill invocation = 1 round, user re-invokes for next.

```
$ /dual-magi-review docs/foo.md --rounds 3
[round 1 output...]
"continue to round 2? re-invoke with --round 2"

$ /dual-magi-review docs/foo.md --round 2
[round 2 output, includes diff from round 1...]
"continue to round 3? re-invoke with --round 3"

$ /dual-magi-review docs/foo.md --round 3
[round 3 output, mostly nit, recommends ship]
```

Why not auto-loop:
- single-session context exhaustion (= 4 round × 3 sub-agent × 2K token = 24K context)
- user state visibility in transcript
- external reviewer async wait (= mailbox response may span hours / user session)

State persistence (= `.dual-magi/`):
- `${doc_dir}/.dual-magi/round_<N>.json` per round findings
- `${doc_dir}/.dual-magi/state.json` overall progress
- gitignore recommended (= meta-state, not artifact)

## Failure modes (= troubleshooting)

| symptom | likely cause | resolution |
|---|---|---|
| Task tool fails / timeouts | rate limit / OOM / network | retry budget 1, then mark gap and proceed |
| < 3 sub-agents return | Task tool partial fail | continue with returned, mark report degraded, document in synthesis |
| mailbox write fails | file lock contention | wait + retry once; if still fail, abort external round, internal-only |
| tmux send-keys to dead pane | external pane closed | abort external round, continue internal-only |
| git push reject (= non-FF) | upstream conflict | abort mutation, prompt user `git pull` manually |
| Edit uniqueness violation | string non-unique in doc | log error, skip this finding's auto-apply, surface to user |
| sub-agent output schema violation | adversarial reviewer formatting | parse best-effort, log as degraded reviewer |
| Codex offline (= --external set) | adapter prereq fail | fall back to internal-only, mark `external-skipped` |
| infinite loop in mutation step | unlikely (= no auto-loop) | user can Ctrl-C, state preserved in `.dual-magi/` |

## Anti-patterns

| anti-pattern | why bad | instead |
|---|---|---|
| invoking on < 200 line doc | overhead > value | manual review |
| `--commit-push` without dirty-state check | overwrites user work | review-only default, manual git after |
| auto-loop N rounds in 1 invocation | context exhaustion | per-round invocation pattern |
| using domain preset for unrelated project | preset assumes project-specific context | start with generic presets, add custom |
| treating Magi findings as ground truth | LLM reviewers have shared bias | dual-family OR human expert pass on critical decisions |
| running cross-family at scale without auth | exposes content to external system | check adapter security, scope to non-sensitive docs |
| omitting `--external` then asking why no Codex | adapter is opt-in | add `--external codex-mailbox` explicitly |

## Cost estimate (= Claude-side only, excludes adapter costs)

| round type | walltime | cost (= LLM) |
|---|---|---|
| internal Magi only (= 3 sub-agent + synthesize) | 20-30 min | $3-5 |
| + cross-family adapter (= mailbox publish + parse reply) | + 5-15 min walltime, external reviewer cost separate | $0-1 (= API cost for mailbox transport) |
| reroll doc (= apply REJECT/HIGH) | 30-60 min | $0 |
| **per round total** | **30-90 min** | **$3-6** |

Typical 2-3 rounds: $10-20 Claude-side, plus adapter-specific external cost.

PRS-LLM observed datum (= 4 applications):
- IDENTITY_STRATEGY_V2: 2 rounds, ~5h, ~$15
- CITATION_AUTHOR_NETWORK: 2 rounds, ~5h, ~$15
- dual-magi-review (= self-application): 1 round so far, ~1h, ~$5
- PG_INFRA_HARDENING + provision_pg (= 2026-05-21): R1 (Claude 3 perspective) → R2 (codex cross-family) → R3 (codex on v2) → v4 (= hybrid gate pivot)、 ~4h compound、 ~$25

## Empirical patterns (= field observation)

- **cross-family round skip 不能**: Claude 3 perspective 後 codex round で毎回 2-5 NEW finding (= 3 project 観測)
- **多段 round で framing pivot**: 各 round で 1 段抽象高い primitive 発見 (= 例 provision_pg v1→v4: leak surface → atomicity → automation)、 線形 refine ではない
- **ship gate = new/total<20%**: R1=100%→R2≈20%→R3≈10% で収束、 但し REJECT 残れば次 round 必須、 production minimum = 3 round

## Related skills

- `/simplify`: small-diff code/doc review (= subset of single-perspective)
- `formation`: spawn long-running peer AI worker in tmux pane (= alternative transport for external reviewer)
- `Task` tool directly: single-perspective review for small artifacts

## Limits

- **Same architecture bias**: same-family Magi shares pre-training data; cross-family partially mitigates
- **No human domain expert**: medical / legal / engineering require domain-specific human review for critical decisions
- **Doc artifact only**: static text review; live system / db inspection out of scope
- **External coordination cost**: cross-family requires adapter setup + external system alive
- **Endless abstraction guard**: each round may erase useful project-specific constraints — review for over-generalization

## Reference material (= optional, project-specific)

PRS-LLM-dev project internal memory pointers (= may not exist in other projects):
- `feedback_dual_magi_iterative_review` (= this pattern's experience log)
- `feedback_4_parallel_agents_disjoint_files` (= 4-parallel agent pattern, related)
- `feedback_magi_v1_review_process` (= original Magi v1 single-family pattern)
- `feedback_agentshield_independent_scan` (= santa-method dual independent reviewer principle, parent)
- `feedback_mailbox_ack_required_with_double_enter` (= mailbox adapter detail)

These are not prerequisites for skill function. Pattern is self-contained in this SKILL.md.

## Revision history

| date | version | change |
|---|---|---|
| 2026-05-14 | 0.1.0 | Initial skeleton |
| 2026-05-14 | 0.3.0 | Isolation pattern canonical化、 user feedback 「session 濁る」 反映:<br>- adapter `codex-mailbox` に `--codex-pane` / `--mailbox-path` / `--spawn-via` options 追加<br>- β isolation pattern (= 専用 pane + 専用 mailbox channel) を recommended default<br>- γ ephemeral spawn pattern (= formation skill 経由 都度 spawn / kill) 追加<br>- shared pane pattern (= v0.2.0 default) deprecated、 v0.4.0 で削除候補<br>- request schema に `project_slug` 追加 (= context 切替明示)<br>- Codex side responsibility 明示 (= skill scope 外、 Codex repo で別 implement) |
| 2026-05-21 | 0.4.0 | Empirical patterns 3 件追加 (= 2026-05-21 PG hardening session 観測): (1) cross-family round skip 不能 (2) design pivot 連鎖 (3) ship gate = new/total<20%。 cost datum 4 件目 (= provision_pg ~4h ~$25) |
| 2026-05-22 | 0.5.0 | **Schema-grounding mandate** を Step 2 sub-agent prompt template に baked-in (= 5/22 PRS-LLM ADR-TPN v0.1 incident 学習)。 AI 自起草 doc の 妄想 column / hallucinated schema を front-load catch、 全 sub-agent が必ず psql `\d` + grep migrations/core/api で table.column 実在 verify + populate state + 既存 code 挙動 premise drift を CRITICAL finding 化。 memory `feedback_design_doc_schema_grounding_required` の structural 上位対応、 behavioral rule から skill skeleton への昇格 |
| 2026-05-22 | 0.5.1 | **Reviewer `verify_commands_executed` MUST-emit** 追加 (= 5/22 ADR-TPN v0.2 dual-magi v3 で MELCHIOR LOW #3 finding 即対応、 gh #194)。 v0.5.0 hook efficacy が anecdotal (= reviewer の self-report) だった問題を、 各 reviewer の output JSON に **実行 command 完全 list を MUST 含む** で measurable 化。 空 list or generic Read のみ = schema_grounding_verdict FAIL 自動判定、 round 全体が degraded mark。 user instruction「N+1 回目を待たない、 100 回叩く前に構造で先回り」 の literal application、 incident → memory → skill skeleton → output-format-mandate の 4 段 escalation 完了 |
| 2026-05-22 | 0.6.0 | **Cross-family default mandatory** (= gh #195 incident 学習)。 v0.5.x までは `--external` opt-in flag、 同日 citation pipeline refactor v0.5 で 4 Claude same-family round plateau CONFIRM 4.3/4.5/4.8 到達後 Codex 1 round REJECT 1/5 + **6 NEW CRITICAL** (= Claude 全 round 全 miss、 production-shape dict drift / paradigm 矛盾 / GLOBAL rewrite span 等)。 root cause = memory `feedback_dual_magi_mandatory_for_scripts` mandate と skill default の乖離、 私 (= AI) が flag 忘れて skip。 structural fix 3 点: (1) `--external` default = `codex-exec` adapter で auto final round (= zero-config sync invocation、 `codex exec --skip-git-repo-check -`)、 (2) plateau CONFIRM declaration block until cross-family round recorded、 (3) cross-family REJECT/CRITICAL → final verdict 優先 overrule Claude CONFIRM。 `--no-cross-family <reason>` opt-out は valid reason 限定 (= quick-iteration / non-design-artifact) + telemetry audit。 memory `feedback_dual_magi_mandatory_for_scripts` の behavioral rule を skill default に焼付け、 私 (= Claude) の self-discipline failure を structural rail で先回り。 v0.5.0 schema-grounding と同 escalation pattern (= behavioral → skill skeleton)、 incident → memory → skill default の 3 段昇格 完了 |
| 2026-05-14 | 0.2.0 | Round 1 dual-Magi review applied (= 12 findings consolidated from Claude Magi v1-1/2/3 + Codex Magi v1-1/2/3 + Codex synthesis). Key changes: <br>- Tool name: `Agent` → `Task` (= Anthropic CLI canonical) <br>- Mutation opt-in (= review-only default, --apply-local / --commit-push flags) <br>- 1 invocation = 1 round pattern (= no in-skill loop, user re-invokes) <br>- Structured finding schema (= 10 fields) <br>- Standardized correlation fields (= request_id UUID + artifact_sha + round + response_kind + expected_count) <br>- Adapter abstraction (= cross-family transport pluggable; codex-mailbox / webhook / shared-file) <br>- Domain presets extracted to `examples/` <br>- Anti-patterns + Troubleshooting sections (= per formation template) <br>- Compressed description for skill-list rendering <br>- EN trigger keywords added <br>- Memory references demoted to optional reference material <br>- Created `feedback_dual_magi_iterative_review.md` memory (= dead reference fix) <br>- Self-application: this v0.2.0 is the recursive output of v0.1.0 reviewed by the pattern it codifies |
