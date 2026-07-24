#!/usr/bin/env python3
"""Regression tests for bounded campaigns and convergence classifications."""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import jsonschema


HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
GUARD = PLUGIN / "scripts" / "magi_campaign_guard.py"
FANOUT = PLUGIN / "scripts" / "magi_fanout_codex.sh"
XFAMILY = PLUGIN / "scripts" / "magi_xfamily.sh"
VALIDATOR = PLUGIN / "scripts" / "magi_validate_findings.py"
SCHEMA = json.loads((PLUGIN / "schemas" / "finding.schema.json").read_text())
sys.path.insert(0, str(PLUGIN / "scripts"))
from magi_validate_findings import validate as validate_findings  # noqa: E402


def run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(args, text=True, capture_output=True, env=merged, check=False)


def finding(
    dup_flag: str,
    severity: str,
    *,
    doc: Path | None = None,
    round_no: int = 1,
) -> dict[str, object]:
    artifact_id = hashlib.sha256(str(doc.resolve()).encode()).hexdigest()[:16] if doc else "0" * 16
    artifact_sha = hashlib.sha256(doc.read_bytes()).hexdigest() if doc else "0" * 64
    finding_payload = {
        "finding_id": "TEST-1",
        "severity": severity,
        "title": "test",
        "location": "section 1",
        "rationale": "test",
        "required_fix": "test",
        "confidence": "high",
        "dup_flag": dup_flag,
        "missed_angle": "test",
    }
    if severity in {"REJECT", "CRITICAL", "HIGH"}:
        finding_payload["subsystem"] = "orchestration"
        finding_payload["root_cause_id"] = "test.root"
    return {
        "reviewer": "TEST",
        "round": round_no,
        "artifact_id": artifact_id,
        "artifact_sha": artifact_sha,
        "verdict": "REVISE",
        "schema_grounding_verdict": "PASS",
        "verify_commands_executed": ["rg contract doc"],
        "source_artifacts": [],
        "dispositions": [],
        "findings": [finding_payload],
    }


def empty_review(doc: Path, round_no: int, reviewer: str) -> dict[str, object]:
    payload = finding("new", "LOW", doc=doc, round_no=round_no)
    payload["reviewer"] = reviewer
    payload["verdict"] = "GO"
    payload["findings"] = []
    return payload


class CampaignGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.doc = self.root / "design.md"
        self.doc.write_text("# design\n")
        self.state = self.root / "state"
        self.state.mkdir()
        source = self.state / "round_8_source.json"
        source.write_text(json.dumps(empty_review(self.doc, 8, "SOURCE")))
        self.prior = self.state / "round_8_codex.json"
        prior_payload = empty_review(self.doc, 8, "SYNTHESIS")
        prior_payload["source_artifacts"] = [
            {"path": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
        ]
        prior_payload["dispositions"] = []
        self.prior.write_text(json.dumps(prior_payload))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def guard(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return run("python3", str(GUARD), *args, env=env)

    def claim(
        self,
        round_no: int,
        phase: str,
        state: Path | None = None,
        *,
        finish: str | None = "success",
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        result = self.guard(
            "claim", str(self.doc), str(round_no), phase, str(state or self.state), env=env
        )
        if result.returncode == 0 and finish is not None:
            claim_id = result.stdout.strip().rsplit("CLAIM_ID=", 1)[-1]
            terminal = self.guard("finish", str(self.doc), claim_id, finish)
            self.assertEqual(terminal.returncode, 0, terminal.stderr)
        return result

    def fill_default_campaign(self) -> None:
        for round_no in range(1, 9):
            phase = "fanout" if round_no % 2 else "xfamily"
            result = self.claim(round_no, phase)
            self.assertEqual(result.returncode, 0, result.stderr)

    def seed_ledger(self, launches: list[dict[str, object]]) -> Path:
        control = self.doc.parent / ".dual-magi"
        control.mkdir(exist_ok=True)
        artifact_id = hashlib.sha256(str(self.doc.resolve()).encode()).hexdigest()[:16]
        path = control / f"CAMPAIGN.{artifact_id}.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "doc_id": artifact_id,
                    "doc_path": str(self.doc.resolve()),
                    "campaigns": [
                        {
                            "campaign_id": "seed",
                            "started_at": "2026-01-01T00:00:00Z",
                            "started_by": "test",
                            "reason": "boundary fixture",
                            "launches": launches,
                        }
                    ],
                }
            )
        )
        return path

    def start_owned_claim(
        self, *, ignore_term: bool = False
    ) -> tuple[subprocess.Popen[str], str]:
        code = textwrap.dedent(
            """
            import fcntl, hashlib, os, signal, subprocess, sys, time

            guard, doc, state, ignore = sys.argv[1:5]
            doc_id = hashlib.sha256(os.path.realpath(doc).encode()).hexdigest()[:16]
            control = os.path.join(os.path.dirname(os.path.realpath(doc)), ".dual-magi")
            os.makedirs(control, exist_ok=True)
            lock_fd = os.open(os.path.join(control, f".review.{doc_id}.lock"),
                              os.O_WRONLY | os.O_CREAT, 0o600)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            if ignore == "1":
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
            child_code = (
                "import signal,time,sys;"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN) if sys.argv[1]=='1' else None;"
                "time.sleep(60)"
            )
            subprocess.Popen([sys.executable, "-c", child_code, ignore], close_fds=True)
            result = subprocess.run(
                [sys.executable, guard, "claim", doc, "1", "fanout", state,
                 "--owner-pid", str(os.getpid()), "--adapter-kind", "fanout"],
                text=True, capture_output=True, check=False,
            )
            print(result.stdout.strip() or result.stderr.strip(), flush=True)
            if result.returncode:
                raise SystemExit(result.returncode)
            while True:
                time.sleep(1)
            """
        )
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                code,
                str(GUARD),
                str(self.doc),
                str(self.state),
                "1" if ignore_term else "0",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdout is not None
        claim_line = process.stdout.readline().strip()
        self.assertIn("CLAIM_ID=", claim_line)
        return process, claim_line.rsplit("CLAIM_ID=", 1)[-1]

    def cancel(
        self, sha: str, *, term: int = 1, kill: int = 1
    ) -> subprocess.CompletedProcess[str]:
        return self.guard(
            "cancel-revision",
            str(self.doc),
            "--expected-artifact-sha",
            sha,
            "--reason",
            "requirements changed",
            "--term-timeout-s",
            str(term),
            "--kill-timeout-s",
            str(kill),
        )

    def wait_owned(self, process: subprocess.Popen[str]) -> None:
        process.wait(timeout=5)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()

    def test_global_fuse_has_no_extension_path(self) -> None:
        self.fill_default_campaign()
        denied = self.claim(9, "fanout")
        self.assertEqual(denied.returncode, 4)
        self.assertIn("NOT PLATEAU", denied.stderr)
        self.assertIn("MAGI_BUDGET_EXHAUSTED", denied.stderr)
        self.assertFalse(any((self.doc.parent / ".dual-magi").glob("PLATEAU*")))

    def test_owner_registration_is_verified_and_optional(self) -> None:
        claimed = self.guard(
            "claim",
            str(self.doc),
            "1",
            "fanout",
            str(self.state),
            "--owner-pid",
            str(os.getpid()),
            "--adapter-kind",
            "fanout",
        )
        self.assertEqual(claimed.returncode, 0, claimed.stderr)
        ledger_path = next((self.doc.parent / ".dual-magi").glob("CAMPAIGN.*.json"))
        launch = json.loads(ledger_path.read_text())["campaigns"][0]["launches"][0]
        self.assertEqual(launch["owner"]["pid"], os.getpid())
        self.assertEqual(launch["owner"]["adapter_kind"], "fanout")
        self.assertGreater(launch["owner"]["start_ticks"], 0)
        claim_id = claimed.stdout.rsplit("CLAIM_ID=", 1)[-1].strip()
        self.assertEqual(
            self.guard("finish", str(self.doc), claim_id, "failed").returncode, 0
        )

        other = self.root / "ownerless.md"
        other.write_text("# ownerless\n")
        ownerless = self.guard(
            "claim", str(other), "1", "fanout", str(self.root / "ownerless-state")
        )
        self.assertEqual(ownerless.returncode, 0, ownerless.stderr)
        other_ledger = json.loads(
            next((other.parent / ".dual-magi").glob(
                f"CAMPAIGN.{hashlib.sha256(str(other.resolve()).encode()).hexdigest()[:16]}.json"
            )).read_text()
        )
        self.assertNotIn("owner", other_ledger["campaigns"][0]["launches"][0])

    def test_second_claim_does_not_abandon_running_owner(self) -> None:
        first = self.claim(1, "fanout", finish=None)
        self.assertEqual(first.returncode, 0, first.stderr)
        ledger_path = next((self.doc.parent / ".dual-magi").glob("CAMPAIGN.*.json"))
        before = ledger_path.read_bytes()
        denied = self.guard("claim", str(self.doc), "1", "fanout", str(self.state))
        self.assertEqual(denied.returncode, 64)
        self.assertIn("still running", denied.stderr)
        self.assertEqual(ledger_path.read_bytes(), before)

    def test_wrong_cancel_sha_does_not_mutate_or_signal(self) -> None:
        process, _ = self.start_owned_claim()
        try:
            ledger_path = next((self.doc.parent / ".dual-magi").glob("CAMPAIGN.*.json"))
            before = ledger_path.read_bytes()
            denied = self.cancel("0" * 64)
            self.assertEqual(denied.returncode, 64)
            self.assertIsNone(process.poll())
            self.assertEqual(ledger_path.read_bytes(), before)
        finally:
            result = self.cancel(hashlib.sha256(self.doc.read_bytes()).hexdigest())
            self.assertEqual(result.returncode, 0, result.stderr)
            self.wait_owned(process)

    def test_ownerless_running_claim_cancellation_fails_closed(self) -> None:
        claimed = self.claim(1, "fanout", finish=None)
        self.assertEqual(claimed.returncode, 0, claimed.stderr)
        blocked = self.cancel(hashlib.sha256(self.doc.read_bytes()).hexdigest())
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("REQUIREMENT_REVISION_CLEANUP_BLOCKED", blocked.stderr)
        ledger_path = next((self.doc.parent / ".dual-magi").glob("CAMPAIGN.*.json"))
        launch = json.loads(ledger_path.read_text())["campaigns"][0]["launches"][0]
        self.assertEqual(launch["status"], "cancellation_in_progress")
        self.assertEqual(launch["cancellation"]["cleanup"], "blocked")

    def test_requirement_revision_cancellation_term_cleanup_and_late_finish(self) -> None:
        process, claim_id = self.start_owned_claim()
        sha = hashlib.sha256(self.doc.read_bytes()).hexdigest()
        marker = self.doc.parent / ".dual-magi" / (
            f"PLATEAU.{hashlib.sha256(str(self.doc.resolve()).encode()).hexdigest()[:16]}."
            f"{sha[:16]}"
        )
        marker.write_text("{}\n")

        cancelled = self.cancel(sha)
        self.assertEqual(cancelled.returncode, 0, cancelled.stderr)
        self.wait_owned(process)
        self.assertFalse(marker.exists())
        ledger_path = next((self.doc.parent / ".dual-magi").glob("CAMPAIGN.*.json"))
        launch = json.loads(ledger_path.read_text())["campaigns"][0]["launches"][0]
        self.assertEqual(launch["status"], "superseded-by-requirement-revision")
        self.assertEqual(launch["cancellation"]["cleanup"], "complete")

        late_failed = self.guard("finish", str(self.doc), claim_id, "failed")
        self.assertEqual(late_failed.returncode, 0, late_failed.stderr)
        late_success = self.guard("finish", str(self.doc), claim_id, "success")
        self.assertEqual(late_success.returncode, 64)
        repeated = self.cancel(sha)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)

    def test_term_ignoring_tree_is_killed_and_charge_survives_rollover(self) -> None:
        process, _ = self.start_owned_claim(ignore_term=True)
        old_sha = hashlib.sha256(self.doc.read_bytes()).hexdigest()
        cancelled = self.cancel(old_sha)
        self.assertEqual(cancelled.returncode, 0, cancelled.stderr)
        self.wait_owned(process)

        ledger_path = next((self.doc.parent / ".dual-magi").glob("CAMPAIGN.*.json"))
        ledger = json.loads(ledger_path.read_text())
        ledger["campaigns"][0]["launches"][0]["protocol_sha"] = "old-protocol"
        ledger_path.write_text(json.dumps(ledger))
        same_sha = self.guard("claim", str(self.doc), "1", "fanout", str(self.state))
        self.assertEqual(same_sha.returncode, 64)
        self.assertIn("changed artifact", same_sha.stderr)

        self.doc.write_text("# revised requirement\n")
        rollover = self.claim(1, "fanout")
        self.assertEqual(rollover.returncode, 0, rollover.stderr)
        self.assertIn("global model launches 6/16", rollover.stdout)
        ledger = json.loads(ledger_path.read_text())
        self.assertEqual(len(ledger["campaigns"]), 2)
        self.assertEqual(
            ledger["campaigns"][0]["launches"][0]["status"],
            "superseded-by-requirement-revision",
        )
        self.assertEqual(
            sum(
                launch["model_launches"]
                for campaign in ledger["campaigns"]
                for launch in campaign["launches"]
            ),
            6,
        )

    def test_fresh_state_directory_does_not_reset_campaign(self) -> None:
        self.assertEqual(self.claim(1, "fanout", finish="failed").returncode, 0)
        self.assertEqual(
            self.claim(1, "fanout", self.root / "fresh-state", finish="failed").returncode,
            0,
        )
        denied = self.claim(1, "fanout", self.root / "another-state")
        self.assertEqual(denied.returncode, 64)
        self.assertIn("MAGI_TRANSITION_ERROR", denied.stderr)

    def test_changed_revision_rolls_over_without_ack(self) -> None:
        self.assertEqual(self.claim(1, "fanout", finish="failed").returncode, 0)
        self.assertEqual(self.claim(1, "fanout", finish="failed").returncode, 0)
        self.doc.write_text("# revised design\n")
        self.assertEqual(self.claim(1, "fanout").returncode, 0)
        ledger = json.loads(next((self.doc.parent / ".dual-magi").glob("CAMPAIGN.*.json")).read_text())
        self.assertEqual(len(ledger["campaigns"]), 2)
        self.assertEqual(ledger["campaigns"][-1]["started_by"], "automatic-rollover")

    def test_tightening_env_cannot_be_used_to_extend(self) -> None:
        for round_no, phase in ((1, "fanout"), (2, "xfamily")):
            result = self.claim(
                round_no,
                phase,
                env={"MAGI_MAX_AUTONOMOUS_MODEL_LAUNCHES": "4"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        denied = self.guard(
            "claim",
            str(self.doc),
            "3",
            "fanout",
            str(self.state),
            env={"MAGI_MAX_AUTONOMOUS_MODEL_LAUNCHES": "4"},
        )
        self.assertEqual(denied.returncode, 4)

        other = self.root / "other-env.md"
        other.write_text("# other env\n")
        invalid = self.guard(
            "claim",
            str(other),
            "1",
            "fanout",
            str(self.root / "other-state"),
            env={"MAGI_MAX_AUTONOMOUS_MODEL_LAUNCHES": "17"},
        )
        self.assertEqual(invalid.returncode, 64)

    def test_failed_fanout_retry_preserves_xfamily_reserve(self) -> None:
        first = self.claim(
            1,
            "fanout",
            finish="failed",
            env={"MAGI_MAX_AUTONOMOUS_MODEL_LAUNCHES": "4"},
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        denied = self.guard(
            "claim",
            str(self.doc),
            "1",
            "fanout",
            str(self.state),
            env={"MAGI_MAX_AUTONOMOUS_MODEL_LAUNCHES": "4"},
        )
        self.assertEqual(denied.returncode, 4)
        self.assertIn("reserved for mandatory xfamily", denied.stderr)

    def test_illegal_transition_precedes_exhausted_budget(self) -> None:
        self.fill_default_campaign()
        denied = self.guard("claim", str(self.doc), "9", "xfamily", str(self.state))
        self.assertEqual(denied.returncode, 64)
        self.assertIn("MAGI_TRANSITION_ERROR", denied.stderr)

    def test_denied_explicit_rollover_is_not_persisted(self) -> None:
        history = (
            (1, "fanout", "success"),
            (2, "xfamily", "failed"),
            (2, "xfamily", "success"),
            (3, "fanout", "success"),
            (4, "xfamily", "success"),
            (5, "fanout", "success"),
            (6, "xfamily", "failed"),
        )
        launches = [
            {
                "round": round_no,
                "phase": phase,
                "model_launches": 3 if phase == "fanout" else 1,
                "status": status,
                "artifact_sha": "old-revision",
                "protocol_sha": "old-protocol",
                "state_dir": str(self.state),
            }
            for round_no, phase, status in history
        ]
        ledger_path = self.seed_ledger(launches)
        denied = self.guard("claim", str(self.doc), "1", "fanout", str(self.state))
        self.assertEqual(denied.returncode, 4, denied.stderr)
        ledger = json.loads(ledger_path.read_text())
        self.assertEqual(len(ledger["campaigns"]), 1)
        self.assertEqual(len(ledger["campaigns"][0]["launches"]), len(history))

    def test_stray_authorization_file_cannot_extend_fuse(self) -> None:
        self.fill_default_campaign()
        control = self.doc.parent / ".dual-magi"
        approval = control / "CAMPAIGN_CONTINUE.untrusted.json"
        approval.write_text('{"schema_version": 1')
        self.assertEqual(self.claim(9, "fanout").returncode, 4)

    def test_administrative_campaign_reset_is_disabled_in_production(self) -> None:
        self.assertEqual(self.claim(1, "fanout").returncode, 0)
        denied = self.guard(
            "new-campaign", str(self.doc), "--operator", "model", "--reason", "reset retries"
        )
        self.assertEqual(denied.returncode, 64)
        self.assertIn("disabled outside deterministic test fixtures", denied.stderr)

    def test_missing_provider_fails_before_claim(self) -> None:
        result = run(
            str(FANOUT),
            str(self.doc),
            "1",
            str(self.state),
            env={"PATH": "/usr/bin:/bin"},
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(any((self.doc.parent / ".dual-magi").glob("CAMPAIGN.*.json")))

    def test_legacy_launch_is_migrated_at_phase_weight(self) -> None:
        control = self.doc.parent / ".dual-magi"
        control.mkdir()
        artifact_id = hashlib.sha256(str(self.doc.resolve()).encode()).hexdigest()[:16]
        for persona in ("melchior", "balthasar", "caspar"):
            (self.state / f"round_1_{persona}.json").write_text("{}\n")
        ledger_path = control / f"CAMPAIGN.{artifact_id}.json"
        ledger_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "doc_id": artifact_id,
                    "doc_path": str(self.doc.resolve()),
                    "campaigns": [
                        {
                            "campaign_id": "legacy",
                            "started_at": "2026-01-01T00:00:00Z",
                            "started_by": "legacy",
                            "reason": "migration fixture",
                            "launches": [
                                {
                                    "round": 1,
                                    "phase": "fanout",
                                    "state_dir": str(self.state),
                                    "artifact_sha": hashlib.sha256(self.doc.read_bytes()).hexdigest(),
                                }
                            ],
                        }
                    ],
                }
            )
        )
        claimed = self.claim(2, "xfamily")
        self.assertEqual(claimed.returncode, 0, claimed.stderr)
        self.assertIn("model launches 4/16", claimed.stdout)
        migrated = json.loads(ledger_path.read_text())
        self.assertEqual(migrated["campaigns"][0]["launches"][0]["model_launches"], 3)

    def test_incorrect_stored_phase_weight_fails_closed(self) -> None:
        self.assertEqual(self.claim(1, "fanout").returncode, 0)
        ledger_path = next((self.doc.parent / ".dual-magi").glob("CAMPAIGN.*.json"))
        ledger = json.loads(ledger_path.read_text())
        ledger["campaigns"][0]["launches"][0]["model_launches"] = 1
        ledger_path.write_text(json.dumps(ledger))
        denied = self.guard("claim", str(self.doc), "2", "xfamily", str(self.state))
        self.assertEqual(denied.returncode, 2)
        self.assertIn("MAGI_STATE_CORRUPTION", denied.stderr)

    def test_later_round_requires_prior_before_provider_launch(self) -> None:
        fanout = run(str(FANOUT), str(self.doc), "3", str(self.state), "--prior", "-")
        self.assertEqual(fanout.returncode, 64)
        xfamily = run(
            str(XFAMILY),
            "--reviewer",
            "claude",
            str(self.doc),
            "2",
            "-",
            str(self.state / "round_2_xfamily"),
        )
        self.assertEqual(xfamily.returncode, 64)

    def test_invalid_xfamily_timeout_fails_before_claim(self) -> None:
        result = run(
            str(XFAMILY),
            str(self.doc),
            "1",
            "-",
            str(self.state / "round_1_xfamily"),
            env={"MAGI_XFAMILY_TIMEOUT_S": "unbounded"},
        )
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertFalse(any((self.doc.parent / ".dual-magi").glob("CAMPAIGN.*.json")))

    def test_prior_is_bound_to_doc_round_and_state(self) -> None:
        schema_path = PLUGIN / "schemas" / "finding.schema.json"
        wrong_round = run(
            "python3",
            str(VALIDATOR),
            str(self.prior),
            str(schema_path),
            "--same-doc",
            str(self.doc),
            "--prior-for-round",
            "3",
            "--state-dir",
            str(self.state),
        )
        self.assertEqual(wrong_round.returncode, 1)

        empty = self.state / "empty.json"
        empty.write_text("{}\n")
        self.assertEqual(
            run("python3", str(VALIDATOR), str(empty), "--same-doc", str(self.doc)).returncode,
            1,
        )

        outside = self.root / "outside.json"
        outside.write_text(self.prior.read_text())
        self.assertEqual(
            run(
                "python3",
                str(VALIDATOR),
                str(outside),
                "--same-doc",
                str(self.doc),
                "--prior-for-round",
                "9",
                "--state-dir",
                str(self.state),
            ).returncode,
            1,
        )

        other = self.root / "other-prior.md"
        other.write_text("# other\n")
        self.assertEqual(
            run(
                "python3",
                str(VALIDATOR),
                str(self.prior),
                "--same-doc",
                str(other),
            ).returncode,
            1,
        )

    def test_budget_denial_happens_before_provider_launch(self) -> None:
        self.fill_default_campaign()
        fanout = run(
            str(FANOUT),
            str(self.doc),
            "9",
            str(self.state),
            "--prior",
            str(self.prior),
        )
        self.assertEqual(fanout.returncode, 4)
        self.assertIn("NOT PLATEAU", fanout.stderr)

    def test_changed_artifact_can_roll_into_weight_one_targeted_pair(self) -> None:
        self.assertEqual(self.claim(1, "fanout").returncode, 0)
        self.assertEqual(self.claim(2, "xfamily").returncode, 0)
        self.doc.write_text("# fixture\n\nsmall fix\n")

        targeted = self.claim(1, "targeted")
        self.assertEqual(targeted.returncode, 0, targeted.stderr)
        final = self.claim(2, "xfamily")
        self.assertEqual(final.returncode, 0, final.stderr)

        ledger = json.loads(next((self.doc.parent / ".dual-magi").glob("CAMPAIGN.*.json")).read_text())
        launches = ledger["campaigns"][-1]["launches"]
        self.assertEqual([launch["phase"] for launch in launches], ["targeted", "xfamily"])
        self.assertEqual([launch["model_launches"] for launch in launches], [1, 1])
        self.assertEqual(
            sum(
                launch["model_launches"]
                for campaign in ledger["campaigns"]
                for launch in campaign["launches"]
            ),
            6,
        )
        status = self.guard(
            "claim-status",
            str(self.doc),
            launches[-1]["claim_id"],
        )
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(status.stdout.strip(), "success")

    def test_protocol_only_rollover_cannot_use_targeted_weight(self) -> None:
        self.assertEqual(self.claim(1, "fanout").returncode, 0)
        self.assertEqual(self.claim(2, "xfamily").returncode, 0)
        ledger_path = next((self.doc.parent / ".dual-magi").glob("CAMPAIGN.*.json"))
        ledger = json.loads(ledger_path.read_text())
        ledger["campaigns"][-1]["launches"][-1]["protocol_sha"] = "stale-protocol"
        ledger_path.write_text(json.dumps(ledger))

        targeted = self.claim(1, "targeted", finish=None)
        self.assertEqual(targeted.returncode, 64)
        fanout = self.claim(1, "fanout")
        self.assertEqual(fanout.returncode, 0, fanout.stderr)

    def test_targeted_cannot_bootstrap_or_replace_requirement_revision(self) -> None:
        initial = self.claim(1, "targeted", finish=None)
        self.assertEqual(initial.returncode, 64)
        self.assertIn("must start at round 1 fanout", initial.stderr)

        owner, _ = self.start_owned_claim()
        old_sha = hashlib.sha256(self.doc.read_bytes()).hexdigest()
        cancelled = self.cancel(old_sha)
        self.assertEqual(cancelled.returncode, 0, cancelled.stderr)
        self.wait_owned(owner)
        self.doc.write_text("# revised requirements\n")

        targeted = self.claim(1, "targeted", finish=None)
        self.assertEqual(targeted.returncode, 64)
        fanout = self.claim(1, "fanout")
        self.assertEqual(fanout.returncode, 0, fanout.stderr)

    def test_claim_rejects_stale_incremental_authorization_sha(self) -> None:
        expected = hashlib.sha256(self.doc.read_bytes()).hexdigest()
        self.doc.write_text("# changed after decision\n")
        result = self.guard(
            "claim",
            str(self.doc),
            "1",
            "fanout",
            str(self.state),
            "--expected-artifact-sha",
            expected,
        )
        self.assertEqual(result.returncode, 64)
        self.assertIn("artifact changed after its authorization", result.stderr)


class FindingSchemaTest(unittest.TestCase):
    def test_blocking_finding_requires_convergence_identity(self) -> None:
        for missing in ("root_cause_id", "subsystem"):
            payload = finding("new", "HIGH")
            del payload["findings"][0][missing]
            with self.assertRaises(jsonschema.ValidationError):
                validate_findings(payload, SCHEMA)

        payload = finding("new", "MED")
        validate_findings(payload, SCHEMA)

    def test_classification_and_severity_contract(self) -> None:
        for flag, severity in (
            ("new", "HIGH"),
            ("duplicate", "HIGH"),
            ("regression", "CRITICAL"),
            ("readiness-gap", "MED"),
            ("scope-expansion", "LOW"),
        ):
            payload = finding(flag, severity)
            if flag in {"readiness-gap", "scope-expansion"}:
                payload["verdict"] = "GO-WITH-REVISE"
            validate_findings(payload, SCHEMA)

        for flag, severity in (
            ("readiness-gap", "CRITICAL"),
            ("scope-expansion", "HIGH"),
            ("invented", "LOW"),
        ):
            with self.assertRaises((jsonschema.ValidationError, ValueError)):
                validate_findings(finding(flag, severity), SCHEMA)

        with self.assertRaises(ValueError):
            validate_findings(finding("scope-expansion", "LOW") | {"verdict": "REVISE"}, SCHEMA)


if __name__ == "__main__":
    unittest.main()
