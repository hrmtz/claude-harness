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

    def test_wrappers_preserve_execution_and_prose(self):
        wrappers = [
            "watch -n1", "watch --interval 1", "unbuffer", "unbuffer -p",
            "strace -f", "strace -e trace=process", "ltrace -f",
            "perf stat", "perf stat -e cycles", "nice", "nice -n 5",
            "stdbuf -oL", "stdbuf --output L",
        ]
        for index, wrapper in enumerate(wrappers):
            with self.subTest(case=index):
                self.assertEqual(
                    self.decision(wrapper + " systemctl status foo")["permissionDecision"],
                    "deny",
                )
                self.assertIsNone(self.decision(wrapper + " echo systemctl status"))
                self.assertIsNone(self.decision(wrapper + " systemctl cat status"))
        for command in [
            "script -c systemctl status foo",
            "script -c 'systemctl status foo'",
            "script --command='systemctl status foo'",
            "watch -n1 'systemctl status foo'",
            "sudo watch -n1 systemctl status foo",
        ]:
            self.assertEqual(self.decision(command)["permissionDecision"], "deny")
        for command in [
            "script -c 'echo systemctl status'",
            "script -c 'printf systemctl status'",
            "echo 'watch -n1 systemctl status foo'",
            "echo 'strace -f systemctl status foo'",
            "printf '%s' 'script -c systemctl status foo'",
        ]:
            self.assertIsNone(self.decision(command))

    def test_optional_values_do_not_consume_the_verb(self):
        for option in ["--legend", "--timestamp", "--when", "--what", "--drop-in"]:
            for flag in [option, option + "=test"]:
                self.assertEqual(
                    self.decision("systemctl " + flag + " status foo")["permissionDecision"],
                    "deny",
                )
                self.assertIsNone(self.decision("systemctl " + flag + " cat status"))
        for option in ["-p", "-H", "-o", "-n"]:
            self.assertEqual(
                self.decision("systemctl " + option + " test status foo")["permissionDecision"],
                "deny",
            )
            self.assertIsNone(self.decision("systemctl " + option + " status is-active foo"))

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
