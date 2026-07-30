#!/usr/bin/env python3
"""Structural stall watcher fixtures (#220)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


FORMATION = Path(__file__).resolve().parents[1]
WATCHER = FORMATION / "bin" / "formation-stall-watch"


class StallWatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "formation-home"
        self.registry = self.home / "formation" / "registry.jsonl"
        self.mailbox = self.home / "mailbox" / "log.jsonl"
        self.registry.parent.mkdir(parents=True)
        self.mailbox.parent.mkdir(parents=True)
        self.mailbox.write_text("", encoding="utf-8")
        self.requests = self.home / "requests" / "events.jsonl"
        self.requests.parent.mkdir(parents=True)
        self.requests.write_text("", encoding="utf-8")
        self.fixture = self.root / "tmux.json"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        tmux = self.bin / "tmux"
        tmux.write_text(
            """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
cfg = json.loads(Path(os.environ["TMUX_FIXTURE"]).read_text())
if call_log := os.environ.get("TMUX_CALL_LOG"):
    with Path(call_log).open("a", encoding="utf-8") as handle:
        handle.write(sys.argv[1] + "\\n")
if sys.argv[1] == "list-panes":
    expected = "#{pane_id}|#{?#{@formation_identity_locked},#{@formation_identity_locked},#{@formation_id}}"
    if "-F" not in sys.argv or sys.argv[sys.argv.index("-F") + 1] != expected:
        raise SystemExit(3)
    for pane in cfg["panes"]:
        print(f'{pane["pane"]}|{pane["worker"]}')
elif sys.argv[1] == "capture-pane":
    pane = sys.argv[sys.argv.index("-t") + 1]
    match = next(item for item in cfg["panes"] if item["pane"] == pane)
    if not match.get("capture_ok", True):
        raise SystemExit(1)
    print(match["snapshot"])
else:
    raise SystemExit(2)
""",
            encoding="utf-8",
        )
        tmux.chmod(tmux.stat().st_mode | stat.S_IXUSR)
        self.env = os.environ.copy()
        self.env.update(
            FORMATION_HOME=str(self.home),
            FORMATION_MAILBOX=str(self.mailbox),
            FORMATION_REGISTRY=str(self.registry),
            FORMATION_REQUEST_LOG=str(self.requests),
            FORMATION_STALL_WATCH_NOW="100",
            TMUX_FIXTURE=str(self.fixture),
            PATH=f"{self.bin}:{self.env['PATH']}",
        )
        self.write_worker(cli="codex", snapshot="Working (1s · esc to interrupt)")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_worker(
        self,
        *,
        cli: str,
        snapshot: str,
        worker: str = "worker-a",
        pane: str = "%42",
        spawned: str = "1970-01-01T00:01:40Z",
        session_id: str | None = None,
    ) -> None:
        self.registry.write_text(
            json.dumps({
                "id": worker,
                "pane_id": pane,
                "cli": cli,
                "spawned": spawned,
                "session_id": session_id,
            })
            + "\n",
            encoding="utf-8",
        )
        self.fixture.write_text(
            json.dumps(
                {
                    "panes": [
                        {
                            "pane": pane,
                            "worker": worker,
                            "snapshot": snapshot,
                            "capture_ok": True,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    def invoke(self, now: int, *args: str) -> subprocess.CompletedProcess[str]:
        env = self.env | {"FORMATION_STALL_WATCH_NOW": str(now)}
        return subprocess.run(
            [str(WATCHER), "--json", "--silence", "60", "--idle", "60", *args],
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )

    def result(self, now: int, *args: str) -> dict[str, object]:
        completed = self.invoke(now, *args)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)[0]

    def test_stall_requires_mailbox_silence_and_stable_pane(self) -> None:
        first = self.result(100)
        self.assertFalse(first["stalled"])
        stalled = self.result(161)
        self.assertTrue(stalled["mailbox_silent"])
        self.assertTrue(stalled["pane_stable"])
        self.assertEqual(stalled["prompt_state"], "UNKNOWN")
        self.assertTrue(stalled["stalled"])

        self.mailbox.write_text(
            json.dumps(
                {
                    "seq": 7,
                    "ts": "1970-01-01T00:02:40Z",
                    "from": "worker-a",
                    "to": "parent",
                    "body": "progress",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        emitted = self.result(170)
        self.assertFalse(emitted["mailbox_silent"])
        self.assertTrue(emitted["pane_stable"])
        self.assertFalse(emitted["stalled"])

    def test_semantic_pane_change_resets_only_pane_clock(self) -> None:
        self.result(100)
        self.write_worker(cli="codex", snapshot="new semantic output")
        changed = self.result(161)
        self.assertTrue(changed["mailbox_silent"])
        self.assertFalse(changed["pane_stable"])
        self.assertFalse(changed["stalled"])

    def test_visible_prompt_text_never_claims_editable_draft(self) -> None:
        call_log = self.root / "tmux-calls.log"
        self.env["TMUX_CALL_LOG"] = str(call_log)
        variants = (
            "❯ owner draft",
            "❯ last-input ghost",
            "❯ chassis follow-up suggestion",
        )
        for index, snapshot in enumerate(variants):
            with self.subTest(snapshot=snapshot):
                self.write_worker(cli="claude", snapshot=snapshot)
                result = self.result(100 + index)
                self.assertEqual(result["prompt_state"], "UNKNOWN")
        self.assertEqual(
            set(call_log.read_text(encoding="utf-8").splitlines()),
            {"list-panes", "capture-pane"},
        )

    def test_observer_has_no_prompt_keystroke_primitive(self) -> None:
        self.assertNotIn('"send-keys"', WATCHER.read_text(encoding="utf-8"))

    def test_codex_busy_duration_counts_as_activity(self) -> None:
        self.result(100)
        self.write_worker(cli="codex", snapshot="Working (61s · esc to interrupt)")
        changed = self.result(161)
        self.assertFalse(changed["pane_stable"])
        self.assertFalse(changed["stalled"])

    def test_kimi_idle_spinner_is_normalized(self) -> None:
        self.write_worker(
            cli="kimi",
            snapshot='  ⠴ MCP server "hippocampus" connecting…',
        )
        self.result(100)
        self.write_worker(
            cli="kimi",
            snapshot='  ⠇ MCP server "hippocampus" connecting…',
        )
        stalled = self.result(161)
        self.assertTrue(stalled["pane_stable"])
        self.assertTrue(stalled["stalled"])

        self.write_worker(
            cli="kimi",
            snapshot=(
                '  ⠧ MCP server "hippocampus" connecting…\n'
                " K3 thinking: high  ~/projects/claude-harness  dev [+15] [PR#149]"
            ),
        )
        changed = self.result(162)
        self.assertFalse(changed["pane_stable"])
        self.assertFalse(changed["stalled"])

    def test_kimi_semantic_duration_is_not_normalized(self) -> None:
        self.write_worker(cli="kimi", snapshot="phase progress 10s remaining")
        self.result(100)
        self.write_worker(cli="kimi", snapshot="phase progress 5s remaining")
        changed = self.result(161)
        self.assertFalse(changed["pane_stable"])
        self.assertFalse(changed["stalled"])

    def test_live_identity_mismatch_is_excluded(self) -> None:
        self.write_worker(cli="codex", snapshot="idle")
        self.result(100)
        state = self.home / "state" / "stall-watch" / "pane-42.json"
        self.fixture.write_text(
            json.dumps(
                {"panes": [{"pane": "%42", "worker": "other", "snapshot": "idle"}]}
            ),
            encoding="utf-8",
        )
        completed = self.invoke(100)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result[0]["status"], "UNKNOWN")
        self.assertEqual(result[0]["reason"], "identity-mismatch")
        self.assertEqual(result[0]["prompt_state"], "UNKNOWN")
        self.assertTrue(state.exists())

    def test_transient_capture_failure_preserves_observation_clock(self) -> None:
        self.write_worker(cli="codex", snapshot="idle")
        self.result(100)
        state = self.home / "state" / "stall-watch" / "pane-42.json"
        fixture = json.loads(self.fixture.read_text())
        fixture["panes"][0]["capture_ok"] = False
        self.fixture.write_text(json.dumps(fixture))
        failed_capture = self.invoke(120)
        self.assertEqual(failed_capture.returncode, 0, failed_capture.stderr)
        result = json.loads(failed_capture.stdout)
        self.assertEqual(result[0]["status"], "UNKNOWN")
        self.assertEqual(result[0]["reason"], "capture-failed")
        self.assertTrue(state.exists())

        fixture["panes"][0]["capture_ok"] = True
        self.fixture.write_text(json.dumps(fixture))
        recovered = self.result(161)
        self.assertTrue(recovered["stalled"])

    def test_respawn_generation_resets_old_clocks(self) -> None:
        self.mailbox.write_text(
            json.dumps(
                {
                    "seq": 7,
                    "ts": "1970-01-01T00:01:10Z",
                    "from": "worker-a",
                    "to": "parent",
                    "body": "old generation",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.result(100)
        self.assertTrue(self.result(161)["stalled"])
        self.write_worker(
            cli="codex",
            snapshot="Working (1s · esc to interrupt)",
            spawned="1970-01-01T00:02:41Z",
            session_id="new-session-id",
        )
        respawned = self.result(161)
        self.assertFalse(respawned["stalled"])
        self.assertEqual(respawned["mailbox_silence_seconds"], 0)
        state = json.loads(
            (self.home / "state" / "stall-watch" / "pane-42.json").read_text()
        )
        self.assertEqual(state["generation"], "new-session-id")

    def test_waiting_parent_is_not_stalled(self) -> None:
        self.requests.write_text(
            json.dumps(
                {
                    "request_id": "req-1",
                    "event": "ask",
                    "state": "WAITING_PARENT",
                    "closed": False,
                    "worker_id": "worker-a",
                    "parent_id": "lead",
                    "question": "choose",
                    "next_state": "RUNNING",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.result(100)
        waiting = self.result(161)
        self.assertEqual(waiting["status"], "WAITING_PARENT")
        self.assertEqual(waiting["reason"], "unresolved-request")
        self.assertTrue(waiting["mailbox_silent"])
        self.assertTrue(waiting["pane_stable"])
        self.assertFalse(waiting["stalled"])

    def test_mailbox_sequence_rollback_resynchronizes(self) -> None:
        self.mailbox.write_text(
            json.dumps(
                {
                    "seq": 50,
                    "ts": "1970-01-01T00:01:40Z",
                    "from": "worker-a",
                    "to": "parent",
                    "body": "before recreation",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.result(100)
        self.mailbox.write_text(
            json.dumps(
                {
                    "seq": 1,
                    "ts": "1970-01-01T00:02:41Z",
                    "from": "worker-a",
                    "to": "parent",
                    "body": "after recreation",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        recovered = self.result(161)
        self.assertEqual(recovered["mailbox_silence_seconds"], 0)
        self.assertFalse(recovered["stalled"])

    def test_missing_registry_preserves_state_and_fails_closed(self) -> None:
        self.result(100)
        state = self.home / "state" / "stall-watch" / "pane-42.json"
        state_before = state.read_bytes()
        self.registry.unlink()
        completed = self.invoke(161)
        self.assertEqual(completed.returncode, 70)
        self.assertIn("cannot read registry", completed.stderr)
        self.assertEqual(state.read_bytes(), state_before)

    def test_malformed_registry_row_suppresses_stale_state_cleanup(self) -> None:
        state_dir = self.home / "state" / "stall-watch"
        state_dir.mkdir(parents=True)
        stale = state_dir / "pane-99.json"
        stale.write_text("preserve until registry is complete\n", encoding="utf-8")
        with self.registry.open("a", encoding="utf-8") as handle:
            handle.write("{malformed\n")
        self.result(100)
        self.assertTrue(stale.exists())

    def test_malformed_state_heals_and_dry_run_does_not_write(self) -> None:
        state_dir = self.home / "state" / "stall-watch"
        state_dir.mkdir(parents=True)
        state = state_dir / "pane-42.json"
        state.write_text("{broken", encoding="utf-8")
        healed = self.result(100)
        self.assertFalse(healed["stalled"])
        self.assertEqual(json.loads(state.read_text())["worker_id"], "worker-a")

        state.unlink()
        dry = self.result(100, "--dry-run")
        self.assertFalse(dry["stalled"])
        self.assertFalse(state.exists())

    def test_watch_does_not_monopolize_state_lock_or_mutate_inputs(self) -> None:
        env = self.env.copy()
        env.pop("FORMATION_STALL_WATCH_NOW")
        self.write_worker(
            cli="codex",
            snapshot="idle",
            spawned="1970-01-01T00:00:01Z",
        )
        registry_before = self.registry.read_bytes()
        mailbox_before = self.mailbox.read_bytes()
        process = subprocess.Popen(
            [
                str(WATCHER),
                "--watch",
                "--quiet",
                "--interval",
                "1",
                "--silence",
                "1",
                "--idle",
                "1",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        try:
            time.sleep(1.4)
            one_shot = subprocess.run(
                [str(WATCHER), "--json", "--silence", "1", "--idle", "1"],
                text=True,
                capture_output=True,
                check=False,
                timeout=2,
                env=env,
            )
            self.assertEqual(one_shot.returncode, 0, one_shot.stderr)
            self.fixture.write_text(json.dumps({"panes": []}), encoding="utf-8")
            time.sleep(1.2)
            self.fixture.write_text(
                json.dumps(
                    {
                        "panes": [
                            {
                                "pane": "%42",
                                "worker": "worker-a",
                                "snapshot": "semantic recovery",
                                "capture_ok": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            time.sleep(1.2)
        finally:
            process.terminate()
            stdout, stderr = process.communicate(timeout=3)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertRegex(
            stdout,
            r"STALL worker=worker-a[^\n]*prompt_state=UNKNOWN",
        )
        self.assertIn(
            "UNKNOWN worker=worker-a pane=%42 cli=codex reason=pane-missing "
            "prompt_state=UNKNOWN",
            stdout,
        )
        self.assertRegex(
            stdout,
            r"ACTIVE worker=worker-a[^\n]*prompt_state=UNKNOWN",
        )
        self.assertEqual(self.registry.read_bytes(), registry_before)
        self.assertEqual(self.mailbox.read_bytes(), mailbox_before)

    def test_invalid_test_clock_fails_cleanly(self) -> None:
        env = self.env | {"FORMATION_STALL_WATCH_NOW": "not-a-clock"}
        completed = subprocess.run(
            [str(WATCHER), "--json"],
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
        self.assertEqual(completed.returncode, 64)
        self.assertIn("must be a non-negative integer", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)


if __name__ == "__main__":
    unittest.main()
