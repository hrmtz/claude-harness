#!/usr/bin/env python3
"""Detect drift between live ~/.claude/settings.json hook wiring and the
claude-harness plugin hooks.json union (gh #30 / #1).

- ORPHAN  = a hook wired live but in NO plugin  -> would be SILENT-DROPPED by a
            plugin->live overwrite; must be ported into a plugin. (FATAL: exit 1)
- DORMANT = a hook wired in a plugin but NOT live -> a guard committed to the
            'canonical' repo that never fires; deploy it or remove it. (WARN)

Known-intentional live-only integrations (not harness guardrails) are allowlisted.
Run in CI / pre-deploy so the repo and the running agent cannot silently diverge.
"""
import json, os, re, glob, sys

HOME = os.path.expanduser("~")
LIVE = f"{HOME}/.claude/settings.json"
PLUG = f"{os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}/plugins"

# live-only events/commands that are deliberately not harness guardrails
ALLOW_LIVE_ONLY = {
    ("SessionEnd", "session_end_ingest.sh"),  # hippocampus ingest integration
}

def names(hooks):
    out = set()
    for event, blocks in hooks.items():
        for blk in blocks:
            for h in blk.get("hooks", []):
                m = re.search(r"/([A-Za-z0-9_]+\.(sh|py))", h.get("command", ""))
                if m:
                    out.add((event, m.group(1)))
    return out

live = names(json.load(open(LIVE)).get("hooks", {}))
plugin = set()
for hj in glob.glob(f"{PLUG}/harness-*/hooks/hooks.json"):
    plugin |= names(json.load(open(hj)).get("hooks", {}))

orphan = {x for x in (live - plugin) if x not in ALLOW_LIVE_ONLY}
dormant = plugin - live

print(f"live-wired: {len(live)}  plugin-wired: {len(plugin)}")
if orphan:
    print("\nORPHAN (live-wired, in NO plugin — would be dropped by overwrite; PORT these):")
    for e, n in sorted(orphan): print(f"  [{e}] {n}")
if dormant:
    print("\nDORMANT (plugin-wired, not live — deploy or remove):")
    for e, n in sorted(dormant): print(f"  [{e}] {n}")
if not orphan and not dormant:
    print("\nIN SYNC ✓ (live wiring == plugin union, modulo allowlisted live-only)")

sys.exit(1 if orphan else 0)
