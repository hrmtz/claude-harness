# MAGI safety-critical review mode — slice 01: manual admission core

Status: DRAFT — implementation prohibited until exact-revision cross-family review of this slice

Date: 2026-08-16

Owner: Claude planning (magi-safety-plan) under Formation briefing
`formation/briefings/magi-safety-review-admission-core.md`; operator-authorized for FA20 ECU
brick-risk review.

Parent: `docs/designs/MAGI_SAFETY_CRITICAL_REVIEW_EXTENSION.md` at SHA-256
`94c08abbe4b5f3a64f933aae1e4977c69cf8cdc33d539b06d29a6909a45f7b6f`, whose latest design
convergence returned `SCOPE_SPLIT` / `DESIGN_SAME_SUBSYSTEM_NEW_ROOTS_RECURRED`
(`docs/designs/.dual-magi/magi-safety-critical-mode-rev{2,3}/`). This slice is the smallest
independently implementable cut of that umbrella. The parent is not edited by this slice.

## 1. Status and exact decision

Implement exactly one thing: a **closed sidecar authorization** that lets the one exhausted FA20
document continue exact-revision Codex-fanout → Claude → Grok review cycles under manual
orchestration, admitting a claim past the model-spend fuse only after ordinary admission denies
solely on affordability. Everything else in the umbrella — autorun integration, heartbeat,
watchdog, `resume-safety-review`, cumulative storage management, recovery/credit changes, generic
safety-mode registration, any second document — is deferred to later slices.

Three deliberate deviations from the parent umbrella, each grounded below:

1. **Sidecar, not ledger schema v2.** Parent §9 migrates the ledger to `schema_version: 2`. The
   real v1 loader rejects any unknown top-level field
   (`plugins/harness-magi-codex/scripts/magi_campaign_guard.py:408-410` — strict
   `set(payload) != expected`), so a v2 ledger is unreadable to every existing reader
   (`magi_design_convergence_gate.py:225-236`, `magi_synthesize.py:88`, `magi_verify_round.py:251`)
   until all of them change, and rollback makes the ledger fail closed (parent §25 accepts this).
   Launch records, by contrast, are validated field-by-field with no closed field set
   (`magi_campaign_guard.py:434-580`), so safety fields on launches are v1-compatible. The sidecar
   keeps `CAMPAIGN.<doc_id>.json` at schema version 1 forever; old code can always read history.
2. **Erratum: the parent cites a nonexistent reason code.** Parent §14 forbids
   `DESIGN_NEXT_XFAMILY_UNAFFORDABLE`; no such code exists. The real affordability terminals in
   the design profile are `DESIGN_NEXT_FANOUT_UNAFFORDABLE`
   (`magi_convergence_kernel.py:355`) and `DESIGN_FINAL_DIVERSE_RECHECK_UNAFFORDABLE`
   (`magi_convergence_kernel.py:344`). This slice excludes exactly those two, nothing else.
3. **Manual-only is enforced, not assumed.** `magi_autorun.py` refuses to drive a
   sidecar-active document (§12.4); the parent's autorun continuation (§16) is deferred.

## 2. Grounded current-code facts

Every fact below was read from this worktree at authoring time.

- Ledger: `<doc-dir>/.dual-magi/CAMPAIGN.<doc_id>.json`, `doc_id = sha256(abs-path)[:16]`
  (`magi_campaign_guard.py:160-161,314-315`). Top level strict v1; campaign level strict
  (`:417-433`); launch level open, field-by-field (`:434-580`).
- Locks: adapters take the review lock `.review.<doc_id>.lock` first via flock fd 9
  (`magi_lock.sh:18`, `magi_xfamily.sh:197`, `magi_fanout_codex.sh:336`), then the guard takes
  the campaign lock `.campaign.<doc_id>.lock` inside `document_lock`
  (`magi_campaign_guard.py:318-323`). Review → campaign, never reversed.
- One live claim: `claim()` scans every campaign for nonterminal launches and raises
  `TransitionError` if any exists (`magi_campaign_guard.py:1459-1470`).
- Transition engine alternates phases: after a success the expected next phase is
  `"xfamily" if last_phase in {"fanout","targeted"} else "fanout"`
  (`magi_campaign_guard.py:849`). Two consecutive xfamily rounds are unrepresentable — this is
  why a dedicated safety transition function is required.
- Retry: at most 2 attempts per (round, phase); the third is `transition-blocked`
  (`magi_campaign_guard.py:832-846`). Weight-0 replacement exists only for the attempt
  immediately following `startup-failed-recoverable` (`:858-872,1519-1526`).
- Budget: `PHASE_WEIGHT = {fanout:3, targeted:1, xfamily:1}` (`:47`), campaign ceiling 12
  (`:46`), global fuse 16 (`:64`), xfamily reserve 1 (`:59`). Denial raises `BudgetDenied`,
  exit 4, printing `CAMPAIGN BUDGET EXHAUSTED — NOT PLATEAU` (`:1528-1529,1944-1952`).
- Rollover: changed artifact (or, for fanout, changed protocol) after a terminal launch opens an
  `automatic-rollover` campaign restarting at round 1 (`:1391-1418,1493-1514`).
- Successful finish re-checks artifact and protocol SHA (`:1601-1609`).
- Accounting: `model_launches()` = gross weights minus replacement and historical credits
  (`:976-993`). Canonical hashing: `canonical_sha256` = sha256 of sorted-keys compact JSON
  (`:360-362`); durable writes via `atomic_json` (`:326-339`, no directory fsync — the plateau
  gate's `fsync_control_dir` at `magi_plateau_gate.sh:78-90` is the directory-fsync precedent).
- Compiled closed attestation precedent: `HISTORICAL_STARTUP_INCIDENTS`
  (`magi_campaign_guard.py:102-120`) — runtime/operator-authored evidence deliberately
  unsupported; adding an incident requires a reviewed protocol change.
- Protocol identity: `magi_protocol.py` `RUNTIME_FILES` (`:26-64`) + `REPOSITORY_FILES`
  (`:66-82`); `protocol_sha()` hashes the closed manifest; claims snapshot it
  (`magi_xfamily.sh:319`). Every file this slice edits (guard, kernel, design gate, plateau
  gate, `magi_xfamily.sh`, `magi_autorun.py`) is already in `RUNTIME_FILES` — **no manifest list
  changes**, so `guard.PROTOCOL_FILES` (`:74-95`) and its test
  (`tests/test_convergence_gate.py:293`) stay untouched.
- Cross-family adapter: `magi_xfamily.sh --reviewer claude|grok` (`:50-60`), models
  `claude-fable-5` / `grok-4.5` defaults (`:204-207`), outputs `round_<N>_xfamily.json` +
  `.meta.json` (`:212-214`), claim at `:297-300`. Meta binds reviewer_family, model_id,
  requested_model, session_id, transcript_path/sha (`:631-654`).
- Post-run binding: `verify_round` G1–G6/G9 (`magi_verify_round.py`) checks family markers
  (`:25-30`), artifact/protocol SHA (G3), output_sha (G4), exactly one successful matching
  ledger claim (G6, `:249-273`), transcript model vs requested model — silent same-family
  downgrade detection (`:351-362`). `magi_plateau_gate.sh` owns G7/G8 and is the only writer of
  `PLATEAU.<doc_id>.<sha16>` markers (`:60-66,115-129`).
- Synthesis: `magi_synthesize.py` fixes basenames `round_<N>_{magi,bug-hunt,xfamily}_synthesis.json`
  (`:299`), discovers exact sources (`:138-155`), and for xfamily requires the committed pair plus
  one matching successful ledger claim (`:60-106`).
- Design convergence: `magi_design_convergence_gate.py evaluate` filters launches to the current
  `protocol_sha` (`:326-329`), summarizes blocking roots per revision via the pure kernel, and
  projects affordability through `bounded_admission_decision` (`:397-424`). Terminals:
  `DESIGN_BLOCKING_ROOT_REPEATED` (REDESIGN), `DESIGN_SAME_SUBSYSTEM_NEW_ROOTS_RECURRED`
  (SCOPE_SPLIT), `DESIGN_BLOCKER_MASS_STALLED`, `DESIGN_MAX_LOGICAL_CYCLES_REACHED`
  (`MAX_LOGICAL_CYCLES = 2`, `magi_convergence_kernel.py:22`), `DESIGN_RETRY_BUDGET_EXHAUSTED`
  (gate `:389-395`), plus the two affordability codes named in §1.
- Autorun: a Stop-hook loop driver; it imports `campaign_admission_status`
  (`magi_autorun.py:19`) and keeps sessions running by emitting `{"decision": "block"}` (`:355`).

FA20 target facts, re-verified against the live repository at authoring time:

- `doc_id("/home/hrmtz/projects/nakamura-fdr-fa20-flasher-plan/docs/designs/FA20-SELF-FLASHER/01-evidence-capability-correction.md")`
  = `367f2e268185c932` (recomputed).
- Exhausted ledger SHA-256 = `2f448bb5a112e84beb5ba6d1c3e5e1755566f11b140670569689cec72c94de21`
  (recomputed from the live file).
- Net used weight = 16 across 4 campaigns / 8 launches, no repairs, no replacements; the last
  campaign ends `round 1 fanout success, round 2 xfamily success` — so ordinary
  `next_transition` offers round 3 fanout and `bounded_admission_decision` denies it on global
  affordability alone (16 + 3 + 1 > 16). Exactly the admission state this slice keys on.
- Current artifact SHA-256 = `7491db24de45c43149a649fc141ed5a46a3acf8b36b1d4e5973909903ecad450`
  (recomputed; equals the attestation's `source_artifact_sha256`).

## 3. Scope and non-goals

In scope: closed activation for the exact FA20 identity; append-only evidence beside the
unchanged v1 ledger; manual orchestration only; affordability-only bypass for the closed target;
per-revision fanout → Claude → Grok with the existing two-attempt retry, fully charged and
linked; one live claim; both providers required at plateau; ordinary 12/16 untouched; targeted
tests, mutation requirements, rollback, plugin reinstall boundary; a simple claim-time
free-space preflight.

Deferred (later slices, listed in §18): autorun continuation; heartbeats/watchdogs/resume/alerts;
storage management beyond adapter ceilings + the preflight; new recovery/credit semantics;
generic safety-mode registration; multiple documents; protocol-drift re-attestation flow.
Forbidden here and in every slice: J2534 open, CAN transmit, diagnostic session, security
access, erase, write, flash, upload; treating review evidence as a vehicle flash receipt.

## 4. Threat model and invariants

Unchanged from parent §5: this protects cooperating processes against accidental skip, stale
reuse, label mismatch, concurrency, and budget mistakes. It does not resist an adversarial
same-UID process; no forgery resistance is claimed.

Invariants I1–I10 of the parent hold verbatim with two mechanical restatements:

- **I3 (append-only)** is realized by never rewriting `CAMPAIGN.<doc_id>.json` semantics: it
  stays schema v1; safety claims append ordinary-shaped launches plus three additional launch
  fields (§9); all prior bytes' meaning is unchanged.
- **I5 (fixed sequence)** is realized by `safety_next_transition` / `validate_safety_transition`
  (§8), because the ordinary alternating engine (`magi_campaign_guard.py:849`) cannot express
  round 2 xfamily → round 3 xfamily.

Additional slice invariant **I11 — validation vs grant separation**: on a sidecar-active
document, safety *validation* (sequence position, reviewer family, revision uniqueness, protocol
equality) applies to **every** claim; the safety *grant* (affordability bypass) applies only when
ordinary admission fails solely on affordability. Without I11, a weight-0 replacement claim
(`:1519-1526`) is ordinarily affordable at 16/16 and would slip past the safety checks.

## 5. Closed attestation and activation CLI

Compiled constant in `magi_campaign_guard.py`, adjacent to `HISTORICAL_STARTUP_INCIDENTS` and
following the same closed-allowlist rationale (`:96-101`):

```python
SAFETY_REVIEW_ATTESTATIONS: tuple[dict[str, object], ...] = (
    {
        "mode_id": "fa20-self-flasher-brick-risk-2026-08-16",
        "risk_code": "VEHICLE_ECU_BRICK_RISK",
        "doc_id": "367f2e268185c932",
        "canonical_doc_path": "/home/hrmtz/projects/nakamura-fdr-fa20-flasher-plan/docs/designs/FA20-SELF-FLASHER/01-evidence-capability-correction.md",
        "source_ledger_sha256": "2f448bb5a112e84beb5ba6d1c3e5e1755566f11b140670569689cec72c94de21",
        "source_artifact_sha256": "7491db24de45c43149a649fc141ed5a46a3acf8b36b1d4e5973909903ecad450",
        "source_protocol_sha256": "54575192c98bcc7fa711f663e59d0647f15a236f198ced9fc2425c1b7a7cccc4",
        "source_used_weight": 16,
        "operator": "hrmtz",
        "revision_sequence": [
            {"position": 1, "round": 1, "phase": "fanout", "weight": 3, "reviewer_family": None},
            {"position": 2, "round": 2, "phase": "xfamily", "weight": 1, "reviewer_family": "claude"},
            {"position": 3, "round": 3, "phase": "xfamily", "weight": 1, "reviewer_family": "grok"},
        ],
    },
)
```

The guard is in `RUNTIME_FILES`, so the attestation is inside the protocol hash; supporting
another target requires a reviewed protocol change. New CLI subcommand (parser `:1863-1898`):

```text
magi_campaign_guard.py activate-safety-review DOC \
  --mode-id fa20-self-flasher-brick-risk-2026-08-16 \
  --risk-code VEHICLE_ECU_BRICK_RISK \
  --expected-ledger-sha256 2f448bb5…de21 \
  --operator hrmtz
```

Under both locks (§7) it verifies document identity **before resolution** — `canonical_doc`
(`:153-157`) resolves symlinks, so a resolved-path comparison alone cannot reject an alias that
points at the attested file. The implementable contract: (a) the lexical input path
(expanduser + normpath, no symlink following) must byte-equal the attested
`canonical_doc_path`; (b) `lstat` of the final component must not be a symlink (precedent:
`magi_design_convergence_gate.py:272-273`); (c) `Path(path).resolve(strict=True)` must equal
the lexical path, rejecting symlinked parent directories. Then it: strictly loads the v1
ledger; compares the whole ledger file SHA against
`--expected-ledger-sha256` **and** the attestation's `source_ledger_sha256`; selects exactly one
attestation by (mode_id, doc_id) and compares path, risk code, operator, source artifact SHA
(recomputed from the document), and `model_launches(campaigns) == source_used_weight == 16`;
requires every launch terminal (same scan as `:1459-1470`); requires no existing sidecar file
and no `PLATEAU.<doc_id>.*` marker; then writes the sidecar (§6) via `atomic_json` followed by a
control-directory fsync. It starts no provider, opens no revision, and never touches
`CAMPAIGN.<doc_id>.json`. Failure exits: 64 usage/transition, 2 state corruption, 3 review-lock
contention (new mapping, mirroring the adapters' exit 3). Success prints
`SAFETY REVIEW MODE ACTIVATED: MODE_ID=… ACTIVATION_PROTOCOL_SHA=…`.

## 6. Sidecar schema and canonical encoding

Path: `<doc-dir>/.dual-magi/SAFETY.<doc_id>.json`, written only by `activate-safety-review`, the
guard's lazy revision materialization (§8.3), and the plateau gate (§10). Exact fields, no
extras tolerated on load:

```json
{
  "schema_version": 1,
  "kind": "magi-safety-review-sidecar",
  "doc_id": "367f2e268185c932",
  "doc_path": "…canonical absolute path…",
  "mode_id": "fa20-self-flasher-brick-risk-2026-08-16",
  "risk_code": "VEHICLE_ECU_BRICK_RISK",
  "operator": "hrmtz",
  "activated_at": "RFC3339 UTC",
  "source_ledger_sha256": "64 lowercase hex",
  "source_artifact_sha256": "64 lowercase hex",
  "source_protocol_sha256": "64 lowercase hex",
  "source_used_weight": 16,
  "activation_protocol_sha256": "64 lowercase hex",
  "attestation": { …verbatim copy of the compiled record… },
  "attestation_sha256": "canonical_sha256(attestation)",
  "revisions": [
    {
      "artifact_sha256": "64 lowercase hex",
      "campaign_id": "uuid of the rollover campaign that reviewed it",
      "opened_at": "RFC3339 UTC",
      "closed_at": "RFC3339 UTC",
      "claim_ids": ["…2 to 6 ids, ledger order…"],
      "status": "reviewed | blocked",
      "status_reason": "stable reason code or empty"
    }
  ]
}
```

Encoding is exactly `atomic_json` (`indent=2`, trailing newline) + directory fsync;
`attestation_sha256` uses `canonical_sha256` (`:360-362`). The parent-v2 fields
`heartbeat_at` / `last_progress` are **absent** (deferred autorun slice), and — deliberately —
so is any stored mode `status`.

**Mode status is derived, never stored.** There is no two-file transaction anywhere: claims
mutate only the ledger (§8.3), and the mode's state is a pure function of
(ledger, current `protocol_sha()`, plateau marker): `plateau` iff a `PLATEAU.<doc_id>.*` marker
exists for a safety revision artifact; `blocked` iff any safety position is retry-exhausted or
the current protocol differs from `activation_protocol_sha256`; otherwise `active`. Convergence
semantic terminals additionally block admission at evaluation time (§11). A stored status could
only ever disagree with this derivation, so it does not exist.

The `revisions` array holds **completion receipts**: append-only records written only when a
revision is complete (`reviewed` = position 3 success; `blocked` = retry exhaustion), each fully
recomputable from the ledger. A crash before a receipt append loses nothing — the next
admission or gate run recomputes and appends it idempotently (§8.3); a stored receipt that
differs from its recomputation is corruption.

`load_safety_sidecar(doc, ledger)` validates on every read: strict JSON (duplicate-key
rejection via `strict_json_loads`), exact field sets at both levels, lowercase-64-hex SHAs,
`doc_id`/`doc_path` match the document, attestation byte-equals the compiled constant and its
hash, revisions unique by `artifact_sha256`, none equal to `source_artifact_sha256`, every
receipt byte-equal to its ledger recomputation, and every `claim_id` resolving to exactly one
ledger launch carrying the matching safety fields (§9). Any mismatch ⇒ `StateError`, exit 2,
`SAFETY_SIDECAR_STATE_CORRUPTION`. The **ledger remains the sole source of truth for claims**;
the sidecar only authorizes admission and carries the activation attestation plus verifiable
receipts.

## 7. Lock ordering and one-live-claim proof

Lock ownership is deliberately unchanged: the review lock `.review.<doc_id>.lock` is held by
the **adapters** (`magi_xfamily.sh:197`, `magi_fanout_codex.sh:336`, both before their guard
claim at `:297`/`:362`) and by the plateau gate; the guard's `claim`/`finish` take only the
campaign lock `.campaign.<doc_id>.lock` (`document_lock`, `:318-323`), exactly as today. The
safety path adds **no** review-lock acquisition inside `claim()` — the calling adapter already
holds it on fd 9, and a second open file description would self-contend. Adapters continue to
close `MAGI_LOCK_FD` in provider descendants (`magi_xfamily.sh:433,449`; `magi_lock.sh:15-16`).

Two safety-specific rules:

- **Standalone commands** own both locks in adapter order: `activate-safety-review` takes the
  review lock first (non-blocking flock; contention exits 3), then the campaign lock, and
  releases campaign → review. The plateau gate already holds the review lock
  (`magi_plateau_gate.sh:36-43`) and takes the campaign lock only around its receipt append.
- **Safety claims must prove the review lock is actually held.** Adapter identity alone
  (command-line SHA) cannot prove the flock was taken before the claim, so the guard verifies
  the *inherited lock itself*: for a safety-admitted claim it requires inherited fd 9
  (`MAGI_LOCK_FD`, `magi_lock.sh:18` — the shell's `exec 9>` fd carries no `FD_CLOEXEC` and is
  inherited through the adapters' command substitution into the guard subprocess), checks
  `fstat(9)` dev/ino equals `lstat` of the exact regular file `.review.<doc_id>.lock`, then
  calls `flock(9, LOCK_EX | LOCK_NB)` on that same open file description — a no-op success if
  the adapter already holds it, an acquisition that persists on the shared description if it
  was unlocked, and a hard failure if a competing holder exists. Additionally
  `--owner-pid`/`--adapter-kind` (optional today, `:1432-1447`) become mandatory and the
  owner's command line must name the official `magi_fanout_codex.sh` or `magi_xfamily.sh` by
  SHA-256 — the `verified_recovery_adapter` pattern (`:1005-1019`). Either proof missing denies
  with `SAFETY_CLAIM_REQUIRES_OFFICIAL_ADAPTER`. A positive test must demonstrate real fd-9
  inheritance through the adapters' command-substitution call path (§13).

One live claim needs no new mechanism: every safety claim flows through the existing `claim()`,
whose nonterminal scan across **all** campaigns (`:1459-1470`) already refuses a second claim,
and `cancel-revision` remains the only cleanup path. Activation additionally requires the same
all-terminal condition. Proof obligation for tests: with a running safety claim, `claim`,
`activate-safety-review`, and a concurrent second activation all fail; exactly one of two
simultaneous activations succeeds; no path deadlocks in either lock order; a paused provider
descendant does not hold the review lock after adapter death (§13).

## 8. Dedicated transition state machine

### 8.1 Sequence and exact names

Each corrected revision runs in its own campaign (same `new_campaign` shape as
`automatic-rollover`, `:342-349,1507-1510`, with operator `safety-revision` and the revision
SHA in the reason; opened by the safety path itself, §8.3 — **not** by ordinary `may_rollover`),
rounds restarting at 1, with one state directory per revision:

```text
state dir:  <doc-dir>/.dual-magi/safety-<doc_id>-<artifact_sha256[:12]>/
position 1  round 1 fanout   round_1_melchior.json / round_1_balthasar.json / round_1_caspar.json (+ .log)
            synthesis        round_1_magi_synthesis.json        (--persona-set magi)
position 2  round 2 xfamily  round_2_xfamily.json + round_2_xfamily.meta.json   (claude)
            synthesis        round_2_xfamily_synthesis.json     (--persona-set xfamily)
position 3  round 3 xfamily  round_3_xfamily.json + round_3_xfamily.meta.json   (grok)
            synthesis        round_3_xfamily_synthesis.json     (--persona-set xfamily)
```

These are exactly the basenames the existing adapters and `magi_synthesize.py` already emit and
enforce (`magi_xfamily.sh:212-214`, `magi_synthesize.py:299`); only the two-xfamily *ordering*
is new.

### 8.2 `safety_next_transition` / `validate_safety_transition`

New pure functions in `magi_campaign_guard.py`, used instead of `next_transition` /
`validate_transition` whenever the sidecar is active. `safety_next_transition(launches)` over
the active (revision) campaign returns the same shapes as `next_transition`:

- empty → candidate position 1 (round 1, fanout, attempt 1);
- last nonterminal → running / cancellation-in-progress (same reasons as `:810-823`);
- last failed/abandoned/startup-failed-recoverable with `same_attempts < 2` → candidate, same
  position, attempt 2; with `same_attempts >= 2` → transition-blocked,
  `SAFETY_PHASE_RETRY_EXHAUSTED`;
- last success at position n < 3 → candidate position n+1 (round n+1, phase and family from
  `revision_sequence`), attempt 1;
- last success at position 3 → transition-blocked, `SAFETY_REVISION_REVIEWED` (a new revision
  requires changed bytes; §8.3).

`validate_safety_transition(launches, round_no, phase, reviewer_family)` accepts only the exact
candidate (round == position, phase matches, family matches: position 1 ⇒ family absent,
position 2 ⇒ `claude`, position 3 ⇒ `grok`); everything else raises `TransitionError` with
`SAFETY_SEQUENCE_POSITION_MISMATCH` or `SAFETY_REVIEWER_FAMILY_MISMATCH`. Successful positions
cannot repeat (inherited shape from `:892-895`).

### 8.3 Claim flow on a sidecar-active document

Exact order inside `claim()` — the safety path **replaces** the ordinary transition engine
rather than running after it, because ordinary `validate_transition`/`may_rollover` would
reject both the first corrected round-1 fanout (the last ordinary campaign expects round 3
fanout) and the round-3 Grok claim (the alternator expects fanout after Claude's xfamily
success, `:849`; `may_rollover` requires `round_no == 1`, `:1404`):

0. Common validation exactly as today: `canonical_doc`, strict v1 `load_ledger`,
   `--expected-artifact-sha` equality, and the all-campaigns nonterminal scan (`:1450-1470`).
   Then detect and validate the sidecar; if absent, continue on the untouched ordinary path.
   With an active sidecar, `next_transition`/`validate_transition`/`may_rollover` are never
   consulted for this claim; steps 1–7 below are the sole legality authority (I5, I11).

1. Protocol equality first: `protocol_sha() == activation_protocol_sha256`, else deny with
   `SAFETY_PROTOCOL_DRIFT_REATTESTATION_REQUIRED`; the mode thereby *derives* as `blocked` (§6)
   with no write. Protocol-only rollover (`may_rollover` `:1415-1418`) is unreachable in
   safety mode.
2. Lazy receipt materialization: if a completed revision (position 3 success → `reviewed`;
   retry exhausted → `blocked`) has no sidecar receipt yet, recompute it from the ledger and
   append it before any new admission. Idempotent and crash-safe: the ledger alone defines the
   truth, a missing receipt is regenerated, a divergent one is corruption (§6). No status
   transition is written anywhere — `blocked` is derived.
3. Revision binding: a claim either continues the running revision (artifact equals its
   `artifact_sha256`) or opens a new one at position 1, whose artifact must differ from
   `source_artifact_sha256` and from every prior revision (`SAFETY_UNCHANGED_ARTIFACT_REROLL_DENIED`).
   The pre-activation artifact `7491db24…` is itself reroll-denied: it already consumed a full
   ordinary review and its unresolved HIGH findings require byte changes regardless. Safety
   positions derive **exclusively from launches bearing safety fields** — the pre-activation
   successes on `7491db24…` (round 1 fanout + round 2 Claude xfamily in the last ordinary
   campaign) can never satisfy any safety position or contribute to a safety plateau.
   Opening a new revision appends a `new_campaign` with operator `safety-revision` (§8.1) in
   the same ledger write as the launch, mirroring `planned_rollover` (`:1532-1533`).
4. Adapter proof: inherited fd-9 review-lock verification plus official-adapter owner binding
   (§7), then sequence/family validation via `validate_safety_transition` (**always**, per I11).
5. Free-space preflight (§14).
6. Grant: run the pure affordability arithmetic (`bounded_admission_decision` over
   **ordinary-only** usage, §8.4; weight 0 iff `replacement_source` matches the safety retry,
   `:858-872`). Legality was fully decided in steps 1–5, so by construction this can only
   pass (in practice: weight-0 replacements → ordinary grant) or fail on affordability alone →
   admit under the safety grant, recording gross spend honestly.
7. Launch append: ordinary launch payload (`:1536-1548`) plus the three safety fields (§9), one
   `atomic_json` write under both locks — claims never need a paired sidecar write.

Printed accounting (exact, parent §10): `SAFETY REVIEW CLAIMED: ordinary global model launches
16/16; safety-review gross launches <G>; revision <sha12> step <n>/3; CLAIM_ID=…;
PROTOCOL_SHA=…`. Never a raised ordinary ceiling.

### 8.4 Accounting split

`ordinary_model_launches(campaigns)` = existing `model_launches` restricted to launches without
`safety_mode_id`; `safety_gross_launches(campaigns)` = gross weights of safety launches (no
credits, replacements charged per existing rules). Ordinary admission everywhere (guard and
design gate) uses ordinary-only numbers, so I1 stays exact: normal documents have zero safety
launches and are numerically unchanged, and FA20's ordinary count is frozen at 16.

## 9. Claude/Grok adapter identity binding

Safety-admitted launch records add exactly three fields; the sidecar-validating loader rejects
them on any launch of a document without an active/terminal sidecar, and rejects safety-field
launches whose ledger has no sidecar:

```json
{
  "safety_mode_id": "fa20-self-flasher-brick-risk-2026-08-16",
  "safety_revision_sha256": "64 lowercase hex",
  "reviewer_family": "claude | grok"        // xfamily positions only; absent for fanout
}
```

`magi_xfamily.sh` adds `--reviewer-family "$REVIEWER"` to its claim invocation (`:297-300`).
Guard rules for the new optional claim flag: allowed only with `phase == xfamily`, value in
`{claude, grok}`; on a sidecar-active document it is **required** and must match the position's
family; on an ordinary document it is validated and discarded — ordinary ledger bytes never
change shape. `magi_fanout_codex.sh` passes nothing; a `--reviewer-family` with `phase=fanout`
is a `UsageError`.

**Exact claim↔evidence join.** Measured fact: today's `round_N_xfamily.meta.json` keyset
(`magi_xfamily.sh:631-654`) carries no ledger `claim_id`, and neither do the fanout persona
outputs or the Deja receipts. Time- or order-based inference is forbidden, so this slice binds
evidence to claims two ways:

- **xfamily (positions 2, 3):** `magi_xfamily.sh` adds `"claim_id": CLAIM_ID` to the meta it
  writes (the adapter owns both the claim and the meta; one added argv into the meta-builder).
  Existing validators read named keys and enforce no closed meta keyset
  (`magi_verify_xfamily_artifacts.py:75-96`, `magi_verify_round.py`), so the addition is
  compatible; the safety plateau gate then requires `meta.claim_id` to equal the unique
  successful ledger claim for that position **and** keeps the existing G6 5-tuple check.
- **fanout (position 1):** persona outputs are provider-authored under the closed finding
  schema, so no field is added there. The join is the existing ledger-side 5-tuple —
  (status=success, phase, round, artifact_sha, protocol_sha, state_dir) with cardinality
  exactly 1, precisely the join G6 already enforces for pairs
  (`magi_verify_round.py:249-273`) and `magi_synthesize.py:60-106` enforces for syntheses.
  Uniqueness holds because successful positions cannot repeat within a campaign, one revision
  is one campaign, and revision artifacts are unique across the mode.

Claim-time family is one binding layer. The other post-run layers remain authoritative: meta
`reviewer_family`/`model_id`/`requested_model`/transcript binding (`magi_xfamily.sh:631-654`),
G2 family markers and G6 transcript/model/downgrade checks
(`magi_verify_round.py:196-225,290-362`). The safety plateau gate (§10) additionally
cross-checks claim-time family against meta family for both xfamily rounds.

## 10. Synthesis and plateau requirements

Synthesis chain per revision (all existing tooling, no changes to `magi_synthesize.py`):
round 1 personas → `round_1_magi_synthesis.json`; Claude consumes it as `--prior`
(validated by `--prior-for-round 2`); Claude pair → `round_2_xfamily_synthesis.json`; Grok
consumes that as prior for round 3; Grok pair → `round_3_xfamily_synthesis.json`. Handwritten
synthesis, missing sources, and cross-revision reuse stay impossible via the existing exact
source discovery (`magi_synthesize.py:138-155`) and pair/claim binding.

`magi_plateau_gate.sh` gains a `--safety` flag. Invocation:
`magi_plateau_gate.sh DOC <state>/round_3_xfamily --reviewer-family grok --safety`. Under the
review lock it keeps G1–G9 on the round-3 pair and additionally verifies, before writing the
marker:

- active sidecar whose attestation byte-equals the compiled constant; current protocol equals
  `activation_protocol_sha256`;
- current document SHA equals the running revision's `artifact_sha256`;
- ledger shows the exact per-revision sequence: fanout success (round 1), Claude xfamily success
  (round 2), Grok xfamily success (round 3), in claim order, all on this artifact and protocol,
  with claim-time `reviewer_family` matching each position — order swap, relabeling, or a
  missing position denies (I5, I9);
- the round-2 pair passes `verify_round(doc, <state>/round_2_xfamily, "codex", "claude",
  require_successful_claim=True)` — Claude evidence is verified directly, not inferred;
- all three syntheses exist, and **no REJECT/CRITICAL/HIGH finding appears anywhere** in
  `round_1_magi_synthesis.json`, `round_2_xfamily.json`, `round_2_xfamily_synthesis.json`, or
  `round_3_xfamily.json` — a clean Grok verdict cannot hide a Claude HIGH (G8 union);
- no later launch exists after the round-3 claim;
- `magi_design_convergence_gate.py evaluate` returns decision `PLATEAU_CANDIDATE`.

Only then does it append the final revision receipt (idempotent, ledger-derived; campaign lock
taken for the append) and atomically publish `PLATEAU.<doc_id>.<sha16>` (existing marker path)
before releasing the review lock. The marker **is** the plateau state — mode status derives from
it (§6); no sidecar status write exists, so there is no two-file transaction gap: a crash after
the receipt but before the marker leaves a derivably-active mode with a harmless receipt, and
the marker itself is the existing single-file atomic publication (`magi_plateau_gate.sh:166-210`
with post-publish re-check). Activation, claims, and finishes can never write the marker (I10).
There is no transition out of `plateau` or `blocked` within this protocol.

## 11. Convergence behavior — excluding only affordability

`magi_design_convergence_gate.py` changes, engaged only when the sidecar is present and the
mode derives `active` (§6):

- the `admissions` projection (`:397-424`) sets `affordable: True` for both phases while
  continuing to report ordinary-only usage and gross safety spend in the output envelope;
- the evaluator selects a new pure kernel profile `dual-magi-design-safety`
  (`evaluate_profile`, `magi_convergence_kernel.py:362-367`), identical to
  `evaluate_dual_magi_design` except: `PLATEAU_CANDIDATE` /
  `DESIGN_READY_FOR_EXISTING_PLATEAU_GATE` requires `state["safety_sequence_complete"]` (all
  three positions successful for the current artifact, derived from the ledger) instead of mere
  `"xfamily" in current_phases`; the two next-step reasons become
  `SAFETY_NEXT_XFAMILY_CLAUDE_REQUIRED` / `SAFETY_NEXT_XFAMILY_GROK_REQUIRED` when positions 2/3
  are pending. Because `admissions` are pre-granted, `DESIGN_NEXT_FANOUT_UNAFFORDABLE` and
  `DESIGN_FINAL_DIVERSE_RECHECK_UNAFFORDABLE` are unreachable for the admitted target — and only
  those.

Every semantic terminal remains live and terminal: `DESIGN_BLOCKING_ROOT_REPEATED` (REDESIGN),
`DESIGN_SAME_SUBSYSTEM_NEW_ROOTS_RECURRED` (SCOPE_SPLIT), `DESIGN_BLOCKER_MASS_STALLED`,
`DESIGN_RETRY_BUDGET_EXHAUSTED`, `DESIGN_MAX_LOGICAL_CYCLES_REACHED`,
`UNSAFE_OR_INCOMPLETE_DESIGN_INPUT`, `DESIGN_LAUNCH_STILL_RUNNING`, and cancellation-pending.
Enforcement is honest about where it lives: the guard does not re-run the evaluator inside
`claim()` (the evaluator imports the guard — `magi_design_convergence_gate.py:21` — and reads
provider evidence; embedding it would invert the dependency). The recorded manual protocol
requires an `evaluate` run after every completed revision, and the mechanical authority is the
plateau gate, which requires `PLATEAU_CANDIDATE`: a claim made in defiance of a semantic
terminal wastes charged spend but can never become plateau. Note on the
cycle fuse: `MAX_LOGICAL_CYCLES = 2` counts completed fanout→xfamily cycles **at the current
protocol** (`:326-329,359-375`); it is a progress heuristic, not a spend cap, and it stays.
Continuing past two stalled cycles therefore requires a reviewed protocol change plus a new
closed attestation — the deferred re-attestation slice — never silent looping. Blocking roots
from all five provider turns aggregate per revision (the gate already feeds every successful
launch's reviews into `summarize_revision`), so an unresolved Claude HIGH keeps
`current_roots` non-empty and forces a document change before the next revision.

## 12. Retry and failure semantics

1. **Attempts.** Exactly the existing two-attempts-per-position rule (§8.2). Success path: 3
   claims / 5 provider turns (3 fanout + 1 + 1). Maximum path: 6 claims / 10 provider turns
   (two fanout attempts at 3 turns each, two attempts each for Claude and Grok). A third attempt
   at any position is `transition-blocked`; after a second failure the revision and mode
   *derive* as `blocked` with `SAFETY_PHASE_RETRY_EXHAUSTED` from the ledger alone — no status
   write exists to race or lose (§6, §8.3.2).
2. **Charging.** Failed and abandoned attempts stay fully charged in gross spend; every claim id
   (failed or successful) appears in the revision's `claim_ids`. The startup-credit path
   (`recover-startup`, `:1124-1194`) is untouched and remains closed to its proven no-turn
   condition; its weight-0 replacement is safety-validated per I11.
3. **Cancellation.** `cancel-revision` is unchanged and remains the only requirement-revision
   path while a claim is live; it already revokes plateau markers (`:1757`). A superseded safety
   claim leaves the revision reopenable only through the normal changed-artifact rules.
4. **Autorun refusal.** `magi_autorun.py hook` loads the sidecar early and, when present in any
   status, prints a terminal `MAGI AUTORUN BLOCKED: … SAFETY_MODE_MANUAL_ONLY` instead of a
   continuation block, and `arm` refuses to arm. This is the entire autorun surface of this
   slice.
5. **Fail-closed defaults.** Malformed sidecar, ambiguous derived status, or claim/sidecar
   linkage mismatch ⇒ `StateError`, exit 2, no write. Provider/adapter failures inside
   `magi_xfamily.sh` / `magi_fanout_codex.sh` keep their existing cleanup contracts unchanged.

## 13. Exact tests and mutations

New/changed test files (conventions follow `tests/test_campaign_guard.py` for Python,
`tests/test_plateau_gate.sh` for shell):

- `tests/test_safety_admission.py` — activation positive control on a synthetic exact-16 fixture
  shaped like the real FA20 ledger (4 campaigns / 8 launches, one failed xfamily attempt;
  fixture-realism rule per `tests/fixtures/issue_271_actual_ledger_sanitized.json` precedent);
  negative controls: wrong id/path/ledger-SHA/artifact/protocol/usage/mode/risk/operator, live
  claim, second activation, uppercase/short SHA, unknown sidecar field, symlinked final
  component, symlinked parent directory, alias path resolving to the attested file,
  non-exhausted target, existing plateau marker; ledger byte-identity before/after activation;
  ordinary-document claims still capped at 12/16 with a sidecar present for a different doc_id;
  safety fields on a normal launch rejected; `--reviewer-family` on fanout rejected; weight-0
  replacement still safety-validated (I11); free-space preflight denial; fd-9 lock proof:
  positive control proves inheritance through the adapter's command-substitution guard call
  (fstat identity + no-op re-flock), negatives prove denial on absent fd 9, fd 9 open on the
  wrong file, and a competing review-lock holder.
- `tests/test_safety_transition.py` — full success sequence; at every position reject wrong
  round/phase/family, same-family relabel, skipped position, repeat-after-success, third
  attempt; unchanged-artifact reroll (including `source_artifact_sha256`) rejected; changed
  artifact admits a second full sequence above ordinary 16; all-retry fixture proves 6 claims /
  10 turns and the derived revision+mode `blocked` state (including after a crash between the
  final `finish` and any receipt append); protocol drift after
  activation yields `SAFETY_PROTOCOL_DRIFT_REATTESTATION_REQUIRED` without record mutation.
- `tests/test_safety_convergence.py` — affordability codes unreachable for the admitted target;
  each semantic terminal (repeated root, subsystem recurrence, stalled mass, max cycles, retry
  exhaustion, corrupt input) still terminal; `PLATEAU_CANDIDATE` requires all three positions;
  Claude HIGH with clean Grok keeps `current_roots` non-empty.
- `tests/test_safety_plateau_gate.sh` — grant on a complete clean sequence; deny on: missing or
  altered round-2 pair (Claude-missing-cannot-plateau), provider order swap, family relabel,
  meta/model/transcript mutation, synthesis removal, Claude HIGH carried in round-2 synthesis,
  later claim after Grok, sidecar absent/blocked, protocol drift. Concurrency probes mirror
  `test_inv7_lock.sh`: adapter paused before campaign claim, activation vs claim, reversed-order
  deadlock probe — exactly one proceeds, none waits indefinitely.
- Extend `tests/test_autorun.py` — sidecar-active document: `hook` emits no continuation block
  and `arm` refuses (`SAFETY_MODE_MANUAL_ONLY`).

Mutation requirements (all must be killed): attestation selection and each compared identity
field; ledger-SHA equality; activation cardinality (0-or-1 sidecar); derived status computation;
revision uniqueness including `source_artifact_sha256`; sequence position/round/phase/family
comparisons; retry ceiling constant; claim↔revision linkage; I11 (grant-only-after-
affordability-denial and validation-always); ordinary/safety accounting split; kernel profile
plateau condition; plateau-gate round-2 verification and G8 union; unchanged
`DEFAULT_MAX_MODEL_LAUNCHES` / `GLOBAL_MAX_MODEL_LAUNCHES`. Deadline and command (parent §21):
`timeout --signal=TERM --kill-after=5s 900s pytest -x plugins/harness-magi-codex/tests`.
Any semantic survivor blocks implementation.

## 14. Resource and preflight bounds

- Sidecar records per ledger: exactly 0 or 1. Added sidecar bytes per revision: under 4 KiB.
- Claims per revision: 3 success / 6 max. Provider turns: 5 success / 10 max.
- Provider timeouts: existing 900 s ceilings (`magi_xfamily.sh:118-125`); document input:
  existing 10 MiB (`:64-71`); reviewer outputs: existing adapter ceilings.
- Activation and safety admission run under 2 s locally with zero network access.
- Free-space preflight: `os.statvfs` on the state-dir filesystem at every safety claim; less
  than 20 GiB free denies with `SAFETY_FREE_SPACE_PREFLIGHT_FAILED` before any provider launch.
  All further storage accounting (cumulative 4 GiB state cap, high-water tracking, staging
  cleanup) is deferred with the storage slice.
- Review spend: measured and printed as gross at every claim; never an admission limit here.

## 15. Rollback and install boundary

Before editing any `RUNTIME_FILES` member, copy the exact originals to
`~/sanada_backup_persistent/magi_safety_slice01_<YYYYMMDD_HHMMSS>/`; before plugin reinstall,
back up the installed cache and marketplace metadata. Pre-activation rollback = restore the
package; no target-ledger change exists to undo. Post-activation rollback must not delete or
rewrite either `CAMPAIGN.<doc_id>.json` (untouched v1 — old code still reads it, a deliberate
improvement over the parent's v2 plan) or `SAFETY.<doc_id>.json` (history). Reverted code
without sidecar support simply cannot admit past 16 — the mode degrades to the ordinary fuse,
which is fail-closed, and safety-field launches remain readable because launch validation is
open-set. Reinstall boundary (parent §26 unchanged): targeted + full tests green, mutations
green, implementation bug-hunt, cross-family reviews, clean `git diff --check` and protocol
snapshot check, committed and pushed source, byte-identical source/cache comparison after
reinstall.

## 16. Family routing

```text
preferred: Claude planning/design -> exact-revision cross-family review -> Codex implementation -> Claude + Grok independent review
actual design route (this slice): Claude drafted from grounded repository evidence (magi-safety-plan)
  -> Codex exact-revision fanout review -> Grok xfamily review/plateau of this slice before any implementation
  -> Codex implements
  -> Claude adversarial implementation-intent review -> Grok independent implementation review (if available; blocked, not substituted, if not)
runtime route (what this slice builds, positions 2 and 3): Codex fanout -> Claude xfamily -> Grok xfamily per FA20 revision
missing: none
degraded_until: both Claude and Grok have reviewed the same exact revision and the mechanical gate passes
```

Grok remains terminal reviewer (position 3) because the operator requires two independent
external families and Claude is the preferred planning family; Grok last tests the combined
Codex+Claude output for shared blind spots. Neither provider can substitute for the other
(I9); provider unavailability after the bounded second attempt blocks the revision rather than
falling back silently.

## 17. ROI and strongest alternative

Benefit: the FA20 self-flasher design — which can immobilize a vehicle and force bench recovery
or ECU replacement — keeps receiving full independent cross-family review after cost exhaustion,
with every non-budget safety gate intact. The operator explicitly values this above model spend
and explicitly rejected an "only one extra review" policy.

**This slice is itself the bounded pilot.** One compiled target, manual orchestration, no
autorun: that is the pilot shape, not a compromise of it. Observed success metric: the FA20
document reaches either a verified exact-revision plateau (both families, gate green) or a
semantic terminal, with (a) zero admission granted to any other document, (b) ordinary
accounting frozen at 16/16 throughout, (c) every safety claim linked to its revision receipt,
and (d) gross safety spend reported at every claim. Post-FA20 decision point: only after the
pilot terminates does the operator decide whether generic safety-mode registration (deferred
slice 06) is worth designing — the pilot's receipts are the evidence for that decision.

Estimated implementation: 6 owned source paths (`magi_campaign_guard.py`,
`magi_convergence_kernel.py`, `magi_design_convergence_gate.py`, `magi_plateau_gate.sh`,
`magi_xfamily.sh`, `magi_autorun.py`), 5 test paths, this document; roughly 400 net code lines
before fixtures; 5–8 engineering hours. Cut line: more than 12 owned paths, 500 net lines, or
10 hours; any weakening of ordinary ceiling behavior; any ledger-history rewrite — stop and take
the alternative. Strongest alternative: keep FA20 blocked at the fuse, continue vendor tooling,
restrict work to read-only capture and offline simulation. Sunk cost never justifies crossing
the cut.

## 18. Acceptance and deferred slices

Accepted only when all hold: (1) normal documents capped at 12/16, numerically unchanged;
(2) only the compiled FA20 attestation activates; (3) `CAMPAIGN.<doc_id>.json` stays schema v1,
append-only, fully charged; (4) safety admission is evidence/convergence bounded, never spend
bounded, and grants only after affordability-only denial; (5) every changed revision receives
fanout → Claude → Grok in order with exact artifact names; (6) unchanged-revision reroll
impossible, including the pre-activation artifact; (7) retry bounded at 2 per position, 6/10
worst case, all linked; (8) plateau requires verified evidence from both Claude and Grok on the
current revision; (9) all non-budget convergence terminals fail closed and block the mode;
(10) no record, count, or activation authorizes implementation, shipping, or any vehicle
operation.

Deferred slices, in intended order: **02** autorun continuation + heartbeat/watchdog/alerting;
**03** `resume-safety-review` crash recovery; **04** storage/state high-water management;
**05** protocol-drift re-attestation flow (new closed attestation preserving the sidecar);
**06** generic safety-mode registration, if ever justified. Each requires its own review cycle;
none may weaken an invariant of this slice.
