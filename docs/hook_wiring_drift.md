# Claude hook wiring drift observer

Issue #186 selects a nightly local cron observer (option A).

The checked state is host-local `~/.claude/settings.json`, so cloud CI cannot
observe it. A `SessionStart` warning would share the same wiring failure domain
as the hooks it checks and would add work to every session. The nightly process
is independent, cheap, and catches both drift directions:

- `ORPHAN`: live-wired but absent from every plugin.
- `DORMANT`: plugin-wired but absent from live wiring.

Either condition exits 1. Input/checker failure exits 2. The cron wrapper logs
the full safe hook-name report locally and posts only a fixed summary to the
`claude-harness` Discord channel; it does not send hook commands or settings
content.

Install or refresh the idempotent crontab entry:

```bash
bash scripts/install_hook_wiring_drift_cron.sh
```

Run the live check directly:

```bash
python3 scripts/check_hook_wiring_drift.py
```
