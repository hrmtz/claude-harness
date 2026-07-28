#!/usr/bin/env python3
"""Mint a local ownership receipt after a Formation session creates a PR."""

from __future__ import annotations

import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
from collections.abc import Mapping
from datetime import datetime, timezone

PR_URL_RE = re.compile(
    r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)"
)
NONCE_RE = re.compile(r"<!-- babysit-pr-nonce: ([0-9a-f]{32}) -->")


def _command(argv: list[str], cwd: str | None = None) -> str:
    result = subprocess.run(
        argv, cwd=cwd, text=True, capture_output=True, check=False, timeout=5
    )
    if result.returncode:
        raise RuntimeError(f"{argv[0]} failed")
    return result.stdout.strip()


def _parse_create(command: str) -> tuple[list[str], str] | None:
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        return None
    if argv[:3] != ["gh", "pr", "create"]:
        return None
    # Refuse compound shell programs. A direct invocation gives the hook one
    # unambiguous stdout producer and prevents matching a quoted/example URL.
    if any(token in {";", "&&", "||", "|", "&"} for token in argv):
        return None
    body = ""
    for index, token in enumerate(argv[3:], 3):
        if token == "--body" and index + 1 < len(argv):
            body = argv[index + 1]
            break
        if token.startswith("--body="):
            body = token.partition("=")[2]
            break
    return argv, body


def _repo_identity(path: str) -> str:
    """Resolve the shared git directory so sibling worktrees are one repository."""
    common = pathlib.Path(_command(["git", "rev-parse", "--git-common-dir"], path))
    if not common.is_absolute():
        common = pathlib.Path(path) / common
    return str(common.resolve())


def _active_formation_row(
    registry: pathlib.Path, session_id: str, cwd: str
) -> Mapping[str, object] | None:
    if not registry.is_file() or not session_id:
        return None
    rows: list[Mapping[str, object]] = []
    try:
        for line in registry.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, Mapping):
                return None
            rows.append(row)
    except (OSError, json.JSONDecodeError):
        return None
    candidates = [row for row in rows if row.get("session_id") == session_id]
    if len(candidates) != 1:
        return None
    pane = str(candidates[0].get("pane_id") or "")
    if not re.fullmatch(r"%[0-9]+", pane):
        return None
    try:
        pane_state = _command([
            "tmux", "display-message", "-p", "-t", pane,
            "#{pane_dead}\t#{pane_current_path}",
        ]).split("\t", 1)
        if len(pane_state) != 2 or pane_state[0] != "0":
            return None
        if _repo_identity(pane_state[1]) != _repo_identity(cwd):
            return None
    except (OSError, RuntimeError):
        return None
    return candidates[0]


def _body_nonce(body: str) -> str | None:
    matches = NONCE_RE.findall(body)
    return matches[0] if len(matches) == 1 else None


def process(payload: Mapping[str, object], home: pathlib.Path) -> pathlib.Path | None:
    # Current Claude Code sends failed Bash tools to PostToolUseFailure. The
    # explicit event check is still defense in depth for older/wrapped shapes.
    if payload.get("hook_event_name") != "PostToolUse":
        return None
    tool_input = payload.get("tool_input")
    response = payload.get("tool_response")
    if not isinstance(tool_input, Mapping) or not isinstance(response, Mapping):
        return None
    parsed = _parse_create(str(tool_input.get("command") or ""))
    if parsed is None:
        return None
    _, body = parsed
    nonce = _body_nonce(body)
    if nonce is None:
        return None
    stdout = response.get("stdout")
    if not isinstance(stdout, str):
        return None
    match = PR_URL_RE.fullmatch(stdout.strip())
    if not match:
        return None
    repo, number = f"{match.group(1)}/{match.group(2)}", int(match.group(3))
    # Formation owns its spawn-scoped id in the inherited environment. Claude's
    # hook payload session_id is a different CLI conversation UUID.
    session_id = os.environ.get("FORMATION_SESSION_ID", "")
    cwd = str(payload.get("cwd") or "")
    registry = home / ".formation" / "formation" / "registry.jsonl"
    if not cwd or _active_formation_row(registry, session_id, cwd) is None:
        return None
    anchor = _command(["git", "rev-parse", "HEAD"], cwd)
    if not re.fullmatch(r"[0-9a-f]{40}", anchor):
        return None
    sanitized = repo.replace("/", "__")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", sanitized):
        return None
    directory = home / ".claude" / "pr_receipts"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = directory / f"{sanitized}_{number}.json"
    receipt = {
        "repo": repo,
        "pr": number,
        "head_oid": anchor,
        "nonce": nonce,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(target, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(receipt, handle, sort_keys=True)
        handle.write("\n")
    return target


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if isinstance(payload, Mapping):
            process(payload, pathlib.Path.home())
    except Exception:
        # PostToolUse hooks are observation rails. Invalid input and local
        # evidence failures must fail closed without disrupting the CLI.
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
