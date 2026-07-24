#!/usr/bin/env python3
"""Tests for the read-only Dual-Magi design convergence adapter."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
SCRIPTS = PLUGIN / "scripts"
os.sys.path.insert(0, str(SCRIPTS))

import magi_campaign_guard as guard  # noqa: E402
import magi_design_convergence_gate as design  # noqa: E402


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DesignConvergenceGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.doc = self.root / "design.md"
        self.doc.write_text("# design v1\n")
        self.control = self.root / ".dual-magi"
        self.control.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        self.campaigns: list[dict[str, object]] = [self.new_campaign()]
        self.launch_sequence = 0

    def tearDown(self) -> None:
        if self.old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.old_home
        self.temp.cleanup()

    def new_campaign(self) -> dict[str, object]:
        return {
            "campaign_id": str(uuid.uuid4()),
            "started_at": "2026-07-24T00:00:00+00:00",
            "started_by": "test",
            "reason": "fixture",
            "launches": [],
        }

    @property
    def launches(self) -> list[dict[str, object]]:
        launches = self.campaigns[-1]["launches"]
        assert isinstance(launches, list)
        return launches

    def current_sha(self) -> str:
        return digest(self.doc)

    def revise(self, number: int) -> str:
        self.doc.write_text(f"# design v{number}\n")
        return self.current_sha()

    def review_payload(
        self,
        *,
        reviewer: str,
        round_no: int,
        artifact_sha: str,
        root: str | None,
        subsystem: str,
        severity: str = "HIGH",
    ) -> dict[str, object]:
        findings: list[dict[str, object]] = []
        if root is not None:
            findings.append(
                {
                    "finding_id": f"{reviewer}-{round_no}-{root}",
                    "severity": severity,
                    "title": "design blocker",
                    "location": "design.md:1",
                    "rationale": "fixture",
                    "required_fix": "revise the design",
                    "confidence": "high",
                    "dup_flag": "new",
                    "missed_angle": "fixture",
                    "subsystem": subsystem,
                    "root_cause_id": root,
                    "affected_invariant": "bounded-design-review",
                    "changes_design_invariant": False,
                    "relation_to_prior": "new-root",
                }
            )
        return {
            "reviewer": reviewer,
            "round": round_no,
            "artifact_id": guard.doc_id(self.doc.resolve()),
            "artifact_sha": artifact_sha,
            "verdict": "REVISE" if findings else "GO",
            "schema_grounding_verdict": "PASS",
            "verify_commands_executed": ["rg -n invariant design.md"],
            "source_artifacts": [],
            "dispositions": [],
            "findings": findings,
        }

    def add_launch(
        self,
        *,
        round_no: int,
        phase: str,
        artifact_sha: str,
        root: str | None = None,
        subsystem: str = "orchestration",
        severity: str = "HIGH",
        status: str = "success",
    ) -> Path:
        self.launch_sequence += 1
        state = self.root / f"state-{self.launch_sequence}"
        state.mkdir()
        launch = {
            "claim_id": f"claim-{self.launch_sequence}",
            "sequence": len(self.launches) + 1,
            "round": round_no,
            "phase": phase,
            "attempt": 1,
            "model_launches": guard.PHASE_WEIGHT[phase],
            "state_dir": str(state),
            "artifact_sha": artifact_sha,
            "protocol_sha": guard.protocol_sha(),
            "claimed_at": "2026-07-24T00:00:00+00:00",
            "status": status,
        }
        self.launches.append(launch)
        if status != "success":
            return state
        if phase == "fanout":
            for persona in design.PERSONAS:
                payload = self.review_payload(
                    reviewer=persona.upper(),
                    round_no=round_no,
                    artifact_sha=artifact_sha,
                    root=root,
                    subsystem=subsystem,
                    severity=severity,
                )
                (state / f"round_{round_no}_{persona}.json").write_text(
                    json.dumps(payload) + "\n"
                )
        else:
            self.write_xfamily(
                state,
                round_no=round_no,
                artifact_sha=artifact_sha,
                root=root,
                subsystem=subsystem,
                severity=severity,
            )
        return state

    def write_xfamily(
        self,
        state: Path,
        *,
        round_no: int,
        artifact_sha: str,
        root: str | None,
        subsystem: str,
        severity: str,
    ) -> None:
        prefix = state / f"round_{round_no}_xfamily"
        payload = self.review_payload(
            reviewer="CLAUDE-XFAMILY",
            round_no=round_no,
            artifact_sha=artifact_sha,
            root=root,
            subsystem=subsystem,
            severity=severity,
        )
        findings_path = Path(f"{prefix}.json")
        findings_path.write_text(json.dumps(payload) + "\n")
        sid = f"11111111-2222-4333-8444-{self.launch_sequence:012d}"
        transcript = self.home / ".claude" / "projects" / "fixture" / f"{sid}.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(
            json.dumps(
                {
                    "message": {
                        "model": "claude-fable-5",
                        "content": [{"type": "tool_use", "name": "Grep", "input": {}}],
                    }
                }
            )
            + "\n"
        )
        meta = {
            "session_id": sid,
            "model_id": "claude-fable-5",
            "requested_model": "claude-fable-5",
            "reviewer_family": "claude",
            "model_usage_keys": ["claude-fable-5"],
            "num_turns": 2,
            "artifact_sha": artifact_sha,
            "permission_denials": [],
            "output_sha": digest(findings_path),
            "transcript_path": str(transcript),
            "transcript_sha": digest(transcript),
        }
        Path(f"{prefix}.meta.json").write_text(json.dumps(meta) + "\n")

    def add_pair(
        self,
        *,
        fanout_round: int,
        artifact_sha: str,
        root: str | None,
        subsystem: str,
        severity: str = "HIGH",
    ) -> None:
        self.add_launch(
            round_no=fanout_round,
            phase="fanout",
            artifact_sha=artifact_sha,
            root=root,
            subsystem=subsystem,
            severity=severity,
        )
        self.add_launch(
            round_no=fanout_round + 1,
            phase="xfamily",
            artifact_sha=artifact_sha,
            root=root,
            subsystem=subsystem,
            severity=severity,
        )

    def write_ledger(self) -> Path:
        ledger = {
            "schema_version": 1,
            "doc_id": guard.doc_id(self.doc.resolve()),
            "doc_path": str(self.doc.resolve()),
            "campaigns": self.campaigns,
        }
        path = self.control / f"CAMPAIGN.{guard.doc_id(self.doc.resolve())}.json"
        path.write_text(json.dumps(ledger, indent=2) + "\n")
        return path

    def run_main(self) -> tuple[int, dict[str, object] | None, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(
                os.sys, "argv", ["magi_design_convergence_gate.py", "evaluate", str(self.doc)]
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = design.main()
        payload = json.loads(stdout.getvalue()) if stdout.getvalue().strip() else None
        return code, payload, stderr.getvalue()

    def assert_decision(self, expected: str, reason: str) -> dict[str, object]:
        self.write_ledger()
        result = design.evaluate(self.doc)
        self.assertEqual(result["decision"], expected)
        self.assertEqual(result["reason_code"], reason)
        self.assertFalse(result["authorizes_shipping"])
        return result

    def test_repeated_root_requests_redesign(self) -> None:
        first = self.current_sha()
        self.add_pair(
            fanout_round=1,
            artifact_sha=first,
            root="root-a",
            subsystem="parser",
        )
        second = self.revise(2)
        self.add_pair(
            fanout_round=3,
            artifact_sha=second,
            root="root-a",
            subsystem="parser",
        )
        self.assert_decision("REDESIGN", "DESIGN_BLOCKING_ROOT_REPEATED")

    def test_same_subsystem_new_roots_requests_scope_split(self) -> None:
        first = self.current_sha()
        self.add_pair(
            fanout_round=1,
            artifact_sha=first,
            root="root-a",
            subsystem="storage",
        )
        second = self.revise(2)
        self.add_pair(
            fanout_round=3,
            artifact_sha=second,
            root="root-b",
            subsystem="storage",
        )
        self.assert_decision(
            "SCOPE_SPLIT", "DESIGN_SAME_SUBSYSTEM_NEW_ROOTS_RECURRED"
        )

    def test_three_revision_stalled_mass_blocks(self) -> None:
        first = self.current_sha()
        self.add_pair(
            fanout_round=1, artifact_sha=first, root="a", subsystem="alpha"
        )
        second = self.revise(2)
        self.add_pair(
            fanout_round=3, artifact_sha=second, root="b", subsystem="beta"
        )
        third = self.revise(3)
        self.add_pair(
            fanout_round=5, artifact_sha=third, root="c", subsystem="gamma"
        )
        self.assert_decision("BLOCKED", "DESIGN_BLOCKER_MASS_STALLED")

    def test_max_cycles_blocks_even_when_roots_change(self) -> None:
        first = self.current_sha()
        self.add_pair(
            fanout_round=1,
            artifact_sha=first,
            root="critical",
            subsystem="alpha",
            severity="CRITICAL",
        )
        second = self.revise(2)
        self.add_pair(
            fanout_round=3,
            artifact_sha=second,
            root="high",
            subsystem="beta",
            severity="HIGH",
        )
        self.assert_decision("BLOCKED", "DESIGN_MAX_LOGICAL_CYCLES_REACHED")

    def test_clean_current_xfamily_is_only_a_plateau_candidate_and_writes_nothing(self) -> None:
        current = self.current_sha()
        self.add_pair(
            fanout_round=1,
            artifact_sha=current,
            root=None,
            subsystem="none",
        )
        ledger = self.write_ledger()
        before = {
            path.relative_to(self.root): digest(path)
            for path in self.root.rglob("*")
            if path.is_file()
        }
        result = design.evaluate(self.doc)
        after = {
            path.relative_to(self.root): digest(path)
            for path in self.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(result["decision"], "PLATEAU_CANDIDATE")
        self.assertEqual(
            result["reason_code"], "DESIGN_READY_FOR_EXISTING_PLATEAU_GATE"
        )
        self.assertFalse(result["authorizes_shipping"])
        self.assertEqual(before, after)
        self.assertTrue(ledger.is_file())
        self.assertFalse(any(self.control.glob("PLATEAU.*")))
        code, cli_result, stderr = self.run_main()
        self.assertEqual(code, 0, stderr)
        assert cli_result is not None
        self.assertEqual(cli_result["decision"], "PLATEAU_CANDIDATE")

    def test_reserved_unaffordable_fanout_blocks(self) -> None:
        self.campaigns = []
        budget_sequence = 0

        def failed_launch(round_no: int, phase: str) -> dict[str, object]:
            nonlocal budget_sequence
            budget_sequence += 1
            return {
                "claim_id": f"budget-{budget_sequence}",
                "sequence": budget_sequence,
                "round": round_no,
                "phase": phase,
                "attempt": 1,
                "model_launches": guard.PHASE_WEIGHT[phase],
                "state_dir": str(self.root),
                "artifact_sha": "0" * 64,
                "protocol_sha": guard.protocol_sha(),
                "claimed_at": "2026-07-24T00:00:00+00:00",
                "status": "failed",
            }

        for _ in range(3):
            campaign = self.new_campaign()
            launches = campaign["launches"]
            assert isinstance(launches, list)
            launches.extend(
                [
                    failed_launch(1, "fanout"),
                    failed_launch(2, "xfamily"),
                ]
            )
            self.campaigns.append(campaign)
        prior = self.new_campaign()
        prior_launches = prior["launches"]
        assert isinstance(prior_launches, list)
        prior_launches.append(failed_launch(1, "xfamily"))
        self.campaigns.append(prior)
        self.campaigns.append(self.new_campaign())
        result = self.assert_decision(
            "BLOCKED", "DESIGN_NEXT_FANOUT_UNAFFORDABLE"
        )
        self.assertEqual(result["usage"], 13)
        self.assertEqual(result["ceiling"], 16)

    def test_stale_review_artifact_fails_closed_with_exit_two(self) -> None:
        current = self.current_sha()
        state = self.add_launch(
            round_no=1,
            phase="fanout",
            artifact_sha=current,
            root="a",
        )
        stale = state / "round_1_melchior.json"
        payload = json.loads(stale.read_text())
        payload["artifact_sha"] = "f" * 64
        stale.write_text(json.dumps(payload) + "\n")
        self.write_ledger()
        code, result, _ = self.run_main()
        self.assertEqual(code, 2)
        assert result is not None
        self.assertEqual(result["decision"], "BLOCKED")
        self.assertEqual(
            result["reason_code"], "UNSAFE_OR_INCOMPLETE_DESIGN_INPUT"
        )

    def test_malformed_review_artifact_fails_closed(self) -> None:
        current = self.current_sha()
        state = self.add_launch(
            round_no=1,
            phase="fanout",
            artifact_sha=current,
            root="a",
        )
        (state / "round_1_caspar.json").write_text("{not-json\n")
        self.write_ledger()
        code, result, _ = self.run_main()
        self.assertEqual(code, 2)
        assert result is not None
        self.assertEqual(
            result["reason_code"], "UNSAFE_OR_INCOMPLETE_DESIGN_INPUT"
        )

    def test_symlinked_review_artifact_fails_closed(self) -> None:
        current = self.current_sha()
        state = self.add_launch(
            round_no=1,
            phase="fanout",
            artifact_sha=current,
            root="a",
        )
        path = state / "round_1_melchior.json"
        target = state / "real.json"
        path.replace(target)
        path.symlink_to(target)
        self.write_ledger()
        code, result, _ = self.run_main()
        self.assertEqual(code, 2)
        assert result is not None
        self.assertEqual(
            result["reason_code"], "UNSAFE_OR_INCOMPLETE_DESIGN_INPUT"
        )

    def test_evidence_mutation_during_evaluation_fails_closed(self) -> None:
        current = self.current_sha()
        state = self.add_launch(
            round_no=1,
            phase="fanout",
            artifact_sha=current,
            root="a",
        )
        watched = state / "round_1_melchior.json"
        self.write_ledger()
        real_stable_bytes = design.stable_bytes
        mutated = False

        def mutate_after_read(path: Path, *, limit: int = design.MAX_JSON_BYTES) -> bytes:
            nonlocal mutated
            raw = real_stable_bytes(path, limit=limit)
            if path == watched and not mutated:
                mutated = True
                watched.write_bytes(raw + b" ")
            return raw

        with mock.patch.object(design, "stable_bytes", side_effect=mutate_after_read):
            with self.assertRaises(design.UnsafeInput):
                design.evaluate(self.doc)

    def test_missing_doc_is_usage_exit_64(self) -> None:
        self.doc.unlink()
        code, result, stderr = self.run_main()
        self.assertEqual(code, 64)
        self.assertIsNone(result)
        self.assertIn("MAGI_DESIGN_CONVERGENCE_USAGE", stderr)

    def test_argparse_errors_are_usage_exit_64(self) -> None:
        for argv in (
            ["magi_design_convergence_gate.py"],
            ["magi_design_convergence_gate.py", "unknown"],
            ["magi_design_convergence_gate.py", "evaluate", "--unknown"],
        ):
            stdout, stderr = io.StringIO(), io.StringIO()
            with (
                mock.patch.object(os.sys, "argv", argv),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                code = design.main()
            self.assertEqual(code, 64, argv)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("MAGI_DESIGN_CONVERGENCE_USAGE", stderr.getvalue())

    def test_changed_document_can_roll_over_retry_exhaustion(self) -> None:
        old_sha = self.current_sha()
        for _ in range(2):
            self.add_launch(
                round_no=1,
                phase="fanout",
                artifact_sha=old_sha,
                status="failed",
            )
        self.revise(2)
        result = self.assert_decision("CONTINUE", "DESIGN_REVIEW_REQUIRED")
        self.assertEqual(result["target_git_sha"], self.current_sha())

    def test_ledger_appearance_during_evaluation_fails_closed(self) -> None:
        ledger = self.control / f"CAMPAIGN.{guard.doc_id(self.doc.resolve())}.json"
        real_verify = design.verify_observed

        def create_then_verify(
            observed: dict[Path, str], absent_paths: tuple[Path, ...] = ()
        ) -> None:
            self.write_ledger()
            real_verify(observed, absent_paths)

        self.assertFalse(ledger.exists())
        with mock.patch.object(design, "verify_observed", side_effect=create_then_verify):
            with self.assertRaisesRegex(design.UnsafeInput, "appeared"):
                design.evaluate(self.doc)


if __name__ == "__main__":
    unittest.main()
