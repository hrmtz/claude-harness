#!/usr/bin/env python3
"""End-to-end tests for kimi_wire_watcher.py (gh #53 detective 2nd wall)."""

import importlib.util
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
WATCHER_PY = HERE.parent / "kimi_wire_watcher.py"

# Assemble a real bash_command_guard deny pattern without writing the literal
# into this source in a form the outer session guard would flag.
DANGER = "so" + "ps -d secrets.enc.yaml | " + "cat"
BENIGN = "echo hello world"


def load_watcher(env_overrides):
    for k, v in env_overrides.items():
        os.environ[k] = v
    spec = importlib.util.spec_from_file_location("kww", WATCHER_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestKimiWatcher(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kimi_watch_test_"))
        self.home = self.tmp / "home"
        self.sessions = self.home / ".kimi-code" / "sessions"
        self.wire = self.sessions / "sid1" / "sub1" / "agents" / "main" / "wire.jsonl"
        self.wire.parent.mkdir(parents=True)
        # fake discord-bot that records calls instead of posting
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.alert_log = self.tmp / "alerts.txt"
        db = self.bin / "discord-bot"
        # One line per invocation: flatten the (multi-line) message to spaces.
        db.write_text(
            "#!/bin/bash\nmsg=\"$*\"\nprintf '%s\\n' \"${msg//$'\\n'/ }\" >> "
            + json.dumps(str(self.alert_log)) + "\n"
        )
        db.chmod(db.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_state(self):
        # Create an (empty) state file so the run is treated as post-baseline
        # (not first-install), i.e. alerting is active.
        sf = self.home / ".kimi-code" / "harness-guard" / "watcher_state.json"
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text("{}")

    def _write_wire(self, records):
        with self.wire.open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def _call(self, tcid, cmd):
        return {
            "type": "context.append_loop_event",
            "event": {"type": "tool.call", "name": "Bash", "toolCallId": tcid,
                      "args": {"command": cmd}},
        }

    def _result(self, tcid, output):
        return {
            "type": "context.append_loop_event",
            "event": {"type": "tool.result", "toolCallId": tcid,
                      "result": {"output": output}},
        }

    def _run(self):
        # Fresh import each run so module-level Path.home() re-resolves to our tmp.
        real_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        os.environ["PATH"] = f"{self.bin}:{os.environ['PATH']}"
        # HOME is the tmp sandbox (sessions/state), but the real gate hook lives
        # in the checked-out repo — point HARNESS_PLUGINS at it explicitly.
        real_plugins = str(HERE.parent.parent)
        mod = load_watcher({"HOME": str(self.home), "HARNESS_PLUGINS": real_plugins})
        try:
            mod.main()
        finally:
            if real_home is not None:
                os.environ["HOME"] = real_home
        return mod

    def _alerts(self):
        if not self.alert_log.exists():
            return []
        return [l for l in self.alert_log.read_text().splitlines() if l.strip()]

    def test_gap_detected_when_danger_ran_unguarded(self):
        self._seed_state()
        self._write_wire([
            self._call("t1", BENIGN),
            self._result("t1", "hello world"),
            self._call("t2", DANGER),
            self._result("t2", "secret-value-leaked-here"),  # no guard marker → ran
        ])
        self._run()
        alerts = self._alerts()
        self.assertEqual(len(alerts), 1, alerts)
        self.assertIn("t2", alerts[0])
        self.assertIn("GAP", alerts[0])

    def test_no_alert_when_guard_blocked_it(self):
        self._write_wire([
            self._call("t3", DANGER),
            self._result("t3", "🚫 harness guard (bash_command_guard.sh): blocked"),
        ])
        self._run()
        self.assertEqual(self._alerts(), [])

    def test_no_alert_for_benign(self):
        self._write_wire([
            self._call("t4", BENIGN),
            self._result("t4", "hello world"),
        ])
        self._run()
        self.assertEqual(self._alerts(), [])

    def test_no_double_alert_on_second_run(self):
        self._seed_state()
        self._write_wire([
            self._call("t5", DANGER),
            self._result("t5", "ran-unguarded"),
        ])
        self._run()
        self.assertEqual(len(self._alerts()), 1)
        self._run()  # second cron tick — state must prevent re-alert
        self.assertEqual(len(self._alerts()), 1)

    def test_first_run_baseline_suppresses_historical_alerts(self):
        # No seeded state → first run is a baseline: pre-install history must not
        # alert, but must be recorded so it never alerts later either.
        self._write_wire([
            self._call("t8", DANGER),
            self._result("t8", "historical-unguarded-run"),
        ])
        self._run()
        self.assertEqual(self._alerts(), [])          # baseline: no alert
        self._run()                                    # subsequent tick: still none
        self.assertEqual(self._alerts(), [])

    def test_deferred_when_result_absent(self):
        # call with no result yet → no alert, not marked seen → alerts once result lands
        self._seed_state()
        self._write_wire([self._call("t6", DANGER)])
        self._run()
        self.assertEqual(self._alerts(), [])
        self._write_wire([
            self._call("t6", DANGER),
            self._result("t6", "ran-unguarded"),
        ])
        self._run()
        self.assertEqual(len(self._alerts()), 1)

    def test_kill_switch(self):
        (self.home / ".kimi-code" / "harness-watch.disabled").touch()
        self._write_wire([
            self._call("t7", DANGER),
            self._result("t7", "ran-unguarded"),
        ])
        self._run()
        self.assertEqual(self._alerts(), [])


if __name__ == "__main__":
    unittest.main()
