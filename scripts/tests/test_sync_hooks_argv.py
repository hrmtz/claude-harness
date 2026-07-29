#!/usr/bin/env python3
"""`sync_hooks_to_live.py` must not deploy when asked for help (gh #243).

The script writes ~/.claude/hooks/ and settings.json, which every session
loads. It used to read argv with membership tests, so `--help` matched no
branch, fell through, and performed the live deploy. Two people hit that in
one day; one wired an unmerged hook into every session before noticing.

These cases run the real script in a subprocess and assert on exit codes and
output only — nothing here may touch the live tree, so the deploy path is
exercised solely through --dry-run.
"""
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, "scripts", "sync_hooks_to_live.py")


def run(*args):
    return subprocess.run(
        [sys.executable, SCRIPT, *args], capture_output=True, text=True
    )


class TestArgvHandling(unittest.TestCase):
    def test_help_prints_usage_and_exits_zero(self):
        for flag in ("--help", "-h"):
            with self.subTest(flag=flag):
                r = run(flag)
                self.assertEqual(r.returncode, 0)
                self.assertIn("usage: sync_hooks_to_live.py", r.stdout)

    def test_help_does_not_deploy(self):
        """The regression itself: help must not reach the copy/write stage."""
        r = run("--help")
        combined = r.stdout + r.stderr
        for marker in ("copied", "wrote settings.json", "syntax gate"):
            self.assertNotIn(marker, combined)

    def test_unknown_argument_is_rejected(self):
        r = run("--bogus")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown argument", r.stderr)
        self.assertNotIn("copied", r.stdout)

    def test_ts_requires_a_value(self):
        r = run("--ts")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--ts requires a value", r.stderr)

    def test_dry_run_still_plans_without_writing(self):
        r = run("--dry-run")
        self.assertEqual(r.returncode, 0)
        self.assertIn("would copy", r.stdout)
        self.assertIn("would write settings.json", r.stdout)
        self.assertIn("(dry-run, skipped)", r.stdout)

    def test_ts_value_reaches_the_backup_path(self):
        r = run("--dry-run", "--ts", "19700101_000000")
        self.assertEqual(r.returncode, 0)
        self.assertIn("hooks_sync_19700101_000000", r.stdout)


if __name__ == "__main__":
    unittest.main()
