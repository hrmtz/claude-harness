#!/usr/bin/env python3
"""Codex PostToolUse hook: auto-tag and release after a main-branch push.

This hook is intentionally narrow:
- only reacts to Bash tool calls whose command looks like `git push`
- only runs on the `main` ref
- skips docs-only pushes
- auto-tags and optionally creates a GitHub Release

The manual `plugins/harness-rails/skills/versioning/SKILL.md` flow remains
available for explicit, confirm-gated use. This hook is the Codex automation
layer requested for the repo's push path.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import shutil
from dataclasses import dataclass
from pathlib import Path


AUTORUN_ENV = "HARNESS_VERSIONING_AUTORUN"
DRYRUN_ENV = "HARNESS_VERSIONING_DRYRUN"


@dataclass
class Commit:
    sha: str
    subject: str
    body: str


def run(cmd: list[str], cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, check=check, text=True, capture_output=True)


def repo_root() -> str | None:
    try:
        out = run(["git", "rev-parse", "--show-toplevel"], check=True)
    except subprocess.CalledProcessError:
        return None
    return out.stdout.strip() or None


def current_branch(root: str) -> str:
    try:
        out = run(["git", "branch", "--show-current"], cwd=root)
    except subprocess.CalledProcessError:
        return ""
    return out.stdout.strip()


def parse_cmd(payload: dict) -> str:
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    return str(tool_input.get("command") or "")


def should_trigger(cmd: str, branch: str) -> bool:
    if os.environ.get(AUTORUN_ENV, "1") != "1":
        return False
    if not re.search(r"(^|[;&|]\s*|\s)git\s+push(\s|$)", cmd):
        return False
    if "--tags" in cmd or "refs/tags/" in cmd:
        return False
    # Trigger when the current branch is main, or the command explicitly
    # mentions main/HEAD:main as the pushed ref.
    if branch == "main":
        return True
    return bool(re.search(r"(\bmain\b|HEAD:main|refs/heads/main)", cmd))


def semver_parts(tag: str) -> tuple[int, int, int] | None:
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", tag.strip())
    if not m:
        return None
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def next_version(current: str, bump: str) -> str:
    major, minor, patch = semver_parts(current) or (0, 0, 0)
    if bump == "major":
        return f"v{major + 1}.0.0"
    if bump == "minor":
        return f"v{major}.{minor + 1}.0"
    return f"v{major}.{minor}.{patch + 1}"


def commits_since(root: str, base: str, ref: str = "main") -> list[Commit]:
    fmt = "%H%x00%s%x00%b%x00"
    out = run(["git", "log", f"{base}..{ref}", f"--format={fmt}"], cwd=root)
    raw = out.stdout.strip("\n")
    if not raw:
        return []
    parts = raw.split("\x00")
    commits: list[Commit] = []
    for i in range(0, len(parts) - 1, 3):
        sha, subject, body = parts[i : i + 3]
        if sha:
            commits.append(Commit(sha=sha, subject=subject.strip(), body=body.strip()))
    return commits


def classify(commits: list[Commit]) -> tuple[str | None, bool]:
    """Return (bump, docs_only). bump is major/minor/patch or None."""
    bump = None
    docs_only = True
    for c in commits:
        text = f"{c.subject}\n{c.body}"
        subject = c.subject.lower()
        if re.search(r"(^|\n)BREAKING CHANGE:", text) or re.search(r"^[a-z]+(!):", subject):
            return "major", False
        if subject.startswith("feat"):
            bump = "minor" if bump != "major" else bump
            docs_only = False
            continue
        if subject.startswith(("fix", "refactor", "perf", "chore")):
            bump = bump or "patch"
            docs_only = False
            continue
        if subject.startswith("docs"):
            continue
        if c.subject:
            docs_only = False
            bump = bump or "patch"
    return bump, docs_only


def release_notes(tag: str, commits: list[Commit], next_tag: str) -> str:
    lines = [
        f"{next_tag} — automated release",
        "",
        f"Generated after push on top of {tag}.",
        "",
        "Commits since the last tag:",
    ]
    for c in commits:
        lines.append(f"- {c.subject} ({c.sha[:7]})")
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    cmd = parse_cmd(payload)
    root = repo_root()
    if not root:
        return 0
    branch = current_branch(root)
    if not should_trigger(cmd, branch):
        return 0

    ref = "main" if branch == "main" or "main" in cmd else branch
    try:
        last_tag = run(["git", "describe", "--tags", "--abbrev=0", ref], cwd=root).stdout.strip()
    except subprocess.CalledProcessError:
        return 0
    if not last_tag:
        return 0

    commits = commits_since(root, last_tag, ref=ref)
    if not commits:
        return 0

    bump, docs_only = classify(commits)
    if docs_only or bump is None:
        print(f"[versioning_autorun] skip: docs-only push since {last_tag}", file=sys.stderr)
        return 0

    next_tag = next_version(last_tag, bump)
    if run(["git", "tag", "--list", next_tag], cwd=root).stdout.strip():
        print(f"[versioning_autorun] skip: tag already exists: {next_tag}", file=sys.stderr)
        return 0

    notes = release_notes(last_tag, commits, next_tag)
    dryrun = os.environ.get(DRYRUN_ENV, "0") == "1"

    print(
        f"[versioning_autorun] {bump} bump after push: {last_tag} -> {next_tag} "
        f"({len(commits)} commit(s))",
        file=sys.stderr,
    )
    if dryrun:
        sys.stdout.write(notes)
        return 0

    try:
        run(["git", "tag", "-a", next_tag, "-m", f"{next_tag} — automated release"], cwd=root)
        run(["git", "push", "origin", next_tag], cwd=root)

        gh = shutil.which("gh")
        if gh:
            with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as fh:
                fh.write(notes)
                notes_path = fh.name
            try:
                existing = subprocess.run([gh, "release", "view", next_tag], cwd=root, text=True, capture_output=True)
                if existing.returncode != 0:
                    subprocess.run(
                        [gh, "release", "create", next_tag, "--latest", "--title", f"{next_tag} — automated release", "--notes-file", notes_path],
                        cwd=root,
                        check=True,
                        text=True,
                        capture_output=True,
                    )
            finally:
                try:
                    os.unlink(notes_path)
                except OSError:
                    pass
        else:
            print("[versioning_autorun] gh CLI not found; tag pushed without GitHub Release", file=sys.stderr)
    except subprocess.CalledProcessError as exc:
        print(
            f"[versioning_autorun] warning: release automation failed after computing {next_tag}: {exc}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
