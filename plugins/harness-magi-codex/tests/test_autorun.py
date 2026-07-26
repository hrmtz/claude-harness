#!/usr/bin/env python3
"""Regression tests for acknowledgement-free Stop-hook continuation."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
AUTORUN = HERE.parent / "scripts" / "magi_autorun.py"
import sys

sys.path.insert(0, str(HERE.parent / "scripts"))
import magi_autorun as autorun_module  # noqa: E402
from magi_campaign_guard import DEFAULT_MAX_MODEL_LAUNCHES  # noqa: E402


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
        return self.hook_payload(
            {"session_id": self.session, "hook_event_name": "Stop"}
        )

    def hook_payload(self, payload: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(AUTORUN), "--hook"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=self.env,
            check=False,
        )

    def test_malformed_hook_input_blocks_visibly(self) -> None:
        for payload in ({}, {"session_id": ""}, {"session_id": 7}, []):
            with self.subTest(payload=payload):
                result = self.hook_payload(payload)
                self.assertEqual(result.returncode, 0)
                output = json.loads(result.stdout)
                self.assertEqual(output["decision"], "block")
                self.assertIn("malformed", output["reason"])

    def test_registry_must_be_bounded_regular_file(self) -> None:
        for kind in ("oversized", "fifo"):
            with self.subTest(kind=kind):
                self.arm()
                path = self.registry_path()
                path.unlink()
                if kind == "oversized":
                    path.write_bytes(b"x" * (autorun_module.MAX_REGISTRY_BYTES + 1))
                else:
                    os.mkfifo(path)
                result = self.hook()
                self.assertEqual(result.returncode, 0)
                output = json.loads(result.stdout)
                self.assertEqual(output["decision"], "block")
                self.assertIn(
                    "size limit" if kind == "oversized" else "regular file",
                    output["reason"],
                )
                path.unlink()

    def arm(self) -> None:
        result = self.command("arm", str(self.doc), "--session", self.session)
        self.assertEqual(result.returncode, 0, result.stderr)

    def registry(self) -> dict[str, object]:
        return json.loads(self.registry_path().read_text())

    def registry_path(self) -> Path:
        safe = hashlib.sha256(self.session.encode()).hexdigest()[:24]
        return Path(self.env["XDG_STATE_HOME"]) / "harness-magi-codex" / "autorun" / f"{safe}.json"

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
        protocol_sha = subprocess.run(
            ["python3", str(HERE.parent / "scripts" / "magi_protocol.py"), "sha"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        (control / f"PLATEAU.{doc_id}.{doc_sha[:16]}").write_text(
            json.dumps(
                {
                    "artifact_sha": doc_sha,
                    "protocol_sha": protocol_sha,
                    "reviewer_family": "claude",
                    "asserts_passed": [f"G{number}" for number in range(1, 10)],
                }
            )
            + "\n"
        )
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

    def test_invalid_plateau_marker_blocks_with_visible_boundary_error(self) -> None:
        self.arm()
        doc_id = hashlib.sha256(str(self.doc.resolve()).encode()).hexdigest()[:16]
        doc_sha = hashlib.sha256(self.doc.read_bytes()).hexdigest()
        control = self.doc.parent / ".dual-magi"
        (control / f"PLATEAU.{doc_id}.{doc_sha[:16]}").write_text("{broken\n")
        output = json.loads(self.hook().stdout)
        self.assertEqual(output["decision"], "block")
        self.assertIn("validation failed", output["reason"])
        self.assertEqual(self.registry()["status"], "blocked")
        self.assertIn("marker JSON is malformed", self.registry()["reason"])

    def test_oversized_plateau_marker_blocks_before_reading_json(self) -> None:
        self.arm()
        doc_id = hashlib.sha256(str(self.doc.resolve()).encode()).hexdigest()[:16]
        doc_sha = hashlib.sha256(self.doc.read_bytes()).hexdigest()
        marker = self.doc.parent / ".dual-magi" / f"PLATEAU.{doc_id}.{doc_sha[:16]}"
        with marker.open("wb") as handle:
            handle.truncate(64 * 1024 + 1)
        output = json.loads(self.hook().stdout)
        self.assertEqual(output["decision"], "block")
        self.assertIn("validation failed", output["reason"])
        self.assertIn("size limit", self.registry()["reason"])

    def test_protocol_validation_detail_is_preserved_and_bounded(self) -> None:
        doc_id = hashlib.sha256(str(self.doc.resolve()).encode()).hexdigest()[:16]
        doc_sha = hashlib.sha256(self.doc.read_bytes()).hexdigest()
        marker = self.doc.parent / ".dual-magi" / f"PLATEAU.{doc_id}.{doc_sha[:16]}"
        marker.parent.mkdir(exist_ok=True)
        marker.write_text("{}\n")
        with mock.patch.object(
            autorun_module,
            "protocol_sha",
            side_effect=ValueError("stage or revert: scripts/runtime.py\nsecond line"),
        ):
            status, detail, artifact_sha = autorun_module.plateau_status(self.doc.resolve())
        self.assertEqual(status, "INVALID")
        self.assertEqual(artifact_sha, "")
        self.assertIn("stage or revert: scripts/runtime.py second line", detail)
        self.assertNotIn("\n", detail)

    def test_complete_transition_rechecks_document_after_persist(self) -> None:
        self.arm()
        doc_id = hashlib.sha256(str(self.doc.resolve()).encode()).hexdigest()[:16]
        doc_sha = hashlib.sha256(self.doc.read_bytes()).hexdigest()
        control = self.doc.parent / ".dual-magi"
        protocol_sha = subprocess.run(
            ["python3", str(HERE.parent / "scripts" / "magi_protocol.py"), "sha"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        (control / f"PLATEAU.{doc_id}.{doc_sha[:16]}").write_text(
            json.dumps(
                {
                    "artifact_sha": doc_sha,
                    "protocol_sha": protocol_sha,
                    "reviewer_family": "grok",
                    "asserts_passed": [f"G{number}" for number in range(1, 10)],
                }
            )
            + "\n"
        )
        original_persist = autorun_module.persist

        def persist_then_mutate(payload: dict[str, object]) -> None:
            original_persist(payload)
            if payload.get("status") == "complete":
                self.doc.write_text("changed during completion persist\n")

        with (
            mock.patch.dict(os.environ, self.env, clear=False),
            mock.patch.object(autorun_module, "persist", side_effect=persist_then_mutate),
        ):
            autorun_module.set_terminal(
                str(self.doc), "complete", "fixture completion", self.session
            )
        self.assertEqual(self.registry()["status"], "blocked")
        self.assertIn("changed while committing", self.registry()["reason"])

    def test_plateau_precommit_revision_change_blocks_visibly(self) -> None:
        self.arm()
        artifact_sha = hashlib.sha256(self.doc.read_bytes()).hexdigest()
        output = io.StringIO()
        with (
            mock.patch.dict(os.environ, self.env, clear=False),
            mock.patch("sys.stdin", io.StringIO(json.dumps({"session_id": self.session}))),
            mock.patch.object(
                autorun_module,
                "plateau_status",
                return_value=("VALID", "fixture", artifact_sha),
            ),
            mock.patch.object(autorun_module, "file_sha", return_value="f" * 64),
            redirect_stdout(output),
        ):
            self.assertEqual(autorun_module.hook(), 0)
        hook_output = json.loads(output.getvalue())
        self.assertEqual(hook_output["decision"], "block")
        self.assertIn("confirming exact-revision", hook_output["reason"])
        self.assertEqual(self.registry()["status"], "blocked")
        self.assertIn("confirming exact-revision", self.registry()["reason"])

    def test_legacy_schema_v1_registry_is_migrated_in_memory(self) -> None:
        self.seed_ledger([[self.launch(1, "fanout", "running")]])
        self.arm()
        path = self.registry_path()
        payload = json.loads(path.read_text())
        payload.pop("completed_artifact_sha")
        path.write_text(json.dumps(payload))
        output = json.loads(self.hook().stdout)
        self.assertEqual(output["decision"], "block")
        self.assertIn("completed_artifact_sha", self.registry())

    def test_three_remaining_fanout_candidate_blocks_for_reserve(self) -> None:
        active = [
            self.launch(1, "fanout", "success"),
            self.launch(2, "xfamily", "failed"),
            self.launch(2, "xfamily", "success"),
            self.launch(3, "fanout", "success"),
            self.launch(4, "xfamily", "success"),
        ]
        self.assertEqual(
            sum(int(launch["model_launches"]) for launch in active),
            DEFAULT_MAX_MODEL_LAUNCHES - 3,
        )
        self.seed_ledger([active])
        self.arm()
        output = json.loads(self.hook().stdout)
        self.assertNotIn("decision", output)
        self.assertEqual(self.registry()["status"], "blocked")
        self.assertIn("reserved for mandatory xfamily", self.registry()["reason"])

    def test_three_remaining_xfamily_candidate_is_not_double_reserved(self) -> None:
        prior = [
            self.launch(1, "fanout", "success"),
        ]
        active = [
            self.launch(1, "fanout", "success"),
        ]
        campaigns = [prior, prior, active]
        self.assertEqual(
            sum(
                int(launch["model_launches"])
                for campaign in campaigns
                for launch in campaign
            ),
            DEFAULT_MAX_MODEL_LAUNCHES - 3,
        )
        self.seed_ledger(campaigns)
        self.arm()
        output = json.loads(self.hook().stdout)
        self.assertEqual(output["decision"], "block")
        registry = self.registry()
        self.assertEqual(registry["status"], "active", registry["reason"])

    def test_changed_requirement_can_roll_over_after_default_campaign(self) -> None:
        active = [
            self.launch(1, "fanout", "success"),
            self.launch(2, "xfamily", "success"),
            self.launch(3, "fanout", "success"),
            self.launch(4, "xfamily", "success"),
            self.launch(5, "fanout", "success"),
            self.launch(6, "xfamily", "success"),
        ]
        self.assertEqual(
            sum(int(launch["model_launches"]) for launch in active),
            DEFAULT_MAX_MODEL_LAUNCHES,
        )
        self.seed_ledger([active])
        self.doc.write_text("# revised requirement\n")
        self.arm()
        output = json.loads(self.hook().stdout)
        self.assertEqual(output["decision"], "block")
        registry = self.registry()
        self.assertEqual(registry["status"], "active", registry["reason"])

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

    def test_malformed_ledger_blocks_hook_without_traceback_or_ledger_mutation(self) -> None:
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
                output = json.loads(result.stdout)
                self.assertEqual(output["decision"], "block")
                self.assertEqual(result.stderr, "")
                self.assertEqual(self.registry()["status"], "blocked")
                self.assertEqual(ledger_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
