#!/usr/bin/env python3
"""Tests for the public-safe temporal_anchor SessionStart hook."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
HOOK = HERE.parent / "hooks" / "temporal_anchor.sh"


class TestTemporalAnchor(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="temporal_anchor_"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.repo, check=True)
        (self.repo / "README.md").write_text("test\n")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "commit", "--no-verify", "-m", "initial"],
            cwd=self.repo,
            check=True,
            capture_output=True,
            env=dict(os.environ, GIT_AUTHOR_DATE="2026-01-01T00:00:00+0000",
                     GIT_COMMITTER_DATE="2026-01-01T00:00:00+0000"),
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_hook(self, cwd: Path, extra_env=None):
        env = dict(os.environ)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps({"cwd": str(cwd)}),
            text=True,
            cwd=cwd,
            env=env,
            capture_output=True,
            check=True,
        )

    def test_outputs_valid_generic_context(self):
        result = self.run_hook(self.repo)
        payload = json.loads(result.stdout)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("project age", ctx)
        self.assertIn(str(self.repo), ctx)
        self.assertNotIn("mafutsu", ctx)
        self.assertNotIn("PRS-LLM", ctx)

    def test_optional_memory_dir(self):
        mem = self.tmp / "memory"
        mem.mkdir()
        (mem / "feedback_example.md").write_text("x\n")
        result = self.run_hook(self.repo, {"HARNESS_TEMPORAL_MEMORY_DIR": str(mem)})
        ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("feedback_example", ctx)

    def test_non_git_dir_silent_skip(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        result = self.run_hook(outside)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
