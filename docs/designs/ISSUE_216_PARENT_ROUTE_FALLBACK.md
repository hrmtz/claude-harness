# Issue #216 — Formation parent route fallback and repair

## Corrected reproduction

Exact base `104dbdce8902f028eaf5f5b30766406f940fc7be` already refuses spawn
when both process-ancestry pane resolution and `FORMATION_SELF` are absent.
It does not reproduce a new row with `parent_id: null`.

The deterministic remaining bug is narrower: a launch wrapper can break the
pane-root PID ancestry chain even though the caller still owns the pane's
controlling TTY and the pane has an immutable `@formation_identity_locked`.
On the base commit, the isolated fixture in
`test_parent_route_repair.sh` exited 2 with:

```text
formation: refusing spawn without an ancestry-proven parent pane or explicit FORMATION_SELF; replies would be unaddressable.
```

The positive fixture has a mismatched `pane_pid`, matching caller TTY and
`pane_tty`, and locked identity `lead-locked`. No real tmux or Formation state
is used.

## Legacy null-row provenance

The observed legacy null route is not attributed to a mechanism that cannot be
reproduced:

- the default `formation` path targets the primary checkout, so a stale PATH
  executable is not established;
- the exact-base `self_id` has nonempty `shell-$$` / `pane-*` fallbacks, so an
  ancestry-proven pane with empty identity does not explain a null value;
- relative execution from another checkout or an already-sourced older
  function remains possible but unproven.

The root cause is therefore recorded as unresolved/non-reproducible. The fix
adds forward invariants instead: successful spawn requires both a verified
pane and a valid locked/legacy semantic identity, or an explicit valid
`FORMATION_SELF`.

A separate real isolated-spawn check of the installed code unset
`FORMATION_SELF`, `FORMATION_PARENT`, and `FORMATION_PARENT_PANE`, used an
isolated `FORMATION_HOME`, and entered through locked pane `%332`. The spawned
disposable pane `%371` recorded `parent_id=hc-orch` and `parent_pane=%332`, then
was reaped; the live registry was untouched. Evidence is retained at:

```text
/home/hrmtz/sanada_backup_persistent/issue216_actual_spawn_20260727_123521
```

Pane `%209` was not injected into or touched. The check used the structurally
equivalent locked-pane path from `%332`.

## Resolution and repair contract

Caller pane proof is ordered:

1. nearest pane root PID in the current process ancestry;
2. otherwise, exactly one caller controlling-TTY ↔ `pane_tty` match.

Inherited `TMUX_PANE`, pane title, and mutable window name never prove a route.
No TTY, an unrelated TTY, or duplicate TTY matches fail closed.

`formation status` is read-only and renders an absent or invalid route as
`parent=UNROUTABLE`. `formation repair-parent <worker_id>`:

- accepts exactly one concrete worker id;
- derives the current parent pane through the same verified resolver;
- requires its valid immutable locked identity;
- requires the target child pane to be live and still carry the worker's
  locked/legacy identity;
- refuses missing or duplicate worker rows, closed/recycled child panes, and
  a different non-null registry or live-pane route;
- writes the full registry, target row, and unset-vs-set pane-option preimages
  to a persistent Sanada journal before mutation;
- sets `@formation_parent_id` and `@formation_parent_pane`, verifies them, then
  rewrites the sole row atomically under `registry.lock`;
- rolls pane options back if either set or the registry replace fails, with a
  distinct partial-repair error if rollback itself fails;
- restores the exact registry/live-pane equality required by
  `formation-mail-nudge` rather than merely making status look healthy;
- appends no row, and an identical second invocation creates no backup and is
  a byte-for-byte no-op.
