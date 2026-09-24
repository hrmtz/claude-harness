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
            # 列指定の綴りは -o + 空白 だけではない。初稿はこの軸を落としており、
            # cross-family review が実測で 8 系統の素通りを出した (#323 BLOCK)。
            "ps -eo pid,args",          # option cluster に o が混ざる
            "ps -Ao args",              # 同 (大文字 option)
            "ps -eo args|grep psql",    # 直後が pipe で空白なし
            "ps -ocmd",                 # -o に列名が付着
            "ps -ocommand",
            "ps -eocmd",                # cluster 末尾 o に列名が付着
            "ps -Aocommand",
            "ps --format pid,args",     # 長形式
            "ps --format=args",         # 長形式の = 付き
            "ps -e -o cmd",
            # round2: 列指定の綴り以外にも軸が残っていた (cross-family review 実測)。
            # terminator が空白と行末のみだったため、spaced 版が deny される一方で
            # 打ち直すと通るという最悪の形になっていた。
            "ps -ef|grep x",            # 無空白 pipe
            "ps -ef>out",               # 無空白 redirect
            "ps -ef;echo x",            # 無空白 separator
            "ps -e -f",                 # 後続 token に分割された -f
            "ps -fp 1",
            "ps ww -p 1",               # BSD format letter (a/x 以外)
            "ps u -p 1",
            "ps s -p 1",
            "ps v -p 1",
            "ps -Oargs",                # 大文字 O に列名が付着
            "ps ocmd",                  # dashless o に列名が付着
            "ps kcmd",                  # sort key に列名
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
            # cluster + metadata 列は通り続ける。cmd と comm を境界で分けられている
            # ことの確認で、これが落ちると metadata 取得が全面的に止まる。
            "ps -eocomm",
            "ps --ppid 100 -o pid=,comm=",
            "ps -o user=,etime= -p 1",
            "ps -o pid=,ppid=,pcpu= -p 5",
            # BSD letter の判定を第 1 token に固定した理由の pin。segment 全体に広げると
            # user= の u/s や args.txt の文字を拾って、metadata 取得まで止まる。
            "ps -o user=,etime= -p 1",
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
