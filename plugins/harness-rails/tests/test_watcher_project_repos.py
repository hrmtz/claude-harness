#!/usr/bin/env python3
"""Tests for public-safe project->GitHub repo mapping."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
WATCHER = HERE.parent / "lib" / "safety_rails" / "watcher.py"


def load_watcher():
    spec = importlib.util.spec_from_file_location("watcher", WATCHER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestProjectRepos(unittest.TestCase):
    def setUp(self):
        self.old_env = dict(os.environ)
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["HOME"] = self.tmp.name
        os.environ.pop("HARNESS_RAILS_PROJECT_REPOS", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)
        self.tmp.cleanup()

    def test_default_has_no_author_repo_mapping(self):
        watcher = load_watcher()
        self.assertIsNone(watcher._project_to_repo("claude-harness"))

    def test_env_mapping(self):
        os.environ["HARNESS_RAILS_PROJECT_REPOS"] = json.dumps({"proj": "owner/repo"})
        watcher = load_watcher()
        self.assertEqual(watcher._project_to_repo("proj"), "owner/repo")

    def test_config_file_mapping(self):
        cfg = Path(self.tmp.name) / ".config" / "safety-rails" / "project_repos.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"proj": "owner/repo"}))
        watcher = load_watcher()
        self.assertEqual(watcher._project_to_repo("proj"), "owner/repo")


if __name__ == "__main__":
    unittest.main()
