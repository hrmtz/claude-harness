#!/usr/bin/env python3
"""Regression tests for Kimi owned-hook migration and full-file drift."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts/lib/render_kimi_hooks.py"
sys.path.insert(0, str(HELPER.parent))
SPEC = importlib.util.spec_from_file_location("render_kimi_hooks", HELPER)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def hook(
    command: str,
    *,
    event: str = "PreToolUse",
    matcher: str | None = "Bash",
    timeout: int = 5,
) -> dict[str, object]:
    return {
        "event": event,
        "matcher": matcher,
        "command": command,
        "timeout": timeout,
    }


class RenderKimiHooksTest(unittest.TestCase):
    def setUp(self) -> None:
        self.command = (
            "export HARNESS_CHASSIS=kimi; bash "
            "/canonical/plugins/harness-core/hooks/bash_command_guard.sh"
        )
        self.expected = [hook(self.command)]

    def table(
        self,
        command: str,
        *,
        event: str = "PreToolUse",
        matcher: str | None = "Bash",
        timeout: int = 5,
    ) -> str:
        matcher_line = f"matcher = '{matcher}'\n" if matcher else ""
        return (
            "[[hooks]]\n"
            f"event = '{event}'\n"
            f"{matcher_line}"
            f"command = '{command}'\n"
            f"timeout = {timeout}\n\n"
        )

    def test_migrates_exact_legacy_root_and_preserves_user_entries(self) -> None:
        legacy = self.table(
            "bash /disposable/plugins/harness-core/hooks/"
            "bash_command_guard.sh"
        )
        user_before = self.table(
            "printf user-before", event="Notification", matcher=None
        )
        user_after = self.table(
            "printf user-after", event="Stop", matcher=None
        )
        managed = MODULE.render_block(self.expected)
        user_comment = "# keep this comment with the following user hook\n"
        old = user_before + legacy + user_comment + managed + user_after

        new, removed = MODULE.merge_config(old, self.expected)
        self.assertEqual(removed, 1)
        self.assertIn(user_before.strip(), new)
        self.assertIn(user_after.strip(), new)
        self.assertIn(user_comment.strip(), new)
        self.assertEqual(new.count(MODULE.MARK_BEGIN), 1)
        self.assertEqual(new.count(MODULE.MARK_END), 1)
        self.assertEqual(MODULE.verify_config(new, self.expected), [])

        second, removed_again = MODULE.merge_config(new, self.expected)
        self.assertEqual(removed_again, 0)
        self.assertEqual(second, new)

    def test_full_file_duplicate_is_drift(self) -> None:
        duplicate = self.table(
            "bash /old-worktree/plugins/harness-core/hooks/"
            "bash_command_guard.sh"
        )
        text = duplicate + MODULE.render_block(self.expected)
        problems = MODULE.verify_config(text, self.expected)
        self.assertTrue(
            any("duplicate_or_unexpected=1" in problem for problem in problems),
            problems,
        )

    def test_managed_block_with_foreign_root_is_drift(self) -> None:
        foreign = [
            hook(
                "export HARNESS_CHASSIS=kimi; bash "
                "/stale-worktree/plugins/harness-core/hooks/"
                "bash_command_guard.sh"
            )
        ]
        problems = MODULE.verify_config(
            MODULE.render_block(foreign), self.expected
        )
        self.assertIn(
            "managed marker hook tuples differ from overlay", problems
        )

    def test_plain_uninstalled_config_is_not_drift(self) -> None:
        plain = 'default_model = "kimi-code/k3"\n'
        self.assertEqual(MODULE.verify_config(plain, self.expected), [])

    def test_different_user_tuple_is_not_migrated_but_is_reported(self) -> None:
        different_timeout = self.table(
            "bash /old-worktree/plugins/harness-core/hooks/"
            "bash_command_guard.sh",
            timeout=99,
        )
        new, removed = MODULE.merge_config(different_timeout, self.expected)
        self.assertEqual(removed, 0)
        self.assertIn("timeout = 99", new)
        self.assertTrue(MODULE.verify_config(new, self.expected))

    def test_same_owned_path_with_altered_args_is_unexpected(self) -> None:
        altered = self.table(
            "bash /old-worktree/plugins/harness-core/hooks/"
            "bash_command_guard.sh --unsafe-extra"
        )
        text = altered + MODULE.render_block(self.expected)
        problems = MODULE.verify_config(text, self.expected)
        self.assertTrue(
            any("duplicate_or_unexpected=1" in problem for problem in problems),
            problems,
        )

    def test_malformed_or_ambiguous_markers_fail_without_output(self) -> None:
        cases = (
            MODULE.MARK_BEGIN + "\n",
            MODULE.MARK_END + "\n",
            MODULE.MARK_BEGIN
            + "\n"
            + MODULE.MARK_BEGIN
            + "\n"
            + MODULE.MARK_END,
            MODULE.MARK_END + "\n" + MODULE.MARK_BEGIN,
        )
        for text in cases:
            with self.subTest(text=text), self.assertRaises(ValueError):
                MODULE.merge_config(text, self.expected)


if __name__ == "__main__":
    unittest.main()
