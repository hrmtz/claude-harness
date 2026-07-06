"""Resolve the `external` hook commands from cross_cli_hooks.json for one CLI.

Externals in the overlay carry NO machine-specific absolute paths. Instead they
reference portable tokens the installer expands at wire time:

  ${HARNESS_DIR}  -> the cloned claude-harness repo root (wherever it lives)
  ${HOME}         -> the installing user's home directory

An entry marked "optional": true is skipped when its target script is absent.
That is for cross-repo hooks (e.g. the companion hippocampus-mcp ghost-context
injector) which only exist on machines where that other project is installed —
a standalone claude-harness install silently ignores them instead of wiring a
dead path.

This module is the SINGLE SOURCE OF TRUTH for "the external set" shared by
install-codex-hooks.sh, install-grok-hooks.sh, and scripts/check_cross_cli_hooks.sh
(the drift test). If each re-implemented the token expansion + optional filter,
those copies could disagree — and the drift test, which exists to catch exactly
that kind of divergence, would be diffing against its own hand-rolled variant.
"""
from __future__ import annotations

import json
import os
import shlex
import sys


def _expand(cmd: str, harness_dir: str) -> str:
    home = os.environ.get("HOME", os.path.expanduser("~"))
    return (cmd.replace("${HARNESS_DIR}", harness_dir)
               .replace("$HARNESS_DIR", harness_dir)
               .replace("${HOME}", home)
               .replace("$HOME", home))


def _target_script(cmd: str):
    """Best-effort path of the script an external runs, for existence checks:
    the first argument after a leading interpreter, else the first token."""
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return None
    if not parts:
        return None
    if parts[0] in ("bash", "sh", "python3", "python") and len(parts) > 1:
        return parts[1]
    return parts[0]


def resolve(overlay_path: str, cli: str, harness_dir: str) -> list:
    """External hooks for `cli` with tokens expanded and optional-absent entries
    dropped. Each item: {event, matcher, command, timeout}. Order preserved."""
    section = json.load(open(overlay_path)).get(cli, {})
    out = []
    for ext in section.get("external", []):
        cmd = _expand(ext["command"], harness_dir)
        if ext.get("optional"):
            tgt = _target_script(cmd)
            if tgt and not os.path.exists(tgt):
                continue
        out.append({
            "event": ext["event"],
            "matcher": ext.get("matcher"),
            "command": cmd,
            "timeout": ext.get("timeout", 10),
        })
    return out


if __name__ == "__main__":
    # CLI: cross_cli_externals.py <overlay> <cli> <harness_dir>
    # Prints one resolved command per line (used to build the drift-test set).
    overlay_arg, cli_arg, hd_arg = sys.argv[1], sys.argv[2], sys.argv[3]
    for entry in resolve(overlay_arg, cli_arg, hd_arg):
        print(entry["command"])
