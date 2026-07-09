#!/usr/bin/env python3
"""Public-safe behavior for optional hippocampus companion hooks."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
ROOT = HERE.parents[2]


class TestCompanionHooks(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="companion_hooks_"))
        self.env = dict(os.environ, HOME=str(self.tmp / "home"))
        Path(self.env["HOME"]).mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_hook(self, name):
        return subprocess.run(
            ["bash", str(HOOKS / name)],
            input=json.dumps({"cwd": str(ROOT)}),
            text=True,
            env=self.env,
            capture_output=True,
            check=True,
        )

    def test_ghost_inject_missing_companion_is_silent(self):
        result = self.run_hook("ghost_inject.sh")
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_recent_topics_missing_companion_is_silent(self):
        result = self.run_hook("recent_topics_inject.sh")
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_companion_hooks_do_not_default_to_author_secrets_repo(self):
        for name in ("ghost_inject.sh", "recent_topics_inject.sh"):
            text = (HOOKS / name).read_text()
            self.assertNotIn("creds-migration", text)


if __name__ == "__main__":
    unittest.main()
