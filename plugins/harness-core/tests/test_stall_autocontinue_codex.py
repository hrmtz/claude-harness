#!/usr/bin/env python3
import json
import os
import pathlib
import subprocess
import tempfile


HOOK = pathlib.Path(__file__).parents[1] / "hooks" / "stall_autocontinue.sh"


def run(message):
    with tempfile.TemporaryDirectory() as home:
        payload = {
            "hook_event_name": "Stop",
            "session_id": "fixture",
            "turn_id": "turn-1",
            "last_assistant_message": message,
            "stop_hook_active": False,
        }
        env = dict(os.environ, HOME=home)
        return subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                              text=True, capture_output=True, env=env, check=False)


blocked = run("<invoke name=exec_command>broken")
assert blocked.returncode == 0
assert json.loads(blocked.stdout) == {
    "decision": "block",
    "reason": "あなたの直前のターンは tool 呼び出しの構文が壊れて parse されず、何も実行されないまま終了しました（stall）。正しい tool-call 構文で直前の呼び出しを再発行し、作業を続行してください。本当に作業が完了している場合のみ、tool を使わず完了報告で終えてください。",
}

normal = run("Task completed normally.")
assert normal.returncode == 0
assert not normal.stdout
print("stall_autocontinue Codex adapter: OK")
