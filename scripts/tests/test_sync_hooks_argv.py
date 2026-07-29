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
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, "scripts", "sync_hooks_to_live.py")


def run(*args, home=None):
    env = dict(os.environ)
    if home is not None:
        env["HOME"] = home
    return subprocess.run(
        [sys.executable, SCRIPT, *args], capture_output=True, text=True, env=env
    )


class FakeHome:
    """A throwaway ~ so a deploy, if one happens, is visible and harmless.

    Asserting on stdout alone cannot distinguish "did not deploy" from
    "deployed but printed nothing"; the messages are the thing most likely to
    change. Here the live artifacts are real files whose bytes we compare.
    """

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="synchooks_home_")
        claude = os.path.join(self.dir, ".claude")
        os.makedirs(os.path.join(claude, "hooks"))
        self.settings = os.path.join(claude, "settings.json")
        with open(self.settings, "w") as handle:
            json.dump({"hooks": {}, "sentinel": "untouched"}, handle)
        self.backups = os.path.join(self.dir, "sanada_backup_persistent")
        self.before = self.snapshot()
        return self

    def snapshot(self):
        with open(self.settings, "rb") as handle:
            settings = handle.read()
        hooks = sorted(os.listdir(os.path.join(self.dir, ".claude", "hooks")))
        backups = sorted(os.listdir(self.backups)) if os.path.isdir(self.backups) else []
        return settings, hooks, backups

    def unchanged(self):
        return self.snapshot() == self.before

    def __exit__(self, *exc):
        shutil.rmtree(self.dir, ignore_errors=True)


class TestArgvHandling(unittest.TestCase):
    def test_help_prints_usage_and_exits_zero(self):
        for flag in ("--help", "-h"):
            with self.subTest(flag=flag):
                r = run(flag)
                self.assertEqual(r.returncode, 0)
                self.assertIn("usage: sync_hooks_to_live.py", r.stdout)

    def test_non_deploying_invocations_leave_the_tree_untouched(self):
        """The regression itself, checked against files rather than messages.

        `--ts --dry-run` is here because the first attempt at this fix let
        --ts swallow the following flag, so that invocation deployed for real
        while reading as a dry run.
        """
        for args in (
            ("--help",),
            ("-h",),
            ("--bogus",),
            ("--ts",),
            ("--ts", "--dry-run"),
            ("--ts", "--help"),
            ("--ts", ""),
            ("--",),
            ("",),
            ("--dry-run", "--bogus"),
        ):
            with self.subTest(args=args), FakeHome() as home:
                r = run(*args, home=home.dir)
                self.assertTrue(
                    home.unchanged(),
                    f"{args} wrote to the live tree (exit {r.returncode})",
                )

    def test_help_exits_zero_other_rejections_exit_two(self):
        for args, code in (
            (("--help",), 0),
            (("-h",), 0),
            (("--bogus",), 2),
            (("--ts",), 2),
            (("--ts", "--dry-run"), 2),
            (("--ts", ""), 2),
            (("--",), 2),
        ):
            with self.subTest(args=args), FakeHome() as home:
                self.assertEqual(run(*args, home=home.dir).returncode, code)

    def test_argument_errors_go_to_stderr(self):
        with FakeHome() as home:
            r = run("--bogus", home=home.dir)
            self.assertIn("unknown argument", r.stderr)
            self.assertNotIn("unknown argument", r.stdout)

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
