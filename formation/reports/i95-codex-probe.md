# Issue 95 Codex identity probe

Measured 2026-07-26 on Codex `0.145.0`, Claude Code `2.1.220`, tmux
`i95codex-*` throwaway sessions only. Empty values below are literal empty tmux
options. `pane_title` is TUI-owned and therefore may include a spinner.

## 1. Fact table

| # | Scenario | Before / stable parent | After child/start | Verdict |
|---|---|---|---|---|
| 1 | Codex interactive, fresh pane `%185` | `window=baseline`, `title=chichibu`, `formation_id=`, `locked=` | `window=baseline`, `title=claude-harness`, `formation_id=`, `locked=` | Identity options/window clean; TUI title changed |
| 2 | `claude -p 'say ok'` launched by Codex `%185` | `window=baseline`, `title=claude-harness`, `formation_id=`, `locked=` | `window=baseline`, `title=⠼ claude-harness`, `formation_id=storm-thistle`, `locked=` | **DRIFT**: Claude stole `@formation_id` |
| 3 | `claude --help` launched by Codex `%187` | `window=fresh`, `title=claude-harness`, `formation_id=`, `locked=` | `window=fresh`, `title=⠼ claude-harness`, `formation_id=`, `locked=` | Clean (apart from Codex spinner) |
| 4 | `codex exec 'say ok'` launched by Claude `%190` | Claude-owned `window=fresh`, `formation_id=steady-wren`, `locked=` | `window=fresh`, `title=✳ Execute say ok command`, `formation_id=steady-wren`, `locked=` | Clean; child did not steal routing option |
| 5 | `codex --help` launched by Claude `%202` | `window=fresh`, `title=✳ Claude Code`, `formation_id=cinder-sparrow`, `locked=` | `window=fresh`, `title=✳ View codex command help`, `formation_id=cinder-sparrow`, `locked=` | Clean; only Claude's own TUI title changed |
| 6 | Codex exits, then Claude interactive in same pane `%195` | after Codex exit: `window=sequential`, `title=`, `formation_id=`, `locked=` | `window=sequential`, `title=✳ Claude Code`, `formation_id=crimson-falcon`, `locked=` | Sequential reuse claims normally |
| 7 | Shared window: Codex pane A `%196`, Claude pane B `%197` | both `window=shared`, `panes=2`, empty identity options | A: `window=shared`, `title=⠙ claude-harness`, empty options; B: `window=shared`, `title=✳ Claude Code`, `formation_id=onyx-thistle`, `locked=` | Window name preserved; pane B claims its own pane option |

Raw tmux output (quoted verbatim):

```text
1 BEFORE|pane=%185|window=baseline|title=chichibu|formation_id=|locked=|pane_pid=331581
1 AFTER|pane=%185|window=baseline|title=claude-harness|formation_id=|locked=|pane_pid=331581

2 SCENARIO2_BEFORE|pane=%185|window=baseline|title=claude-harness|formation_id=|locked=
2 SCENARIO2_AFTER|pane=%185|window=baseline|title=⠼ claude-harness|formation_id=storm-thistle|locked=

3 BEFORE|pane=%187|window=fresh|title=chichibu|formation_id=|locked=|pane_pid=382581
3 CODEX_STARTED|pane=%187|window=fresh|title=claude-harness|formation_id=|locked=
3 AFTER|pane=%187|window=fresh|title=⠼ claude-harness|formation_id=|locked=

4 BEFORE|pane=%190|window=fresh|title=chichibu|formation_id=|locked=|pane_pid=417421
4 CLAUDE_STARTED|pane=%190|window=fresh|title=✳ Claude Code|formation_id=|locked=
4 SCENARIO4_FINAL|pane=%190|window=fresh|title=✳ Execute say ok command|formation_id=steady-wren|locked=

5 BEFORE|pane=%202|window=fresh|title=chichibu|formation_id=|locked=|pane_pid=590443
5 CLAUDE_STARTED|pane=%202|window=fresh|title=✳ Claude Code|formation_id=cinder-sparrow|locked=
5 AFTER|pane=%202|window=fresh|title=✳ View codex command help|formation_id=cinder-sparrow|locked=

6 BEFORE_CODEX|pane=%195|window=sequential|title=chichibu|formation_id=|locked=|pane_pid=486303
6 CODEX_ACTIVE|pane=%195|window=sequential|title=claude-harness|formation_id=|locked=
6 CODEX_EXITED|pane=%195|window=sequential|title=|formation_id=|locked=
6 CLAUDE_AFTER|pane=%195|window=sequential|title=✳ Claude Code|formation_id=crimson-falcon|locked=

7 BEFORE_A|pane=%196|window=shared|panes=2|title=chichibu|formation_id=|locked=|pane_pid=510508
7 BEFORE_B|pane=%197|window=shared|panes=2|title=chichibu|formation_id=|locked=|pane_pid=510523
7 AFTER_A|pane=%196|window=shared|panes=2|title=⠙ claude-harness|formation_id=|locked=
7 AFTER_B|pane=%197|window=shared|panes=2|title=✳ Claude Code|formation_id=onyx-thistle|locked=
```

The strongest regression witness is scenario 2. While it was active:

```text
337235  331581 claude-harness- /home/hrmtz/.local/libexec/claude-harness-codex-real
363604  337235 claude          claude -p say ok
```

## 2. `comm` findings

The current fixed classifier is defeated by the normal `codex` command on
`PATH`. It looks for `codex|kimi|kimi-code|grok`, but this installation executes
the real binary through a long symlink name.

| Launch route | Observed process chain / `comm` | Classifier result |
|---|---|---|
| Real binary by canonical absolute path | `547224 ... codex /home/hrmtz/.codex/.../bin/codex` | matches |
| Normal `codex` on `PATH` | `337235 ... claude-harness- /home/hrmtz/.local/libexec/claude-harness-codex-real` | **misses** |
| `harness-cross-cli --allow-self-name -- codex` | `bash(552577) → claude-harness-(552616)` | **misses** the actual Codex owner |

Exact observed output:

```text
DIRECT_BINARY pane=%199 shell=543910 real=/home/hrmtz/.codex/packages/standalone/releases/0.145.0-x86_64-unknown-linux-musl/bin/codex
 547224  543910 codex           /home/hrmtz/.codex/packages/standalone/releases/0.145.0-x86_64-unknown-linux-musl/bin/codex

CROSS_CLI pane=%200 shell=548022
zsh,548022
  `-bash,552577 plugins/harness-core/bin/harness-cross-cli --target-pane %200 --allow-self-name -- codex
      `-claude-harness-,552616
```

Minimal missed-launch reproduction in an isolated tmux session:

```bash
tmux new-session -d -s i95-miss 'env -u FORMATION_SELF codex'
sleep 10
shell_pid=$(tmux display-message -p -t i95-miss: '#{pane_pid}')
ps --ppid "$shell_pid" -o pid=,ppid=,comm=,args=
# comm => claude-harness- ; current case list does not match it
```

This kernel exposes at most **15 bytes** in `comm`. I set the task name with
`prctl(PR_SET_NAME)` to 15, 16, and 20 ASCII bytes; the measured output was:

```text
PRCTL_LENGTH_TEST
 565573 abcdefghijklmno python3 -c ...
 565574 abcdefghijklmno python3 -c ...
 565575 abcdefghijklmno python3 -c ...
```

Renaming behavior:

- A symlink name can defeat name matching. Invoking the Codex ELF through
  `claude-harness-codex-real` made `comm=claude-harness-`; invoking the same ELF
  by its canonical path made `comm=codex`.
- `exec -a` changes `argv[0]`, not `comm`, on this machine:

  ```text
  EXEC_A_TEST
   565576 sleep           renamed-to-codex-wrapper 8
  ```

- A shell function name also does not survive the eventual exec; the executable
  controls `comm`:

  ```text
  SHELL_FUNCTION_TEST
   565577 sleep           /bin/sleep 8
  ```

  A function can still defeat the classifier indirectly by choosing a
  misleadingly named executable or symlink, but its own function name is not
  observable in `ps -o comm=`.

## 3. Owner-PID derivation

### Positive real-Codex result

I made Codex run `bash -c 'sleep 60'`. The shell exec collapsed to `sleep`, so
the claimant-side ancestry was especially clear:

```text
OWNER_DERIVATION pane_pid=382581 claimant=575837
 575837  386444 sleep           sleep 60
 386444  382581 claude-harness- /home/hrmtz/.local/libexec/claude-harness-codex-real
 382581    3886 zsh             -zsh
```

Walking upward to `pane_pid=382581` and selecting its immediate child gives
PID `386444`, the live Codex CLI. This handles the exact missed-`comm` case.

### Boundary cases

| Case | Result | Concrete consequence |
|---|---|---|
| Direct CLI, including long symlink name | Handles | PID `386444` is selected without consulting `comm` |
| `exec` chain / CLI re-exec | Handles | exec preserves PID; the measured `bash -c 'sleep 60'` became `sleep(575837)` without breaking ancestry |
| `setsid` only | Handles | measured `sleep(610156)`, `PPID=610012`, `SID=610156`; session detachment did not detach parentage |
| `harness-cross-cli` | Detects lifetime, but does **not** select the documented “CLI process” | ancestry is CLI `552616` → guard bash `552577` → pane shell `548022`; the rule selects guard PID `552577`. The guard lives for the child lifetime, so nesting protection works, but the ownership-record definition is inaccurate |
| Daemonized/double-fork child | Breaks | measured child `610015` was reparented to PID 1; its ancestry cannot reach the pane shell. A claimant hook in that child cannot derive a pane owner |
| Owner daemonizes after claim | Breaks | recorded parent PID becomes dead while the real daemon remains live, so a foreign CLI can claim concurrently |
| `tmux respawn-pane` | Pane options survive | old process dead normally permits reclaim, but the stale numeric PID remains and can collide with PID reuse |
| `tmux kill-server` / native restart | Options do not survive | a new server reused pane `%0` but both ownership options were empty; cross-reboot risk exists only if some external restore layer replays options |

Measured daemon and `setsid` controls:

```text
DAEMONIZED_CHILD
 610015       1  610012 i95-daemon      python3 -c ...
SETSID_CHILD caller_pid=610012
 610156  610012  610156 sleep           sleep 8
```

Measured inactive-pane respawn (no `-k` was needed):

```text
BEFORE_RESPAWN|pane=%203|dead=1|pane_pid=627514|owner=424242|chassis=codex
AFTER_RESPAWN|pane=%203|dead=0|pane_pid=628352|owner=424242|chassis=codex
```

Measured separate-socket server restart:

```text
BEFORE_KILL|pane=%0|owner=424242|chassis=codex
AFTER_RESTART|pane=%0|owner=|chassis=
```

## 4. Adversarial design review

**Verdict: PIVOT before treating the ownership record as authoritative.** Recorded
ownership is the right replacement for `comm`, and it directly fixes the
measured regression. A bare PID plus the stated decision/apply protocol is not
yet a durable ownership identity.

1. **[BLOCKER] A resolving `TMUX_PANE` is not proof that the claimant descends
   from that pane.**

   Scenario: a detached hook inherits stale `TMUX_PANE=%185`; `%185` still
   exists, but the hook actually descends from pane `%202`. Rule 2 passes
   because `%185` resolves. The proposed ancestry walk never reaches `%185`'s
   `pane_pid`, yet no decision rule says to refuse; a fallback/empty result can
   claim and mutate the wrong live pane. Require the walk to terminate exactly
   at the targeted `pane_pid`; otherwise
   `REFUSE(claimant-not-pane-descendant)`. This check belongs immediately after
   rule 2.

2. **[BLOCKER] PID liveness is not process identity.**

   Scenario: pane `%203` retains `@harness_chassis=codex` and
   `@harness_owner_pid=424242` across `respawn-pane`. Codex exits; later the
   kernel assigns PID 424242 to an unrelated long-lived process. A legitimate
   Claude start hits rule 5 (`live foreign owner not in ancestry`) and refuses,
   potentially for days. Store and compare a birth token, at minimum Linux
   `/proc/$pid/stat` starttime; adding boot ID makes persisted/restored state
   explicit. A mismatched birth token must be treated as dead/stale.

3. **[HIGH] “One transaction” is not provided by several `tmux set-option`
   calls.**

   Scenario: Claude and Codex hooks both resolve a free pane before either
   applies. Both decide `CLAIM(first)`, then their step-1 writes interleave. The
   pane can end with `@harness_chassis` from one claimant and
   `@harness_owner_pid` from the other, followed by independently interleaved
   routing/display writes. Fixed write order and partial-failure reporting do
   not prevent this, and reporting cannot undo a half-applied identity. Serialize
   resolve+apply under a pane-scoped lock and re-resolve after acquiring it, or
   use a single versioned record with compare-and-swap semantics.

4. **[HIGH] The Codex adapter has no evidenced source for `mode`.**

   Scenario: measured `codex exec 'say ok'` runs SessionStart hooks, but the
   current Codex hook input parses only `.session_id`; it has no CLI argv. The
   design requires that adapter to declare `one-shot`, without defining how it
   learns that this was `exec` rather than interactive. If it defaults to
   `session-start`, standalone `codex exec` claims; if it defaults to one-shot,
   interactive Codex never claims. The launcher must parse argv and transport a
   trustworthy mode to the hook (or one-shot entrypoints must suppress the
   naming hook). Rule 3 is correctly early **only after** this contract exists.

5. **[HIGH] A live same-chassis owner can have its token overwritten.**

   Scenario: Codex A owns a pane and is suspended. Codex B starts as a sibling,
   so A is live but not in B's ancestry. Rules 4 and 5 apply only to a *foreign*
   chassis; rule 8 then lets B claim the locked identity and replace
   `@harness_owner_pid`. B exits, its PID is now dead, and Claude can claim while
   Codex A is still live and resumable. Add a live-owner/not-in-ancestry refusal
   independent of chassis. Same-chassis ancestry may preserve/repair without
   changing the owner token.

6. **[MEDIUM] Rule 5 deliberately breaks a plausible suspended-owner handoff.**

   Scenario: the user presses Ctrl-Z on Codex A and intentionally launches
   Claude from the returned shell prompt without killing A. A is live and a
   sibling, not Claude's ancestor, so rule 5 refuses. Refusal is the safe default
   because A can resume, but the workflow is legitimate. Provide an explicit
   release/handoff operation (or document that the old TUI must exit); do not
   weaken rule 5 based only on foreground status.

7. **[MEDIUM] “Owner PID is the CLI itself” is false for a non-exec
   supervisor.**

   Scenario: measured `harness-cross-cli` ancestry is Codex PID 552616 → guard
   bash PID 552577 → pane shell PID 548022. The rule selects PID 552577. The
   current guard waits, so this is a usable lifetime token, but a future
   supervisor that spawns-and-exits makes the owner immediately stale while the
   CLI remains. Define the field as the pane launch owner/supervisor and require
   that it remain alive for the CLI lifetime, or pass the actual CLI PID from
   the launcher.

8. **[INFO] Native tmux state does not survive `kill-server`; respawn state
   does.**

   The separate-socket measurement showed ownership options empty after a new
   tmux server was created, even though pane ID `%0` was reused. Thus the
   design's reboot concern is not native tmux persistence. It becomes real only
   if a resurrection plugin restores pane options. `respawn-pane`, however,
   demonstrably retained both ownership options and therefore needs the
   PID-birth-token fix now.

### Decision-order recommendation

Keep kill switch and no-tmux checks first, but strengthen the order to:

1. disabled / target does not resolve;
2. claimant ancestry must reach the target `pane_pid`;
3. one-shot preserves;
4. validate stored owner by PID **and birth token**;
5. any live owner not in ancestry refuses, regardless of chassis;
6. live foreign owner in ancestry refuses;
7. only then evaluate Formation/locked/sentinel/first-claim rules.

Resolve and apply must execute under the same pane-scoped lock. With those
changes, the ownership model is materially stronger than every name-based
classifier tested here.

## Follow-up for implementation owner

The implementation owner reports that the in-progress core now bounds the
ancestor walk at `#{pane_pid}`. That bound does not break any observed valid
launch:

- direct Codex, `exec`/re-exec, `setsid`, and `harness-cross-cli` all retain a
  parent chain reaching the pane shell;
- a detached process whose ancestry does not reach the pane shell is not
  entitled to claim that pane, even when it inherited a resolving `TMUX_PANE`.

The concrete daemon failure shape is:

```text
pane shell → Codex → tool/launcher parent → forked child
                                      parent exits
forked child is reparented to PID 1 → hook runs with inherited TMUX_PANE
```

The measured minimal form was a forking Python process whose parent exited:

```text
610015       1  610012 i95-daemon python3 -c ...
```

The resulting hook ancestry is `hook → daemon child → PID 1`, never
`#{pane_pid}`. The safe decision is to refuse, not fall back: fallback would
restore the stale-`TMUX_PANE` wrong-pane mutation. A daemon-based interactive
CLI must instead keep a pane-descendant supervisor alive, claim before
detaching with a verifiable lifetime token, or use a non-pane identity surface.

After explicit approval, active `respawn-pane -k` confirmed the inactive-pane
result:

```text
BEFORE_K|pane=%208|dead=0|pane_pid=843264|owner=424242|chassis=codex|cmd=sleep
AFTER_K|pane=%208|dead=0|pane_pid=843378|owner=424242|chassis=codex|cmd=sleep
```

The active process and `pane_pid` changed while both ownership options
survived.
