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

    def seed_ledger(self, campaigns: list[list[dict[str, object]]]) -> Path:
        control = self.doc.parent / ".dual-magi"
        control.mkdir(exist_ok=True)
        doc_id = hashlib.sha256(str(self.doc.resolve()).encode()).hexdigest()[:16]
        path = control / f"CAMPAIGN.{doc_id}.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "doc_id": doc_id,
                    "doc_path": str(self.doc.resolve()),
                    "campaigns": [
                        {
                            "campaign_id": f"seed-{index}",
                            "started_at": "2026-01-01T00:00:00Z",
                            "started_by": "test",
                            "reason": "autorun boundary fixture",
                            "launches": launches,
                        }
                        for index, launches in enumerate(campaigns, start=1)
                    ],
                }
            )
        )
        return path

    def launch(
        self, round_no: int, phase: str, status: str, *, weight: int | None = None
    ) -> dict[str, object]:
        return {
            "round": round_no,
            "phase": phase,
            "model_launches": weight if weight is not None else (3 if phase == "fanout" else 1),
            "status": status,
            "artifact_sha": hashlib.sha256(self.doc.read_bytes()).hexdigest(),
            "protocol_sha": "fixture",
            "state_dir": str(self.root / "reviews"),
        }

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

    def test_used_thirteen_fanout_candidate_blocks_for_reserve(self) -> None:
        active = [
            self.launch(1, "fanout", "success"),
            self.launch(2, "xfamily", "failed"),
            self.launch(2, "xfamily", "success"),
            self.launch(3, "fanout", "success"),
            self.launch(4, "xfamily", "success"),
            self.launch(5, "fanout", "success"),
            self.launch(6, "xfamily", "success"),
        ]
        self.seed_ledger([active])
        self.arm()
        output = json.loads(self.hook().stdout)
        self.assertNotIn("decision", output)
        self.assertEqual(self.registry()["status"], "blocked")
        self.assertIn("reserved for mandatory xfamily", self.registry()["reason"])

    def test_used_thirteen_xfamily_candidate_is_not_double_reserved(self) -> None:
        active = [
            self.launch(1, "fanout", "success"),
            self.launch(2, "xfamily", "failed"),
            self.launch(2, "xfamily", "success"),
            self.launch(3, "fanout", "success"),
            self.launch(4, "xfamily", "failed"),
            self.launch(4, "xfamily", "success"),
            self.launch(5, "fanout", "success"),
        ]
        self.seed_ledger([active])
        self.arm()
        output = json.loads(self.hook().stdout)
        self.assertEqual(output["decision"], "block")
        self.assertEqual(self.registry()["status"], "active")

    def test_retry_exhaustion_blocks_on_first_hook(self) -> None:
        active = [
            self.launch(1, "fanout", "failed"),
            self.launch(1, "fanout", "failed"),
        ]
        self.seed_ledger([active])
        self.arm()
        output = json.loads(self.hook().stdout)
        self.assertNotIn("decision", output)
        self.assertEqual(self.registry()["status"], "blocked")
        self.assertIn("retry budget exhausted", self.registry()["reason"])

    def test_xfamily_retry_exhaustion_blocks_on_first_hook(self) -> None:
        active = [
            self.launch(1, "fanout", "success"),
            self.launch(2, "xfamily", "failed"),
            self.launch(2, "xfamily", "failed"),
        ]
        ledger_path = self.seed_ledger([active])
        before = ledger_path.read_bytes()
        self.arm()
        output = json.loads(self.hook().stdout)
        self.assertNotIn("decision", output)
        self.assertEqual(self.registry()["status"], "blocked")
        self.assertIn("retry budget exhausted for round 2 xfamily", self.registry()["reason"])
        self.assertEqual(ledger_path.read_bytes(), before)

    def test_running_claim_is_observed_without_mutation(self) -> None:
        active = [self.launch(1, "fanout", "running")]
        ledger_path = self.seed_ledger([active])
        before = ledger_path.read_bytes()
        self.arm()
        output = json.loads(self.hook().stdout)
        self.assertEqual(output["decision"], "block")
        self.assertEqual(self.registry()["status"], "active")
        self.assertEqual(ledger_path.read_bytes(), before)

    def test_malformed_ledger_fails_open_without_traceback_or_mutation(self) -> None:
        ledger_path = self.seed_ledger([[]])
        malformed_payloads = (
            b"{not-json",
            json.dumps(
                {
                    "schema_version": 1,
                    "doc_id": hashlib.sha256(str(self.doc.resolve()).encode()).hexdigest()[:16],
                    "doc_path": str(self.doc.resolve()),
                    "campaigns": [],
                }
            ).encode(),
        )
        for malformed in malformed_payloads:
            with self.subTest(malformed=malformed):
                ledger_path.write_bytes(malformed)
                before = ledger_path.read_bytes()
                self.arm()
                result = self.hook()
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "")
                self.assertEqual(self.registry()["status"], "active")
                self.assertEqual(ledger_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
