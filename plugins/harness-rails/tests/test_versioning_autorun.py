#!/usr/bin/env python3
"""Tests for the Codex versioning autorun hook."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
HOOK = HERE.parent / "hooks" / "versioning_autorun.py"


class TestVersioningAutorun(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="versioning_autorun_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_repo(self, subject: str) -> Path:
        repo = self.tmp / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "branch", "-M", "main"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
        (repo / "README.md").write_text("seed\n")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
        env = dict(os.environ, GIT_AUTHOR_DATE="2026-01-01T00:00:00+0000", GIT_COMMITTER_DATE="2026-01-01T00:00:00+0000")
        subprocess.run(["git", "commit", "--no-verify", "-m", "initial"], cwd=repo, check=True, capture_output=True, env=env)
        subprocess.run(["git", "tag", "-a", "v1.0.0", "-m", "v1.0.0"], cwd=repo, check=True, capture_output=True)
        (repo / "change.txt").write_text("x\n")
        subprocess.run(["git", "add", "change.txt"], cwd=repo, check=True)
        env = dict(os.environ, GIT_AUTHOR_DATE="2026-01-02T00:00:00+0000", GIT_COMMITTER_DATE="2026-01-02T00:00:00+0000")
        subprocess.run(["git", "commit", "--no-verify", "-m", subject], cwd=repo, check=True, capture_output=True, env=env)
        return repo

    def run_hook(self, repo: Path):
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
            "cwd": str(repo),
            "transcript_path": str(repo / "session.jsonl"),
        }
        env = dict(os.environ, HARNESS_VERSIONING_AUTORUN="1", HARNESS_VERSIONING_DRYRUN="1")
        return subprocess.run(
            ["python3", str(HOOK)],
            cwd=repo,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=True,
            env=env,
        )

    def test_docs_only_push_noops(self):
        repo = self.make_repo("docs: update release note")
        result = self.run_hook(repo)
        self.assertEqual(result.stdout.strip(), "")
        self.assertIn("docs-only push", result.stderr)
        self.assertEqual(subprocess.run(["git", "tag", "--list", "v1.0.1"], cwd=repo, capture_output=True, text=True).stdout.strip(), "")

    def test_feat_push_proposes_minor_bump(self):
        repo = self.make_repo("feat: add release automation")
        result = self.run_hook(repo)
        self.assertIn("v1.1.0", result.stderr)
        self.assertIn("v1.1.0", result.stdout)
        self.assertIn("automated release", result.stdout)


if __name__ == "__main__":
    unittest.main()
