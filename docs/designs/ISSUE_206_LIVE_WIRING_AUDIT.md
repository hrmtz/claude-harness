# Issue 206: live rail wiring audit

Status: execution-ready audit design  
Issue: https://github.com/hrmtz/claude-harness/issues/206

## Mission

Produce a reproducible, read-only inventory proving which repository rails are
tracked, installed, canonically rooted, and active. Every exception must have
an exact path/count and an owner or follow-up issue. “Looked correct” is not
evidence.

## Safety boundary

This phase is audit only.

- Do not edit primary or live configuration.
- Do not run installers, hook update commands, cron entries, or watcher
  activation commands.
- Do not stop the live `formation-mail-nudge --watch` process.
- Do not delete `formation/ultramagi-cap-wip.patch` yet.
- Do not read credential files, encrypted values, environment values, or raw
  auth/config bodies.
- Config inspection is limited to marker counts, command/path fields, and
  hashes; redact nothing by printing nothing sensitive.
- Use SOPS only if later remediation genuinely needs a value; this audit does
  not.

The primary checkout is resolved mechanically as the first entry from:

```text
git worktree list --porcelain
```

Expected primary for this host:

```text
/home/hrmtz/projects/claude-harness
```

## Deliverable

Write:

```text
docs/reports/ISSUE_206_LIVE_WIRING_AUDIT.md
```

The report includes:

- UTC and JST measurement time;
- primary commit and origin/dev comparison;
- exact commands or a precise method sufficient to reproduce every count;
- numerator and denominator for every category;
- exact exception paths, never only a total;
- confidence/limitations;
- a final owned remediation table.

Commit and push only this report. Open a draft PR to `dev`. Do not remediate in
the audit branch.

## 1. Primary worktree inventory

Measure with both porcelain status and filesystem enumeration:

- tracked modified files;
- untracked regular files, directories, and symlinks;
- ignored files only where an untracked directory masks content;
- each untracked item's type, mode, size, SHA-256 (regular files), first
  non-comment purpose, and whether a live process references it;
- whether an equivalent tracked file exists in any branch/worktree.

Read every untracked regular file fully. Classify content, not filename.

Known candidates to verify rather than assume:

- `plugins/harness-formation/bin/formation-mail-nudge`;
- `plugins/harness-formation/bin/formation-window-status`;
- `formation/ultramagi-cap-wip.patch`.

For the WIP patch, prove whether #189 made it obsolete by comparing touched
paths/hunks with current `dev`; do not apply it.

## 2. Claude hook parity

Inventory real regular files and symlinks immediately below:

```text
~/.claude/hooks/
```

Compare them with the canonical sources/install manifest in the primary plugin
tree. Report:

- live total;
- canonical-owned expected total;
- exact byte matches;
- normalized matches where an installer intentionally stamps the canonical
  root;
- live extras;
- canonical missing live files;
- stale/disposable worktree references;
- broken symlinks and non-executable commands.

Use the repository's checker where available, but independently verify its
denominator and path normalization so a checker blind spot cannot report 0/0.
Do not print hook bodies that contain arbitrary user data.

## 3. Crontab audit

Snapshot `crontab -l` in memory and parse every nonblank, noncomment scheduled
entry without executing it. Report:

- total entries;
- entries with an absolute command path;
- entries rooted in canonical checkout;
- entries rooted in any other claude-harness worktree;
- entries with missing command targets;
- entries whose command target is not executable where execution requires it;
- duplicate semantic jobs/markers;
- entries that invoke shell/interpreter plus a script path, validating the
  script operand rather than only `/bin/bash` or `python3`.

List every noncanonical/missing exception with line number and sanitized
command path.

Explicitly verify the #193 line:

- exactly one `sanada_backup_retention_daily` marker;
- schedule `50 4 * * *`;
- canonical script path;
- `/usr/bin/flock -n`;
- `--apply`;
- writable log directory;
- runner default without `--apply` is dry-run, using tests/help/source rather
  than running deletion.

The previously quoted “38 entries” is a hypothesis. Report the actual
denominator from two independent counts.

## 4. Managed hook blocks for three CLIs

Audit Codex, Grok, and Kimi managed hook registrations using the repository
parsers/checkers where possible. Inspect only managed marker regions and
command/path fields.

For each CLI report:

- managed block start/end marker count;
- owned hook registration count;
- primary-root command count;
- disposable/nonprimary-root command count;
- missing/non-executable target count;
- exact offending paths;
- unmarked legacy entries duplicating a managed owned command.

Cross-check each checker result against an independent structural count. A
checker that examines only the marker block does not prove there are no
duplicate owned entries outside it.

## 5. Kimi duplicate registration (#199)

Specifically count every live Kimi registration whose command resolves to
`credential_file_read_guard.sh`, separated into:

- inside the managed block;
- outside the managed block;
- unique command paths;
- effective duplicate registrations per hook event.

Also inspect the current installer and checker behavior to state whether:

- install migrates exact legacy unmarked entries;
- checker detects duplicate owned entries outside markers;
- #199 is resolved, still open, or superseded.

No installer run is authorized.

## 6. Live process and canonical dependency audit

List live processes whose argv references claude-harness or a Formation helper,
reporting only PID, elapsed time, executable/script path, and canonicality.
Exclude environment inspection.

At minimum verify:

- the live mail-nudge watcher path and whether that file is tracked;
- no active process points at a disposable `claude-harness-wt-*` root unless
  explicitly owned and temporary;
- relay/service PID files, if consulted, match process argv rather than only
  `kill -0`.

## 7. Observation validity

For every critical conclusion use two independent observations, for example:

- `git status --porcelain=v1` plus `git ls-files --error-unmatch`;
- crontab parsed entry count plus line-oriented count;
- official drift checker plus direct marker/path count;
- process list plus `/proc/<pid>/cmdline` argv shape.

If the methods disagree, report the disagreement; do not choose the convenient
number.

Temporal claims must use file metadata, process elapsed seconds, git history,
or the supplied 2026-07-27 anchor.

## Acceptance

The audit is acceptable only when the report contains:

1. exact primary commit and dirty/untracked counts;
2. a content-based classification for every untracked item;
3. Claude hook parity with nonzero denominators and exact exceptions;
4. actual crontab total and canonical/noncanonical/missing counts;
5. per-CLI managed block counts and roots;
6. exact #199 duplicate count inside/outside markers;
7. #193 cron schedule/path/behavior verification;
8. live process canonicality counts;
9. a table where every exception is either `owned remediation`, `intentional
   live temporary`, or `unexplained`;
10. no unexplained exception described as zero without dual-method evidence.

Final “未配線 0” is allowed only after the tracked #168 work replaces the live
untracked dependency, obsolete WIP state is safely removed after backup, and
all other remediation rows are closed. The audit report itself may correctly
conclude nonzero.

## Ownership

A dedicated Codex subagent performs the read-only measurements and report.
hc-orch reviews denominators, reproduces critical counts, assigns remediation,
and requests an exact-head Kimi review before merging the audit report.

