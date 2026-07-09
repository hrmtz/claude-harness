#!/usr/bin/env python3
"""Regression tests for public-safe opt-in credential incident behavior."""

import importlib.util
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SCRUB = HERE.parent / "hooks" / "credential_scrub.py"
AUTOROTATE = HERE.parent / "hooks" / "autorotate_leaked_cred.sh"


def load_scrub():
    spec = importlib.util.spec_from_file_location("credential_scrub", SCRUB)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestPublicSafeConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="harness_public_safe_"))
        self.old_env = dict(os.environ)
        os.environ["HOME"] = str(self.tmp / "home")
        os.environ.pop("HARNESS_CREDENTIAL_LEAK_ISSUES", None)
        os.environ.pop("CREDENTIAL_LEAK_ISSUE_REPO", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_resume_context_disabled_issue_does_not_claim_no_manual_steps(self):
        scrub = load_scrub()
        msg = scrub.resume_context(1, scan_complete=True)
        self.assertIn("incident issue filing is disabled", msg)
        self.assertNotIn("No manual steps needed", msg)
        self.assertIn("rotate the affected credential if this was a real exposure", msg)

    def test_resume_context_ignores_stale_issue_repo(self):
        state = Path(os.environ["HOME"]) / ".claude" / "state" / "credential_scrub"
        state.mkdir(parents=True)
        (state / "last_issue").write_text("old/repo#12")
        os.environ["HARNESS_CREDENTIAL_LEAK_ISSUES"] = "1"
        os.environ["CREDENTIAL_LEAK_ISSUE_REPO"] = "new/repo"
        scrub = load_scrub()
        msg = scrub.resume_context(1, scan_complete=True)
        self.assertIn("incident issue filing is enabled", msg)
        self.assertNotIn("old/repo#12", msg)
        self.assertNotIn("new/repo#12", msg)

    def test_autorotate_does_not_comment_stale_repo_issue(self):
        home = Path(os.environ["HOME"])
        state = home / ".claude" / "state" / "credential_scrub"
        state.mkdir(parents=True)
        (state / "last_issue").write_text("old/repo#12")

        runbook = self.tmp / "rotate.sh"
        runbook.write_text("#!/bin/sh\nexit 0\n")
        runbook.chmod(runbook.stat().st_mode | stat.S_IEXEC)

        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        gh_log = self.tmp / "gh.log"
        gh = bin_dir / "gh"
        gh.write_text("#!/bin/sh\necho \"$@\" >> " + repr(str(gh_log)) + "\nexit 0\n")
        gh.chmod(0o700)

        env = dict(os.environ)
        env.update({
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HARNESS_AUTOROTATE_SCRIPT": str(runbook),
            "HARNESS_AUTOROTATE_ISSUE_REPO": "new/repo",
            "LEAK_CLASS": "pg_dsn",
            "LEAK_ROLE": "prs_ingest",
            "LEAK_TRUST": "trusted",
            "LEAK_SESSION_ID": "sid",
        })
        subprocess.run(["bash", str(AUTOROTATE)], env=env, check=True, capture_output=True, text=True)
        self.assertFalse(gh_log.exists(), gh_log.read_text() if gh_log.exists() else "")


if __name__ == "__main__":
    unittest.main()
