#!/usr/bin/env python3
"""Deploy the claude-harness plugin hooks as the canonical live artifact (gh #30).

plugin (SoT) -> live ~/.claude/:
  1. backup live settings.json + ~/.claude/hooks/ to ~/sanada_backup_persistent/
  2. syntax-gate every plugin hook (bash -n / py_compile)
  3. copy all plugin hook files -> ~/.claude/hooks/
  4. rebuild settings.json hooks = plugin hooks.json union (paths rewritten to
     $HOME/.claude/hooks/), PRESERVING live-only events (e.g. SessionEnd) and all
     non-hooks keys.

Idempotent. --dry-run prints the plan without writing. Pairs with
check_hook_wiring_drift.py (run that after to confirm in-sync).
"""
import json, os, glob, subprocess, sys, shutil, datetime

HOME = os.path.expanduser("~")
LIVE_SETTINGS = f"{HOME}/.claude/settings.json"
LIVE_HOOKS = f"{HOME}/.claude/hooks"
PLUG = f"{os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}/plugins"
PLUGINS = sorted(
    os.path.basename(os.path.dirname(os.path.dirname(path)))
    for path in glob.glob(f"{PLUG}/harness-*/hooks/hooks.json")
)
USAGE = """usage: sync_hooks_to_live.py [--dry-run] [--ts YYYYmmdd_HHMMSS]

Deploy the plugin hooks as the canonical live artifact.

  --dry-run   print the plan, write nothing
  --ts TS     backup directory timestamp (default: now)
  -h, --help  show this message and exit

This writes to ~/.claude/hooks/ and ~/.claude/settings.json, which every
session loads. Run check_hook_wiring_drift.py afterwards to confirm.
"""


def _parse_argv(argv):
    """Reject unknown flags instead of running the deploy anyway (gh #243).

    argv was previously read with membership tests, so `--help` matched
    nothing, fell through, and performed the live deploy. Two people hit that
    in one day; one of them wired an unmerged hook into every session.
    """
    known = {"--dry-run", "--ts"}
    dry, ts = False, None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            print(USAGE, end="")
            sys.exit(0)
        elif arg == "--dry-run":
            dry = True
        elif arg == "--ts":
            # A flag-shaped value means the timestamp was omitted. Swallowing
            # it would drop the *next* flag: `--ts --dry-run` consumed
            # --dry-run and deployed for real — the same defect this fix is
            # for, reintroduced one argument over.
            if i + 1 >= len(argv):
                print("error: --ts requires a value\n", file=sys.stderr)
                print(USAGE, end="", file=sys.stderr)
                sys.exit(2)
            value = argv[i + 1]
            if value.startswith("-") or not value.strip():
                print(f"error: --ts requires a timestamp, got {value!r}\n", file=sys.stderr)
                print(USAGE, end="", file=sys.stderr)
                sys.exit(2)
            ts = value
            i += 1
        else:
            print(f"error: unknown argument {arg!r}\n", file=sys.stderr)
            print(USAGE, end="", file=sys.stderr)
            sys.exit(2)
        i += 1
    return dry, ts or datetime.datetime.now().strftime("%Y%m%d_%H%M%S"), known


DRY, TS, _KNOWN = _parse_argv(sys.argv[1:])

def gate():
    fail = []
    for p in PLUGINS:
        for f in glob.glob(f"{PLUG}/{p}/hooks/*.sh"):
            if subprocess.run(["bash", "-n", f], capture_output=True).returncode: fail.append(f)
        for f in glob.glob(f"{PLUG}/{p}/hooks/*.py"):
            if subprocess.run(["python3", "-m", "py_compile", f], capture_output=True).returncode: fail.append(f)
    if fail:
        print("SYNTAX GATE FAILED:", fail); sys.exit(1)
    print("syntax gate: OK")

def build_settings():
    live = json.load(open(LIVE_SETTINGS))
    merged = {}
    for p in PLUGINS:
        hj = f"{PLUG}/{p}/hooks/hooks.json"
        if not os.path.exists(hj): continue
        for event, blocks in json.load(open(hj)).get("hooks", {}).items():
            for blk in blocks:
                nb = {k: v for k, v in blk.items() if k != "hooks"}
                nb["hooks"] = [dict(h, command=h["command"].replace(
                    "${CLAUDE_PLUGIN_ROOT}/hooks/", f"{HOME}/.claude/hooks/").replace(
                    "${CLAUDE_PLUGIN_ROOT}/", f"{PLUG}/{p}/")) for h in blk.get("hooks", [])]
                merged.setdefault(event, []).append(nb)
    final = dict(live.get("hooks", {}))     # keep live-only events (SessionEnd ...)
    final.update(merged)                      # plugin union authoritative for shared events
    new = dict(live); new["hooks"] = final
    return new, sorted(set(live.get("hooks", {})) - set(merged))

def main():
    gate()
    bk = f"{HOME}/sanada_backup_persistent/hooks_sync_{TS}"
    if not DRY:
        os.makedirs(bk, exist_ok=True)
        shutil.copy2(LIVE_SETTINGS, f"{bk}/settings.json")
        shutil.copytree(LIVE_HOOKS, f"{bk}/hooks", dirs_exist_ok=True)
    print(f"backup -> {bk}{' (dry-run, skipped)' if DRY else ''}")

    files = [f for p in PLUGINS for ext in ("sh", "py") for f in glob.glob(f"{PLUG}/{p}/hooks/*.{ext}")]
    for f in files:
        dst = f"{LIVE_HOOKS}/{os.path.basename(f)}"
        # skip if src and dst are the same file (hardlink/symlink/identical path)
        if os.path.exists(dst) and os.path.samefile(f, dst): continue
        if not DRY: shutil.copy2(f, dst)
    print(f"{'would copy' if DRY else 'copied'} {len(files)} hook files -> {LIVE_HOOKS}")

    new, preserved = build_settings()
    if not DRY:
        json.dump(new, open(LIVE_SETTINGS, "w"), indent=2)
    print(f"{'would write' if DRY else 'wrote'} settings.json  (preserved live-only events: {preserved})")
    print("done." + ("  run check_hook_wiring_drift.py to confirm." if not DRY else ""))

if __name__ == "__main__":
    main()
