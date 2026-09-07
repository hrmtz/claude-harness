#!/usr/bin/env python3
"""Execution-aware systemctl status denial (#303); commands are never executed."""
import json
from pathlib import Path
import subprocess
import unittest

GUARD = Path(__file__).resolve().parents[1] / "hooks" / "bash_command_guard.sh"


class SystemctlStatusGuard(unittest.TestCase):
    def decision(self, command):
        result = subprocess.run(
            ["bash", str(GUARD)],
            input=json.dumps({"tool_input": {"command": command}}),
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, "guard classifier failed")
        if not result.stdout.strip():
            return None
        return json.loads(result.stdout)["hookSpecificOutput"]

    def test_execution_is_denied(self):
        commands = [
            "systemctl status foo", "systemctl --user status foo",
            "/usr/bin/systemctl status foo", "systemctl status --user foo",
            "systemctl --no-pager --full status foo",
            "systemctl -H host status foo", "systemctl --host=host status foo",
            "systemctl -- status foo", "sudo -u root systemctl status foo",
            "env LC_ALL=C systemctl status foo", "command systemctl status foo",
            "timeout 5 systemctl status foo", "true && systemctl status foo",
            "true; systemctl status foo", "true\nsystemctl status foo",
            "if systemctl status foo; then true; fi",
            "systemctl status foo | head -n 2",
            "sh -c 'systemctl --user status foo'",
            "ssh host 'systemctl --user status foo'",
            "ssh -p 22 host systemctl status foo",
            "eval 'systemctl status foo'", '"systemctl" "status" foo',
            "system'ctl' status foo",
            'echo "$(systemctl status foo)"',
            "echo `systemctl status foo`",
            'result=$(systemctl --user status foo)',
        ]
        for index, command in enumerate(commands):
            with self.subTest(case=index):
                output = self.decision(command)
                self.assertIsNotNone(output)
                self.assertEqual(output["permissionDecision"], "deny")
                reason = output["permissionDecisionReason"]
                self.assertIn("systemctl is-active <unit>", reason)
                self.assertIn("systemctl --user is-active <unit>", reason)
                self.assertIn("journalctl -u <unit>", reason)
                self.assertTrue(reason.endswith("次これで行こう。"))

    def test_safe_commands_and_prose_are_allowed(self):
        commands = [
            "systemctl is-active foo", "systemctl --user is-active foo",
            "systemctl restart foo", "systemctl start foo", "systemctl stop foo",
            "systemctl is-enabled foo", "systemctl cat foo",
            "systemctl list-units", "systemctl --failed", "journalctl -u foo",
            "echo '$(systemctl status foo)'",
            "echo '`systemctl status foo`'",
            "echo 'systemctl status'",
            'echo "systemctl status"', "echo systemctl status foo",
            "printf '%s\\n' 'systemctl status foo'",
            "grep 'systemctl status' README.md", "rg systemctl README.md",
            "git commit -m 'Document systemctl status alternatives'",
            "systemctl cat status", "systemctl --host status is-active foo",
            "sh -c 'echo systemctl status'",
            'ssh host "echo systemctl status"',
            "cat <<'DOC'\nsystemctl status foo\nDOC\n",
            "cat <<-DOC\n\tsystemctl status foo\n\tDOC\n",
            "echo 'first line\nsystemctl status foo'",
        ]
        for index, command in enumerate(commands):
            with self.subTest(case=index):
                self.assertIsNone(self.decision(command))


if __name__ == "__main__":
    unittest.main()
