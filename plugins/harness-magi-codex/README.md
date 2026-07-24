# harness-magi-codex

**Codex is the orchestrator; Claude or Grok is the cross-family reviewer.** Claude remains the
default; Grok is an explicit fallback for Claude quota/capacity failures. The mirror image of
[`harness-magi`](../harness-magi/), which runs the same protocol the other way round.

version: `0.1.0-codex` · design: [`docs/designs/CODEX_MAGI_MIRROR.md`](../../docs/designs/CODEX_MAGI_MIRROR.md)

## Why a mirror exists

A "Magi" panel is three independent, perspective-orthogonal reviewers. **dual-magi** adds a
reviewer from a *different model family*, because same-family reviewers share training-data blind
spots and will confidently agree with one another.

That claim is not theoretical. Reviewing the design of *this very plugin*:

| round | family | verdict | new findings |
|---|---|---|---|
| 1 | Claude ×3 | REVISE | 25 (6 CRITICAL) |
| 2 | **Codex** | **REJECT** | **5 CRITICAL — none of which the three Claude reviewers touched** |
| 3 | Codex | REJECT | 3 |
| 4 | Codex | REJECT | 0 (one doc self-contradiction) |
| 5 | Codex | **GO** | 0 → plateau |

Two of round 2's criticals were mechanisms the design specified that **do not exist**: a
`--json-schema @file` argument form (the CLI answers `Unrecognized token '@'`) and a prompt-hash
field in the transcript (there isn't one). A third was a live credential-leak path. Three
same-family reviewers read the same text and found none of them.

## What's inside

```
schemas/finding.schema.json   SSOT. codex takes --output-schema <file>; claude needs it inlined.
schemas/implementation-convergence.schema.json
                              opt-in report-only implementation manifest
scripts/
  magi_autorun.py             session-bound no-ack campaign controller
  magi_fanout_codex.sh        3 personas as parallel `codex exec` (sole author of their prompts)
  magi_xfamily.sh             provider-selectable adapter -> headless Claude or Grok
  magi_xfamily_claude.sh      backward-compatible Claude wrapper
  magi_campaign_guard.py      fixed global fuse + claim lifecycle + legacy migration
  magi_validate_findings.py   validates cross-field convergence rules after constrained output
  magi_verify_round.py        write-free G1-G6/G9 verification
  magi_git.py                 ambient-config-free Git object reads
  magi_review_packet.py       exact-SHA/tree/full-diff manifest builder + history archive
  magi_convergence_gate.py    report-only implementation convergence evaluator
  magi_plateau_gate.sh        the ONLY thing that may write a plateau marker
  magi_lock.sh                flock(2) helper (recursion + concurrency guard)
  magi_scrub.py               redacts credential-shaped strings before anything hits disk
hooks/magi_autorun_hook.sh    Stop hook; continues armed campaigns to plateau/blocked
skills/{dual-magi-review,ultramagi}/SKILL.md
tests/                        exit codes, G-asserts, lock semantics, read-only rail, doc-drift
```

Persona templates are **not** copied — they are read from the canonical `harness-magi` plugin.
(The `harness-kimi` copies have already drifted from their originals.)

## Install

Preferred: install the native `harness-magi-codex` plugin from the repository
Codex marketplace; see [`docs/codex_plugins.md`](../../docs/codex_plugins.md).
The legacy `install-codex-skills.sh` symlink flow remains only for migration and
is removed with `uninstall-codex-skills.sh` after native plugin installation.

Requires `codex`, `flock`, Python 3 with `jsonschema`, and the selected reviewer CLI (`claude` or
`grok`). A missing selected CLI fails closed (exit `2`). There is no automatic provider fallback:
the caller must explicitly choose Grok so provenance and routing remain auditable.

## Use

```bash
D=docs/designs/MY_DESIGN.md; S=docs/designs/.dual-magi; mkdir -p "$S"

python3 scripts/magi_autorun.py arm "$D"                              # once per campaign
scripts/magi_fanout_codex.sh      "$D" 1 "$S" --persona-set magi     # same-family ×3
# Synthesize the three outputs into $S/round_1_codex.json, then:
scripts/magi_xfamily.sh --reviewer claude \
  "$D" 2 "$S/round_1_codex.json" "$S/round_2_xfamily"
scripts/magi_plateau_gate.sh "$D" "$S/round_2_xfamily" --reviewer-family claude

# Explicit fallback when Claude is unavailable:
scripts/magi_xfamily.sh --reviewer grok \
  "$D" 2 "$S/round_1_codex.json" "$S/round_2_xfamily"
scripts/magi_plateau_gate.sh "$D" "$S/round_2_xfamily" --reviewer-family grok
```

Revise the doc with the findings and re-run. `--persona-set bug-hunt` swaps the personas to review
an *implementation* instead of a design (ultramagi gate [4]).

For implementation campaigns, create an untracked exact-SHA packet at one stable path, review
that packet, then evaluate the already-charged history:

```bash
python3 scripts/magi_review_packet.py \
  --repo "$PWD" --base <base-commit> --scope <issue-or-task> \
  --invariant <invariant-id> --deadline <RFC3339> \
  --output "$PWD/.magi-implementation-review.json"

python3 scripts/magi_convergence_gate.py evaluate path/to/implementation-review.json
```

The packet embeds the exact target tree and full `--binary --full-index` diff. Updating the stable
packet path archives the prior bytes by SHA-256 so historical review artifacts remain bound to
their target Git revision.

The evaluator is read-only and report-only. It returns only `CONTINUE`,
`FINAL_REVIEW_REQUIRED`, `BLOCKED`, or `REDESIGN`; it never launches a reviewer, changes the
ledger, writes a plateau marker, emits PASS, or authorizes shipping. Two complete logical
`fanout(3) -> xfamily(1)` cycles are the maximum. Existing exact-revision G1-G9 plateau and human
judgment remain the PASS authority.

Every round after round 1 requires a schema-valid prior synthesis artifact from the same state
directory, canonical document identity, and immediately preceding round. Every output carries
`artifact_id` and `artifact_sha`. `dup_flag` is constrained to
`new`, `duplicate`, `regression`, `readiness-gap`, or `scope-expansion`; the last two cannot be
HIGH-or-worse.

The synthesis must use `reviewer: SYNTHESIS`, list every preceding-round source filename and
SHA-256 in `source_artifacts`, and disposition every `<source-file>#<finding_id>` as `carried`,
`duplicate`, `resolved`, or `deferred`. This prevents a single reviewer output or incomplete
subset from masquerading as the round synthesis.

## Campaign guard

The default autonomous campaign stops after 16 weighted model launches: fan-out costs 3 and
cross-family costs 1, permitting four pairs without retries.
Fan-out admission also preserves one weighted launch for the immediately following mandatory
cross-family review. If that reserve cannot be preserved, the campaign is blocked before any
provider starts; denial is never permission to ship. Cross-family admission charges only its real
weight, so the reserve is not charged twice.
Both reviewer adapters append to a canonical document-scoped campaign ledger before launching a
model. Retries consume budget; a fresh state directory or repeated round 1 cannot reset it. Exit `4`
means `CAMPAIGN BUDGET EXHAUSTED — NOT PLATEAU`: apply an in-scope correction or scope/primitive
change, then invoke round 1. A changed document or review-protocol SHA rolls into the next campaign
automatically, without acknowledgement.

`MAGI_MAX_AUTONOMOUS_MODEL_LAUNCHES` may tighten the fixed global ceiling of 16; it cannot extend it.
All revision campaigns share those same 16 weighted model launches. Changing state directory is not a reset. Global exhaustion produces a definitive blocked result,
not an acknowledgement prompt.

Arming binds the workflow to the current Codex thread. On its intact path, the bundled Stop hook keeps the turn chain
moving without user acknowledgement until the exact-revision plateau marker exists or the
controller records a definitive blocked state. Two continued turns with no durable document or
ledger progress terminate blocked rather than loop. Hook input/registry/ledger parse or I/O errors
fail open so the session may stop, while the independent campaign guard still bounds spend.

Fan-out and cross-family calls have tightening-only deadlines via `MAGI_FANOUT_TIMEOUT_S` and
`MAGI_XFAMILY_TIMEOUT_S` (default/max `900`).
Timeout and signal cleanup release the canonical lock, close the claim as failed, and preserve one
bounded retry. Exit `4` is reserved for the global fuse; state corruption exits `2`, and illegal
phase transitions exit `64`.

If requirements change while an adapter owns a live claim, cancel that exact charged revision
before modifying the document:

```bash
python3 scripts/magi_campaign_guard.py cancel-revision "$D" \
  --expected-artifact-sha "$(sha256sum "$D" | cut -d' ' -f1)" \
  --reason "requirements changed: <brief reason>"
```

The guard records cancellation intent before signaling the verified adapter process tree, waits
for bounded TERM/KILL cleanup, proves the canonical review lock is released, and then marks the
claim `superseded-by-requirement-revision`. The launch remains charged and is never review
evidence. Repeating the same command is idempotent. A replacement round 1 is admitted only after
the document content SHA changes; changing only the review protocol does not restart a superseded
revision. Do not unlink `.review.*.lock`, and do not treat a cleanup-blocked result as permission
to launch or ship.

`new-campaign` is not a production escape hatch. It is disabled unless deterministic fixtures set
`MAGI_TEST_ALLOW_NEW_CAMPAIGN=1`; even there, the canonical global fuse remains unchanged.

## The plateau gate

The model does not get to say review is finished. `magi_plateau_gate.sh` does, and only when:

| assert | blocks |
|---|---|
| `G1` | missing cross-family round, or a verdict outside the schema enum |
| `G2` | a same-family model, or one provider's artifact masquerading as another provider |
| `G3` | a **stale** round that reviewed a different revision (`artifact_sha` mismatch) |
| `G4` | findings swapped after the adapter wrote them (`output_sha` mismatch) |
| `G5` | `num_turns < 1`, or `num_turns <= 1` while operations are reported |
| `G6` | a `session_id` resolving to no selected-provider transcript, transcript/model mismatch, or changed Grok transcript |
| `G7` | a `REJECT` **or `REVISE`** verdict |
| `G8` | any unresolved `REJECT`/`CRITICAL`/`HIGH` finding — severity calibration cannot hide a blocker |
| `G9` | ungrounded rounds: a self-reported `schema_grounding_verdict: FAIL`, an **empty** `verify_commands_executed`, or commands claimed while the transcript shows no tool use |

gh #195's root cause was an AI forgetting a behavioral rule. A sentence in a SKILL.md is not a
rail. This is.

## Threat model — read this

The gate protects against **T1: accidental skip** — a forgotten flag, a buggy script, a stale
artifact reused as fresh.

It does **not** protect against **T2: an adversarial process running as the same OS user**. Such a
process can write the findings, the meta, and the transcript itself; `~/.claude/projects/` is an
ordinary same-user directory with no cryptographic binding. **Nothing here is forgery-resistant,
and this document does not claim otherwise.** T2 would need a signed attestation held outside the
orchestrator's write authority.

Equally honest: schema conformance guarantees nothing about content *truth*. Both CLIs use
constrained decoding and will fabricate a required field to satisfy a schema. G9 checks only that
the reported operation list is non-empty and that the provider transcript contains some tool use;
it does not prove that the listed operations match those calls. A reviewer that runs one read tool
and invents its findings passes. That is the largest residual risk, and it is unfixed.

## Constraints

- **DB-grounded docs are out of scope for v1.** The reviewer allowlist has no database tools: a
  reviewer running `psql 'postgres://u:PASSWORD@h/db'` would persist that credential into
  `verify_commands_executed`, which is fed into the next round's prompt and shipped to another
  vendor's API. A credential-safe wrapper is future work.
- The doc is transmitted in full to another model vendor. Ensure it contains no secrets.

## Contract

Adapter exit codes: `0` complete · `2` fail-closed (no usable result; no plateau) · `3` lock held
(recursion or a concurrent review of the same doc) · `4` autonomous campaign budget exhausted
(autonomous pivot or definitive blocked result required; not plateau) · `64` invalid invocation or
ceiling arguments. Exit `2` also covers requirement-revision cleanup that cannot yet prove the
verified owner tree and canonical lock are gone; retry the same cancellation, never launch around it.
Fan-out exit `5` = a same-round sibling output already exists (re-running would contaminate).

Env: `MAGI_XFAMILY_CLAUDE_MODEL` (fallback legacy `MAGI_XFAMILY_MODEL`, default
`claude-fable-5`) · `MAGI_XFAMILY_GROK_MODEL` (default `grok-4.5`) ·
`MAGI_XFAMILY_TIMEOUT_S` (default `900`) · `MAGI_MAX_AUTONOMOUS_MODEL_LAUNCHES` (default `16`, tightening
only) · `MAGI_FANOUT_TIMEOUT_S` (default/max `900`, tightening only).

## Tests

```bash
python3 tests/test_docs_match_scripts.py     # doc-vs-code contract (exit codes, G-asserts, env)
python3 tests/test_campaign_guard.py          # global fuse, rollover, migration, prior/schema contracts
python3 tests/test_autorun.py                 # no-ack Stop continuation, plateau, terminal block
bash    tests/test_fanout_scrub.sh           # FIFO pre-write scrub + three-persona/sibling rail
bash    tests/test_inv7_lock.sh              # flock: both sides, concurrency, SIGKILL, recursion
bash    tests/test_plateau_gate.sh           # G1..G9 each block; a valid round passes
bash    tests/test_claude_provider.sh         # Claude default route + structural rail argv + provenance
bash    tests/test_grok_provider.sh           # Grok dispatch + provenance + family mismatch
bash    tests/test_stale_round_failclosed.sh  # failed rerun cannot leave stale success certifiable
MAGI_TEST_LIVE=1 bash tests/test_inv6_readonly.sh   # read-only rail + @file regression (live CLI)
MAGI_TEST_LIVE=1 bash tests/test_fanout_scrub.sh    # real codex -o FIFO interface probe
```
