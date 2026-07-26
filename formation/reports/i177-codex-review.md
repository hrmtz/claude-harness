# PR #179 adversarial review — Codex hook environment

Reviewer: `i177-codex-review`  
Target: PR #179, head `5dee5354d3776b0c50179d60c390ebe08536f566`  
Runtime checked: `codex-cli 0.145.0` (`rust-v0.145.0`)

## Decision

**BLOCKER.** The native Codex plugin path is sound, but the commands emitted by
`install-codex-hooks.sh` do not reliably deliver `HARNESS_CHASSIS=codex` to the
hook. The stamp is a shell assignment attached only to the first simple command
of the compound command copied from `hooks.json`.

## Findings

### BLOCKER — the Codex installer stamp is scoped to `test`, not to the dispatched hook

Locations:

- `install-codex-hooks.sh:247`
- `plugins/harness-core/hooks/hooks.json:155-180`
- `plugins/harness-core/tests/test_hook_dispatch_chassis.sh:145-153`

The installer constructs:

```sh
HARNESS_CHASSIS=codex test -x "$HOME/.local/bin/harness-hook" &&
  grep ... ||
  exec bash "/absolute/plugin/root/hooks/tmux_self_name.sh";
exec "$HOME/.local/bin/harness-hook" harness-core hooks/tmux_self_name.sh
```

POSIX shell assignment-prefix semantics apply `HARNESS_CHASSIS=codex` only to
the environment of the immediately following `test`. They do not persist it in
the shell for `grep`, either `exec`, or `harness-hook`.

Measured on this host:

```text
$ bash -lc 'unset HARNESS_CHASSIS; HARNESS_CHASSIS=codex test -x /bin/true && true || :; printf "%s\n" "${HARNESS_CHASSIS:-UNSET}"'
UNSET
```

Concrete failure scenario:

1. Input: run `install-codex-hooks.sh`, then start Codex through the generated
   config-layer `SessionStart` hook with a valid `~/.local/bin/harness-hook`.
2. Codex starts the whole hook string through its shell.
3. Only `test` receives `HARNESS_CHASSIS=codex`.
4. The final dispatcher receives neither `HARNESS_CHASSIS` nor `PLUGIN_ROOT`
   (`PLUGIN_ROOT` is injected for plugin-bundled hooks, not config-layer hooks).
5. `harness-hook` correctly avoids fabricating `PLUGIN_ROOT`, and
   `tmux_self_name.sh` therefore selects the Claude adapter.
6. Wrong result: a fresh Codex pane can be claimed/named
   `claude-<codename>` instead of `codex-<codename>`.

The same loss makes the generated
`codex_hippocampus_session_start.sh` command see neither signal and exit 0 at
lines 12-15. Wrong result: the companion SessionStart action silently does not
run. This is a concrete possible contributor to #180's frozen companion log
when the config-layer installer path is the path under observation; it does not
show that Codex failed to dispatch the event.

The added test only greps each installer source for the literal stamp. It never
executes a generated compound command, so all assertions can pass while the
stamp is dynamically absent at the adapter.

Suggested repair: make the value part of the shell environment before the
compound command, for example:

```sh
export HARNESS_CHASSIS=codex; <original command>
```

Then add a test which executes a representative generated Codex command and
asserts the value observed inside the final dispatcher/adapter, not the text in
the installer.

### HIGH — the direct fallback cannot locate the Codex adapter

Locations:

- `plugins/harness-core/hooks/hooks.json:165`
- `plugins/harness-core/hooks/tmux_self_name.sh:23-31`

This is independently observable after fixing the BLOCKER above.

Concrete failure scenario:

1. Input: a Codex config-layer SessionStart command has an exported
   `HARNESS_CHASSIS=codex`, but `~/.local/bin/harness-hook` is absent, stale, or
   fails its dispatcher-ID check.
2. The generated command takes its intended direct fallback:
   `exec bash "/absolute/plugin/root/hooks/tmux_self_name.sh"`.
3. Config-layer hooks receive no plugin env injection, and the installer
   substitutes the absolute root only in the outer command string; it does not
   set `PLUGIN_ROOT` or `CLAUDE_PLUGIN_ROOT`.
4. `tmux_self_name.sh` builds `CODEX_ADAPTER=/hooks/codex_tmux_self_name.sh`.
5. The executable check fails; the fallback Claude core defaults to
   `$HOME/.claude/hooks/tmux_self_name_core.sh`.
6. Wrong result: the explicit Codex chassis produces no Codex identity output
   (or can enter an unrelated Claude install if that path exists).

This defeats the fallback that the shared command deliberately provides.
Resolve the adapter relative to `tmux_self_name.sh` itself when neither plugin
root variable exists, or have the installer export the absolute plugin root
before entering the direct fallback.

### INFO — Codex 0.145.0 does use a shell for hook command strings

The feared direct-`execve` interpretation does not occur in 0.145.0.

Exact `rust-v0.145.0` source:

- [`command_runner.rs`](https://github.com/openai/codex/blob/rust-v0.145.0/codex-rs/hooks/src/engine/command_runner.rs)
  constructs the configured shell and appends the entire hook string as the
  `-lc` argument. On Unix, the default is `$SHELL -lc`, falling back to
  `/bin/sh -lc`.
- The current Codex hook documentation also uses shell expansion and command
  substitution in hook command examples.

Therefore `HARNESS_CHASSIS=codex bash ...` is parsed as an environment
assignment plus command; Codex does not look for an executable literally named
`HARNESS_CHASSIS=codex`. The BLOCKER is the narrower shell-scope bug above.

### INFO — native Codex plugin hooks receive both root variable families

The comment that native Codex sets both variables is correct for 0.145.0.

Exact `rust-v0.145.0` source:

- [`discovery.rs`](https://github.com/openai/codex/blob/rust-v0.145.0/codex-rs/hooks/src/engine/discovery.rs)
  adds all four values to every plugin-bundled handler:
  `PLUGIN_ROOT`, `CLAUDE_PLUGIN_ROOT`, `PLUGIN_DATA`, and
  `CLAUDE_PLUGIN_DATA`.
- The current Codex manual states the same plugin-hook contract.

Thus the native marketplace path reaches the Codex adapter even without
`HARNESS_CHASSIS`: `PLUGIN_ROOT` is present and the retained fallback matches.

No evidence was found for a 0.145.0 native plugin execution in which only
`CLAUDE_PLUGIN_ROOT` is set. A hypothetical legacy host that sets only the
Claude-compatible name would not reach the Codex adapter, because the fallback
intentionally requires `PLUGIN_ROOT`; however, that was also true before PR
#179 and is not a regression demonstrated by this change.

## #180 observation

The local companion log remained at:

```text
/home/hrmtz/.local/log/codex_session_start.log
mtime=2026-07-25 19:41:37.051190728 +0900
```

while Codex 0.145.0 reports the local `harness-core@claude-harness` plugin as
installed and enabled. That mtime alone cannot distinguish “SessionStart was
not dispatched” from “the dispatched config-layer command silently no-op'd.”
The BLOCKER supplies an exact no-op path for the latter. Native plugin hooks
remain a separate path because Codex injects `PLUGIN_ROOT` there.

## Verification summary

- PR metadata and patch inspected through the connected GitHub repository.
- Exact OpenAI Codex `rust-v0.145.0` hook runner and plugin env injection source
  inspected.
- Local executable confirmed as `codex-cli 0.145.0`.
- Shell assignment scope reproduced without modifying repo or user config.
- No repository files changed except this report.
