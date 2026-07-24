#!/usr/bin/env python3
"""Tests for the report-only implementation convergence evaluator."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
SCRIPT = PLUGIN / "scripts" / "magi_convergence_gate.py"
PACKET = PLUGIN / "scripts" / "magi_review_packet.py"
FANOUT = PLUGIN / "scripts" / "magi_fanout_codex.sh"
VALIDATOR = PLUGIN / "scripts" / "magi_validate_findings.py"
sys.path.insert(0, str(PLUGIN / "scripts"))
import magi_campaign_guard as guard  # noqa: E402
import magi_convergence_gate as convergence  # noqa: E402


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
    def test_protocol_digest_covers_load_bearing_convergence_files(self) -> None:
        expected = {
            "schemas/finding.schema.json",
            "schemas/implementation-convergence.schema.json",
            "scripts/magi_campaign_guard.py",
            "scripts/magi_classify_failure.py",
            "scripts/magi_convergence_gate.py",
            "scripts/magi_convergence_kernel.py",
            "scripts/magi_design_convergence_gate.py",
            "scripts/magi_fanout_codex.sh",
            "scripts/magi_git.py",
            "scripts/magi_lock.sh",
            "scripts/magi_plateau_gate.sh",
            "scripts/magi_review_packet.py",
            "scripts/magi_scrub.py",
            "scripts/magi_validate_findings.py",
            "scripts/magi_verify_round.py",
            "scripts/magi_xfamily.sh",
            "scripts/magi_xfamily_claude.sh",
        }
        self.assertEqual(set(guard.PROTOCOL_FILES), expected)

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
        review_base: str | None = None,
        incremental: bool = False,
        surface_changes: dict[str, bool] | None = None,
    ) -> None:
        packet_base = review_base or self.base_sha
        changed_paths = self.git(
            "diff",
            "--name-only",
            packet_base,
            self.target_sha,
            "--",
        ).splitlines()
        diff = subprocess.run(
            [
                "git",
                "-C",
                str(self.repo),
                "diff",
                "--binary",
                "--full-index",
                packet_base,
                self.target_sha,
                "--",
                *changed_paths,
            ],
            capture_output=True,
            check=True,
        ).stdout
        payload: dict[str, object] = {
            "schema": "magi-implementation-convergence/v1",
            "scope_id": scope,
            "implementation_campaign_id": "11111111-2222-4333-8444-555555555555",
            "canonical_control_path": str(self.manifest.resolve()),
            "risk_class": "standard",
            "repository_root": str(self.repo),
            "target_git_sha": self.target_sha,
            "base_git_sha": self.base_sha,
            "review_base_git_sha": packet_base,
            "changed_paths": changed_paths,
            "affected_invariants": ["bounded-review-loop"],
            "incremental_review": {
                "eligible": incremental,
                "changed_loc": sum(
                    int(value)
                    for line in self.git(
                        "diff",
                        "--numstat",
                        packet_base,
                        self.target_sha,
                        "--",
                        *changed_paths,
                    ).splitlines()
                    for value in line.split("\t", 2)[:2]
                    if value != "-"
                ),
                "surface_changes": surface_changes
                or {
                    "public_interface": False,
                    "trust_boundary": False,
                    "persistence_schema_rollback": False,
                    "design_invariant": False,
                },
            },
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
        elif phase == "targeted":
            payload = self.finding_payload(
                reviewer="gnat",
                round_no=round_no,
                artifact_sha=artifact_sha,
                root=root,
                changes_design=changes_design,
                subsystem=subsystem,
                dup_flag=dup_flag,
                relation=relation,
            )
            (output_state / f"round_{round_no}_targeted.json").write_text(
                json.dumps(payload) + "\n"
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

    def test_legacy_v1_packet_without_incremental_fields_remains_full_review(self) -> None:
        payload = json.loads(self.manifest.read_text())
        payload.pop("review_base_git_sha")
        payload.pop("incremental_review")
        self.manifest.write_text(json.dumps(payload, indent=2) + "\n")

        result, decision = self.evaluate()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(decision["next_mode"], "initial-full")
        self.assertEqual(decision["next_persona"], None)

    def test_packet_builder_archives_previous_exact_revision(self) -> None:
        previous_sha = file_sha(self.manifest)
        previous_target = self.target_sha
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
            "issue-107",
            "--invariant",
            "bounded-review-loop",
            "--deadline",
            deadline,
            "--allow-incremental",
            "--output",
            str(self.manifest),
        )
        self.assertEqual(built.returncode, 0, built.stderr)
        payload = json.loads(self.manifest.read_text())
        self.assertEqual(payload["target_git_sha"], self.target_sha)
        self.assertEqual(payload["review_base_git_sha"], previous_target)
        self.assertEqual(payload["changed_paths"], ["implementation.py"])
        self.assertTrue(payload["incremental_review"]["eligible"])
        self.assertEqual(payload["incremental_review"]["changed_loc"], 2)
        self.assertEqual(
            payload["implementation_campaign_id"],
            "11111111-2222-4333-8444-555555555555",
        )
        self.assertEqual(
            payload["historical_manifests"][0]["artifact_sha"], previous_sha
        )
        archive = Path(payload["historical_manifests"][0]["path"])
        self.assertEqual(file_sha(archive), previous_sha)
        result, decision = self.evaluate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(decision["decision"], "CONTINUE")

    def test_copied_packet_cannot_reset_control_path(self) -> None:
        copied = self.repo / "copied-review.json"
        copied.write_bytes(self.manifest.read_bytes())
        result = run(
            "python3",
            str(SCRIPT),
            "evaluate",
            str(copied),
            env={"HOME": str(self.fake_home)},
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("canonical_control_path", result.stdout)

    def test_packet_builder_rejects_scope_drift_at_stable_path(self) -> None:
        before = file_sha(self.manifest)
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
            "different-scope",
            "--invariant",
            "bounded-review-loop",
            "--deadline",
            deadline,
            "--output",
            str(self.manifest),
        )
        self.assertEqual(built.returncode, 2)
        self.assertEqual(file_sha(self.manifest), before)
        self.assertFalse((self.repo / ".dual-magi").exists())

    def test_git_external_diff_is_not_executed(self) -> None:
        helper = Path(self.temp.name) / "external-diff"
        sentinel = Path(self.temp.name) / "external-diff-ran"
        helper.write_text(f"#!/bin/sh\ntouch {sentinel}\nexit 99\n")
        helper.chmod(0o755)
        result, payload = self.evaluate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["decision"], "CONTINUE")
        external_result = run(
            "python3",
            str(SCRIPT),
            "evaluate",
            str(self.manifest),
            env={
                "HOME": str(self.fake_home),
                "GIT_EXTERNAL_DIFF": str(helper),
                "SECRET_SENTINEL": "must-not-reach-helper",
            },
        )
        self.assertEqual(external_result.returncode, 0, external_result.stderr)
        self.assertFalse(sentinel.exists())

    def test_git_replace_cannot_substitute_named_commit_objects(self) -> None:
        canonical = json.loads(self.manifest.read_text())
        (self.repo / "implementation.py").write_text("VALUE = 999\n")
        self.git("add", "implementation.py")
        self.git("commit", "-qm", "replacement object")
        replacement_sha = self.git("rev-parse", "HEAD")
        self.git("reset", "--hard", self.target_sha)
        self.git("replace", self.target_sha, replacement_sha)

        replaced_tree = self.git("rev-parse", f"{self.target_sha}^{{tree}}")
        self.assertNotEqual(replaced_tree, canonical["review_packet"]["target_tree_sha"])

        result, payload = self.evaluate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["decision"], "CONTINUE")

        generated = self.repo / "replacement-review.json"
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
            "replacement-proof",
            "--invariant",
            "bounded-review-loop",
            "--deadline",
            deadline,
            "--output",
            str(generated),
        )
        self.assertEqual(built.returncode, 0, built.stderr)
        generated_packet = json.loads(generated.read_text())["review_packet"]
        self.assertEqual(
            generated_packet["target_tree_sha"],
            canonical["review_packet"]["target_tree_sha"],
        )
        self.assertEqual(
            generated_packet["diff_sha256"],
            canonical["review_packet"]["diff_sha256"],
        )
        self.assertEqual(generated_packet["diff"], canonical["review_packet"]["diff"])

    def test_fanout_requires_reserved_final_diverse_review(self) -> None:
        artifact = file_sha(self.manifest)
        self.add_launch(1, "fanout", artifact, root="root-a")
        ledger_path = self.write_ledger()
        result, payload = self.evaluate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["decision"], "FINAL_REVIEW_REQUIRED")
        self.assertEqual(payload["next_mode"], "final-full")
        self.assertEqual(payload["usage"], 3)

    def test_small_fix_revision_uses_weight_one_targeted_review(self) -> None:
        old_artifact = file_sha(self.manifest)
        old_target = self.target_sha
        self.add_launch(1, "fanout", old_artifact, root="root-a")
        self.add_launch(2, "xfamily", old_artifact, root="root-a")
        history = [self.archive_manifest()]
        self.advance_target()
        self.write_manifest(
            historical=history,
            review_base=old_target,
            incremental=True,
        )
        self.write_ledger()

        result, payload = self.evaluate()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["decision"], "CONTINUE")
        self.assertEqual(payload["next_mode"], "incremental-fix")
        self.assertEqual(payload["reason_code"], "INCREMENTAL_FIX_REVIEW_REQUIRED")
        self.assertEqual(payload["next_persona"], "gnat")
        self.assertEqual(payload["prior_blocking_roots"], ["root-a"])
        self.assertEqual(payload["usage"], 4)

    def test_incremental_targeted_review_still_requires_final_diverse_review(self) -> None:
        old_artifact = file_sha(self.manifest)
        old_target = self.target_sha
        self.add_launch(1, "fanout", old_artifact, root="root-a")
        self.add_launch(2, "xfamily", old_artifact, root="root-a")
        history = [self.archive_manifest()]
        self.advance_target()
        self.write_manifest(
            historical=history,
            review_base=old_target,
            incremental=True,
        )
        current_artifact = file_sha(self.manifest)
        self.add_launch(1, "targeted", current_artifact)
        self.write_ledger()

        result, payload = self.evaluate()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["decision"], "FINAL_REVIEW_REQUIRED")
        self.assertEqual(payload["next_mode"], "final-full")
        self.assertEqual(payload["usage"], 5)

    def test_incremental_adapter_runs_one_authorized_persona_and_charges_one(self) -> None:
        old_artifact = file_sha(self.manifest)
        old_target = self.target_sha
        self.add_launch(1, "fanout", old_artifact, root="root-a")
        self.add_launch(2, "xfamily", old_artifact, root="root-a")
        history = [self.archive_manifest()]
        self.advance_target()
        self.write_manifest(
            historical=history,
            review_base=old_target,
            incremental=True,
        )
        ledger_path = self.write_ledger()
        stub_bin = Path(self.temp.name) / "bin"
        stub_bin.mkdir()
        codex = stub_bin / "codex"
        codex.write_text(
            """#!/usr/bin/env python3
import json, re, sys
args = sys.argv[1:]
if args == ["exec", "--help"]:
    print("--output-schema --output-last-message --ephemeral")
    raise SystemExit(0)
out = args[args.index("-o") + 1]
prompt = sys.stdin.read()
def field(name):
    return re.search(rf"^{name}: (.+)$", prompt, re.M).group(1)
payload = {
    "reviewer": "GNAT",
    "round": int(field("ROUND")),
    "artifact_id": field("ARTIFACT ID"),
    "artifact_sha": field("ARTIFACT SHA256"),
    "verdict": "GO",
    "schema_grounding_verdict": "PASS",
    "verify_commands_executed": ["fixture exact-diff check"],
    "source_artifacts": [],
    "dispositions": [],
    "findings": [],
}
open(out, "w").write(json.dumps(payload) + "\\n")
print("incremental fixture")
"""
        )
        codex.chmod(0o755)
        output_state = self.repo / "incremental-state"

        result = run(
            str(FANOUT),
            str(self.manifest),
            "1",
            str(output_state),
            "--persona-set",
            "bug-hunt",
            "--review-mode",
            "incremental",
            env={
                "PATH": f"{stub_bin}:{os.environ['PATH']}",
                "HOME": str(self.fake_home),
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((output_state / "round_1_targeted.json").is_file())
        synthesis = output_state / "round_1_codex.json"
        self.assertTrue(synthesis.is_file())
        self.assertFalse((output_state / "round_1_hornet.json").exists())
        self.assertFalse((output_state / "round_1_wasp.json").exists())
        prior = run(
            "python3",
            str(VALIDATOR),
            str(synthesis),
            "--same-doc",
            str(self.manifest),
            "--prior-for-round",
            "2",
            "--state-dir",
            str(output_state),
        )
        self.assertEqual(prior.returncode, 0, prior.stderr)
        ledger = json.loads(ledger_path.read_text())
        launches = [
            launch
            for campaign in ledger["campaigns"]
            for launch in campaign["launches"]
        ]
        self.assertEqual(launches[-1]["phase"], "targeted")
        self.assertEqual(launches[-1]["model_launches"], 1)

    def test_surface_change_forces_full_fanout(self) -> None:
        old_artifact = file_sha(self.manifest)
        old_target = self.target_sha
        self.add_launch(1, "fanout", old_artifact, root="root-a")
        self.add_launch(2, "xfamily", old_artifact, root="root-a")
        history = [self.archive_manifest()]
        self.advance_target()
        self.write_manifest(
            historical=history,
            review_base=old_target,
            incremental=False,
            surface_changes={
                "public_interface": True,
                "trust_boundary": False,
                "persistence_schema_rollback": False,
                "design_invariant": False,
            },
        )
        self.write_ledger()

        result, payload = self.evaluate()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["next_mode"], "initial-full")
        self.assertEqual(payload["next_persona"], None)

    def test_requirement_revision_forces_full_even_for_small_diff(self) -> None:
        old_artifact = file_sha(self.manifest)
        old_target = self.target_sha
        self.add_launch(1, "fanout", old_artifact, root="root-a")
        self.add_launch(2, "xfamily", old_artifact, root="root-a")
        self.add_launch(
            3,
            "fanout",
            old_artifact,
            status="superseded-by-requirement-revision",
        )
        self.launches[-1]["cancellation"] = {
            "expected_artifact_sha": old_artifact,
            "reason": "fixture requirement revision",
            "requested_at": "2026-07-24T00:01:00+00:00",
            "term_timeout_s": 1,
            "kill_timeout_s": 1,
            "inventory": [],
            "cleanup": "complete",
            "cleanup_detail": "",
            "completed_at": "2026-07-24T00:02:00+00:00",
        }
        history = [self.archive_manifest()]
        self.advance_target()
        self.write_manifest(
            historical=history,
            review_base=old_target,
            incremental=True,
        )
        self.write_ledger()

        result, payload = self.evaluate()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["next_mode"], "initial-full")
        self.assertEqual(payload["reason_code"], "INITIAL_FULL_REQUIRED")

    def test_forged_incremental_changed_loc_fails_closed(self) -> None:
        old_artifact = file_sha(self.manifest)
        old_target = self.target_sha
        self.add_launch(1, "fanout", old_artifact, root="root-a")
        self.add_launch(2, "xfamily", old_artifact, root="root-a")
        history = [self.archive_manifest()]
        self.advance_target()
        self.write_manifest(
            historical=history,
            review_base=old_target,
            incremental=True,
        )
        payload = json.loads(self.manifest.read_text())
        payload["incremental_review"]["changed_loc"] += 1
        self.manifest.write_text(json.dumps(payload, indent=2) + "\n")
        self.write_ledger()

        result, blocked = self.evaluate()

        self.assertEqual(result.returncode, 2)
        self.assertEqual(blocked["decision"], "BLOCKED")
        self.assertIn("changed_loc does not match", blocked["detail"])

    def test_stale_protocol_fanout_requires_initial_full_rollover(self) -> None:
        artifact = file_sha(self.manifest)
        self.add_launch(1, "fanout", artifact)
        self.launches[-1]["protocol_sha"] = "stale-protocol"
        self.write_ledger()

        result, payload = self.evaluate()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["decision"], "CONTINUE")
        self.assertEqual(payload["reason_code"], "INITIAL_FULL_REQUIRED")
        self.assertEqual(payload["next_mode"], "initial-full")

    def test_stale_protocol_xfamily_requires_initial_full_rollover(self) -> None:
        artifact = file_sha(self.manifest)
        self.add_launch(1, "fanout", artifact)
        self.add_launch(2, "xfamily", artifact)
        for launch in self.launches:
            launch["protocol_sha"] = "stale-protocol"
        self.write_ledger()

        result, payload = self.evaluate()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["decision"], "CONTINUE")
        self.assertEqual(payload["reason_code"], "INITIAL_FULL_REQUIRED")
        self.assertEqual(payload["next_mode"], "initial-full")

    def test_stale_protocol_retry_exhaustion_requires_rollover(self) -> None:
        artifact = file_sha(self.manifest)
        self.add_launch(1, "fanout", artifact, status="failed")
        self.add_launch(1, "fanout", artifact, status="failed")
        for launch in self.launches:
            launch["protocol_sha"] = "stale-protocol"
        self.write_ledger()

        result, payload = self.evaluate()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["decision"], "CONTINUE")
        self.assertEqual(payload["reason_code"], "INITIAL_FULL_REQUIRED")
        self.assertEqual(payload["next_mode"], "initial-full")

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
        self.write_manifest(historical=historical)
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
        self.write_manifest(historical=historical)
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
        self.write_manifest(historical=history)
        second_artifact = file_sha(self.manifest)
        self.add_launch(3, "fanout", second_artifact, root="root-b", subsystem="scheduler")
        self.add_launch(4, "xfamily", second_artifact, root="root-b", subsystem="scheduler")
        history.append(self.archive_manifest())

        (self.repo / "implementation.py").write_text("VALUE = 3\n")
        self.git("add", "implementation.py")
        self.git("commit", "-qm", "implementation revision 3")
        self.target_sha = self.git("rev-parse", "HEAD")
        self.write_manifest(historical=history)
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
        self.write_manifest(historical=history)
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

    def test_cancellation_in_progress_blocks_evaluation_and_stays_charged(self) -> None:
        artifact = file_sha(self.manifest)
        self.add_launch(
            1,
            "fanout",
            artifact,
            status="cancellation_in_progress",
        )
        self.launches[-1]["cancellation"] = {
            "expected_artifact_sha": artifact,
            "reason": "fixture requirement revision",
            "requested_at": "2026-07-24T00:01:00+00:00",
            "term_timeout_s": 1,
            "kill_timeout_s": 1,
            "inventory": [],
            "cleanup": "pending",
            "cleanup_detail": "fixture cleanup pending",
        }
        self.write_ledger()

        result, payload = self.evaluate()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertEqual(
            payload["reason_code"],
            "REQUIREMENT_REVISION_CANCELLATION_IN_PROGRESS",
        )
        self.assertEqual(payload["usage"], 3)

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
        self.write_manifest()
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
        self.write_manifest(historical=historical)
        self.write_ledger()
        result, blocked = self.evaluate()
        self.assertEqual(result.returncode, 2)
        self.assertIn("changed_paths does not exactly cover", blocked["detail"])

    def test_review_target_history_rejects_rollback_order(self) -> None:
        target_one_artifact = file_sha(self.manifest)
        target_one_archive = self.archive_manifest()

        self.advance_target()
        self.write_manifest(historical=[target_one_archive])
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
        self.write_manifest(historical=[target_one_archive, target_two_archive])
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

    def test_argparse_errors_are_usage_exit_64(self) -> None:
        for args in ((), ("unknown",), ("evaluate", "--unknown")):
            with self.subTest(args=args):
                result = run("python3", str(SCRIPT), *args)
                self.assertEqual(result.returncode, 64)
                self.assertIn("MAGI_CONVERGENCE_USAGE:", result.stderr)

    def test_noninteger_guard_ceiling_is_stable_usage_error(self) -> None:
        result = run(
            "python3",
            str(SCRIPT),
            "evaluate",
            str(self.manifest),
            env={"MAGI_MAX_AUTONOMOUS_MODEL_LAUNCHES": "invalid"},
        )
        self.assertEqual(result.returncode, 64)
        self.assertEqual(result.stdout, "")
        self.assertIn("MAGI_CONVERGENCE_USAGE:", result.stderr)
        self.assertIn("must be an integer", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_over_limit_guard_ceiling_is_stable_usage_error(self) -> None:
        result = run(
            "python3",
            str(SCRIPT),
            "evaluate",
            str(self.manifest),
            env={"MAGI_MAX_AUTONOMOUS_MODEL_LAUNCHES": "17"},
        )
        self.assertEqual(result.returncode, 64)
        self.assertEqual(result.stdout, "")
        self.assertIn("MAGI_CONVERGENCE_USAGE:", result.stderr)
        self.assertIn("global fuse cannot be extended", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_malformed_guard_ledger_is_stable_blocked_json(self) -> None:
        control = self.manifest.parent / ".dual-magi"
        control.mkdir()
        ledger = control / f"CAMPAIGN.{guard.doc_id(self.manifest.resolve())}.json"
        ledger.write_text("{}\n")

        result, payload = self.evaluate()

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")
        self.assertEqual(payload["mode"], "report-only")
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertEqual(payload["reason_code"], "UNSAFE_OR_INCOMPLETE_INPUT")
        self.assertFalse(payload["authorizes_shipping"])
        self.assertIn("ledger fields do not match schema version 1", payload["detail"])
        self.assertNotIn("Traceback", result.stdout)

    def test_symlink_loop_historical_manifest_is_stable_blocked_json(self) -> None:
        loop = self.repo / "loop"
        loop.symlink_to("loop")
        payload = json.loads(self.manifest.read_text())
        payload["historical_manifests"] = [
            {
                "path": str(loop / "archive.json"),
                "artifact_sha": "0" * 64,
            }
        ]
        self.manifest.write_text(json.dumps(payload))

        result, blocked = self.evaluate()

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")
        self.assertEqual(blocked["mode"], "report-only")
        self.assertEqual(blocked["decision"], "BLOCKED")
        self.assertEqual(blocked["reason_code"], "UNSAFE_OR_INCOMPLETE_INPUT")
        self.assertFalse(blocked["authorizes_shipping"])
        self.assertIn("Symlink loop", blocked["detail"])
        self.assertNotIn("Traceback", result.stdout)

    def test_resampling_oserror_is_stable_blocked_json(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(
                convergence,
                "evaluate",
                side_effect=OSError("resampling failed"),
            ),
            mock.patch.object(
                sys,
                "argv",
                [str(SCRIPT), "evaluate", str(self.manifest)],
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            returncode = convergence.main()

        blocked = json.loads(stdout.getvalue())
        self.assertEqual(returncode, 2)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(blocked["mode"], "report-only")
        self.assertEqual(blocked["decision"], "BLOCKED")
        self.assertEqual(blocked["reason_code"], "UNSAFE_OR_INCOMPLETE_INPUT")
        self.assertEqual(blocked["detail"], "resampling failed")
        self.assertFalse(blocked["authorizes_shipping"])
        self.assertNotIn("Traceback", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
