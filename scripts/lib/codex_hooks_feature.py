#!/usr/bin/env python3
"""Classify the canonical Codex `hooks` feature from `codex features list`."""

import re
import sys


def classify(text: str) -> str:
    for line in text.splitlines():
        match = re.match(r"^hooks\s+(\S+)\s+(true|false)\s*$", line)
        if not match:
            continue
        stage, enabled = match.groups()
        if stage == "removed":
            return "removed"
        return "enabled" if enabled == "true" else "disabled"
    return "unknown"


def main() -> int:
    state = classify(sys.stdin.read())
    print(state)
    return {"enabled": 0, "disabled": 1}.get(state, 2)


if __name__ == "__main__":
    raise SystemExit(main())
