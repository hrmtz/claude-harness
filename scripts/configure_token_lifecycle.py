#!/usr/bin/env python3
"""Set Claude's native auto-compact window; preview unless --apply is explicit.

Does not change hooks or restart sessions. Run canonical hook sync separately.
Native environment/launch/managed overrides retain their normal precedence.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import tempfile


def configure(path, window, apply=False):
    current = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(current, dict):
        raise ValueError("settings must be a JSON object")
    if current.get("autoCompactWindow") == window:
        return "unchanged"
    if not apply:
        return f"would set autoCompactWindow={window}"
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = Path.home() / "sanada_backup_persistent" / (
        "token_lifecycle_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    backup.mkdir(parents=True, mode=0o700)
    if path.exists():
        shutil.copy2(path, backup / "settings.json")
    current["autoCompactWindow"] = window
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".token-lifecycle-")
    try:
        with os.fdopen(fd, "w") as out:
            json.dump(current, out, indent=2, ensure_ascii=False)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return f"set autoCompactWindow={window}; backup={backup}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, default=Path.home() / ".claude/settings.json")
    parser.add_argument("--window", type=int, default=1000000)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not 100000 <= args.window <= 1000000:
        parser.error("--window must be between 100000 and 1000000")
    print(configure(args.settings, args.window, args.apply))
    if os.environ.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW"):
        print("Native environment override is set and takes precedence over this setting.")


if __name__ == "__main__":
    main()
