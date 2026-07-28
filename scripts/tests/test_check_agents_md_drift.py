#!/usr/bin/env python3
"""Fixture-based regression tests for the AGENTS.md drift checker."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts" / "check_agents_md_drift.py"

TEMPLATE = """# Agent harness — behavioral rails

> preamble body

## 1. Alpha

alpha body

## 2. Beta

beta body

<!-- harness-agents-template: rev=test -->
"""

SYNCED = """# Agent harness — behavioral rails

> preamble body

## 1. Alpha

alpha body

## 2. Beta

beta body
"""


class AgentsMdDriftTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.projects = self.root / "projects"
        self.projects.mkdir()
        self.template = self.root / "AGENTS.md.template"
        self.template.write_text(TEMPLATE, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def install(self, name: str, text: str, *, worktree: bool = False) -> Path:
        project = self.projects / name
        project.mkdir(parents=True)
        if worktree:
            (project / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
        else:
            (project / ".git").mkdir()
        (project / "AGENTS.md").write_text(text, encoding="utf-8")
        return project

    def invoke(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(CHECKER),
                "--template",
                str(self.template),
                "--roots",
                str(self.projects),
                *extra,
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_in_sync_is_zero(self) -> None:
        self.install("proj-a", SYNCED)
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("IN SYNC", result.stdout)

    def test_stamp_is_not_section_content(self) -> None:
        """Installed files predate the stamp; it must not register as drift."""
        self.assertIn("harness-agents-template", TEMPLATE)
        self.assertNotIn("harness-agents-template", SYNCED)
        self.install("proj-a", SYNCED)
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stale_unrefluxed_diverged_are_all_drift(self) -> None:
        drifted = SYNCED.replace("## 2. Beta\n\nbeta body\n", "")
        drifted += "\n## 99. Local\n\nlocal only\n"
        drifted = drifted.replace("alpha body", "alpha body edited")
        self.install("proj-a", drifted)
        result = self.invoke()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("STALE", result.stdout)
        self.assertIn("## 2. Beta", result.stdout)
        self.assertIn("UNREFLUXED", result.stdout)
        self.assertIn("## 99. Local", result.stdout)
        self.assertIn("DIVERGED", result.stdout)
        self.assertIn("## 1. Alpha", result.stdout)

    def test_marker_less_file_is_out_of_scope(self) -> None:
        self.install("proj-a", SYNCED)
        (self.projects / "proj-b").mkdir()
        (self.projects / "proj-b" / ".git").mkdir()
        (self.projects / "proj-b" / "AGENTS.md").write_text(
            "# Project-local notes\n", encoding="utf-8"
        )
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("canonical-installed: 1", result.stdout)

    def test_zero_marker_matches_is_error_not_green(self) -> None:
        (self.projects / "proj-b").mkdir()
        (self.projects / "proj-b" / "AGENTS.md").write_text(
            "# Project-local notes\n", encoding="utf-8"
        )
        result = self.invoke()
        self.assertEqual(result.returncode, 2)
        self.assertIn("AGENTS DRIFT CHECK ERROR", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_worktree_skipped_by_git_file_not_path_name(self) -> None:
        self.install("proj-a", SYNCED)
        # Name carries no -wt- marker; only the .git file marks it a worktree.
        self.install("recall", SYNCED.replace("alpha body", "stale alpha"), worktree=True)
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("worktrees skipped: 1", result.stdout)
        result = self.invoke("--include-worktrees")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("INFO worktree installs", result.stdout)
        self.assertIn("recall", result.stdout)

    def test_missing_root_is_error(self) -> None:
        self.install("proj-a", SYNCED)
        result = subprocess.run(
            [
                "python3",
                str(CHECKER),
                "--template",
                str(self.template),
                "--roots",
                str(self.root / "does-not-exist"),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("AGENTS DRIFT CHECK ERROR", result.stderr)

    def test_unreadable_template_is_error(self) -> None:
        self.install("proj-a", SYNCED)
        self.template.write_bytes(b"\xff\xfe binary")
        result = self.invoke()
        self.assertEqual(result.returncode, 2)
        self.assertIn("AGENTS DRIFT CHECK ERROR", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
