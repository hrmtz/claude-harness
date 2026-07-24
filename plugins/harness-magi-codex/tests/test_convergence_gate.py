#!/usr/bin/env python3
"""Tests for the report-only implementation convergence evaluator."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
SCRIPT = PLUGIN / "scripts" / "magi_convergence_gate.py"
PACKET = PLUGIN / "scripts" / "magi_review_packet.py"
sys.path.insert(0, str(PLUGIN / "scripts"))
import magi_campaign_guard as guard  # noqa: E402


def run(
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        args, cwd=cwd, text=True, capture_output=True, check=False, env=merged
    )


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ConvergenceGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("switch", "-qc", "dev")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")
        (self.repo / "base.txt").write_text("base\n")
        self.git("add", "base.txt")
        self.git("commit", "-qm", "base")
        self.base_sha = self.git("rev-parse", "HEAD")
        (self.repo / "implementation.py").write_text("VALUE = 1\n")
        (self.repo / "helper.py").write_text("HELPER = 1\n")
        self.git("add", "implementation.py", "helper.py")
        self.git("commit", "-qm", "implementation")
        self.target_sha = self.git("rev-parse", "HEAD")
        self.manifest = self.repo / "implementation-review.json"
        self.write_manifest()
        self.state = self.repo / "review-state"
        self.state.mkdir()
        self.fake_home = Path(self.temp.name) / "home"
        self.fake_home.mkdir()
        self.launches: list[dict[str, object]] = []

    def tearDown(self) -> None:
        self.temp.cleanup()

    def git(self, *args: str) -> str:
        result = run("git", "-C", str(self.repo), *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def write_manifest(
        self,
        *,
        scope: str = "issue-107",
        ceiling: int | None = None,
        historical: list[dict[str, str]] | None = None,
    ) -> None:
        diff = subprocess.run(
            [
                "git",
                "-C",
                str(self.repo),
                "diff",
                "--binary",
                "--full-index",
                self.base_sha,
                self.target_sha,
                "--",
                "helper.py",
                "implementation.py",
            ],
            capture_output=True,
            check=True,
        ).stdout
        payload: dict[str, object] = {
            "schema": "magi-implementation-convergence/v1",
            "scope_id": scope,
            "risk_class": "standard",
            "repository_root": str(self.repo),
            "target_git_sha": self.target_sha,
            "base_git_sha": self.base_sha,
            "changed_paths": ["helper.py", "implementation.py"],
            "affected_invariants": ["bounded-review-loop"],
            "review_packet": {
                "target_tree_sha": self.git("rev-parse", f"{self.target_sha}^{{tree}}"),
                "diff_sha256": hashlib.sha256(diff).hexdigest(),
                "diff": diff.decode(),
            },
            "wall_clock_deadline": (
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat(),
        }
        if ceiling is not None:
            payload["max_model_launches"] = ceiling
        if historical:
            payload["historical_manifests"] = historical
        self.manifest.write_text(json.dumps(payload, indent=2) + "\n")

    def archive_manifest(self) -> dict[str, str]:
        digest = file_sha(self.manifest)
        archive = self.repo / f"manifest-{digest}.json"
        archive.write_bytes(self.manifest.read_bytes())
        return {"path": str(archive), "artifact_sha": digest}

    def advance_target(self) -> None:
        (self.repo / "implementation.py").write_text("VALUE = 2\n")
        self.git("add", "implementation.py")
        self.git("commit", "-qm", "implementation revision 2")
        self.target_sha = self.git("rev-parse", "HEAD")

    def finding_payload(
        self,
        *,
        reviewer: str,
        round_no: int,
        artifact_sha: str,
        root: str | None = None,
        changes_design: bool = False,
        title: str = "blocking bug",
        subsystem: str = "orchestration",
        dup_flag: str = "new",
        relation: str = "new-root",
    ) -> dict[str, object]:
        findings: list[dict[str, object]] = []
        if root is not None:
            findings.append(
                {
                    "finding_id": f"{reviewer}-{round_no}-{root}",
                    "severity": "HIGH",
                    "title": title,
                    "location": "implementation.py:1",
                    "rationale": "fixture",
                    "required_fix": "fix it",
                    "confidence": "high",
                    "dup_flag": dup_flag,
                    "missed_angle": "fixture",
                    "subsystem": subsystem,
                    "root_cause_id": root,
                    "affected_invariant": "bounded-review-loop",
                    "changes_design_invariant": changes_design,
                    "relation_to_prior": relation,
                }
            )
        return {
            "reviewer": reviewer,
            "round": round_no,
            "artifact_id": guard.doc_id(self.manifest.resolve()),
            "artifact_sha": artifact_sha,
            "verdict": "REVISE" if findings else "GO",
            "schema_grounding_verdict": "PASS",
            "verify_commands_executed": ["git diff --check"],
            "source_artifacts": [],
            "dispositions": [],
            "findings": findings,
        }

    def add_launch(
        self,
        round_no: int,
        phase: str,
        artifact_sha: str,
        *,
        root: str | None = None,
        changes_design: bool = False,
        subsystem: str = "orchestration",
        dup_flag: str = "new",
        relation: str = "new-root",
        status: str = "success",
        state: Path | None = None,
    ) -> None:
        output_state = state or self.state
        launch = {
            "claim_id": f"claim-{len(self.launches) + 1}",
            "sequence": len(self.launches) + 1,
            "round": round_no,
            "phase": phase,
            "attempt": 1,
            "model_launches": guard.PHASE_WEIGHT[phase],
            "state_dir": str(output_state),
            "artifact_sha": artifact_sha,
            "protocol_sha": guard.protocol_sha(),
            "claimed_at": "2026-07-24T00:00:00+00:00",
            "status": status,
        }
        self.launches.append(launch)
        if status != "success":
            return
        if phase == "fanout":
            for persona in ("melchior", "balthasar", "caspar"):
                path = output_state / f"round_{round_no}_{persona}.json"
                path.write_text(
                    json.dumps(
                        self.finding_payload(
                            reviewer=persona.upper(),
                            round_no=round_no,
                            artifact_sha=artifact_sha,
                            root=root,
                            changes_design=changes_design,
                            subsystem=subsystem,
                            dup_flag=dup_flag,
                            relation=relation,
                        )
                    )
                    + "\n"
                )
        else:
            findings = output_state / f"round_{round_no}_xfamily.json"
            findings.write_text(
                json.dumps(
                    self.finding_payload(
                        reviewer="GROK",
                        round_no=round_no,
                        artifact_sha=artifact_sha,
                        root=root,
                        changes_design=changes_design,
                        subsystem=subsystem,
                        dup_flag=dup_flag,
                        relation=relation,
                    )
                )
                + "\n"
            )
            session_id = f"session-{round_no}"
            transcript = (
                self.fake_home
                / ".grok"
                / "sessions"
                / "fixture"
                / session_id
                / "chat_history.jsonl"
            )
            transcript.parent.mkdir(parents=True, exist_ok=True)
            transcript.write_text(
                json.dumps(
                    {
                        "type": "assistant",
                        "model_id": "grok-4.5-build",
                        "tool_calls": [{"name": "read_file"}],
                    }
                )
                + "\n"
            )
            (output_state / f"round_{round_no}_xfamily.meta.json").write_text(
                json.dumps(
                    {
                        "artifact_sha": artifact_sha,
                        "output_sha": file_sha(findings),
                        "reviewer_family": "grok",
                        "model_id": "grok-4.5-build",
                        "model_usage_keys": ["grok-4.5-build"],
                        "requested_model": "grok-4.5",
                        "session_id": session_id,
                        "transcript_path": str(transcript),
                        "transcript_sha": file_sha(transcript),
                        "num_turns": 2,
                    }
                )
                + "\n"
            )

    def write_ledger(self) -> Path:
        control = self.manifest.parent / ".dual-magi"
        control.mkdir(exist_ok=True)
        path = control / f"CAMPAIGN.{guard.doc_id(self.manifest.resolve())}.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "doc_id": guard.doc_id(self.manifest.resolve()),
                    "doc_path": str(self.manifest.resolve()),
                    "campaigns": [
                        {
                            "campaign_id": "fixture-campaign",
                            "started_at": "2026-07-24T00:00:00+00:00",
                            "started_by": "fixture",
                            "reason": "test",
                            "launches": self.launches,
                        }
                    ],
                },
                indent=2,
            )
            + "\n"
        )
        return path

    def evaluate(self) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        result = run(
            "python3",
            str(SCRIPT),
            "evaluate",
            str(self.manifest),
            env={"HOME": str(self.fake_home)},
        )
        payload = json.loads(result.stdout) if result.stdout else {}
        return result, payload

    def test_no_ledger_requests_initial_full_without_writes(self) -> None:
        before = self.git("status", "--porcelain=v1", "--untracked-files=all")
        result, payload = self.evaluate()
        after = self.git("status", "--porcelain=v1", "--untracked-files=all")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["decision"], "CONTINUE")
        self.assertEqual(payload["next_mode"], "initial-full")
        self.assertFalse(payload["authorizes_shipping"])
        self.assertEqual(before, after)
        self.assertFalse((self.repo / ".dual-magi").exists())

    def test_packet_builder_archives_previous_exact_revision(self) -> None:
        previous_sha = file_sha(self.manifest)
        self.advance_target()
        deadline = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        built = run(
            "python3",
            str(PACKET),
            "--repo",
            str(self.repo),
            "--base",
            self.base_sha,
            "--target",
            self.target_sha,
            "--scope",
            "issue-107-revision-2",
            "--invariant",
            "bounded-review-loop",
            "--deadline",
            deadline,
            "--output",
            str(self.manifest),
        )
        self.assertEqual(built.returncode, 0, built.stderr)
        payload = json.loads(self.manifest.read_text())
        self.assertEqual(payload["target_git_sha"], self.target_sha)
        self.assertEqual(
            payload["historical_manifests"][0]["artifact_sha"], previous_sha
        )
        archive = Path(payload["historical_manifests"][0]["path"])
        self.assertEqual(file_sha(archive), previous_sha)
        result, decision = self.evaluate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(decision["decision"], "CONTINUE")

    def test_fanout_requires_reserved_final_diverse_review(self) -> None:
        artifact = file_sha(self.manifest)
        self.add_launch(1, "fanout", artifact, root="root-a")
        self.write_ledger()
        result, payload = self.evaluate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["decision"], "FINAL_REVIEW_REQUIRED")
        self.assertEqual(payload["next_mode"], "final-full")
        self.assertEqual(payload["usage"], 3)

    def test_one_blocking_cycle_requests_fix(self) -> None:
        artifact = file_sha(self.manifest)
        self.add_launch(1, "fanout", artifact, root="root-a")
        self.add_launch(2, "xfamily", artifact, root="root-a")
        self.write_ledger()
        result, payload = self.evaluate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["decision"], "CONTINUE")
        self.assertEqual(payload["next_mode"], "full-target-fix")
        self.assertEqual(payload["blocker_mass"], 4)

    def test_design_invariant_change_redesigns(self) -> None:
        artifact = file_sha(self.manifest)
        self.add_launch(1, "fanout", artifact, root="root-a", changes_design=True)
        self.add_launch(2, "xfamily", artifact, root="root-a", changes_design=True)
        self.write_ledger()
        result, payload = self.evaluate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["decision"], "REDESIGN")
        self.assertEqual(payload["reason_code"], "DESIGN_INVARIANT_CHANGED")

    def test_repeated_explicit_root_redesigns(self) -> None:
        old_artifact = file_sha(self.manifest)
        self.add_launch(1, "fanout", old_artifact, root="root-a")
        self.add_launch(2, "xfamily", old_artifact, root="root-a")
        historical = [self.archive_manifest()]
        self.advance_target()
        self.write_manifest(scope="issue-107-revision-2", historical=historical)
        current_artifact = file_sha(self.manifest)
        self.add_launch(3, "fanout", current_artifact, root="root-a")
        self.add_launch(4, "xfamily", current_artifact, root="root-a")
        self.write_ledger()
        result, payload = self.evaluate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["decision"], "REDESIGN")
        self.assertEqual(payload["reason_code"], "BLOCKING_ROOT_REPEATED")

    def test_two_cycles_with_distinct_blockers_stop(self) -> None:
        old_artifact = file_sha(self.manifest)
        self.add_launch(1, "fanout", old_artifact, root="root-a", subsystem="parser")
        self.add_launch(2, "xfamily", old_artifact, root="root-a", subsystem="parser")
        historical = [self.archive_manifest()]
        self.advance_target()
        self.write_manifest(scope="issue-107-revision-2", historical=historical)
        current_artifact = file_sha(self.manifest)
        self.add_launch(3, "fanout", current_artifact, root="root-b", subsystem="scheduler")
        self.add_launch(4, "xfamily", current_artifact, root="root-b", subsystem="scheduler")
        self.write_ledger()
        result, payload = self.evaluate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertEqual(payload["reason_code"], "MAX_LOGICAL_CYCLES_WITH_BLOCKERS")
        self.assertEqual(payload["logical_cycles"], 2)
        self.assertEqual(payload["new_blocking_roots"], ["root-b"])
        self.assertEqual(payload["resolved_blocking_roots"], ["root-a"])

    def test_two_pairs_on_unchanged_target_count_as_two_cycles(self) -> None:
        artifact = file_sha(self.manifest)
        self.add_launch(1, "fanout", artifact, root="root-a", subsystem="parser")
        self.add_launch(2, "xfamily", artifact, root="root-a", subsystem="parser")
        self.add_launch(3, "fanout", artifact, root="root-b", subsystem="scheduler")
        self.add_launch(4, "xfamily", artifact, root="root-b", subsystem="scheduler")
        self.write_ledger()
        result, payload = self.evaluate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertEqual(payload["logical_cycles"], 2)

    def test_consecutive_fix_induced_regressions_redesign(self) -> None:
        old_artifact = file_sha(self.manifest)
        self.add_launch(
            1,
            "fanout",
            old_artifact,
            root="regression-a",
            subsystem="parser",
            dup_flag="regression",
            relation="fix-induced-regression",
        )
        self.add_launch(
            2,
            "xfamily",
            old_artifact,
            root="regression-a",
            subsystem="parser",
            dup_flag="regression",
            relation="fix-induced-regression",
        )
        historical = [self.archive_manifest()]
        self.advance_target()
        self.write_manifest(historical=historical)
        current_artifact = file_sha(self.manifest)
        self.add_launch(
            3,
            "fanout",
            current_artifact,
            root="regression-b",
            subsystem="scheduler",
            dup_flag="regression",
            relation="fix-induced-regression",
        )
        self.add_launch(
            4,
            "xfamily",
            current_artifact,
            root="regression-b",
            subsystem="scheduler",
            dup_flag="regression",
            relation="fix-induced-regression",
        )
        self.write_ledger()
        result, payload = self.evaluate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["decision"], "REDESIGN")
        self.assertEqual(
            payload["reason_code"], "CONSECUTIVE_FIX_INDUCED_REGRESSIONS"
        )

    def test_same_subsystem_new_roots_redesign(self) -> None:
        old_artifact = file_sha(self.manifest)
        self.add_launch(1, "fanout", old_artifact, root="root-a", subsystem="parser")
        self.add_launch(2, "xfamily", old_artifact, root="root-a", subsystem="parser")
        historical = [self.archive_manifest()]
        self.advance_target()
        self.write_manifest(historical=historical)
        current_artifact = file_sha(self.manifest)
        self.add_launch(3, "fanout", current_artifact, root="root-b", subsystem="parser")
        self.add_launch(4, "xfamily", current_artifact, root="root-b", subsystem="parser")
        self.write_ledger()
        result, payload = self.evaluate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["decision"], "REDESIGN")
        self.assertEqual(payload["reason_code"], "SAME_SUBSYSTEM_NEW_ROOTS_RECURRED")

    def test_three_revision_non_decreasing_mass_blocks(self) -> None:
        first_artifact = file_sha(self.manifest)
        self.add_launch(1, "fanout", first_artifact, root="root-a", subsystem="parser")
        self.add_launch(2, "xfamily", first_artifact, root="root-a", subsystem="parser")
        history = [self.archive_manifest()]

        self.advance_target()
        self.write_manifest(scope="revision-2", historical=history)
        second_artifact = file_sha(self.manifest)
        self.add_launch(3, "fanout", second_artifact, root="root-b", subsystem="scheduler")
        self.add_launch(4, "xfamily", second_artifact, root="root-b", subsystem="scheduler")
        history.append(self.archive_manifest())

        (self.repo / "implementation.py").write_text("VALUE = 3\n")
        self.git("add", "implementation.py")
        self.git("commit", "-qm", "implementation revision 3")
        self.target_sha = self.git("rev-parse", "HEAD")
        self.write_manifest(scope="revision-3", historical=history)
        third_artifact = file_sha(self.manifest)
        self.add_launch(5, "fanout", third_artifact, root="root-c", subsystem="storage")
        self.add_launch(6, "xfamily", third_artifact, root="root-c", subsystem="storage")
        self.write_ledger()

        result, payload = self.evaluate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertEqual(payload["reason_code"], "BLOCKER_MASS_STALLED")

    def test_third_cycle_cannot_start_after_two_completed_pairs(self) -> None:
        artifact = file_sha(self.manifest)
        self.add_launch(1, "fanout", artifact, root="root-a", subsystem="parser")
        self.add_launch(2, "xfamily", artifact, root="root-a", subsystem="parser")
        self.add_launch(3, "fanout", artifact, root="root-b", subsystem="scheduler")
        self.add_launch(4, "xfamily", artifact, root="root-b", subsystem="scheduler")
        history = [self.archive_manifest()]
        self.advance_target()
        self.write_manifest(scope="third-cycle", historical=history)
        self.write_ledger()
        result, payload = self.evaluate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertEqual(payload["reason_code"], "MAX_LOGICAL_CYCLES_REACHED")

    def test_failed_claim_charges_but_is_not_evidence(self) -> None:
        artifact = file_sha(self.manifest)
        self.add_launch(1, "fanout", artifact, status="failed")
        self.write_ledger()
        result, payload = self.evaluate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["usage"], 3)
        self.assertEqual(payload["decision"], "CONTINUE")
        self.assertEqual(payload["next_mode"], "initial-full")

    def test_fanout_retry_exhaustion_is_terminal(self) -> None:
        artifact = file_sha(self.manifest)
        self.add_launch(1, "fanout", artifact, status="failed")
        self.add_launch(1, "fanout", artifact, status="failed")
        self.write_ledger()
        result, payload = self.evaluate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertEqual(payload["reason_code"], "RETRY_BUDGET_EXHAUSTED")

    def test_xfamily_retry_exhaustion_is_terminal(self) -> None:
        artifact = file_sha(self.manifest)
        self.add_launch(1, "fanout", artifact, root="root-a")
        self.add_launch(2, "xfamily", artifact, status="failed")
        self.add_launch(2, "xfamily", artifact, status="failed")
        self.write_ledger()
        result, payload = self.evaluate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertEqual(payload["reason_code"], "RETRY_BUDGET_EXHAUSTED")

    def test_budget_preserves_final_reserve(self) -> None:
        self.write_manifest(ceiling=3)
        self.write_ledger()
        result, payload = self.evaluate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertEqual(payload["reason_code"], "NEXT_FANOUT_UNAFFORDABLE")

    def test_clean_review_never_emits_pass(self) -> None:
        artifact = file_sha(self.manifest)
        self.add_launch(1, "fanout", artifact)
        self.add_launch(2, "xfamily", artifact)
        self.write_ledger()
        result, payload = self.evaluate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertEqual(payload["reason_code"], "REPORT_ONLY_READY_FOR_EXISTING_PLATEAU_GATE")
        self.assertFalse(payload["authorizes_shipping"])
        self.assertNotIn("PASS", json.dumps(payload))

    def test_stale_target_sha_fails_closed(self) -> None:
        payload = json.loads(self.manifest.read_text())
        payload["target_git_sha"] = self.base_sha
        self.manifest.write_text(json.dumps(payload))
        result, blocked = self.evaluate()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(blocked["decision"], "BLOCKED")
        self.assertEqual(blocked["reason_code"], "UNSAFE_OR_INCOMPLETE_INPUT")

    def test_tampered_embedded_diff_fails_closed(self) -> None:
        payload = json.loads(self.manifest.read_text())
        payload["review_packet"]["diff"] += "\nforged\n"
        self.manifest.write_text(json.dumps(payload))
        result, blocked = self.evaluate()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(blocked["decision"], "BLOCKED")

    def test_historical_success_without_archived_manifest_fails_closed(self) -> None:
        old_artifact = file_sha(self.manifest)
        self.add_launch(1, "fanout", old_artifact, root="root-a")
        self.add_launch(2, "xfamily", old_artifact, root="root-a")
        self.advance_target()
        self.write_manifest(scope="missing-history")
        self.write_ledger()
        result, blocked = self.evaluate()
        self.assertEqual(result.returncode, 2)
        self.assertIn("historical manifest archive missing", blocked["detail"])

    def test_historical_packet_cannot_omit_a_changed_path(self) -> None:
        incomplete = json.loads(self.manifest.read_text())
        incomplete["changed_paths"] = ["implementation.py"]
        diff = subprocess.run(
            [
                "git",
                "-C",
                str(self.repo),
                "diff",
                "--binary",
                "--full-index",
                self.base_sha,
                self.target_sha,
                "--",
                "implementation.py",
            ],
            capture_output=True,
            check=True,
        ).stdout
        incomplete["review_packet"]["diff"] = diff.decode()
        incomplete["review_packet"]["diff_sha256"] = hashlib.sha256(diff).hexdigest()
        self.manifest.write_text(json.dumps(incomplete, indent=2) + "\n")
        old_artifact = file_sha(self.manifest)
        self.add_launch(1, "fanout", old_artifact, root="root-a")
        self.add_launch(2, "xfamily", old_artifact, root="root-a")
        historical = [self.archive_manifest()]
        self.advance_target()
        self.write_manifest(scope="complete-current", historical=historical)
        self.write_ledger()
        result, blocked = self.evaluate()
        self.assertEqual(result.returncode, 2)
        self.assertIn("changed_paths does not exactly cover", blocked["detail"])

    def test_review_target_history_rejects_rollback_order(self) -> None:
        target_one_artifact = file_sha(self.manifest)
        target_one_archive = self.archive_manifest()

        self.advance_target()
        self.write_manifest(scope="target-two", historical=[target_one_archive])
        target_two_artifact = file_sha(self.manifest)
        target_two_archive = self.archive_manifest()

        self.add_launch(
            1, "fanout", target_two_artifact, root="root-two", subsystem="scheduler"
        )
        self.add_launch(
            2, "xfamily", target_two_artifact, root="root-two", subsystem="scheduler"
        )
        self.add_launch(
            3, "fanout", target_one_artifact, root="root-one", subsystem="parser"
        )
        self.add_launch(
            4, "xfamily", target_one_artifact, root="root-one", subsystem="parser"
        )

        (self.repo / "implementation.py").write_text("VALUE = 3\n")
        self.git("add", "implementation.py")
        self.git("commit", "-qm", "implementation revision 3")
        self.target_sha = self.git("rev-parse", "HEAD")
        self.write_manifest(
            scope="target-three",
            historical=[target_one_archive, target_two_archive],
        )
        self.write_ledger()

        result, blocked = self.evaluate()
        self.assertEqual(result.returncode, 2)
        self.assertIn("ordered ancestry chain", blocked["detail"])

    def test_expired_deadline_is_terminal_not_corruption(self) -> None:
        payload = json.loads(self.manifest.read_text())
        payload["wall_clock_deadline"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        self.manifest.write_text(json.dumps(payload))
        result, blocked = self.evaluate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(blocked["decision"], "BLOCKED")
        self.assertEqual(blocked["reason_code"], "WALL_CLOCK_DEADLINE_EXPIRED")

    def test_missing_success_artifact_fails_closed(self) -> None:
        artifact = file_sha(self.manifest)
        self.add_launch(1, "fanout", artifact)
        (self.state / "round_1_caspar.json").unlink()
        self.write_ledger()
        result, payload = self.evaluate()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(payload["decision"], "BLOCKED")

    def test_usage_error_is_64(self) -> None:
        result = run("python3", str(SCRIPT), "evaluate", str(self.repo / "missing.json"))
        self.assertEqual(result.returncode, 64)


if __name__ == "__main__":
    unittest.main()
