#!/usr/bin/env python3
"""Detect drift between live Claude hook wiring and the plugin hook union.

ORPHAN hooks are live but absent from every plugin. DORMANT hooks are declared
by a plugin but absent from live wiring. Either direction is drift and exits 1.
Unreadable or malformed inputs exit 2 so a cron observer can distinguish a
broken check from a confirmed mismatch.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LIVE = Path.home() / ".claude" / "settings.json"
DEFAULT_PLUGINS = ROOT / "plugins"

# Live-only integrations that deliberately do not belong to claude-harness.
ALLOW_LIVE_ONLY = {
    ("SessionEnd", "session_end_ingest.sh"),  # hippocampus ingest integration
}


def hook_names(hooks: Any) -> set[tuple[str, str]]:
    if not isinstance(hooks, dict):
        raise ValueError("hooks must be an object")
    found: set[tuple[str, str]] = set()
    for event, blocks in hooks.items():
        if not isinstance(event, str) or not isinstance(blocks, list):
            raise ValueError("hook events must map to arrays")
        for block in blocks:
            if not isinstance(block, dict):
                raise ValueError(f"{event} hook block must be an object")
            entries = block.get("hooks", [])
            if not isinstance(entries, list):
                raise ValueError(f"{event} hooks must be an array")
            for hook in entries:
                if not isinstance(hook, dict):
                    raise ValueError(f"{event} hook entry must be an object")
                command = hook.get("command", "")
                if not isinstance(command, str):
                    raise ValueError(f"{event} hook command must be a string")
                match = re.search(r"/([A-Za-z0-9_]+\.(?:sh|py))", command)
                if match:
                    found.add((event, match.group(1)))
    return found


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def compare(live_path: Path, plugins_root: Path) -> tuple[
    set[tuple[str, str]],
    set[tuple[str, str]],
    set[tuple[str, str]],
    set[tuple[str, str]],
]:
    live_payload = read_json(live_path)
    if not isinstance(live_payload, dict):
        raise ValueError("live settings must be an object")
    live = hook_names(live_payload.get("hooks", {}))

    plugin: set[tuple[str, str]] = set()
    hook_files = sorted(
        Path(path)
        for path in glob.glob(str(plugins_root / "harness-*" / "hooks" / "hooks.json"))
    )
    if not hook_files:
        raise ValueError(f"no plugin hooks.json files found under {plugins_root}")
    for hook_file in hook_files:
        payload = read_json(hook_file)
        if not isinstance(payload, dict):
            raise ValueError(f"{hook_file} must contain an object")
        plugin |= hook_names(payload.get("hooks", {}))

    orphan = {item for item in live - plugin if item not in ALLOW_LIVE_ONLY}
    dormant = plugin - live
    return live, plugin, orphan, dormant


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--live", type=Path, default=DEFAULT_LIVE)
    result.add_argument("--plugins", type=Path, default=DEFAULT_PLUGINS)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        live, plugin, orphan, dormant = compare(
            args.live.expanduser().resolve(),
            args.plugins.expanduser().resolve(),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"HOOK WIRING CHECK ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(f"live-wired: {len(live)}  plugin-wired: {len(plugin)}")
    if orphan:
        print("\nORPHAN (live-wired, in NO plugin — would be dropped by overwrite):")
        for event, name in sorted(orphan):
            print(f"  [{event}] {name}")
    if dormant:
        print("\nDORMANT (plugin-wired, not live — guard does not fire):")
        for event, name in sorted(dormant):
            print(f"  [{event}] {name}")
    if not orphan and not dormant:
        print("\nIN SYNC ✓ (live wiring == plugin union, modulo allowlisted live-only)")
    return 1 if orphan or dormant else 0


if __name__ == "__main__":
    raise SystemExit(main())
