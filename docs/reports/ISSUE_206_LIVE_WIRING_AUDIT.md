# Issue 206: live rail wiring audit report

Measurements concluded 2026-07-27T01:00:40Z
(2026-07-27T10:00:40+09:00). Final process snapshot:
2026-07-27T00:58:39Z (2026-07-27T09:58:39+09:00). Final Git snapshot:
2026-07-27T01:00:40Z (2026-07-27T10:00:40+09:00).

This was a read-only audit of the primary checkout and live state. No installer,
cron job, watcher activation, config edit, or deletion was run by the auditor.
No credential/config body or environment value was printed. Config inspection
was restricted to marker counts, hook event/matcher structure, command paths,
file metadata, and digests.

## Result

The live surface is not at `未配線 0`.

| Exception | Exact evidence | Classification / owner |
|---|---|---|
| Formation mail watcher depends on an untracked primary file | PIDs `2582674`, `2582701` use `plugins/harness-formation/bin/formation-mail-nudge`; PID file points to `2582701`; live SHA-256 `a7f5a31d…0276eed`; `git ls-files --error-unmatch` fails | owned remediation: #168 |
| Window-status helper remains untracked | `plugins/harness-formation/bin/formation-window-status`, SHA-256 `91381c00…b4702`, no live argv reference | owned remediation: #168 |
| Claude SessionStart command retains an unresolved plugin variable | argv0 is literal `${CLAUDE_PLUGIN_ROOT}/bin/install-cache-safe-entrypoints`; canonical executable exists, but the live global command is not a resolved path | owned remediation: #209 |
| Kimi has legacy registrations outside its managed block | 16 outside entries: 15 structurally duplicate managed registrations and one distinct legacy `session_end_scrub.sh` | owned remediation: #199 |
| Kimi credential guard is registered four times | two managed registrations plus two unmarked registrations, one path, two duplicated matchers | owned remediation: #199 |

The initially present `formation/ultramagi-cap-wip.patch` disappeared during
the audit without action by this auditor. It was already obsolete with respect
to merged #189; details and the concurrent-state limitation are below.

## 1. Primary worktree

Primary resolution used the first `worktree` record from
`git worktree list --porcelain`:

```text
/home/hrmtz/projects/claude-harness
```

At the final snapshot:

- primary HEAD: `ffbcbd45a016d1b22dc45f1042e052ec4e5e2e9a`
- `origin/dev`: `ffbcbd45a016d1b22dc45f1042e052ec4e5e2e9a`
- ahead/behind: `0/0`
- tracked modified: `0`
- untracked regular files: `2`
- untracked directories: `0`
- untracked symlinks: `0`

Dual observations agreed for tracked state: porcelain status with
`--untracked-files=no` returned zero rows, and the union of
`git diff --name-only` plus `git diff --cached --name-only` returned zero
paths. Final porcelain and `git ls-files --others --exclude-standard` both
returned the same two regular files. Because there was no untracked directory,
no ignored subtree could be masked by an untracked directory.

### Final untracked items

| Path | Type / mode / bytes | SHA-256 | Content-based purpose | Live reference | Equivalent tracked state |
|---|---|---:|---|---|---|
| `plugins/harness-formation/bin/formation-mail-nudge` | regular / `0775` / 6,498 | `a7f5a31d204dcbb5e3199be0d9d53fd8891232a8cb845266701e88fbb0276eed` | watches durable mail badges and sends a one-per-sequence pull nudge after stale/idle gates | yes, PIDs `2582674` and `2582701` | #168 branch tracks the same path, but its SHA is `bfe5db73…026c87`, not byte-equivalent |
| `plugins/harness-formation/bin/formation-window-status` | regular / `0775` / 8,262 | `91381c002db9cd0c830582caa251e554395ca757809540904cc966c9638b4702` | applies tmux window identity/task/mail formatting and optional arrangement | no process argv reference | #168 branch tracks the same path, but its SHA is `d5502dfe…33e90c`, not byte-equivalent |

Both regular files were read completely. Exact live blob hashes were absent
from `git rev-list --objects --all`; the tracked #168 versions are later,
different content rather than exact equivalents.

### WIP patch race and #189 comparison

The first snapshot contained a third untracked regular file:

```text
formation/ultramagi-cap-wip.patch
mode=0664 size=7607 lines=158
sha256=85857d5e84332ef3af91e705fbaec9c8f1b8634e0e944b66bb8e12f26b7f27e9
mtime=2026-07-26T20:30:41Z
```

It was read completely. It was a 10-hunk partial patch touching exactly:

- `plugins/harness-magi-codex/scripts/magi_campaign_guard.py`
- `plugins/harness-magi-codex/tests/test_campaign_guard.py`

Its purpose was to lower the per-campaign default from 16 to 12 and make
campaign-guard tests ceiling-relative. #189 is closed and merged through #190:
feature commit `e751648de7dcd8fd9ad072210c75f8dccb2e785a`, merge commit
`c42efaee4fa3fc3a18c010338957d70682af76b1`. Current `dev` independently
contains `DEFAULT_MAX_MODEL_LAUNCHES = 12`, `GLOBAL_MAX_MODEL_LAUNCHES = 16`,
the dynamic `while spent < DEFAULT_MAX_MODEL_LAUNCHES` fixture, and six
`self.filled_rounds` references. The merged feature touched these two paths
plus thirteen more required protocol, skill, design, and test paths.

Neither forward nor reverse `git apply --check` succeeded because the merged
implementation expanded beyond the partial patch. The semantic checks above,
the merged issue record, and the broader merged diff establish obsolescence;
the patch was not applied. Before the final 00:54:07Z Git snapshot the path had
been removed concurrently. This is why the first observation had three
untracked regular files while both final methods report two.

## 2. Claude hook parity

### Live artifact directory

Immediately below `~/.claude/hooks/`:

- regular files: `43`
- symlinks: `1`
- real-file-or-symlink total: `44`
- subdirectories (outside denominator): `2` (`__pycache__`, `tests`)
- broken symlinks: `0`

The canonical union of `plugins/harness-*/hooks/*.{sh,py}` has 44 unique
basenames. Independent basename set comparison found:

- expected/live: `44/44`
- exact byte matches: `44/44`
- normalized/stamped matches: not applicable (`0`; Claude artifacts are copied
  or linked, not chassis-stamped)
- canonical missing live: `0`
- live extras: `0`
- byte mismatches: `0`

The one symlink is `formation_suggest.sh`; it resolves to the canonical primary
path and its target bytes match. Six shell files have mode `0664`, but every
corresponding live command passes them to `bash`; therefore none requires its
own executable bit.

### Wiring structure and checker blind spot

`python3 scripts/check_hook_wiring_drift.py` returned:

```text
live-wired: 31  plugin-wired: 30
IN SYNC (one allowlisted SessionEnd integration)
```

Independent JSON structure counts were:

- live command registrations: `33`
- plugin structural registrations recognized as hook scripts: `31`
- live unique `(event, script)` pairs recognized by the checker: `31`
- plugin unique `(event, script)` pairs: `30`
- missing canonical hook/script targets: `0`
- stale/disposable worktree references: `0`

The two live-only structural registrations are the allowlisted hippocampus
`SessionEnd` script and a `SessionStart` command named
`install-cache-safe-entrypoints`. The latter has no `.sh`/`.py` suffix, so the
checker regex at `scripts/check_hook_wiring_drift.py:50` does not count it.

The live SessionStart argv0 remains literal:

```text
${CLAUDE_PLUGIN_ROOT}/bin/install-cache-safe-entrypoints
```

The canonical primary target exists with mode `0775`. However,
`scripts/sync_hooks_to_live.py:49-50` rewrites only
`${CLAUDE_PLUGIN_ROOT}/hooks/`, not the `bin/` prefix. Thus the checker can say
in-sync while this global live command is unresolved. Remediation is #209.

## 3. Crontab

The crontab was held in memory and never executed. Two independent scheduled
line counts agreed:

- AWK nonblank/noncomment/non-assignment count: `38`
- Python line parser count: `38`

Shell-aware lexical inspection, excluding redirection destinations, produced:

- scheduled entries: `38/38`
- entries containing a literal absolute command/script operand: `25/38`
- entries whose command resolves after `$HOME`, `~`, or PATH resolution:
  `38/38`
- entries rooted in canonical claude-harness: `6/38`
- entries rooted in another claude-harness worktree: `0/38`
- missing command/script operands: `0/82` validated targets
- required executable targets lacking execute permission: `0/82`
- exact duplicate scheduled lines: `0`
- duplicate `sanada_backup_retention_daily` markers: `0`
- duplicate `harness_hook_wiring_drift_nightly` markers: `0`

Three command signatures intentionally recur at distinct schedules:
`cron_ingest.sh` three times, `discord-bot` twice, and
`run_refresh_ig.sh` twice. No schedule+command line is duplicated.

### #193 retention job

The single `sanada_backup_retention_daily` row is crontab line 59:

- marker count: `1`
- schedule: `50 4 * * *`
- wrapper: `/usr/bin/flock -n`
- runner:
  `/home/hrmtz/projects/claude-harness/scripts/sanada_backup_retention.py`
- mode: `--apply` present once
- disposable worktree reference: none
- log directory: exists and is writable

Dry-run default was verified without running deletion in three ways:

1. `--help` exposes only an opt-in `--apply` deletion switch.
2. Source lines 177 and 187–188 select `DRY-RUN` and skip deletion when
   `args.apply` is false.
3. The retention and cron-installer unit suites passed 7/7 tests, including
   `test_default_is_dry_run_and_policy_is_classified_by_name`.

## 4. Codex, Grok, and Kimi managed registrations

The official checker returned
`cross-CLI hook overlay: in sync (24 hooks checked, live=1)`. Independent
marker/path parsing found:

| CLI | Begin/end markers | Expected/live managed registrations | Primary-root commands | Intentional nonprimary | Disposable-root | Missing / non-executable required | Unmarked exact owned duplicates |
|---|---:|---:|---:|---:|---:|---:|---:|
| Codex | `1/1` | `28/28` | `27/28` | `1/28` hippocampus external | `0` | `0/0` | `0` |
| Grok | `0/0` (whole generated file, one `_generated_by`) | `11/11` | `11/11` | `0` | `0` | `0/0` | `0` |
| Kimi | `1/1` | `16/16` | `16/16` | `0` | `0` | `0/0` | `15` |

Codex has 27 unique normalized command strings because `sr_depth_gate.py` is
registered for two events. Five nonowned Codex command fields exist outside the
marker block, with zero exact overlap with the managed set. Its stable
`~/.local/bin/harness-hook` dispatcher exists and is executable; all 28 resolved
script/dispatcher references exist.

Grok has only `harness.json` immediately under its hook directory. Its eleven
normalized command strings are unique.

Kimi has 32 total `[[hooks]]` entries: 16 inside and 16 outside the marker.
Of the outside entries, 15 exactly duplicate the managed event/matcher/script
signatures. The remaining outside entry is a distinct legacy
`SessionEnd/session_end_scrub.sh`; the one managed-only entry is
`UserPromptSubmit/secret_delivery_prompt.py`.

## 5. Kimi credential guard / #199

For `credential_file_read_guard.sh`:

- inside managed block: `2`
- outside managed block: `2`
- total live registrations: `4`
- unique command paths: `1`
- hook event: `PreToolUse` for all four
- distinct matcher signatures: `2` (`Read` and the MCP read-file matcher)
- effective duplicate registrations: `2` (one extra registration per matcher)

The installer at `install-kimi-hooks.sh:150-157` replaces only the existing
marker span; when markers exist it does not migrate exact legacy entries
outside the span. The checker at `scripts/check_cross_cli_hooks.sh:117-130`
extracts only the marker block and therefore cannot detect the 15 duplicate
outside registrations. Issue #199 remains open and is not superseded.

## 6. Live process and canonical dependency snapshot

At 00:58:39Z, `ps -eo pid,etimes,args` and independent
`/proc/<pid>/cmdline` enumeration agreed exactly:

- matching processes: `15/15`
- intersection: `15`
- ps-only/proc-only: `0/0`
- argv with canonical primary helper/script: `9/15`
- argv with disposable `claude-harness-wt-*` or `_formation_wt`: `0/15`
- stable local or `/tmp` scratch-only references: `6/15`

| PID | Elapsed seconds | Executable | Referenced path class |
|---:|---:|---|---|
| 563840 | 15662 | `/usr/bin/bash` | `/home/hrmtz/projects/claude-harness/plugins/harness-formation/lib/mailbox_relay.sh` |
| 566857 | 15654 | `/usr/bin/bash` | `/home/hrmtz/projects/claude-harness/plugins/harness-formation/lib/mailbox_relay.sh` |
| 568721 | 15649 | Codex 0.145.0 binary | `/home/hrmtz/.local/libexec/claude-harness-codex-real` |
| 1701945 | 7378 | `/usr/bin/bash` | `/home/hrmtz/projects/claude-harness/plugins/harness-formation/lib/mailbox_relay.sh` |
| 2039550 | 6191 | `/usr/bin/bash` | `/home/hrmtz/projects/claude-harness/plugins/harness-formation/lib/mailbox_relay.sh` |
| 2582674 | 3219 | `/usr/bin/zsh` | `/home/hrmtz/projects/claude-harness/plugins/harness-formation/bin/formation-mail-nudge` (untracked) |
| 2582701 | 3219 | `/usr/bin/bash` | `/home/hrmtz/projects/claude-harness/plugins/harness-formation/bin/formation-mail-nudge` (untracked) |
| 3258034 | 1006 | `/usr/bin/zsh` | `/tmp/claude-1000/-home-hrmtz-projects-claude-harness/2e628d2d-ba34-4e52-bafa-509a1ea7f566/scratchpad/stall_watch.py` |
| 3258044 | 1006 | `/usr/bin/python3.12` | `/tmp/claude-1000/-home-hrmtz-projects-claude-harness/2e628d2d-ba34-4e52-bafa-509a1ea7f566/scratchpad/stall_watch.py` |
| 3650232 | 98 | Codex 0.145.0 binary | `/home/hrmtz/.local/libexec/claude-harness-codex-real` |
| 3660821 | 64 | `/usr/bin/bash` | `/home/hrmtz/projects/claude-harness/plugins/harness-formation/lib/mailbox_relay.sh` |
| 3670702 | 36 | `/usr/bin/dash` | `/home/hrmtz/projects/claude-harness/plugins/harness-kimi/kimi_session_scrub.sh` |
| 3670705 | 36 | `/usr/bin/python3.12` | `/home/hrmtz/projects/claude-harness/plugins/harness-kimi/kimi_session_scrub.py` |
| 4095194 | 18085 | `/usr/bin/zsh` | `/tmp/claude-1000/-home-hrmtz-projects-claude-harness/2e628d2d-ba34-4e52-bafa-509a1ea7f566/scratchpad` |
| 4095196 | 18085 | Codex 0.145.0 binary | `/home/hrmtz/.local/libexec/claude-harness-codex-real` plus `/tmp/claude-1000/-home-hrmtz-projects-claude-harness/2e628d2d-ba34-4e52-bafa-509a1ea7f566/scratchpad/{pr232.diff,verify-232}` |

The sole consulted PID file,
`~/.formation/state/mail-nudge.pid`, contained `2582701`; `/proc/2582701/cmdline`
named the same canonical-root helper. The process-visible helper SHA matched the
live file SHA exactly. `git ls-files --error-unmatch` failed, proving that the
active watcher depends on an untracked file.

### Mail-nudge observation

No cause is inferred from repaint behavior. These are direct observations:

- first sample: pane `%340` pending `1186`, nudged `1162`, badge age `3005s`;
  pane `%359` pending `1192`, nudged `1187`, age `2578s`
- five seconds later both pane-content checksums had changed
- over one 31-second watcher interval, using the helper's exact
  `tail -n 40 | cksum` shape:
  - `%340`: live checksum `1806830961 → 472898035`; stored idle checksum
    `2057376519@1785113947 → 3201143700@1785113978`
  - `%359`: live checksum `3580507700 → 1179690681`; stored idle checksum
    `3170686131@1785113947 → 3020745751@1785113978`
- final state: `%340` pending `1193`, nudged `1162`, since `1785110934`,
  age `3087s`; `%359` pending `1192`, nudged `1187`, since `1785111361`,
  age `2660s`

Thus both panes had checksum churn and neither nudged sequence caught up during
the measured interval. The measurement does not establish why the panes
repainted or whether checksum churn is the sole cause.

## 7. Reproduction and limitations

Critical conclusions used two observations:

- Git dirt: porcelain plus diff/`ls-files`.
- Hook wiring: repository checkers plus direct JSON/TOML marker and command
  structure parsing.
- Hook artifacts: basename set comparison plus SHA-256 byte comparison.
- Cron: AWK line count plus Python shell-aware parsing; target existence was
  checked after wrapper/interpreter operand resolution.
- Processes: `ps` plus `/proc/<pid>/cmdline`; PID-file state was checked against
  `/proc`, not only `kill -0`.
- Mail idle state: tmux option metadata plus two pane checksum methods and the
  watcher's persisted checksum state.

Commands sufficient to reproduce the non-sensitive counts:

```bash
git worktree list --porcelain
git -C /home/hrmtz/projects/claude-harness status --porcelain=v1 --untracked-files=all
git -C /home/hrmtz/projects/claude-harness ls-files --others --exclude-standard
python3 scripts/check_hook_wiring_drift.py
bash scripts/check_cross_cli_hooks.sh --live
crontab -l
ps -eo pid,etimes,args
```

Limitations:

- Live state changed during measurement. The WIP patch disappeared, short-lived
  reviewer/hook processes came and went, and pane `%340` advanced from pending
  sequence 1186 to 1193. Final denominators are explicitly timestamped.
- Process canonicality is an argv snapshot, not proof that a process did not
  open another file earlier.
- Config parsers inspected command/path structure only; arbitrary config values
  were intentionally not audited.
- No hook was fired merely to prove runtime behavior. Active/nonactive claims
  are limited to wiring, target resolution, and observed live processes.

## Owned remediation table

| Item | State at final snapshot | Owner |
|---|---|---|
| Track and replace live mail-nudge dependency | open; active untracked dependency remains | #168 |
| Track/land window-status helper | open; untracked helper remains | #168 |
| Remove unresolved Claude SessionStart plugin-root argv | open checker-blind wiring defect | #209 |
| Migrate Kimi unmarked legacy registrations and extend checker | open; 15 duplicates remain | #199 |
| Remove obsolete #189 WIP patch | path absent at final snapshot; no auditor action | completed concurrently, #189/#190 provenance |
