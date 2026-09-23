#!/usr/bin/env python3
"""ps / pgrep が argv を露出する形を止める (#316); commands are never executed.

#316 は、guard 自身の代替案が `ps -o pid,comm,args` を勧めていたために起きた。
`args` 列は同一 user の任意プロセスの argv を出すので、credential を argv で受け取る
呼び出し (psql / mysql / curl -u / rclone 等) の値がそのまま出力に乗る。

env 列だけを塞いだ時に、同じ出口に乗るもう 1 つの経路 (argv) を数えていなかった形なので、
この test は「塞いだ形」と「通すべき形」の両方を pin する。片方だけだと、次に誰かが
regex を広げた時に誤爆へ倒れたか取りこぼしへ倒れたかが分からない。
"""
import json
from pathlib import Path
import subprocess
import unittest

GUARD = Path(__file__).resolve().parents[1] / "hooks" / "bash_command_guard.sh"


class PsArgvGuard(unittest.TestCase):
    def decision(self, command):
        result = subprocess.run(
            ["bash", str(GUARD)],
            input=json.dumps({"tool_input": {"command": command}}),
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, "guard classifier failed")
        if not result.stdout.strip():
            return None
        return json.loads(result.stdout)["hookSpecificOutput"]

    def assertDenied(self, command):
        out = self.decision(command)
        self.assertIsNotNone(out, f"expected a decision for: {command}")
        self.assertEqual(out.get("permissionDecision"), "deny", f"not denied: {command}")

    def assertAllowed(self, command):
        out = self.decision(command)
        if out is not None:
            self.assertNotEqual(
                out.get("permissionDecision"), "deny", f"unexpectedly denied: {command}"
            )

    def test_argv_exposing_forms_are_denied(self):
        for command in [
            # -o の列指定に argv を出す列が入る形
            "ps -o pid,args",
            "ps -o pid,cmd",
            "ps -o pid,command",
            "ps -o pid=,cmd= -p 1234",
            "ps -p 1 -o args=",
            # BSD 形式は既定で cmdline を出す
            "ps aux",
            "ps auxww | grep psql",
            "ps axww",
            # -f / -F を含む option cluster
            "ps -ef",
            "ps -fu someuser",
            "ps -F -p 1",
            # pgrep で cmdline 全体を出す形
            "pgrep -a psql",
            "pgrep -af santei_api",
            "pgrep --list-full psql",
            # 前置きが付いても同じ
            "sudo ps aux",
            "env LC_ALL=C ps -ef",
        ]:
            with self.subTest(command=command):
                self.assertDenied(command)

    def test_metadata_only_forms_stay_allowed(self):
        # 誤爆すると process の生死確認という日常動作が全部止まるので、
        # argv を出さない形は通り続けることを同じ強さで pin する。
        for command in [
            "ps -o pid=,comm= -p 1234",
            "ps -o pid,comm",
            "ps -eo pid,comm",
            "ps --ppid 100 -o pid=,comm=",
            "ps -o user=,etime= -p 1",
            "ps -o pid=,ppid=,pcpu= -p 5",
            "pgrep -f santei_api",
            "pgrep -l psql",
            "pgrep -c psql",
            "ls -la",
        ]:
            with self.subTest(command=command):
                self.assertAllowed(command)

    def test_remediation_text_no_longer_teaches_argv_columns(self):
        # #316 の直接原因は代替案の文面そのものだった。文面が argv 列を勧める形に
        # 戻ったら落ちるようにしておく。
        source = GUARD.read_text(encoding="utf-8")
        for taught in ["-o comm,args", "-o pid,comm,args"]:
            self.assertNotIn(
                taught, source,
                f"remediation text still recommends an argv-exposing column list: {taught}",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
