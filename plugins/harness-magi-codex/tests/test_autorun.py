#!/usr/bin/env python3
"""Regression tests for acknowledgement-free Stop-hook continuation."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
AUTORUN = HERE.parent / "scripts" / "magi_autorun.py"


class AutorunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.doc = self.root / "design.md"
        self.doc.write_text("# design\n")
        self.env = os.environ.copy()
        self.env["XDG_STATE_HOME"] = str(self.root / "state")
        self.session = "test-session"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def command(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(AUTORUN), *args],
            text=True,
            capture_output=True,
            env=self.env,
            check=False,
        )

    def hook(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(AUTORUN), "--hook"],
            input=json.dumps({"session_id": self.session, "hook_event_name": "Stop"}),
            text=True,
            capture_output=True,
            env=self.env,
            check=False,
        )

    def arm(self) -> None:
        result = self.command("arm", str(self.doc), "--session", self.session)
        self.assertEqual(result.returncode, 0, result.stderr)

    def registry(self) -> dict[str, object]:
        safe = hashlib.sha256(self.session.encode()).hexdigest()[:24]
        path = Path(self.env["XDG_STATE_HOME"]) / "harness-magi-codex" / "autorun" / f"{safe}.json"
        return json.loads(path.read_text())

    def test_hook_continues_then_fails_closed_on_no_progress(self) -> None:
        self.arm()
        first = json.loads(self.hook().stdout)
        second = json.loads(self.hook().stdout)
        terminal = json.loads(self.hook().stdout)
        self.assertEqual(first["decision"], "block")
        self.assertEqual(second["decision"], "block")
        self.assertNotIn("decision", terminal)
        self.assertEqual(self.registry()["status"], "blocked")
        self.assertIn("no durable campaign progress", self.registry()["reason"])

    def test_exact_revision_plateau_completes_without_ack(self) -> None:
        self.arm()
        doc_id = hashlib.sha256(str(self.doc.resolve()).encode()).hexdigest()[:16]
        doc_sha = hashlib.sha256(self.doc.read_bytes()).hexdigest()
        control = self.doc.parent / ".dual-magi"
        (control / f"PLATEAU.{doc_id}.{doc_sha[:16]}").write_text("{}\n")
        output = json.loads(self.hook().stdout)
        self.assertNotIn("decision", output)
        self.assertEqual(self.registry()["status"], "complete")

    def test_terminal_command_needs_no_user_ack(self) -> None:
        self.arm()
        result = self.command(
            "blocked", str(self.doc), "--reason", "fixed fuse exhausted", "--session", self.session
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.registry()["status"], "blocked")
        self.assertEqual(self.hook().stdout, "")

    def test_complete_command_cannot_bypass_plateau_gate(self) -> None:
        self.arm()
        result = self.command(
            "complete", str(self.doc), "--reason", "model says done", "--session", self.session
        )
        self.assertEqual(result.returncode, 64)
        self.assertEqual(self.registry()["status"], "active")


if __name__ == "__main__":
    unittest.main()
