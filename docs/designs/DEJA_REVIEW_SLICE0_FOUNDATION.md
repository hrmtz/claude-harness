# Deja Review Slice 0 corpus foundation

Status: implementation candidate under fresh exact-revision review
Owners: claude-harness#106 and hippocampus-mcp#111
Implementation owner for this slice: claude-harness
Date: 2026-07-24

## FAMILY_ROUTING

```text
preferred:
  Claude design/planning plateau
  -> Codex implementation
  -> Claude exact-revision design-intent review
  -> Codex final fixes/tests
actual:
  Codex design
  -> Grok exact-revision design plateau
  -> Codex implementation
  -> Grok exact-revision design-intent review
  -> Codex final fixes/tests
missing:
  family: Claude
  phases: design/planning and implementation design-intent review
  reason: preserve the user-reported near-limit Claude API quota
degraded_until:
  the exact design revision passes the Grok gate, the exact implementation
  revision passes Grok adversarial review, and Codex applies every blocking
  finding and runs the final test suite
```

Claude is not invoked during this task.

The implementation is not accepted before the final Grok review and Codex
fix/test phases are evidenced.

The original design revision
`fb5d591b484c20b6e442e249a5d46a307482b56de6a5827524dc4ed2cc158b22`
received a mechanical Grok plateau marker before implementation began.

Implementation bug-hunt then found that `run.lock` was a replaceable,
incorrect lock domain.

The implementation draft changed to state-root and campaign-directory locks.

That blocking fix required this post-implementation design reconciliation and
invalidated the old exact-revision marker.

Therefore the current sequence is truthfully:

```text
old design plateau
-> unaccepted implementation draft
-> adversarial bug-hunt
-> blocking lock-domain fix
-> current design reconciliation
-> current exact-revision design re-plateau
-> exact implementation review
-> Codex blocking fixes and tests
```

No implementation acceptance, commit, or release occurs before the last three
steps.

## 1. Task and invariant

This task implements only the filesystem foundation for Deja Review Slice 0.

It converts existing schema-valid Magi review artifacts into a deterministic,
ephemeral corpus without changing those artifacts or any production reviewer
prompt.

The invariant is:

> A source artifact is read-only, every derived finding remains attributable
> to its exact source bytes, and an interrupted or concurrent run cannot
> publish a mixed or falsely complete corpus.

This is one reversible task.

It does not authorize the durable Hippocampus backend.

## 2. Verified repository facts

The current source protocol is:

```text
plugins/harness-magi-codex/schemas/finding.schema.json
```

It requires:

- reviewer;
- round;
- artifact ID and SHA;
- verdict;
- grounding verdict and verification operations;
- source artifacts;
- dispositions;
- findings.

Each finding has:

- finding ID;
- severity;
- title;
- location;
- rationale;
- required fix;
- confidence;
- duplicate classification;
- missed angle.

It does not have categories, repository, campaign ID, target path, lifecycle
status, or evidence bodies.

Those absent fields must not be invented as canonical facts.

The repository ignores:

```text
docs/**/.dual-magi/
```

The default experiment state can therefore live below:

```text
docs/.dual-magi/deja-review-slice0/
```

without becoming a normal commit.

Python 3 and `jsonschema` are already runtime dependencies of the Magi plugin.

The implementation may use Python standard-library `fcntl.flock`.

## 3. Scope

### 3.1 In scope

- a checked-in JSON Schema for normalized Slice 0 finding records;
- a checked-in JSON Schema for the corpus manifest;
- a Python CLI that prepares and validates the corpus;
- deterministic artifact discovery from explicit inputs;
- exact source-byte digests;
- schema validation before normalization;
- stable occurrence IDs;
- best-effort category derivation from bounded fields;
- canonical JSONL serialization;
- atomic stage publication;
- campaign-scoped non-blocking `flock`;
- append-only progress events;
- safe restart of a completed prepare stage;
- local resource preflight;
- unit and integration fixtures;
- documentation of the CLI.

### 3.2 Out of scope

- PostgreSQL;
- pgvector or `halfvec`;
- embedding API calls;
- lexical or semantic retrieval;
- relevance judgments;
- human label entry;
- promotion to Slice 1;
- Hippocampus migrations;
- MCP tools;
- lifecycle mutation;
- receipt storage;
- reviewer prompt injection;
- automatic source discovery outside explicit paths;
- reading credentials or environment values;
- deleting derived state.

The absence of retrieval means this task alone cannot satisfy the full Slice 0
quality gate.

It is foundation work only.

## 4. Planned files

Under `plugins/harness-magi-codex/`:

```text
schemas/deja-review-slice0-record.schema.json
schemas/deja-review-slice0-manifest.schema.json
scripts/deja_review_slice0.py
tests/test_deja_review_slice0.py
```

Documentation:

```text
plugins/harness-magi-codex/README.md
```

No existing protocol file is modified merely to include this experimental
CLI.

The Magi review protocol SHA remains independent from the Slice 0 corpus
format.

## 5. CLI contract

The executable interface is:

```text
python3 scripts/deja_review_slice0.py prepare \
  --campaign-id <safe-id> \
  --state-root <directory> \
  --source <explicit-json-file> [--source ...]

python3 scripts/deja_review_slice0.py validate \
  --campaign-dir <directory>

python3 scripts/deja_review_slice0.py status \
  --campaign-dir <directory>
```

### 5.1 `prepare`

`prepare`:

1. validates the campaign ID and write-target containment;
2. lexically validates every explicit source path;
3. performs an `lstat` identity/size admission pass for all sources without
   reading source bodies;
4. computes the input-intent digest from those admitted identities and treats
   every explicit source as required input without silently excluding
   one by basename;
5. performs a metadata-only size and disk-reservation pass without reading
   source bodies;
6. processes one candidate at a time by opening with `O_NOFOLLOW`, validating
   the descriptor with `fstat`, reading and hashing with enforced ceilings,
   and repeating `fstat` after the read;
7. parses, validates, normalizes, and spools those same captured bytes without
   reopening the source;
8. fsyncs the artifact spool, records progress, releases the captured bytes
   and parsed object, and closes the descriptor before the next source;
9. verifies embedded `artifact_sha` against an explicit reviewed target only
   when the source supplies such a target path; this foundation has no target
   path and therefore preserves the field without claiming it is verified;
10. emits one normalized record per finding to the artifact-scoped spool;
11. writes manifests and JSONL to run-unique temporary files;
12. validates and re-hashes every temporary output;
13. atomically publishes outputs;
14. publishes the prepare stage receipt as the last immutable corpus write.

The command exits:

```text
0  success or exact reusable stage
2  invalid input or validation failure
3  campaign lock held
4  immutable-input mismatch with existing completed stage
64 invalid CLI usage
```

It does not partially accept a malformed artifact.

One malformed candidate fails the whole prepare attempt.

### 5.2 `validate`

`validate` is read-only.

It checks:

- campaign manifest schema;
- source digest list;
- normalized JSONL schema;
- JSONL line count;
- artifact and finding counts;
- canonical sort order;
- duplicate occurrence IDs;
- every source reference;
- stage receipt output digests;
- immutable-input digest consistency.

It exits zero only when the completed stage is reusable.

### 5.3 `status`

`status` is read-only.

It prints a single JSON object containing only:

- campaign ID;
- state: absent, running, stalled, complete, invalid, or stale-owner;
- last stage;
- completed and total counts;
- last outcome and reason code;
- immutable-input digest if known;
- heartbeat age in seconds if known.

It never prints source bodies, finding bodies, command lines, environment
values, or credentials.

## 6. Source discovery

Every `--source` is explicit.

A file input is accepted only when:

- it is a regular file;
- its suffix is `.json`;
- it is not a symlink;
- it is opened with `os.open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC)`;
- `fstat` confirms a regular file before any read;
- reads stop at the byte ceiling rather than trusting a prior `stat`;
- `fstat` after reading has the same device, inode, size, and modification
  timestamp as the first `fstat`.

Directory inputs are rejected.

The foundation deliberately gives up recursive convenience to avoid an
`openat` traversal subsystem before retrieval value is established.

There is a hard ceiling of:

- 256 explicit candidate files;
- 10 MiB per file;
- 128 MiB total source bytes.

Duplicate device/inode pairs supplied through repeated paths are deduplicated.

Every explicit source must validate as a Magi finding artifact.

Synthesis files are valid sources when they satisfy the same schema.

Provider metadata and other JSON files fail validation rather than being
silently omitted.

The source display path is the lexical explicit path made absolute and
normalized without dereferencing the final component.

It is never used as an authorization boundary.

## 7. Input validation

The checked-in Magi finding schema is the source contract.

No unknown top-level or finding keys are accepted because the existing schema
sets `additionalProperties: false`.

Additional foundation checks:

- `reviewer` is non-empty after trimming;
- `round` is positive;
- `artifact_id` and `artifact_sha` already satisfy the source schema;
- every finding ID is non-empty;
- finding IDs are unique within one artifact;
- text fields contain no NUL;
- each text field is at most 64 KiB encoded UTF-8;
- optional convergence fields `subsystem`, `root_cause_id`, and
  `affected_invariant` receive the same UTF-8, NUL, and byte-ceiling checks;
- the sum of normalized finding text is at most 8 MiB per artifact;
- no source artifact has more than 1,024 findings.

The CLI never executes or interpolates source text.

## 8. Stable identity

The source digest is:

```text
source_sha256 = sha256(exact_source_bytes)
```

The normalized occurrence ID is:

```text
occurrence_id = sha256(
  "deja-review-slice0-occurrence-v1\0"
  + source_sha256 + "\0"
  + reviewer + "\0"
  + decimal_round + "\0"
  + finding_id
)
```

The complete 64-hex digest is stored.

The source artifact ID is preserved separately.

It is not reused as the occurrence ID.

The immutable-input digest is:

```text
sha256(canonical JSON of {
  schema_version,
  normalizer_version,
  normalizer_implementation_sha,
  ordered [source display path, source_sha256],
  normalized-record-schema SHA,
  source-finding-schema SHA
})
```

Canonical JSON means:

- UTF-8;
- object keys sorted;
- no insignificant whitespace;
- Unicode preserved;
- one trailing newline for files.

`normalizer_implementation_sha` covers the exact bytes of:

- `scripts/deja_review_slice0.py`;
- its checked-in category lexicon, which lives in that file;
- both Slice 0 schemas.

Reuse recomputes this digest.

A byte change to any covered file makes an existing completed campaign an
immutable-input mismatch.

The final immutable-input digest is known only after all explicit sources have
been read and hashed.

The active owner record instead carries:

```text
input_intent_digest = sha256(canonical JSON of {
  ordered explicit lexical source paths,
  initial lstat device/inode/size/mtime identities,
  normalizer_implementation_sha
})
```

This detects that a resumed attempt addresses the same intended input set
without pretending the unread source-byte digest is already known.

## 9. Normalized record

Each JSONL line contains:

```text
schema_version
normalizer_version
occurrence_id
source_path
source_sha256
source_artifact_id
reviewed_artifact_sha
reviewer
round
verdict
schema_grounding_verdict
finding_id
severity
title
location
rationale
required_fix
confidence
dup_flag
missed_angle
subsystem (when present)
root_cause_id (when present)
affected_invariant (when present)
changes_design_invariant (when present)
relation_to_prior (when present)
categories
category_derivation
trust
```

`trust` is always:

```text
untrusted-review-content
```

The optional convergence fields added by issue #107 are preserved verbatim
when the source finding carries them. In particular, blocking-finding
`subsystem` and `root_cause_id` identity is not discarded by normalization.

There is no `status` field because the source schema has no lifecycle status.

There is no fabricated repository or campaign ID on individual records.

Records sort by:

1. source SHA;
2. reviewer;
3. round;
4. finding ID;
5. occurrence ID.

## 10. Category derivation

The function is versioned:

```text
magi-category-v1
```

It reads only:

- title;
- location;
- missed angle;
- severity;
- duplicate classification.

It Unicode-normalizes with NFKC and lowercases.

This document is the freeze point for the function and its fixed lexicon:

```text
rollback:
  rollback revert restore recovery backup resume checkpoint
security:
  security injection credential secret auth permission acl exfil
data-integrity:
  data loss corrupt digest identity atomic transaction migration constraint
  idempotent lineage supersession
performance:
  performance latency timeout memory cpu disk scale batch index hnsw resource
operability:
  operability monitor alert heartbeat runbook maintenance deploy rollout
  scheduler
maintainability:
  maintainability drift duplication complexity coupling refactor
api-design:
  api contract request response compatibility versioning
testing:
  test fixture coverage verification preflight gate
cost:
  roi cost spend payback commercial operator-hour
privacy:
  privacy tenant visibility disclosure
```

Canonical category order is:

```text
correctness
rollback
security
data-integrity
performance
operability
maintainability
api-design
testing
cost
privacy
other
```

At most three matched categories are emitted in canonical order.

If none match:

- REJECT, CRITICAL, or HIGH emits `correctness`;
- MED, LOW, or nit emits `other`.

Categories are untrusted best-effort routing metadata.

They are not security or authorization data.

## 11. Campaign state

The campaign directory is:

```text
<state-root>/<campaign-id>/
```

The campaign ID must match exactly:

```text
^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$
```

It therefore cannot be empty, absolute, contain a path separator, or equal a
dot segment.

Before creating the campaign directory:

- the state root must already exist as a real directory and not a symlink;
- it is opened with `O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC`;
- its real path and `fstat` identity are captured;
- joining the validated campaign ID must produce a path strictly below that
  real path;
- a missing campaign directory is created with `mkdir(..., dir_fd=root_fd)`;
- the campaign directory is opened relative to `root_fd` with
  `O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC`;
- an existing campaign path must resolve to the same real directory and
  `fstat` identity as the held campaign descriptor.

An invalid ID is rejected as CLI usage with exit 64.

An unsafe or unverifiable state root/campaign path exits 2.

Both cases occur before any write.

Published prepare outputs are:

```text
campaign.json
source-digests.json
normalizer-manifest.json
normalized-findings.jsonl
resource-preflight.json
progress.jsonl
heartbeat.json
run-owner.json
stage-receipts/prepare.json
run.lock
```

The prepare receipt covers only these immutable corpus outputs:

```text
campaign.json
source-digests.json
normalizer-manifest.json
normalized-findings.jsonl
resource-preflight.json
```

It does not cover mutable runtime-control files:

```text
progress.jsonl
heartbeat.json
run-owner.json
run.lock
```

Those files are validated by their own structural and lifecycle rules.

`run.lock` may remain as an empty inode after completion.

It is non-authoritative metadata.

Replacing, unlinking, or locking `run.lock` does not create or probe the
serialization domain.

`run-owner.json` exists only while an owner is active or after an unclean
termination.

Temporary files include the run ID and `.tmp`.

## 12. Lock and owner

Before any campaign-state mutation, `prepare`:

1. opens and verifies the state-root descriptor from Section 11;
2. takes `LOCK_EX | LOCK_NB` directly on that state-root directory descriptor
   before creating or opening a campaign name;
3. creates or opens and verifies the campaign directory relative to the held
   root descriptor;
4. takes `LOCK_EX | LOCK_NB` directly on the campaign directory descriptor;
5. creates or opens `run.lock` only as non-authoritative metadata;
6. exits 3 without owner/progress/output writes if either authoritative
   directory lock fails.

Every foundation CLI operation that creates, renames, replaces, or removes a
campaign name must honor the state-root directory lock.

Every foundation writer for an existing campaign must also honor the campaign
directory lock.

There is one lock domain and one acquisition order:

```text
state-root directory -> campaign directory
```

After acquiring the lock it publishes `run-owner.json` atomically with:

- run ID;
- campaign ID;
- hostname;
- PID;
- Linux process start ticks from `/proc/<pid>/stat`;
- start timestamp;
- input-intent digest.

It does not record argv or environment.

The heartbeat is refreshed after each artifact and at least once per minute
during active Python work.

A monotonic watchdog remains active while the lock owner is running.

It:

- refreshes heartbeat independently during bounded reads, JSON decode/schema
  validation, spool fsync, final concatenation, output validation, and
  directory fsync;
- writes no progress advancement by itself;
- interrupts the main operation when the 15-minute monotonic deadline is
  reached so the terminal-failure and owner-cleanup path runs;
- surfaces its own write failure as a blocking run failure.

Foundation liveness limits are fixed before implementation:

- no progress for 120 seconds: `status` reports `stalled`;
- total prepare wall time above 15 minutes: the healthy process appends
  `foundation-deadline-exceeded` and exits 2;
- an operator running this interactively checks `status` after two minutes
  without visible CLI completion.

Progress means a change to `completed` or `last_item_digest`.

A fresh heartbeat without progress does not suppress the stalled state.

During publish, every validated and fsynced output increments `completed` and
sets `last_item_digest` to that output digest.

Discovery advances once when the admitted candidate set and input-intent
digest are finalized.

Normalize advances after every artifact spool is fsynced, using the completed
artifact count, stage-local total, and spool digest.

The 120-second stalled rule therefore applies to discover, normalize, and
publish uniformly.

There is no overnight paging for this offline task.

On clean exit:

- a terminal progress event is appended;
- `run-owner.json` is removed;
- both authoritative locks are released by closing their directory
  descriptors.

On later acquisition with leftover owner metadata:

- same hostname, dead PID/start identity, and heartbeat older than five
  minutes: append `stale-lock-reclaimed`;
- live matching process: fail closed as `owner-still-live`;
- remote hostname, malformed identity, or heartbeat not old enough: fail
  closed as `owner-unverifiable`.

This foundation has no override flag.

An operator may preserve the state and choose a new campaign ID.

For remote-host owner metadata, the recovery runbook is:

1. use read-only `status` to record that owner liveness is unverifiable;
2. do not infer lock state from `run.lock`;
3. preserve the abandoned campaign directory;
4. create a named replacement campaign ID;
5. prepare and validate the replacement;
6. record the abandoned and replacement IDs in the operator's promotion
   notes.

`status` does not take an authoritative lock and never claims that it probed
one.

No force-unlock flag is added.

The test report records maximum-fixture prepare wall time as the conservative
full-rerun cost.

## 13. Atomic publication

Every bounded manifest JSON snapshot:

1. is opened relative to the held campaign directory descriptor as a
   run-unique temporary file using `O_CREAT | O_EXCL | O_NOFOLLOW`;
2. is flushed;
3. is `fsync`ed;
4. is parsed and schema-validated from that temporary file;
5. has its SHA-256 calculated;
6. is renamed with `os.replace` using `src_dir_fd` and `dst_dir_fd` set to the
   held campaign descriptor;
7. causes the held campaign descriptor to be `fsync`ed.

All lock, temporary-file creation, readback, unlink, and replacement
operations use the held directory descriptor.

`normalized-findings.jsonl` is never fully loaded for publication or
validation.

It is concatenated, hashed, decoded, and schema-validated one line at a time
with peak residency bounded by one record, one fixed I/O chunk, and decoder
state.

The publication memory formula relies on that streaming bound.

Immediately before prepare receipt publication, the runner `stat`s the
campaign ID relative to the held state-root descriptor without following
symlinks and requires the result to match the held campaign descriptor's
device and inode.

A rename, replacement, or symlink swap fails the run without publishing the
receipt.

The implementation is Linux-only v1 because it relies on `O_NOFOLLOW`,
`O_DIRECTORY`, `/proc`, and descriptor-relative operations.

`progress.jsonl` is append-only under the held campaign lock.

Each event is one canonical JSON line.

The file is flushed and `fsync`ed after every event.

The prepare receipt is published only after all five declared immutable corpus
outputs exist and match the receipt digests.

Repeated `source_sha256` across distinct retained files or any repeated
`occurrence_id` fails with exit 2 before receipt publication.

Clean success ordering is:

1. publish and validate all five immutable corpus outputs;
2. publish the prepare receipt as the last immutable corpus write;
3. append the terminal success progress event;
4. remove `run-owner.json`;
5. release the lock.

A crash after step 2 does not invalidate the immutable corpus.

The next lock owner validates the receipt and all five digests, appends a
`post-publish-recovery` terminal event, removes stale owner metadata when the
Section 12 rules permit it, and reuses the completed corpus.

## 14. Restart and immutability

If `stage-receipts/prepare.json` exists:

- matching immutable-input digest and valid outputs: validate and return 0
  without rewriting outputs;
- different immutable-input digest: return 4;
- matching digest but invalid output: append failure and return 2.

The CLI never silently deletes or overwrites a completed stage.

To rebuild different inputs, use a new campaign ID.

An incomplete attempt without a prepare receipt may replace only its own
derived output basenames after validating the current immutable inputs.

Normalization is artifact-incremental:

- the metadata-only admission pass records bounded file identities and sizes
  but never source bodies;
- only one source artifact body is read and parsed at a time;
- its exact captured bytes are hashed, parsed, validated, and normalized
  without reopening the source;
- its normalized records are sorted and written to a run-unique
  artifact-scoped spool;
- the captured bytes and parsed object are released before the next artifact;
- final JSONL publication concatenates artifact spools in canonical source
  order;
- the process never holds multiple source bodies, all parsed artifacts, or
  all normalized records in memory.

For exact completed-stage reuse, the runner reads and hashes each source once
without normalizing it, computes the current immutable-input digest, and then
validates the receipt-covered outputs.

Source files are never written.

## 15. Progress records

Every record includes:

```text
run_id
campaign_id
stage
completed
total
last_item_digest
elapsed_seconds
outcome
reason_code
recorded_at
```

Foundation stage values are:

```text
discover
normalize
publish
```

Outcome values are:

```text
running
success
failure
```

Reason codes are closed constants in the implementation.

Unexpected exception text is written to stderr after credential-safe
sanitization but is not persisted to campaign state.

## 16. Resource preflight

The foundation preflight records:

- candidate artifact count;
- source bytes;
- finding count;
- normalized bytes;
- temporary bytes required;
- free bytes at the campaign filesystem;
- process peak RSS;
- available system memory;
- CPU count;
- configured CPU worker cap, fixed at one for this task;
- configured embedding concurrency, fixed at zero;
- configured database concurrency, fixed at zero.

An early disk admission check runs after the metadata-only `lstat` size pass
and before any source body is read.

Each subsequent descriptor-safe read enforces the declared file and total
ceilings and rejects identity/size drift.

Memory admission runs immediately before reading each artifact using that
artifact's bounded metadata size.

For each source, the early memory projection uses:

```text
normalized_record_expansion =
  max_findings_per_artifact
  * (encoded_source_path_bytes * 6 + 4096)

projected_incremental_memory =
  source_size * 6
  + normalized_record_expansion * 2
  + 1 MiB
```

The first normalized-record term covers the in-memory record list and sort
state.

The second covers the largest concurrent canonical serialization/spool
buffer.

The final 1 MiB covers fixed parser, key, and merge bookkeeping.

The disk projection is:

```text
projected_simultaneous_peak_bytes =
  retained_existing_campaign_bytes
  + artifact_spool_bytes_upper_bound
  + final_jsonl_temporary_bytes_upper_bound
  + manifest_and_receipt_temporary_bytes
  + projected_progress_append_bytes
  + atomic_snapshot_output_count * 4096 bytes publication overhead
```

The early operands are:

```text
artifact_spool_bytes_upper_bound =
  sum(
    source_size * 6
    + max_findings_per_artifact
      * (encoded_source_path_bytes * 6 + 4096)
  )

final_jsonl_temporary_bytes_upper_bound =
  artifact_spool_bytes_upper_bound

manifest_and_receipt_temporary_bytes =
  1 MiB + candidate_count * 512 bytes

projected_progress_append_bytes =
  (candidate_count * 3 + 8) * 1024 bytes

atomic_snapshot_output_count = 8
```

The eight possible same-directory snapshot basenames are five immutable
corpus outputs, the prepare receipt, heartbeat, and owner metadata.

Only one temporary generation per basename may exist in one healthy attempt.

`max_findings_per_artifact` is the fixed input ceiling of 1,024.

The factor six covers worst-case JSON control-character escaping in source
text and paths during canonical serialization.

The 4,096-byte per-record term covers fixed keys, digests, enums, integers,
category values, and structural punctuation.

`retained_existing_campaign_bytes` includes incomplete outputs from earlier
attempts because they are not deleted during recovery.

The projection is sampled from metadata sizes before source-body reads and
again before publication using actual spool sizes.

Effective memory headroom is the minimum available value among:

- host `MemAvailable`;
- cgroup v2 `memory.max - memory.current`, when bounded;
- applicable `RLIMIT_AS` headroom, when bounded.

Projected incremental memory includes descriptor collection, the current
parsed source object, its normalized records, sort buffers, and serialization
buffers.

Only one artifact may occupy those structures at a time.

Prepare refuses before parsing when:

```text
free_bytes < 1.5 * projected_simultaneous_peak_bytes
```

or:

```text
projected_peak_rss =
  current_rss
  + max_over_sources(projected_incremental_memory)

effective_free_headroom <
  1.5 * max_over_sources(projected_incremental_memory)
```

`projected_peak_rss` is recorded for observability.

It is not compared directly with free headroom.

Before publication the runner remeasures free headroom and requires:

```text
effective_free_headroom >=
  1.5 * (largest_actual_spool_bytes + 1 MiB)
```

Measured peak RSS is persisted as evidence but is not substituted into either
free-headroom inequality.

The numeric inputs, formulas, operands, and decision are persisted.

Tests assert every operand as well as the resulting peak.

This task does not inspect crontab, databases, credentials, embedding capacity,
or external processes because it uses none of them.

The later full Slice 0 retrieval task must separately specify and perform its
broader operator inventory before it uses a database or embedding service.

## 17. Security

All review bodies are untrusted data.

The CLI:

- never evaluates source strings;
- never builds a shell command;
- never follows source symlinks;
- never logs source bodies;
- never logs environment values;
- rejects NULs and oversized input;
- uses JSON serialization rather than templates;
- emits `trust=untrusted-review-content`;
- stores only explicit local source paths and digests;
- has no network code;
- has no database code.

Credential-shaped sentinel text remains data in normalized JSONL.

Tests assert it is not copied to stdout, stderr, progress, heartbeat,
manifests, owner metadata, or receipts.

The normalized JSONL is itself sensitive derived data and remains under the
ignored experiment directory.

## 18. Test plan

The test suite uses temporary directories and synthetic schema-valid Magi
artifacts.

It covers:

- one artifact and one finding;
- multiple artifacts and deterministic sort order;
- repeated run produces byte-identical outputs;
- repeated source paths deduplicate files;
- malformed JSON;
- source-schema failure;
- duplicate finding ID;
- file-count ceiling;
- per-file and total-byte ceiling through injectable small test limits;
- NUL and text-size rejection;
- category lexicon matching;
- category three-item cap and canonical order;
- severity fallback;
- occurrence ID golden vector;
- immutable-input digest golden vector;
- source-byte change under the same campaign returns 4;
- completed exact rerun does not rewrite outputs;
- invalid completed output returns 2;
- prepare receipt is last;
- the receipt covers only the five immutable corpus outputs;
- progress, heartbeat, owner, and lock mutation cannot invalidate a valid
  corpus receipt;
- clean exit appends terminal success after receipt, removes owner metadata,
  and remains valid for `validate` and exact reuse;
- interruption after receipt is recovered as a completed corpus;
- receipt digest mismatch;
- incomplete temporary file is ignored;
- source symlink rejection;
- directory input rejection;
- campaign IDs containing `..`, `/`, an absolute path, an empty string, or
  more than 64 characters are rejected before writes;
- symlink state roots and symlink campaign directories are rejected;
- the resolved campaign path is strictly contained by the resolved state
  root;
- campaign directory rename/replacement before receipt publication is
  detected and no receipt is published;
- a campaign-directory symlink swap is rejected;
- all campaign mutations remain bound to the held directory descriptor;
- an explicit non-artifact JSON fails rather than being omitted;
- a source changed between open and final `fstat` is rejected;
- normalization uses the exact captured bytes whose digest is recorded;
- a 256-source fixture proves only one source body and parsed artifact
  contribute to peak memory at a time;
- an entry swapped to a symlink before open is rejected;
- nonblocking lock contention returns 3 and writes nothing else;
- stale dead same-host owner reclamation;
- live owner refusal;
- remote owner refusal;
- progress is valid append-only JSONL;
- owner contains no argv or environment;
- source bodies do not leak to metadata or process output;
- disk margin failure;
- memory margin failure through injected measurements;
- memory admission fails before JSON parsing or snapshot publication;
- recovery projection includes pre-existing incomplete outputs;
- a maximum-finding-count plus maximum-path fixture remains below the
  calculated spool and final JSONL bounds;
- baseline RSS and projected incremental parse demand are added rather than
  compared with `max`;
- maximum findings and maximum path length are included twice in incremental
  memory for the record list and serialization/spool buffer;
- free headroom is compared only with incremental demand, while absolute
  projected peak RSS is recorded separately;
- normalizer implementation-byte change invalidates reuse;
- fresh heartbeat without progress becomes stalled after 120 seconds;
- publish advances progress after every validated and fsynced output;
- maximum-fixture prepare wall time is recorded;
- validate is read-only;
- status output excludes bodies and paths not in its contract.

The test runner is:

```text
python3 plugins/harness-magi-codex/tests/test_deja_review_slice0.py
```

It requires no network, database, embedding service, or credentials.

## 19. Acceptance criteria

This foundation is complete only when:

1. all planned files exist;
2. tests pass without network or credentials;
3. a fixture corpus prepares deterministically;
4. `validate` accepts it;
5. exact rerun reuses it without mutation;
6. changed input under the same campaign fails closed;
7. a concurrent starter fails without campaign-state mutation;
8. source bodies occur only in normalized JSONL;
9. source files remain byte-identical;
10. no existing production prompt, migration, MCP tool, or lifecycle state
    changes.

## 19.1 Cost and decision budget

This foundation is not a business-value result.

It is justified only as the minimum provenance-safe input layer required by
the first labeled retrieval smoke test.

Precommitted effort limits:

```text
deterministic one-artifact corpus available: <= 6 operator/engineering hours
complete foundation implementation: <= 12 operator/engineering hours
external compute/API spend: USD 0
```

Grounded campaign timing:

- autorun armed at `2026-07-24T01:47:41Z`;
- the post-bug-hunt reconciliation check at `2026-07-24T03:05:44Z`
  measured 1 hour 18 minutes of tracked wall time.

That wall interval is not a complete operator/engineering-hours ledger.

Because exact charged hours cannot be reconstructed sufficiently to prove
remaining headroom, the conservative cut applies now:

- preserve the working spike;
- freeze new foundation features;
- permit only design-intent reconciliation, blocking correctness/security
  fixes, adversarial review, and tests;
- preserve the separate 12-hour retrieval-smoke-test budget in full.

The competing task is the fixed-query retrieval smoke test.

Twelve operator/engineering hours are reserved for that task and may not be
consumed by adding convenience features to this foundation.

The foundation stops and preserves the reversible spike when any holds:

- no deterministic corpus exists by hour 6;
- total foundation effort reaches hour 12;
- projected remaining foundation effort exceeds the separately reserved
  12-hour retrieval-smoke-test budget.

At the cut, recursive discovery, overrides, dashboards, and other reusable
campaign conveniences are deferred.

The later retrieval task preregisters a fixed labeled query set.

Its business decision metric is:

```text
at least five queries recover a known relevant prior finding that lexical
path-only lookup misses, with zero unsafe sentinel injection, zero lifecycle
misrepresentation, and zero provenance mismatch
```

No durable backend or production integration is authorized by this document.

The later retrieval or Slice 1 design must restate its full numeric quality,
safety, cost, maintenance, and payback predicates inline, pass its own
exact-revision review gate, and then satisfy those predicates.

## 20. Rollback

The implementation adds only new checked-in files plus README text.

Runtime state is derived and ignored.

Rollback is:

- stop any running foundation process;
- preserve state for diagnosis if needed;
- remove the new code only through an ordinary reviewed source change.

No data migration or canonical write must be reversed.

## 21. Follow-up boundary

The next task may add fixed queries, labels, lexical retrieval, embedding, and
ephemeral PostgreSQL parity.

It must not treat this foundation as evidence that:

- retrieval is relevant;
- lifecycle handling is correct;
- 50,000 exact rows meet the two-second p95 bound;
- a durable backend has positive value;
- Slice 1 is authorized.

This section is the authoritative local freeze point for two later-phase
gates; it does not depend on an external design artifact:

- receipt-retention transaction boundaries are outside this no-database task
  and remain blocked before Slice 1;
- the 50,000-row exact-scan ceiling is not inherited here and must be replaced
  by a measured maximum in the later retrieval task.
