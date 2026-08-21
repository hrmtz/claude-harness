# harness-magi-codex

**Codex is the orchestrator; Claude or Grok is the cross-family reviewer.** Claude remains the
default; Grok is an explicit fallback for Claude quota/capacity failures. The Claude
[`harness-magi`](../harness-magi/) package mirrors the human-readable contract but currently
fails closed because it does not yet ship a Claude-native structural runner.

version: see [`.codex-plugin/plugin.json`](.codex-plugin/plugin.json) (authoritative) · design:
[`docs/designs/CODEX_MAGI_MIRROR.md`](../../docs/designs/CODEX_MAGI_MIRROR.md)

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
schemas/finding.schema.json   Backward-compatible local SSOT; cross-field rules stay in validator.
schemas/finding.codex.schema.json
                              Provider response schema for Codex and cross-family constrained
                              decoding; deliberately stricter than persisted artifacts.
schemas/implementation-convergence.schema.json
                              opt-in report-only implementation manifest
schemas/preflight-{review,decision}.schema.json
                              one-shot Magi evidence and decision contracts
schemas/preflight-run.schema.json
                              structural three-reviewer run binding
schemas/protocol.external.json
                              generated hash-bound external protocol payloads
scripts/
  magi_autorun.py             session-bound no-ack campaign controller
  magi_fanout_codex.sh        3 personas as parallel `codex exec` (sole author of their prompts)
  magi_synthesize.py          lossless, deterministic synthesis envelope
  magi_xfamily.sh             provider-selectable adapter -> headless Claude or Grok
  magi_xfamily_claude.sh      backward-compatible Claude wrapper
  magi_campaign_guard.py      fixed global fuse + claim lifecycle + legacy migration
  magi_classify_failure.py    bounded content-free fan-out failure classification
  magi_validate_findings.py   validates cross-field convergence rules after constrained output
  magi_verify_round.py        write-free G1-G6/G9 verification
  magi_git.py                 ambient-config-free Git object reads
  magi_review_packet.py       exact-SHA/tree/full-diff manifest builder + history archive
  magi_protocol.py            closed protocol identity + immutable claim snapshots
  magi_deja_context.py        exact-SHA historical capture/select/render receipts
  deja_review_slice0.py       provenance-preserving local corpus normalizer/validator
  magi_rename_noreplace.py    atomic no-replace installer publication primitive
  magi_verify_canonical_templates.py
                              canonical prompt fingerprint verification
  magi_verify_xfamily_artifacts.py
                              cross-family findings/metadata pair verification
  magi_convergence_gate.py    report-only implementation convergence evaluator
  magi_convergence_kernel.py  pure normalization, delta, affordability, profile policy
  magi_design_convergence_gate.py
                              report-only Dual-Magi design convergence adapter
  magi_preflight.py           deterministic one-round Magi aggregation/veto
  magi_preflight_codex.sh     exactly-three structural pre-flight fanout
  magi_plateau_gate.sh        the ONLY thing that may write a plateau marker
  magi_lock.sh                flock(2) helper (recursion + concurrency guard)
  magi_scrub.py               redacts credential-shaped strings before anything hits disk
hooks/magi_autorun_hook.sh    Stop hook; continues armed campaigns to plateau/blocked
skills/{magi,dual-magi-review,ultramagi}/SKILL.md
tests/                        exit codes, G-asserts, lock semantics, read-only rail, doc-drift
```

One-shot Magi prompts are built deterministically by `magi_preflight.py` from the exact brief and
the bundled review contract. Multi-round dual-magi persona templates are not maintained as
hand-copied sources: fan-out reads the fingerprinted canonical files from `harness-magi` in a
checkout. Native detached plugin packages carry a generated, hash-bound payload of those same
external inputs. Each campaign materializes and pins one verified closed-protocol snapshot; the
payload is not an independently editable template source.

## Install

Install both the native `harness-core` and `harness-magi-codex` plugins from the
repository Codex marketplace; see
[`docs/codex_plugins.md`](../../docs/codex_plugins.md). `harness-core` supplies
the mandatory cross-CLI identity guard used for every reviewer launch.
The legacy `install-codex-skills.sh` symlink flow remains only for migration and
is removed with `uninstall-codex-skills.sh` after native plugin installation.
Both commands refuse foreign skill paths: the installer will not replace an
unowned directory or symlink, and the uninstaller removes only entries carrying
the harness ownership marker. The three skill publications commit as one
generation; a later publication failure restores earlier predecessors.

Requires `codex`, `flock`, `bubblewrap`, Python 3 with `jsonschema`, and the
selected reviewer CLI (`claude` or `grok`). Magi pre-flight uses a private
mount/PID namespace per reviewer to hide sibling staging artifacts (Codex's
state directory remains available to the CLI) and fails closed if `bubblewrap`
is absent.
A missing selected CLI fails closed (exit `2`). There is no automatic provider
fallback: the caller must explicitly choose Grok so provenance and routing
remain auditable.

`MAGI_MAX_ARTIFACT_BYTES` may tighten the shared fan-out/cross-family review
artifact ceiling from its 10 MiB default into `1..10485760`. Oversized input is
refused before a campaign claim or provider launch.

Deja Review context is optional and exact-SHA only. Round-1 fanout freezes one
selection from `${DEJA_REVIEW_STATE_ROOT:-$HOME/.deja-review}` into the Magi
state directory. Every selected Slice 0 campaign is validator-clean, every
finding reviewed the current target bytes, and the canonical payload is capped
at 8 findings / 12 KiB after credential scrubbing. Same-family and
cross-family prompts consume the same immutable block and publish
`deja-consumption-{fanout,xfamily}-r<N>.json` before provider launch.
Missing or individually invalid historical corpora do not block review;
frozen identity/digest drift and unprovable consumption fail closed. Successful
arms are captured only after their ordinary artifacts become authoritative,
and capture failure never changes the Magi verdict. Capture is synchronously
bounded to 120 seconds; `MAGI_DEJA_CAPTURE_TIMEOUT_S` may tighten that boundary
to `1..120`.

## Use

One-shot pre-flight consumes exactly three independent artifacts bound to the
same brief and never launches a second round:

```bash
scripts/magi_preflight_codex.sh /absolute/path/to/brief.md \
  /absolute/path/to/output-directory
```

The result is only `PROCEED`, `PIVOT`, or `ABORT`; unsupported minority roots
remain explicit questions, while grounded minority CRITICAL/security/data-loss/
irreversibility findings retain veto power. Every result is report-only and
sets `authorizes_shipping: false`.

The output directory is single-use. Exit `5` means canonical output already
exists, so retry with a fresh empty directory; exit `3` means an active run owns
the directory lock. `MAGI_PREFLIGHT_TIMEOUT_S` accepts `1..900` seconds and
defaults to `900`. Runner exit `1` covers dependency/provider/runtime failure,
exit `2` covers unsafe or incomplete input/evidence, exit `64` is usage, and
INT/TERM are preserved as `130`/`143`. An invalid brief may fail before an
envelope exists; unsafe evaluator input emits a report-only `ABORT` with
`UNSAFE_OR_INCOMPLETE_DESIGN_INPUT`.

```bash
D=docs/designs/MY_DESIGN.md; S=docs/designs/.dual-magi; mkdir -p "$S"

python3 scripts/magi_autorun.py arm "$D"                              # once per campaign
scripts/magi_fanout_codex.sh      "$D" 1 "$S" --persona-set magi     # same-family ×3
python3 scripts/magi_synthesize.py "$D" 1 "$S" \
  "$S/round_1_magi_synthesis.json" --persona-set magi
python3 scripts/magi_design_convergence_gate.py evaluate "$D"
scripts/magi_xfamily.sh --reviewer claude \
  "$D" 2 "$S/round_1_magi_synthesis.json" "$S/round_2_xfamily"
scripts/magi_plateau_gate.sh "$D" "$S/round_2_xfamily" --reviewer-family claude

# Explicit fallback when Claude is unavailable:
scripts/magi_xfamily.sh --reviewer grok \
  "$D" 2 "$S/round_1_magi_synthesis.json" "$S/round_2_xfamily"
scripts/magi_plateau_gate.sh "$D" "$S/round_2_xfamily" --reviewer-family grok
```

Follow the design evaluator's bounded next action before revising. A
`PLATEAU_CANDIDATE` still requires `magi_plateau_gate.sh`; it is not plateau.
`--persona-set bug-hunt` swaps the personas to review
an *implementation* instead of a design (ultramagi gate [4]).

For implementation campaigns, create an untracked exact-SHA packet at one stable path, review
that packet, then evaluate the already-charged history:

```bash
MANIFEST="$PWD/.magi-implementation-review.json"
STATE="$PWD/.magi-implementation-review-state"

python3 scripts/magi_review_packet.py \
  --repo "$PWD" --base <base-commit> --scope <issue-or-task> \
  --invariant <invariant-id> --deadline <RFC3339> \
  --output "$MANIFEST"

scripts/magi_fanout_codex.sh "$MANIFEST" 1 "$STATE" \
  --persona-set bug-hunt --prior -
python3 scripts/magi_synthesize.py "$MANIFEST" 1 "$STATE" \
  "$STATE/round_1_bug-hunt_synthesis.json" --persona-set bug-hunt
python3 scripts/magi_convergence_gate.py evaluate "$MANIFEST"

scripts/magi_xfamily.sh --reviewer claude "$MANIFEST" 2 \
  "$STATE/round_1_bug-hunt_synthesis.json" "$STATE/round_2_xfamily"
FINAL_DECISION="$(python3 scripts/magi_convergence_gate.py evaluate "$MANIFEST")"
printf '%s\n' "$FINAL_DECISION"
if printf '%s' "$FINAL_DECISION" | python3 -c \
  'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if (d.get("decision"), d.get("reason_code")) == ("BLOCKED", "REPORT_ONLY_READY_FOR_EXISTING_PLATEAU_GATE") else 1)'
then
  scripts/magi_plateau_gate.sh "$MANIFEST" "$STATE/round_2_xfamily" \
    --reviewer-family claude
else
  echo "not ready for plateau gate; follow the evaluator decision" >&2
fi
```

The packet embeds the exact target tree and a `--binary --full-index` diff. Updating the stable
packet path archives the prior bytes by SHA-256. Full review keeps the original base-to-target
diff; an eligible `--allow-incremental` rebuild changes the packet diff base to the immediately
preceding target SHA, so a fix review receives only the exact revision delta while historical
review artifacts remain bound to their Git revisions.

For a standard-risk fix, opt into the bounded incremental policy when rebuilding the packet:

```bash
python3 scripts/magi_review_packet.py \
  --repo "$PWD" --base <original-base> --scope <issue-or-task> \
  --invariant <invariant-id> --deadline <RFC3339> --allow-incremental \
  --output "$PWD/.magi-implementation-review.json"

scripts/magi_fanout_codex.sh path/to/implementation-review.json 1 state-dir \
  --persona-set bug-hunt --review-mode incremental
scripts/magi_xfamily.sh --reviewer claude path/to/implementation-review.json 2 \
  state-dir/round_1_codex.json state-dir/round_2_xfamily
```

The evaluator selects exactly one bug-hunt persona deterministically from the affected invariants.
The adapter mechanically wraps that one result in `round_1_codex.json` for the existing validated
prior-envelope contract; this is not another model launch.
The targeted claim costs 1 and still reserves 1 for the mandatory exact-SHA cross-family final
review. Incremental mode is denied unless a prior reviewed revision exists, risk is `standard`,
the exact fix is at most 8 paths and 200 changed lines, and no public-interface, trust-boundary,
persistence/schema/rollback, or design-invariant change is declared. Use `--surface-change
<kind>` (`public_interface`, `trust_boundary`, `persistence_schema_rollback`, or
`design_invariant`) when rebuilding the packet to force full review (or REDESIGN for a design
invariant).

The evaluator is read-only and report-only. It returns only `CONTINUE`,
`FINAL_REVIEW_REQUIRED`, `BLOCKED`, or `REDESIGN`; it never launches a reviewer, changes the
ledger, writes a plateau marker, emits PASS, or authorizes shipping. Two complete logical cycles
are the maximum: initial `fanout(3) -> xfamily(1)`, followed by either full fanout or an eligible
`targeted(1) -> xfamily(1)` fix cycle. Existing exact-revision G1-G9 plateau and human judgment
remain the PASS authority. After a clean cross-family cycle, `BLOCKED` with reason
`REPORT_ONLY_READY_FOR_EXISTING_PLATEAU_GATE` is an evaluator-terminal handoff: run
`magi_plateau_gate.sh` as shown above. It is not a hard campaign blocker and does not itself
authorize shipping. Every other `BLOCKED` reason remains fail-closed.

Every round after round 1 requires a schema-valid prior synthesis artifact from the same state
directory, canonical document identity, and immediately preceding round. Every output carries
`artifact_id` and `artifact_sha`. `dup_flag` is constrained to
`new`, `duplicate`, `regression`, `readiness-gap`, or `scope-expansion`; the last two cannot be
HIGH-or-worse.

Create full-round synthesis artifacts only with `magi_synthesize.py`. It emits
`reviewer: SYNTHESIS`, records every preceding-round source filename and SHA-256, and carries every
`<source-file>#<finding_id>` into the provenance-specific
`round_<N>_<persona-set>_synthesis.json` basename. This prevents a single reviewer output or
incomplete subset from masquerading as the round synthesis. Incremental targeted review remains
the exception: fan-out mechanically publishes its validated one-source
`round_<N>_codex.json` wrapper.

## Campaign guard

The default autonomous campaign stops after 12 weighted model launches: fan-out costs 3,
incremental targeted review costs 1, and cross-family costs 1. Fan-out and targeted admission both
preserve one weighted launch for the immediately following mandatory cross-family review. If that
reserve cannot be preserved, the campaign is blocked before any provider starts; denial is never
permission to ship. Cross-family admission charges only its real weight, so the reserve is not
charged twice.
Both reviewer adapters append to a canonical document-scoped campaign ledger before launching a
model. Retries normally consume budget; a fresh state directory or repeated round 1 cannot reset
it. The one exception is a claim-scoped Codex replacement after all three provider processes reject
the response schema before producing any structured-output bytes. The closed fan-out adapter must
publish the three bounded diagnostics, prove its exact provider process tree has exited, and
authorize recovery while it still owns the claim. The failed launch remains charged and visible.
Only the immediately following attempt-2 launch for the same campaign, round, phase, and artifact
may carry `replacement_for`, so the logical phase is charged once rather than twice. A second
startup rejection, partial fan-out, timeout, malformed or substantive response, arbitrary provider
exit, cancellation, or non-adapter request remains charged and cannot chain credit. Exit `4` means
`CAMPAIGN BUDGET EXHAUSTED — NOT PLATEAU`: apply an in-scope correction or scope/primitive change,
then invoke round 1. A changed document or review-protocol SHA rolls into the next campaign
automatically, without acknowledgement. The bounded replacement is the deliberate protocol-only
exception: it may run the corrected provider contract against the exact same artifact SHA.

The finalized #271 incident predates claim-scoped recovery and its legacy bounded artifact cannot
prove the newer `turn_observed` classification. It is repaired only by the reviewed closed
attestation in `magi_campaign_guard.py`, invoked as
`magi_campaign_guard.py repair-historical-startup <doc> <claim-id>`. This is not a generic
operator-evidence or acknowledgement path: the attestation pins the one canonical document ID,
claim, artifact SHA, original protocol SHA, completion time, six-launch history prefix hash, gross
usage 14/16, three-reviewer pre-turn provider stage, and three-unit credit. Any runtime-authored
JSON is ignored. The exact pre-repair history must match before a distinct `repairs` transition is
appended; launch entries remain unchanged. The selected attestation is embedded as an immutable
tombstone in that transition, so later allowlist cleanup cannot orphan the ledger; at-rest
validation rechecks its digest and history prefix after later launches. Unknown documents/claims,
changed history or identity, a live
claim, unchanged protocol, an existing replacement, or any second repair remain charged. Adding
another historical incident requires a reviewed protocol/code change.

`MAGI_MAX_AUTONOMOUS_MODEL_LAUNCHES` may tighten the per-campaign ceiling of 12; it cannot extend it.
All revision campaigns share a separate fixed global allowance of 16 weighted model launches.
Changing state directory is not a reset. Global exhaustion produces a definitive blocked result,
not an acknowledgement prompt.

Arming binds the workflow to the current Codex thread. On its intact path, the bundled Stop hook keeps the turn chain
moving without user acknowledgement until the exact-revision plateau marker exists or the
controller records a definitive blocked state. Two continued turns with no durable document or
ledger progress terminate blocked rather than loop. Hook input/registry/ledger parse or I/O errors
return one visible `decision: block`, persist a blocked registry when possible, and rely on the
independent campaign guard to keep provider spend bounded.

Fan-out and cross-family calls have tightening-only deadlines via `MAGI_FANOUT_TIMEOUT_S` and
`MAGI_XFAMILY_TIMEOUT_S` (default/max `900`).
Timeout and signal cleanup release the canonical lock, close the claim as failed, and preserve one
bounded retry. Exit `4` is reserved for the global fuse; state corruption exits `2`, and illegal
phase transitions exit `64`.

Each cross-family claim must match the document and review-protocol identity of its successful
same-family source. If the document changes between fan-out and cross-family, the cross-family
claim is refused before accounting and the new exact revision must start at round 1 fan-out. A
legacy history already stranded by terminal cross-family failures on one different exact revision
has one mechanically recognized recovery: round 1 fan-out starts a new campaign for that revision.
The failed launches stay charged, no replacement credit is created, and the fixed global fuse still
applies. Same-revision retry exhaustion remains blocked.

Use a revision-scoped state directory such as
`.dual-magi/revisions/$(sha256sum "$D" | cut -c1-16)` whenever round numbering restarts. Successful
launches retain their `state_dir` as exact-revision convergence evidence. The guard refuses reuse
of a state directory already bound to another document/protocol revision before accounting, while
fan-out also refuses existing same-round persona basenames before claiming. Same-revision
cross-family retries may clear their own stale canonical output after a new charged claim; they
cannot reach output promotion through a state directory bound to another exact revision.

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
Before a claim, fan-out checks that the local Codex launcher exposes the required `exec` interface.
That check writes only `round_<N>_fanout.PREFLIGHT_FAILED.json` metadata on failure. A charged
reviewer failure writes `round_<N>_fanout.<claim-id>.FAILED.json` with provider/scrubber exit codes,
byte counts, redaction counts, and a bounded classification. Neither failure artifact contains the
provider response, prompt, document, or scrubbed log content, and neither occupies a canonical
persona artifact path. Classifications distinguish missing child status, scrubber/provider/timeout
failure, live-document drift, empty output, JSON parse/schema/convergence rejection, post-scrub
corruption, exact artifact-identity rejection, and the narrowly allowlisted zero-output provider
schema startup rejection used by the claim-bound replacement path.
Fan-out lock I/O failure exits `2`; live lock contention exits `5`.

Env: `MAGI_XFAMILY_CLAUDE_MODEL` (fallback legacy `MAGI_XFAMILY_MODEL`, default
`claude-fable-5`) · `MAGI_XFAMILY_GROK_MODEL` (default `grok-4.5`) ·
`MAGI_XFAMILY_TIMEOUT_S` (default `900`) · `MAGI_MAX_AUTONOMOUS_MODEL_LAUNCHES` (default `16`, tightening
only) · `MAGI_FANOUT_TIMEOUT_S` (default/max `900`, tightening only).
`MAGI_CANONICAL_SKILLS_DIR` may override the canonical `harness-magi/skills` template root for a
compatible checkout layout.

## Tests

```bash
python3 tests/test_docs_match_scripts.py     # doc-vs-code contract (exit codes, G-asserts, env)
python3 tests/test_campaign_guard.py          # global fuse, rollover, migration, prior/schema contracts
python3 tests/test_convergence_gate.py        # exact-SHA packet + bounded implementation convergence
python3 tests/test_failure_classification.py  # content-free provider/FIFO/schema/identity diagnostics
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
