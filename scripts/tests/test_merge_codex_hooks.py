#!/usr/bin/env python3

import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).parents[1] / "lib" / "merge_codex_hooks.py"
SPEC = importlib.util.spec_from_file_location("merge_codex_hooks", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


MANAGED = '''[[hooks.PreToolUse]]
matcher = "Bash"

[[hooks.PreToolUse.hooks]]
type = "command"
command = "bash /repo/plugins/harness-core/hooks/guard.sh"
timeout = 5
'''


class MergeCodexHooksTest(unittest.TestCase):
    def test_legacy_migration_preserves_unrelated_hooks_and_trust(self):
        original = '''model = "gpt-5"

[[hooks.PreToolUse]]
matcher = "Bash"

[[hooks.PreToolUse.hooks]]
type = "command"
command = "bash /repo/plugins/harness-core/hooks/guard.sh"

[[hooks.PreToolUse]]
matcher = "Bash"

[[hooks.PreToolUse.hooks]]
type = "command"
command = "bash /other-product/hooks/guard.sh"

[hooks.state]
trusted = "keep-this-hash"

[[profiles.work]]
model = "gpt-5.2"
'''
        result = MODULE.merge(original, MANAGED)
        self.assertEqual(result.count("/repo/plugins/harness-core/hooks/guard.sh"), 1)
        self.assertIn("/other-product/hooks/guard.sh", result)
        self.assertIn('trusted = "keep-this-hash"', result)
        self.assertIn("[[profiles.work]]", result)

    def test_reinstall_replaces_only_marked_block(self):
        original = f'''[[hooks.SessionStart]]
[[hooks.SessionStart.hooks]]
command = "bash /hippocampus/hooks/inject.sh"

[hooks.state]
trusted = "keep"

{MODULE.BEGIN}
[[hooks.Stop]]
[[hooks.Stop.hooks]]
command = "bash /repo/plugins/old.sh"
{MODULE.END}
'''
        result = MODULE.merge(original, MANAGED)
        self.assertNotIn("/repo/plugins/old.sh", result)
        self.assertIn("/hippocampus/hooks/inject.sh", result)
        self.assertIn('trusted = "keep"', result)
        self.assertEqual(result.count(MODULE.BEGIN), 1)
        self.assertEqual(result.count(MODULE.END), 1)

    def test_mixed_legacy_group_keeps_parent_for_unrelated_child(self):
        original = '''[[hooks.PreToolUse]]
matcher = "Bash"
[[hooks.PreToolUse.hooks]]
command = "bash /repo/plugins/harness-core/hooks/guard.sh"
[[hooks.PreToolUse.hooks]]
command = "bash /other/hooks/guard.sh"
'''
        result = MODULE.merge(original, MANAGED)
        self.assertEqual(result.count('matcher = "Bash"'), 2)
        self.assertIn("/other/hooks/guard.sh", result)

    def test_unbalanced_markers_fail_closed(self):
        with self.assertRaises(ValueError):
            MODULE.merge(f"{MODULE.BEGIN}\nold", MANAGED)

    def test_managed_block_ignores_third_party_hooks(self):
        content = f'''[[hooks.Stop]]
[[hooks.Stop.hooks]]
command = "bash /third-party/hook.sh"

{MODULE.BEGIN}
{MANAGED}{MODULE.END}
'''
        block = MODULE.managed_block(content)
        self.assertIsNotNone(block)
        self.assertIn("/repo/plugins/harness-core/hooks/guard.sh", block)
        self.assertNotIn("/third-party/hook.sh", block)

    def test_managed_block_rejects_duplicates(self):
        content = f"{MODULE.BEGIN}\na\n{MODULE.END}\n{MODULE.BEGIN}\nb\n{MODULE.END}\n"
        with self.assertRaises(ValueError):
            MODULE.managed_block(content)


if __name__ == "__main__":
    unittest.main()
