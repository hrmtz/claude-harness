# #95 — one ownership core for tmux self-naming

Status: implemented (2026-07-26), after a PIVOT verdict from an independent
codex probe and two live measurement passes
Scope: `claude` / `codex` / `kimi` / `grok` chassis identity on a tmux pane.

## Why this is not another instance fix

Three landed fixes (`8e5f879`, `b3889af`, `11b52c7`) each closed the instance in
front of them and each was defeated by the next surface:

| date | surface | what defeated the guard |
|---|---|---|
| 2026-07-19 | `kimi --help` renamed the parent window | no non-interactive gate |
| 2026-07-24 | Kimi child stamped `kimi-` on a Codex parent | rename derived chassis from the child, target from the parent |
| 2026-07-26 | `claude -p` inside a Codex pane produced `claude-slate-wren-2067` | ancestor classifier matched `ps comm` against a fixed name list; the live surface ran as `claude-harness-codex-real` |

The common root is that **ownership is inferred, never recorded**. Each adapter
re-derives "is this pane mine?" from a different proxy — a process name, a window
label prefix, a sentinel file — and every proxy has a blind spot that only shows
up once a new wrapper exists. Adding `claude-harness-codex-real` to the name list
fixes today's instance and leaves tomorrow's wrapper open.

The fix is to make ownership a **recorded fact on the pane** and to have exactly
one component allowed to read and write it.

## The ownership record

On a successful claim the owner writes four pane options as one transaction:

| option | meaning |
|---|---|
| `@harness_chassis` | which family owns the pane (`claude`/`codex`/`kimi`/`grok`) |
| `@harness_owner_key` | `<pid>:<start-token>:<boot-id>` of the pane's launch owner |
| `@formation_identity_locked` | routing id — unchanged semantics, still authoritative |
| `@formation_id` | compatibility alias — unchanged |

`@harness_owner_key` is the load-bearing addition. The pid is resolved by
walking the claimant's ancestry up to `#{pane_pid}` and taking the ancestor
directly below it: the process the pane's shell launched, whatever it is named
and however long that name is. Where a supervisor such as `harness-cross-cli`
sits between shell and CLI, that supervisor is the recorded owner — it outlives
the CLI, so it is a valid lifetime token, and a launcher that knows the real CLI
pid can override it.

The pid alone is not an identity. `respawn-pane` was measured to carry ownership
options across a respawn with the recorded process long dead, so a pane can hold
a stale pid for days and a recycled pid would read as a live owner. The start
token pins the process within a boot; the boot id pins the boot, in case a
session-restore layer ever replays pane options across one.

Nested-launch detection then becomes name-free:

```
owner alive AND owner ∉ my ancestors                       → REFUSE (any chassis)
owner alive AND owner ∈ my ancestors AND chassis differs   → REFUSE
```

`claude -p` under `claude-harness-codex-real` is refused because the codex CLI's
pid is literally in its ancestor chain — no string matching, no 15-character
`comm` truncation, no wrapper-name list to keep current.

A dead `@harness_owner_key` means the previous CLI exited, so sequential reuse of
the same pane still claims normally. That is the behaviour `8e5f879` added and it
is preserved.

## The authority

`plugins/harness-core/hooks/identity_owner.sh`, sourced by every adapter. Two entry
points:

```
harness_identity_resolve --pane <id> --chassis <c> --mode <m> [--routing-id <id>]
  → HARNESS_IDENTITY_DECISION  = CLAIM | PRESERVE | REFUSE
    HARNESS_IDENTITY_REASON    = machine-readable slug
    HARNESS_IDENTITY_ROUTING_ID
    HARNESS_IDENTITY_NAME      = <chassis>-<routing_id>
    HARNESS_IDENTITY_RESUMED   = 0 | 1

harness_identity_apply   # only legal after a CLAIM; performs the writes
harness_identity_claim   # what adapters call: lock, resolve, apply, unlock
harness_identity_release # deliberate handoff of a pane to another CLI
```

`PRESERVE` and `REFUSE` are distinct on purpose: `PRESERVE` means "the pane is
fine, touch nothing" (one-shot invocations), `REFUSE` means "this pane is not
yours" (nested foreign chassis, formation mismatch). Both exit without mutating;
the split exists so the structured log can tell a benign `--help` from a real
ownership conflict.

Adapters declare chassis + mode and do nothing else. No adapter calls
`tmux rename-window`, `tmux set-option`, or reads a sentinel directly.

## Decision order

Evaluated top to bottom, first match wins:

1. any kill switch set → `REFUSE(disabled)`
2. not in tmux, or pane does not resolve → `REFUSE(no-tmux)`
3. the pane's own process is not in our ancestry → `REFUSE(not-in-pane)`
4. `mode = one-shot` → `PRESERVE(one-shot)` — never claims, even on a free pane
5. live owner outside our ancestry → `REFUSE(owner-live-elsewhere)` — any chassis
6. live owner in our ancestry, different chassis → `REFUSE(foreign-owner-nested)`
7. no owner key, foreign chassis in ancestry by name → `REFUSE(legacy-foreign-ancestor)`
8. no owner key, sibling's label on a shared window → `REFUSE(legacy-shared-window)`
9. `FORMATION_SELF` disagrees with `@formation_identity_locked` → `REFUSE(formation-mismatch)`
10. `FORMATION_SELF` agrees → `CLAIM(formation)`
11. `@formation_identity_locked` present → `CLAIM(locked)` — repair display to match
12. sentinel holds `<chassis>-<id>`, still free → `CLAIM(sentinel)`, resumed=1
13. otherwise → `CLAIM(first)` with a generated, collision-checked codename

Rule 3 is why an inherited `TMUX_PANE` cannot rename a live stranger's window: a
resolving pane id proves the pane exists, not that we are in it. Rules 5–6 rule
out the 2026-07-26 regression. Rule 4 rules out the original `kimi --help`
report. Rule 5 covers *any* chassis on purpose — restricted to foreign chassis,
a second codex started while the first was suspended could take the pane,
overwrite the owner token, exit, and leave the pane claimable while the original
was still resumable.

Rules 7 and 8 are a transitional fallback for panes claimed before this core
existed and therefore carrying no `@harness_chassis`. They are the old
name-matching check, kept deliberately weak and deliberately second.

## Mode

| mode | who declares it | may claim |
|---|---|---|
| `session-start` | SessionStart hooks (claude, codex, grok) | yes |
| `interactive` | wrappers with a TTY on stdout (kimi) | yes |
| `one-shot` | `--help` / `--version` / `-p` / non-TTY / known one-shot subcommands | no |

A wrapper decides its own mode; the core does not sniff argv. That keeps argv
parsing where the argv grammar is known.

A SessionStart hook has no argv at all, so it cannot tell `codex exec` from an
interactive TUI by itself. Launchers that do know say so through
`HARNESS_IDENTITY_MODE`, which every adapter reads; `harness-cross-cli` already
disables naming outright for its children. Defaulting a hook to `session-start`
is the safe half of that gap: a bare one-shot in a free pane may name it, but a
one-shot inside another CLI's pane is refused by ownership — which is the case
that actually caused drift.

## Transaction

A fixed write order is not a transaction. Two hooks that resolve a free pane
before either applies both decide `CLAIM(first)`, and their writes interleave —
the pane ends with one claimant's chassis beside the other's owner token, which
no amount of partial-failure reporting can undo. So `harness_identity_claim`
takes a pane-scoped lock and resolves *inside* it: a claimant that loses the
race sees the winner's record rather than the free pane it first saw. A pane
whose lock cannot be taken is refused rather than written to.

Under that lock, `harness_identity_apply` writes:

1. `@harness_chassis`, `@harness_owner_key`
2. `@formation_identity_locked`, `@formation_id`
3. `window_name` — **only** when `#{window_panes} = 1`
4. `pane_title`

Shared windows keep one name for several panes, so step 3 is skipped there and
the pane title carries the identity. A pane whose shared window is *labelled by
another chassis* claims nothing at all (#101): half an identity — a routing id
with no display surface, sitting beside the lead's in formation's view — is
worse than none.

Formation claims skip the sentinel. That file is keyed by pane, a worker's
identity is scoped to its spawn, and writing one would let a recycled pane id
hand a retired worker's display name to whatever starts there next.

## Kill switch

One canonical name, all legacy names honoured for every chassis:

- canonical: `HARNESS_TMUX_SELF_NAME_DISABLE=1`
- legacy, still read: `CLAUDE_TMUX_NAME_DISABLE`, `CODEX_TMUX_NAME_DISABLE`,
  `KIMI_TMUX_NAME_DISABLE`, `GROK_TMUX_NAME_DISABLE`, `HIPPOCAMPUS_TMUX_NAME_DISABLE`

The core reads all six regardless of chassis, so `GROK_TMUX_NAME_DISABLE` stops
becoming a variable that is exported by `harness-cross-cli` and read by nobody.

## Grok

Measured on 2026-07-26 in isolated tmux sessions, twice and independently:

- `grok --help` renames nothing. The original issue's `--help` failure mode does
  not exist on this surface.
- interactive `grok` leaves `window_name` untouched and sets `pane_title` to
  `grok` via an OSC title sequence — a different mechanism from every other
  chassis, which use `tmux rename-window` explicitly.
- a hook-set `pane_title` is clobbered by grok's next prompt; a hook-set
  `window_name` survives prompts and forced redraws. **`window_name` is grok's
  only durable identity surface.**
- grok's SessionStart fires at the *first prompt*, not at TUI boot. `TMUX_PANE`
  is set by then, so the claim works — but a grok pane stays unnamed until the
  user says something, and the hook loses the title race by construction.

So grok needed an adapter for the opposite reason to the other three: it never
stole an identity, it had none. The adapter is a SessionStart hook registered
through `plugins/cross_cli_hooks.json` (grok external set) that claims through
the same core.

One honest limitation, measured rather than assumed: `GROK_TMUX_NAME_DISABLE=1`
now stops *the harness* from naming a grok pane, but grok still writes its own
`grok` pane title — it exposes no environment variable that suppresses the OSC
sequence, and `[ui.notifications.title]` in `config.toml` does not gate the boot
title. The kill switch governs harness behaviour, which is all a harness can
promise.

## Acceptance

Mapped from the issue's checklist:

- [x] `claude -p` / `claude --help` inside a Codex pane leaves window name, pane
      title and identity options byte-identical
- [x] the mirror case for codex / kimi / grok one-shots inside a Claude pane
- [x] standalone interactive grok acquires `grok-<codename>` plus routing metadata
      (from its first prompt onward — grok's SessionStart fires there, not at boot)
- [x] formation workers of all four families hold `<chassis>-<locked-id>` across
      compact/resume
- [x] split windows preserve a foreign owner; new windows name only the target pane
- [x] a negative-control fixture whose wrapper name is longer than 15 characters
      and does not appear in any name list is still refused
- [x] the common kill switch stops the rename in all four families
      (24 combinations: six variable names × four chassis)
- [x] the test suite contains a known-positive that must CLAIM and a
      known-positive that must REFUSE, so a green run proves the detector works

Suites: `test_identity_owner.sh` 57/0, `test_formation_identity.sh` 49/0,
`test_cross_cli_guard.sh` 14/0, `test_formation_hardening.sh` 72/0.

## What this does not fix

- Grok panes are unnamed until the user's first prompt. The hook has no earlier
  event to run on.
- `GROK_TMUX_NAME_DISABLE=1` stops the harness naming a grok pane; grok still
  writes its own `grok` pane title, and exposes no way to suppress it.
- A daemonised CLI (double-fork, reparented to PID 1) cannot derive a pane
  owner, so it is refused rather than guessed at. No measured launch route on
  this machine has that shape.
- The idle heuristic behind `formation-mail-nudge` is a heuristic. A pane that
  has not repainted is probably at an empty prompt; `--exclusive-input` is the
  only real assurance available.
