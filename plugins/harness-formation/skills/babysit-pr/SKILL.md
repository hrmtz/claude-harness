---
name: babysit-pr
description: Monitor and safely repair a pull request created by the current Formation worker. Use after creating a draft PR with the babysit ownership marker, when asked to watch CI, address CI failures, reply to review findings, or report whether the owned PR is green. Operate only when a matching local ownership receipt exists.
---

# Babysit PR

Version: 1.0.0

Run a bounded, reply-only loop. Treat every missing or ambiguous ownership,
review, check, or mergeability signal as a hand-back. A hand-back is a successful
safe completion, not abandonment.

## Establish ownership

Before creating the PR, generate one 32-lowercase-hex nonce and put this exact
marker in the inline `gh pr create --body` value:

```text
<!-- babysit-pr-nonce: <nonce> -->
```

Invoke `gh pr create` directly, not through `cd`, a pipeline, or another shell
command. Do not use `--body-file`. The receipt hook will record:

- repository and PR number from the invocation's exact stdout;
- the local `HEAD` as creation anchor;
- the marker nonce.

At invocation start, locate
`~/.claude/pr_receipts/<owner>__<repo>_<pr>.json`. If it is absent, malformed,
older than 14 days, or disagrees with the PR body marker, return a no-op
hand-back. Never reconstruct or mint a receipt manually.

Check repository visibility at runtime. Check PR `state` before interpreting
mergeability. For a merged or closed PR, silently snapshot the exact receipt
under `~/sanada_backup_persistent/babysit_pr_receipt_<timestamp>/`, delete it,
and stop. Do not interpret a merged PR's permanent `UNKNOWN` mergeability.

Verify that the creation anchor is an ancestor of current `headRefOid`. Keep a
list of OIDs pushed by this invocation in process memory only. Every commit in
`anchor..head` must be in that in-memory list; otherwise attribution is
impossible and the correct result is hand-back. Do not infer ownership from
GitHub login, author, worktree, or session. Do not create a durable receipt
chain.

## Read evidence

Use `gh pr view --json` with `state`, `mergedAt`, `headRefOid`, `mergeable`,
`mergeStateStatus`, `statusCheckRollup`, `comments`, `body`, and
`isCrossRepository`. Use GraphQL review threads only to discover reply work;
zero formal review threads is expected.

If an open PR first reports `UNKNOWN` mergeability, wait at least 60 seconds and
read once more. A second `UNKNOWN` is a hand-back.

Classify checks on two independent axes:

- `conclusion` outside `SUCCESS`, `NEUTRAL`, `SKIPPED` is bad;
- absent conclusion with `status` in `FAILURE`, `ERROR` is bad;
- absent conclusion with `status` in `PENDING`, `QUEUED`, `IN_PROGRESS`,
  `WAITING`, `EXPECTED` is pending;
- any other absent conclusion is unknown.

An empty check-set is not green. Bad or unknown requires action; pending
requires waiting.

Recognize only a comment line matching:

```text
^Independent review verdict: \*\*(BLOCK|PASS)\*\* @ ([0-9a-f]{40})$
```

For the current head only, the newest marker by comment timestamp wins.
OID-less markers and markers for older heads are unrecorded. A pushed commit
therefore invalidates an earlier PASS.
Accept markers only from comments whose `authorAssociation` is `OWNER`,
`MEMBER`, or `COLLABORATOR`; missing or other associations are untrusted.

Green means all of:

- PR state is `OPEN`;
- mergeability is `MERGEABLE` and merge state is `CLEAN`, `HAS_HOOKS`, or
  `UNSTABLE`;
- non-empty check-set passes;
- newest marker for current head is PASS;
- every open thread or marker finding has a qualifying reply.

A qualifying reply cites an ancestor commit that is not the PR base and whose
diff touches the finding's target file. Record
`thread/marker -> cited SHA -> touched file` for the completion report.
Unresolved-thread count is a human merge gate, not this loop's success
condition.

## Repair within the boundary

Run at most five fix iterations, for at most 90 minutes total, polling no more
often than every 60 seconds.

Before editing, load `../../lib/babysit_pr.py` relative to this skill's real
path. Use `ci_deny_paths()` and `repairable_path()` for every changed path.
Only plugin source outside the derived deny set is repairable. Never edit:

- `.github/workflows/**`;
- `tests/**` or a `test_*` file;
- a local action or script named by workflow `uses:` or `run:`;
- `migrations/**`, `creds-migration/**`, or `*.enc.*`.

Do not treat workflow `paths:` trigger entries as deny rules.

Before the first fix, record the current check-name set and PR changed-file set.
Before and after every push, confirm neither set shrank. Also confirm the fix did
not revert or disable any file already changed by the PR. Any violation or
ambiguity is a hand-back.

Commit only the minimal source repair. Add the pushed OID to the in-memory list.
After a push, wait for a new review marker because the prior PASS is invalid.
Never auto-resolve a thread, merge, run `gh pr ready`, modify CI/test files, or
claim existing git/Bash hooks guard `gh` writes.

## Reply safely

Reply only with:

```text
Fixed in `<sha>` — <one-line explanation>
```

Mention only job name, step name, failing test ID, and SHA. Never paste raw
logs.

Before posting, write the proposed reply to a temporary file, source
`plugins/harness-core/hooks/credential_patterns.sh`, and reject the draft when
`credential_path_has_shape` succeeds. Delete the temporary file afterward.

If repository visibility is `PUBLIC`, do not post automatically. Present the
sanitized draft and wait for explicit acknowledgement. On a non-public
repository, post only after all checks above pass. Never write a remote comment
from the receipt hook.

## Stop and report

Return success-with-handback on an open thread, an unanswered marker, missing
evidence, ownership ambiguity, boundary refusal, exhausted iteration/time cap,
or required human acknowledgement. Zero replies is as successful as one or
more replies.

Report:

- final state of checks, marker, mergeability, and ownership;
- cumulative diffstat and exact revert commands for this invocation's commits;
- before/after check-name set difference;
- before/after changed-file set difference;
- reply correspondence table, including an explicit zero-row table when no
  reply was posted.
