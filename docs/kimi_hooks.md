# Kimi Hooks — BASH_ENV Interception Design and Setup

## Background

Kimi Code CLI has no native hook API (no PreToolUse/PostToolUse, unlike
Claude Code and Codex `plugin_hooks`). This document records how harness-kimi
achieves PreToolUse-equivalent **preventive** blocking anyway, and the setup.

Resolved in [#52](https://github.com/hrmtz/claude-harness/issues/52)
(2026-07-02, pair-worked with a Kimi formation peer). Companion doc:
[codex_hooks.md](./codex_hooks.md).

---

## The bypass problem

The first guard attempt placed a `bash` wrapper at the front of `PATH`
(`guarded-bash.sh`). It worked when invoked by name, but Kimi's Bash tool
executes `/bin/bash` by **absolute path**, so PATH resolution never happens
and the wrapper was bypassed.

## The mechanism: BASH_ENV + BASH_EXECUTION_STRING

Two bash facts make preventive guarding possible with zero C code and no
mount tricks:

1. When bash starts **non-interactively** — including `/bin/bash -c 'cmd'`
   invoked by absolute path — it sources the file named by the `BASH_ENV`
   environment variable *before* running the command.
2. Inside that sourced file, `$BASH_EXECUTION_STRING` holds the **full `-c`
   command string** — pipes, loops, redirections, everything the
   shell-construct-level guards need to see.

So `kimi-wrapper.sh` exports `BASH_ENV=guard-env.sh` into the Kimi process
tree. Every Bash tool call then runs the harness guards *before* the command
executes; a deny `exit 2`s the shell before any side effect.

Empirically verified inside a real Kimi session: each Bash tool call is a
fresh one-shot `bash -c` (no env inheritance between calls, new PID each
call), and `BASH_ENV` survives into the tool environment. If Kimi ever
switches to a persistent shell, the fallback design is `shopt -s extdebug` +
a DEBUG trap in the same file — still pure bash.

## Layers

| Layer | File | Role |
|---|---|---|
| 0th (model) | `AGENTS.md` | behavioral rules; Kimi often refuses dangerous patterns before even calling Bash |
| 1st (preventive) | `guard-env.sh` via `BASH_ENV` | runs guards before every `bash -c`, catches absolute-path invocations |
| fallback | `guarded-bash-dir/bash` PATH shim | catches PATH-resolved `bash` (kept for defense in depth) |
| shared core | `guard-check.sh` | hook runner used by both layers: insurance → gates → hints |
| 2nd (detective) | wire.jsonl watcher — [#53](https://github.com/hrmtz/claude-harness/issues/53), planned | post-hoc detection if the 1st wall is dropped |
| long term | native hooks request to MoonshotAI — [#54](https://github.com/hrmtz/claude-harness/issues/54) | feature parity without any of this |

Which guards run is selected by `plugins/cross_cli_hooks.json` (`kimi`
section) — the same cross-CLI overlay that drives Codex, with events/timeouts
SSOT in each plugin's `hooks/hooks.json`. As of #55 the kimi set is:
sanada_autobackup (insurance), bash_command_guard, branch_policy_guard,
pg_rotation_propagation_guard, pipeline_preflight_gate, phase_review_gate
(gates), long_task_advisor (hint).

## guard-env.sh constraints

The file is *sourced into the guarded shell*, so it must be side-effect-free:

- no `set -e/-u/-o`, no `shopt` changes, no stray variables or functions left
  behind — the command's shell must be indistinguishable from unguarded
- recursion sentinel `HARNESS_KIMI_GUARD_ACTIVE=1` so nested bash inside an
  allowed command is not re-guarded (the guard already saw the full string)
- heavy lifting happens in a `guard-check.sh` subprocess, keeping the parent
  shell environment clean
- **fail-open** with a loud stderr warning if `guard-check.sh` is missing:
  this is a rail against the agent's own mistakes, not a sandbox; bricking
  every Bash call is worse than running unguarded

## Setup

```bash
bash plugins/harness-kimi/install-kimi-bash-guard.sh   # installs to ~/.kimi-code/bin/guarded-bash-dir/
bash plugins/harness-kimi/install-kimi-wrapper.sh      # kimi wrapper (PATH before real kimi)
HARNESS_KIMI_BASH_GUARD=1 kimi                          # guard active
```

Check sync state (overlay vs SSOT vs installed copies):

```bash
bash scripts/check_cross_cli_hooks.sh --live
```

After changing the kimi section of `cross_cli_hooks.json`, re-run
`install-kimi-bash-guard.sh`. Guard *script* content changes need no
re-install for hooks (they run from the repo), but `guard-check.sh` /
`guard-env.sh` themselves are copied — re-run the installer.

## Threat model note

`unset BASH_ENV` removes the rail — same residual risk class as Claude Code's
own hooks (the model could bypass those too). The guard defends against the
agent's *mistakes*, not an adversarial agent. Detection of a dropped wall is
the 2nd-wall watcher's job (#53).

### Known bypasses of the BASH_ENV primary layer (verified)

bash only sources `BASH_ENV` for **non-interactive, non-POSIX** shell startup.
These invocations therefore skip it and are NOT caught by the BASH_ENV layer:

- `bash --posix -c '…'` — POSIX mode consults `$ENV` for *interactive* shells
  only; a non-interactive `--posix` shell sources nothing (setting `ENV` does
  not help — verified).
- `bash -i -c '…'` — interactive startup sources `.bashrc`, not `BASH_ENV`.
- `sh -c '…'` — not bash; never sources `BASH_ENV`.

The **PATH-shim layer does catch `--posix`/`-i` for PATH-resolved `bash`**
because it parses the command from argv itself and always guards inline
(it does not defer based on `BASH_ENV` being set — a deferral there was a
v0.10.1 regression closed in v0.10.2 after security review). But an
**absolute-path** `/bin/bash --posix -c '…'` (or `-i`, or `sh`) bypasses both
layers. There is no env-var mitigation for this; closing it requires either a
native Kimi hook (#54) or the post-hoc wire.jsonl watcher as a detective
control (#53). Documented rather than silently assumed-covered.

## Verification record (2026-07-02)

- Local simulation 8/8 PASS: absolute-path interception, decrypt-pipe deny
  rc=2, bulk-parallel-loop deny rc=2, nested-bash single-fire, clean stdout,
  disabled-guard pass-through, PATH-shim compat
- E2E in a fresh Kimi session 5/5 PASS (run by the Kimi formation peer):
  BASH_ENV survives env handling, benign pass, #52 repro blocked by
  pipeline_preflight_gate, decrypt-pipe blocked by bash_command_guard,
  normal git work unaffected
