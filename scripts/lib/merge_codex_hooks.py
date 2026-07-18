#!/usr/bin/env python3
"""Merge the claude-harness Codex hook block without deleting other owners."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path


BEGIN = "# BEGIN claude-harness managed hooks"
END = "# END claude-harness managed hooks"
HEADER = re.compile(r"(?m)^\s*(\[\[?[^\n]+\]\]?)\s*$")
PARENT = re.compile(r"^\[\[hooks\.([^.\]]+)\]\]$")
CHILD = re.compile(r"^\[\[hooks\.([^.\]]+)\.hooks\]\]$")
COMMAND = re.compile(r'^\s*command\s*=\s*"(.*)"\s*$', re.MULTILINE)
LEGACY_COMMENT = re.compile(
    r"^\s*#\s*(?:harness hooks —|plugins/cross_cli_hooks\.json|"
    r"Do not edit this block by hand|To trust: open Codex).*$",
    re.MULTILINE,
)


def _sections(content: str) -> list[tuple[str | None, str]]:
    matches = list(HEADER.finditer(content))
    result: list[tuple[str | None, str]] = []
    if not matches:
        return [(None, content)]
    result.append((None, content[: matches[0].start()]))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        result.append((match.group(1).strip(), content[match.start() : end]))
    return result


def _commands(block: str) -> set[str]:
    return {match.group(1) for match in COMMAND.finditer(block)}


def _remove_managed_blocks(content: str) -> tuple[str, bool]:
    if BEGIN not in content and END not in content:
        return content, False
    if content.count(BEGIN) != content.count(END):
        raise ValueError("unbalanced claude-harness managed hook markers")
    pattern = re.compile(
        rf"(?ms)^\s*{re.escape(BEGIN)}\n.*?^\s*{re.escape(END)}\s*\n?"
    )
    cleaned, count = pattern.subn("", content)
    if count != content.count(BEGIN):
        raise ValueError("invalid claude-harness managed hook marker order")
    return cleaned, True


def _migrate_legacy(content: str, owned_commands: set[str]) -> str:
    """Remove only legacy leaf tables whose command is owned by this installer."""
    sections = _sections(content)
    keep = [True] * len(sections)
    current_parent: dict[str, int] = {}
    owned_removed: set[int] = set()
    unowned_child: set[int] = set()

    for index, (header, body) in enumerate(sections):
        if header is None:
            continue
        parent = PARENT.match(header)
        if parent:
            current_parent[parent.group(1)] = index
            continue
        child = CHILD.match(header)
        if not child:
            continue
        event = child.group(1)
        parent_index = current_parent.get(event)
        command = COMMAND.search(body)
        if command and command.group(1) in owned_commands:
            keep[index] = False
            if parent_index is not None:
                owned_removed.add(parent_index)
        elif parent_index is not None:
            unowned_child.add(parent_index)

    for parent_index in owned_removed - unowned_child:
        keep[parent_index] = False

    migrated = "".join(body for flag, (_, body) in zip(keep, sections) if flag)
    return LEGACY_COMMENT.sub("", migrated)


def merge(content: str, block: str) -> str:
    content, had_markers = _remove_managed_blocks(content)
    if not had_markers:
        content = _migrate_legacy(content, _commands(block))
    content = content.rstrip("\n")
    managed = f"{BEGIN}\n{block.strip()}\n{END}\n"
    return (content + "\n\n" if content else "") + managed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("block", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    result = merge(args.config.read_text(), args.block.read_text())
    args.output.write_text(result)
    os.replace(args.output, args.config)


if __name__ == "__main__":
    main()
