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
PLUGINS = ["harness-core", "harness-rails", "harness-formation"]
DRY = "--dry-run" in sys.argv
TS = (sys.argv[sys.argv.index("--ts") + 1] if "--ts" in sys.argv
      else datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))

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
                    "${CLAUDE_PLUGIN_ROOT}/hooks/", f"{HOME}/.claude/hooks/")) for h in blk.get("hooks", [])]
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
        if os.path.abspath(f) == os.path.abspath(dst): continue
        if not DRY: shutil.copy2(f, dst)
    print(f"{'would copy' if DRY else 'copied'} {len(files)} hook files -> {LIVE_HOOKS}")

    new, preserved = build_settings()
    if not DRY:
        json.dump(new, open(LIVE_SETTINGS, "w"), indent=2)
    print(f"{'would write' if DRY else 'wrote'} settings.json  (preserved live-only events: {preserved})")
    print("done." + ("  run check_hook_wiring_drift.py to confirm." if not DRY else ""))

if __name__ == "__main__":
    main()
