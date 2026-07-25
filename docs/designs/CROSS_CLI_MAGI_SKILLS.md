# Cross-CLI Magi skills

Status: release candidate; deterministic tests and the exact-revision review gate are required
before publication. Crash-window reconciliation is explicitly deferred; this increment fails closed and may
require a fresh bounded attempt after process death, but never treats residue as reviewed output.

## 1. Scope

Make `magi`, `dual-magi-review`, and `ultramagi` discoverable and executable from both Codex and
Kimi Code CLI without weakening the existing dual-magi plateau contract.

The support matrix after this change is:

| Skill | Claude plugin | Codex plugin | Kimi plugin |
|---|---|---|---|
| `magi` | existing | add | existing |
| `dual-magi-review` | existing | existing | add |
| `ultramagi` | existing | existing | add |

This change does not add Kimi as a reviewer provider. Kimi is a supported orchestrator surface.
The Kimi dual-magi port invokes the hardened runtime that already launches three independent Codex
reviewers and a mandatory Claude reviewer, with Grok as the explicit cross-family fallback.

## 2. Invariant

The invariant that must not break is:

> No Codex- or Kimi-orchestrated dual-magi or ultramagi workflow may claim plateau unless
> `magi_plateau_gate.sh` verifies a supported cross-family reviewer against the exact current
> artifact revision and G1 through G9 all pass.

The port must not replace the gate with prose, an orchestrator judgment, same-family agreement, or
an unchecked file-existence test.

## 3. Verified CLI facts

The following facts were probed locally against Kimi Code CLI 0.28.1:

1. `kimi --prompt` cannot be combined with `--plan`.
2. `kimi --prompt` cannot be combined with `--auto`.
3. `kimi --output-format stream-json --prompt ...` emits an assistant record followed by a meta
   record containing a `session_id`.
4. Kimi's non-interactive help exposes no read-only sandbox flag, tool allowlist, or JSON Schema
   output flag equivalent to the Codex and Claude rails used by the current runtime.
5. Kimi session provenance is recorded under
   `~/.kimi-code/sessions/*/session_*/agents/main/wire.jsonl`.

Consequently, adding Kimi as a reviewer provider now would either weaken the read-only invariant or
require a new OS sandbox and free-form-output parser. That is separate scope. The safe, useful port
is to let Kimi orchestrate the existing reviewed runtime.

## 4. Architecture

### 4.1 Codex `magi`

Add `plugins/harness-magi-codex/skills/magi/SKILL.md`. The installed skill resolves its owning
plugin and delegates to `magi_preflight_codex.sh`; `magi_preflight.py` constructs the three
persona-specific prompts deterministically from the exact brief snapshot and the bundled review
contract. It launches all three reviewers before consuming any result. The installed Codex skill
therefore does not depend on copied Claude persona files. It remains a pre-flight workflow; it
does not use the dual-magi campaign ledger or plateau marker.

Multi-round dual-magi review is a separate runtime path. Its fan-out reads the fingerprinted
canonical templates from `plugins/harness-magi`, and protocol generation pins those repository
files into each charged claim snapshot. The Codex plugin carries a generated, hash-bound payload
of those external protocol inputs so native detached plugin installations can materialize the same
snapshot without a source checkout. It is regenerated from the canonical files and
protocol-manifest tests detect drift; it is not a second editable template source.

The Codex installer and uninstaller must own all three skill names:

- `magi`
- `dual-magi-review`
- `ultramagi`

Both Codex and Kimi installers publish staged skill entries with the shared
`magi_rename_noreplace.py` primitive. It uses `renameat2(RENAME_NOREPLACE)` so a destination
created after ownership validation causes a fail-closed refusal instead of overwrite or a nested
directory move. Publication is one install-set transaction: validated predecessors remain under
private recovery names until every skill is published and, for Kimi, the runtime identity passes
its final check. Any earlier failure restores already-published skills in reverse order; only a
fully validated generation removes the predecessors. Rollback first displaces a published entry
to a unique private quarantine name and validates that exact displaced object; it never validates
one public pathname and then recursively deletes a potentially substituted object at that name.
Because a same-user process can also substitute the private name after validation, rollback does
not recursively delete quarantined publications. It restores predecessors and leaves those
complete quarantine trees for explicit recovery cleanup.

The Codex plugin manifest description, display metadata, and default prompt must mention the full
three-skill surface.

### 4.2 Kimi `dual-magi-review`

Add `plugins/harness-kimi/skills/dual-magi-review/SKILL.md`. It is a Kimi-facing orchestration
contract, not a duplicated implementation of the gate.

It resolves the runtime in this order:

1. `$HARNESS_MAGI_RUNTIME` when explicitly set.
2. `$KIMI_CODE_HOME/harness-magi-runtime` when `KIMI_CODE_HOME` is set.
3. `~/.kimi-code/harness-magi-runtime` otherwise.

The installer places a checkout-linked runtime symlink at that location. The checkout is a live
dependency, and `$HARNESS_MAGI_RUNTIME` must name a compatible `harness-magi-codex` plugin root.
The skill runs:

1. `magi_fanout_codex.sh` for three isolated Codex reviewer processes.
2. A schema-valid synthesis envelope covering every source finding.
3. `magi_xfamily.sh --reviewer claude`, or explicit Grok fallback.
4. `magi_plateau_gate.sh --orchestrator-family codex --reviewer-family ...`.

The gate's orchestrator family remains `codex`, because G2 describes the reviewer-family pair that
produced the artifacts, not the interactive shell that launched the scripts. Kimi is the workflow
controller; the same-family review panel is Codex. Naming Kimi as the artifact orchestrator would
misrepresent the actual family pair and require a weaker or novel G2 route.

The Kimi skill must state this distinction explicitly.

### 4.3 Kimi `ultramagi`

Add `plugins/harness-kimi/skills/ultramagi/SKILL.md`. It keeps the same phase boundaries as the
Codex port:

1. scope and invariant;
2. local design;
3. dual-magi gate;
4. implementation;
5. implementation bug-hunt;
6. final review and tests.

Kimi may implement reversible work, but the default family routing remains:

```text
Claude: planning / design plateau
Codex: implementation or executable review
Claude: adversarial design-intent review
Codex: final fixes and tests
```

When Kimi performs an implementation phase directly, the handoff must record that actual routing
and retain Codex executable review before ship. This makes Kimi support explicit without silently
changing the review-family contract.

### 4.4 Shared runtime installation — thin checkout port

Extend `plugins/harness-kimi/install-kimi-skills.sh` to install:

- the four Kimi skills (`magi`, `bug-hunt`, `dual-magi-review`, `ultramagi`);
- an ownership-safe symlink at `~/.kimi-code/harness-magi-runtime` pointing to the canonical
  checkout path `plugins/harness-magi-codex`.

This increment intentionally requires the checkout to remain present. The Kimi skill resolves the
symlink with `readlink -f` before each bounded review phase and fails clearly if the target is
missing. `$HARNESS_MAGI_RUNTIME` remains an explicit override. Detached copied-runtime packaging,
bundled templates, generation swaps, and runtime update locks are deferred until two real
Kimi-orchestrated campaigns pass G1-G9 or two manual handoffs are demonstrably eliminated.

The existing runtime still needs target-workspace correctness. `magi_fanout_codex.sh` must canonicalize the
document, prior, output, schema, and template paths before launching providers. It must derive the
review workspace with `git -C <document-directory> rev-parse --show-toplevel`, falling back to the
document directory when the target is not inside a Git worktree. `codex exec -C` uses that target
workspace, never a directory derived from the installed plugin. Prompts contain the canonical
absolute document path.

The campaign pins the review protocol even though the checkout is mutable. The manifest root is
the canonical checkout repository root, not the `harness-magi-codex` plugin root. Define the
protocol as a sorted manifest containing every shipped script and schema under
`plugins/harness-magi-codex`, the exact prompt templates under
`plugins/harness-magi/skills/{magi,bug-hunt}/templates`, the relevant Claude/Codex/Kimi
`dual-magi-review` and `ultramagi` contracts, and the new Codex `magi` contract. The manifest
includes the plateau gate, lock helper, scrubber, validator, campaign guard, fan-out, adapters, and
synthesis helper; adding a new executable input requires adding it to this closed manifest.
`$HARNESS_MAGI_RUNTIME` is compatible only when its canonical repository root contains those
enumerated siblings. Compute one digest from those canonical paths. The first successful
claim records `protocol_sha`; fan-out copies the closed manifest inputs into a claim-scoped
snapshot, requires its digest to match the generation returned by the claim, and composes provider
inputs only from that snapshot. Every later claim in the campaign
must match the recorded digest or fail closed before a provider launch. Cross-family metadata
records the same digest, and the plateau gate recomputes
the current runtime protocol digest and verifies it against both metadata and the successful phase
chain in the campaign ledger. A checkout generation that remains changed at a phase boundary
therefore blocks and requires a new revision campaign. An ABA edit during fan-out cannot alter the
snapshotted reviewer inputs, so it cannot mix protocol generations silently.

## 5. Installation ownership and safety

Both installers must gain ownership verification before replacing anything. The current Codex
installer does not perform this check despite its comment. In this increment, every one of its
three skill names (`magi`, `dual-magi-review`, and `ultramagi`) may be
replaced only when it is a symlink resolving to the expected source directory or a copied directory
whose `.harness-magi-codex` marker names that exact source. A foreign directory or foreign symlink
is a hard error and is never removed.

The current Kimi skill installer has no ownership check. Replace that behavior for all four owned
skill names. A destination may be replaced only when its `.harness-kimi-skill` marker records the
canonical source path and skill name. A foreign directory or symlink is a hard error and remains
unchanged. First installation stages each skill and marker before an atomic rename, so interruption
does not create an apparently foreign partial destination.

The runtime link may be replaced only when it is a symlink resolving to this checkout's canonical
`plugins/harness-magi-codex` directory. A foreign symlink, directory, or file is a hard error and is
never removed. Link creation is one atomic namespace operation; there is no copied generation,
partial runtime, backup, or cross-command runtime lock in this increment. A late runtime identity
failure is inside the Kimi install transaction and rolls all published skill replacements back.

## 6. Continuation limitation on Kimi

The Codex runtime's Stop hook is not installed into Kimi and the Kimi plugin documents Stop as
unsupported. Therefore the Kimi port cannot promise acknowledgement-free automatic continuation
through that hook.

Kimi must not invoke `magi_autorun.py`: that controller requires `CODEX_THREAD_ID` or an explicit
Codex session and exists solely for the Codex Stop hook. The campaign ledger is owned by
`magi_campaign_guard.py`, while exact-revision markers are owned by `magi_plateau_gate.sh`; neither
depends on autorun state. The Kimi port describes continuation honestly:

- one Kimi invocation drives the bounded loop while active;
- if the Kimi session ends, the durable `.dual-magi` state permits manual resume;
- no Kimi Stop hook is claimed;
- no plateau is claimed without the gate marker.

On resume, inspect `CAMPAIGN.<doc-id>.json`, the document review lock, and the active state
directory. A successful fan-out is
followed by synthesis and then the next numbered cross-family round; a successful cross-family
round is followed by the gate. A `running` ledger entry with no owning process is conservatively
abandoned by the next claim and remains charged. Exit 4 is terminal for the global allowance.
Expose these decisions in the Kimi skill; do not add a second heartbeat/state machine in this
bounded port because the canonical ledger already owns transition state.

This increment does not implement crash-window reconciliation. Provider output remains
claim-scoped until validation, successful ledger transition, and canonical promotion complete.
Ordinary EXIT/signal cleanup attempts to remove temporary state and emits an explicit error if
finalization or removal is incomplete. `SIGKILL` may leave claim-scoped staging or canonical
prepublication residue and a ledger/artifact mismatch. Synthesis and the plateau gate must reject
any cross-family pair without one matching successful ledger claim. Resume must inspect the
canonical ledger and canonical artifacts, then use the next legal
bounded attempt or an explicit new campaign. It must not edit the ledger manually or promote
claim-scoped residue.

The execution lock remains tri-state. Helper return `1` means live contention; return `2` means
lock-file open/I/O failure and fails closed loudly without claiming a live owner. Automated
execution-lock→ledger-lock reconciliation, staging quarantine, and commit-window recovery are a
separate increment. They are justified only after real Kimi campaigns show that bounded retry is a
material operational cost.

Plateau markers are published transactionally: write a same-directory temporary file, flush and
fsync it, `os.replace` it to the exact-revision name, then fsync the directory where supported.
Every completion/resume consumer parses the JSON and checks artifact SHA, protocol SHA, reviewer
family, and the complete G1-G9 assert list; file existence alone is never completion.

This is an orchestration limitation, not a gate weakening.

## 7. Failure behavior

All existing runtime exits retain their meanings:

- `0`: phase complete;
- `1`: fan-out/provider output failure, or plateau gate denial; implementation remains blocked;
- `2`: fail closed, no usable cross-family result;
- `3`: cross-family adapter lock held;
- `4`: global autonomous model-launch budget exhausted;
- `5`: fan-out lock contention or same-round fan-out output already exists; inspect the lock before
  considering cleanup;
- `64`: invalid invocation or invalid transition.

Lock I/O failure is a distinct fail-closed error path and is never reported as contention.

Signal exits `130` and `143` are interrupted/terminated phases and also block. Meanings are
command-specific; the Kimi skill treats every nonzero as non-success and reports the originating
command plus status.

The Kimi skill supplies a concrete `run_checked` Bash wrapper. It captures `$?` in the same tool
call, writes `MAGI_PHASE_FAILED phase=<phase> status=<status> command: <shell-escaped argv>` to the
handoff, and exits before the next phase or implementation.

The Kimi skill must not translate any nonzero gate or adapter exit into success. It must surface the
blocking condition and must not begin implementation after a failed design gate.

If Codex CLI is missing, the same-family fan-out fails before a valid round is produced. If Claude
is missing, the operator may explicitly select Grok. If neither cross-family provider exists, the
workflow is limited to a reversible spike or documentation and cannot claim plateau.

## 8. Schema and synthesis

No schema changes are required. Kimi orchestrates the same artifacts and validators. The synthesis
contract remains:

- `reviewer` equals `SYNTHESIS`;
- document identity and artifact hash match the current document;
- round number is the preceding round;
- every source artifact is listed with its digest;
- every source finding has one disposition;
- carried and duplicate dispositions reference a real synthesis finding;
- later phases consume the immediately preceding synthesis.

The Kimi skill must never hand-compose the three reviewer prompts. Only
`magi_fanout_codex.sh` authors them and launches the processes before reading output.

Add `magi_synthesize.py` to construct a lossless baseline envelope: it reads every source artifact,
copies every finding into synthesis with a `carried` disposition, records exact source digests, and
validates the result. The orchestrator may subsequently deduplicate or resolve entries, but must
re-run the validator immediately. Cross-family prompts include the lossless baseline plus raw
source artifact paths and digests so HIGH-or-worse source findings remain visible even if a later
semantic disposition is mistaken.

The helper assigns each copied finding a deterministic source-qualified synthesis ID and preserves
its original `source_ref`. The validator requires unique synthesis finding IDs and one carried
target per source finding unless explicit duplicate dispositions converge multiple sources on one
unique target. Colliding reviewer-local IDs are a required fixture.

## 9. Documentation

Update:

- `plugins/harness-magi-codex/README.md` for the Codex `magi` surface and target-workspace runtime
  behavior;
- `plugins/harness-kimi/README.md` for all four installed skills, runtime location, provider
  requirements, and the Stop-hook limitation;
- the Codex plugin manifest metadata;
- installer comments and terminal messages.

Avoid claiming that Kimi itself is a same-family reviewer in this version. Avoid claiming that
Kimi has the Codex autorun Stop hook.

## 10. Tests

Add or extend deterministic tests for:

1. Codex and Kimi expected skill directories exist.
2. Each new skill passes the Codex skill quick validator where its frontmatter is compatible.
3. Codex installer enumerates all three Codex skills.
4. Kimi installer enumerates all four Kimi skills.
5. Runtime installation creates the expected canonical symlink and is idempotent.
6. A foreign Kimi runtime path is not deleted or replaced.
7. A missing checkout target fails clearly before any provider launch.
8. `magi_fanout_codex.sh` resolves canonical templates from the checkout runtime.
9. Existing provider, gate, stale-round, lock, scrub, campaign, autorun, and docs-contract tests
   remain green.
10. README claims match the installer and runtime.
11. Foreign directories and foreign symlinks for all three Codex skill names survive installation
    refusal unchanged.
12. Detached runtime execution uses the target Git worktree and absolute document path.
13. Every documented nonzero outcome blocks the next lifecycle phase.
14. Foreign Kimi skill directories and symlinks survive installation refusal unchanged.
15. Protocol mutation between fan-out and cross-family claim fails before another provider launch.
16. The lossless synthesis helper carries every source finding and validates its envelope.
17. Mutating the gate, validator, synthesis helper, or a persona template changes protocol SHA and
    blocks transition or plateau.
18. A real provider execution lock blocks synthesis and gate publication.
19. Plateau marker fault injection never exposes a partial valid marker, and consumers reject a
    malformed marker.

Live reviewer calls are not required for the implementation test suite. Existing deterministic
provider stubs cover command construction and provenance. The Kimi orchestration port does not add
a Kimi reviewer adapter, so it introduces no untested Kimi output parser.

## 11. Rollback

Rollback is file-level and reversible:

- remove the Codex `magi` skill and restore its installer/manifest loop;
- remove the two Kimi skills and restore its installer loop;
- remove the installed `~/.kimi-code/harness-magi-runtime` only when it is the exact symlink owned
  by this installer.

No user data, database, remote service, or canonical production artifact is mutated by this
change.

## 12. Acceptance criteria

The change is complete when:

1. all three requested skills are present on both Codex and Kimi surfaces;
2. Kimi dual-magi invokes the same mechanical G1-G9 gate as Codex;
3. the documented checkout-linked runtime resolves correctly and the detached packaging
   limitation is explicit;
4. installers are idempotent and ownership-safe for newly introduced runtime state;
5. all deterministic tests and skill/plugin validators pass;
6. an adversarial implementation review finds no unresolved HIGH-or-worse issue;
7. documentation states the Kimi Stop-hook and reviewer-provider limitations accurately.

Commercial validation: within the first three real Kimi uses, require two exact-revision gated
campaigns and either two eliminated manual cross-CLI handoffs or at least 30 minutes of operator
time saved per campaign. Record only pass/fail and elapsed operator time; do not add telemetry. If
the threshold is missed, retain safe installer/runtime fixes but do not promote detached packaging.

Scope cut line: implement only this six-cell skill matrix and the checkout-linked runtime necessary
to make it work. Do not add Kimi as a reviewer provider, copied runtime generations, a heartbeat
daemon, crash-window reconciliation, or a new marketplace. Promote detached packaging or automated
reconciliation or atomic live-install generation swaps only after two real Kimi campaigns pass the
gate or bounded retry/manual recovery is
measured as a material cost. The explicit user request is demand for the matrix; the promotion
threshold separately tests demand for detached distribution and recovery automation.

The simultaneous six-cell matrix is an explicit user requirement, so a staged release that omits
Codex `magi` or Kimi `ultramagi` does not satisfy this increment. The economic cut line applies to
adjacent scope: no Kimi reviewer adapter or detached packaging is started, and those changes remain
closed unless the first-three-use commercial metric passes.

## 13. FAMILY_ROUTING

```text
preferred: Claude design -> Codex code -> Claude review -> Codex fixes/tests
actual: Codex drafted design -> Codex same-family fan-out -> Claude cross-family gate ->
        Codex implementation -> Claude implementation-intent review -> Codex fixes/tests
missing: Claude CLI is installed, but this Codex session has no integrated Claude worker tool that
         can own and edit the local design artifact; the bounded Claude adapter remains available
         as the mandatory exact-revision cross-family design reviewer
degraded_until: the exact-revision Claude cross-family gate passes before implementation
```
