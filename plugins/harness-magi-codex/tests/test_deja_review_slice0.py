#!/usr/bin/env python3
"""Offline integration tests for the Deja Review Slice 0 foundation."""

from __future__ import annotations

import fcntl
import errno
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "deja_review_slice0.py"
SPEC = importlib.util.spec_from_file_location("deja_review_slice0", SCRIPT)
assert SPEC and SPEC.loader
slice0 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = slice0
SPEC.loader.exec_module(slice0)


def finding(
    finding_id: str = "F-001",
    *,
    severity: str = "HIGH",
    title: str = "Atomic rollback test",
) -> dict:
    return {
        "finding_id": finding_id,
        "severity": severity,
        "title": title,
        "location": "test fixture",
        "rationale": "Synthetic credential-shaped SENTINEL_SECRET_VALUE remains untrusted data.",
        "required_fix": "Keep the operation atomic and add a fixture.",
        "confidence": "high",
        "dup_flag": "new",
        "missed_angle": "rollback security performance",
        "subsystem": "slice0-normalizer",
        "root_cause_id": "fixture-root",
        "affected_invariant": "exact-source-provenance",
        "changes_design_invariant": False,
        "relation_to_prior": "new-root",
    }


def artifact(*findings: dict, reviewer: str = "MELCHIOR", round_: int = 1) -> dict:
    return {
        "reviewer": reviewer,
        "round": round_,
        "artifact_id": "0123456789abcdef",
        "artifact_sha": "a" * 64,
        "verdict": "REVISE",
        "schema_grounding_verdict": "PASS",
        "verify_commands_executed": ["read fixture"],
        "source_artifacts": [],
        "dispositions": [],
        "findings": list(findings or (finding(),)),
    }


def write_json(path: Path, payload: object) -> bytes:
    data = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode()
    path.write_bytes(data)
    return data


class Slice0IntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.state = self.base / "state"
        self.sources = self.base / "sources"
        self.state.mkdir()
        self.sources.mkdir()
        self.source = self.sources / "round_1_melchior.json"
        self.source_bytes = write_json(self.source, artifact())

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_cli(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        command_env = os.environ.copy()
        if env:
            command_env.update(env)
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            text=True,
            capture_output=True,
            env=command_env,
            timeout=30,
        )

    def prepare(
        self, campaign: str = "fixture", *sources: Path, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess:
        selected = sources or (self.source,)
        args = [
            "prepare",
            "--campaign-id",
            campaign,
            "--state-root",
            str(self.state),
        ]
        for source in selected:
            args.extend(["--source", str(source)])
        return self.run_cli(*args, env=env)

    def campaign(self, name: str = "fixture") -> Path:
        return self.state / name

    def immutable_bytes(self, name: str = "fixture") -> dict[str, bytes]:
        directory = self.campaign(name)
        return {item: (directory / item).read_bytes() for item in slice0.IMMUTABLE_OUTPUTS}

    def replace_immutable_output(self, campaign: str, name: str, data: bytes) -> None:
        directory = self.campaign(campaign)
        (directory / name).write_bytes(data)
        receipt_path = directory / "stage-receipts" / "prepare.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["outputs"][name] = hashlib.sha256(data).hexdigest()
        write_json(receipt_path, receipt)

    def test_prepare_validate_status_and_exact_reuse(self) -> None:
        before_source = self.source.read_bytes()
        first = self.prepare()
        self.assertEqual(first.returncode, 0, first.stderr)
        immutable = self.immutable_bytes()
        mtimes = {name: (self.campaign() / name).stat().st_mtime_ns for name in immutable}

        validate = self.run_cli("validate", "--campaign-dir", str(self.campaign()))
        self.assertEqual(validate.returncode, 0, validate.stderr)
        self.assertEqual(json.loads(validate.stdout)["status"], "valid")

        status = self.run_cli("status", "--campaign-dir", str(self.campaign()))
        self.assertEqual(status.returncode, 0, status.stderr)
        status_payload = json.loads(status.stdout)
        self.assertEqual(status_payload["state"], "complete")
        self.assertNotIn("SENTINEL_SECRET_VALUE", status.stdout + status.stderr)

        time.sleep(0.01)
        second = self.prepare()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(immutable, self.immutable_bytes())
        self.assertEqual(
            mtimes,
            {name: (self.campaign() / name).stat().st_mtime_ns for name in immutable},
        )
        progress = [
            json.loads(line)
            for line in (self.campaign() / "progress.jsonl").read_text().splitlines()
        ]
        self.assertEqual(progress[-1]["reason_code"], "exact-reuse")
        self.assertEqual(before_source, self.source.read_bytes())

    def test_normalized_record_and_receipt_contract(self) -> None:
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        records = [
            json.loads(line)
            for line in (self.campaign() / "normalized-findings.jsonl").read_text().splitlines()
        ]
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["source_sha256"], hashlib.sha256(self.source_bytes).hexdigest())
        self.assertEqual(record["trust"], "untrusted-review-content")
        self.assertEqual(record["subsystem"], "slice0-normalizer")
        self.assertEqual(record["root_cause_id"], "fixture-root")
        self.assertEqual(record["affected_invariant"], "exact-source-provenance")
        self.assertFalse(record["changes_design_invariant"])
        self.assertEqual(record["relation_to_prior"], "new-root")
        self.assertEqual(record["categories"], ["rollback", "security", "data-integrity"])
        self.assertIn("SENTINEL_SECRET_VALUE", record["rationale"])

        receipt = json.loads(
            (self.campaign() / "stage-receipts" / "prepare.json").read_text()
        )
        self.assertEqual(set(receipt["outputs"]), set(slice0.IMMUTABLE_OUTPUTS))
        self.assertNotIn("progress.jsonl", receipt["outputs"])
        self.assertNotIn("heartbeat.json", receipt["outputs"])
        self.assertFalse((self.campaign() / "run-owner.json").exists())
        progress = [
            json.loads(line)
            for line in (self.campaign() / "progress.jsonl").read_text().splitlines()
        ]
        self.assertEqual(progress[-1]["outcome"], "success")
        self.assertTrue(any(item["stage"] == "discover" and item["completed"] == 1 for item in progress))
        self.assertTrue(any(item["stage"] == "normalize" and item["completed"] == 1 for item in progress))
        self.assertTrue(any(item["stage"] == "publish" and item["completed"] == 5 for item in progress))

    def test_changed_source_on_completed_campaign_returns_four(self) -> None:
        self.assertEqual(self.prepare().returncode, 0)
        write_json(self.source, artifact(finding(title="Changed performance fixture")))
        result = self.prepare()
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn("immutable-input-mismatch", result.stderr)

    def test_distinct_inode_duplicate_content_fails_without_receipt(self) -> None:
        copy = self.sources / "round_1_copy.json"
        copy.write_bytes(self.source.read_bytes())
        result = self.prepare("duplicates", self.source, copy)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("duplicate-source-content", result.stderr)
        self.assertFalse(
            (self.campaign("duplicates") / "stage-receipts" / "prepare.json").exists()
        )

    def test_repeated_same_inode_is_deduplicated(self) -> None:
        alias = self.sources / "hardlink.json"
        os.link(self.source, alias)
        result = self.prepare("hardlink", self.source, alias)
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.campaign("hardlink") / "campaign.json").read_text())
        self.assertEqual(manifest["artifact_count"], 1)

    def test_invalid_campaign_ids_write_nothing(self) -> None:
        for value in ("..", "../escape", "/absolute", "a/b", "", "a" * 65):
            with self.subTest(value=value):
                before = set(self.state.iterdir())
                result = self.prepare(value)
                self.assertEqual(result.returncode, 64, (value, result.stderr))
                self.assertEqual(before, set(self.state.iterdir()))

    def test_source_symlink_and_directory_are_rejected(self) -> None:
        symlink = self.sources / "symlink.json"
        symlink.symlink_to(self.source)
        for path in (symlink, self.sources):
            with self.subTest(path=path):
                result = self.prepare(f"reject-{path.name}", path)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(
                    (self.campaign(f"reject-{path.name}") / "stage-receipts" / "prepare.json").exists()
                )

    def test_non_artifact_json_is_not_silently_omitted(self) -> None:
        bad = self.sources / "metadata.json"
        write_json(bad, {"reviewer_family": "grok"})
        result = self.prepare("bad-json", bad)
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.campaign("bad-json") / "stage-receipts" / "prepare.json").exists())

    def test_malformed_and_duplicate_finding_fail(self) -> None:
        malformed = self.sources / "malformed.json"
        malformed.write_text("{", encoding="utf-8")
        result = self.prepare("malformed", malformed)
        self.assertEqual(result.returncode, 2)
        duplicate = self.sources / "duplicate-finding.json"
        write_json(duplicate, artifact(finding("DUP"), finding("DUP")))
        result = self.prepare("duplicate-finding", duplicate)
        self.assertEqual(result.returncode, 2)
        self.assertIn("finding IDs", result.stderr)

    def test_receipt_tamper_is_detected(self) -> None:
        self.assertEqual(self.prepare().returncode, 0)
        corpus = self.campaign() / "normalized-findings.jsonl"
        corpus.write_bytes(corpus.read_bytes() + b"\n")
        result = self.run_cli("validate", "--campaign-dir", str(self.campaign()))
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid-output", result.stderr)

    def test_receipt_consistent_unknown_source_reference_is_rejected(self) -> None:
        campaign = "unknown-source"
        self.assertEqual(self.prepare(campaign).returncode, 0)
        corpus_path = self.campaign(campaign) / "normalized-findings.jsonl"
        record = json.loads(corpus_path.read_text())
        record["source_path"] = "/unknown/review.json"
        record["source_sha256"] = "f" * 64
        record["occurrence_id"] = slice0.occurrence_id(
            record["source_sha256"],
            record["reviewer"],
            record["round"],
            record["finding_id"],
        )
        corpus = slice0.canonical_bytes(record)
        self.replace_immutable_output(campaign, "normalized-findings.jsonl", corpus)

        validate = self.run_cli(
            "validate",
            "--campaign-dir",
            str(self.campaign(campaign)),
        )
        self.assertEqual(validate.returncode, 2)
        self.assertIn("unknown source", validate.stderr)
        status = self.run_cli("status", "--campaign-dir", str(self.campaign(campaign)))
        self.assertEqual(status.returncode, 2)
        self.assertEqual(json.loads(status.stdout)["state"], "invalid")

    def test_receipt_consistent_crlf_corpus_is_rejected(self) -> None:
        campaign = "crlf-corpus"
        self.assertEqual(self.prepare(campaign).returncode, 0)
        corpus_path = self.campaign(campaign) / "normalized-findings.jsonl"
        corpus = corpus_path.read_bytes().replace(b"\n", b"\r\n")
        self.replace_immutable_output(campaign, "normalized-findings.jsonl", corpus)

        validate = self.run_cli(
            "validate",
            "--campaign-dir",
            str(self.campaign(campaign)),
        )
        self.assertEqual(validate.returncode, 2)
        self.assertIn("line ending is noncanonical", validate.stderr)
        status = self.run_cli("status", "--campaign-dir", str(self.campaign(campaign)))
        self.assertEqual(status.returncode, 2)
        self.assertEqual(json.loads(status.stdout)["state"], "invalid")

    def test_resource_preflight_requires_closed_valid_passed_shape(self) -> None:
        invalid_payloads = (
            None,
            [],
            {},
        )
        for index, payload in enumerate(invalid_payloads):
            with self.subTest(payload=payload):
                campaign = f"bad-resource-{index}"
                self.assertEqual(self.prepare(campaign).returncode, 0)
                self.replace_immutable_output(
                    campaign,
                    "resource-preflight.json",
                    slice0.canonical_bytes(payload),
                )
                result = self.run_cli(
                    "validate",
                    "--campaign-dir",
                    str(self.campaign(campaign)),
                )
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("Traceback", result.stderr)

        campaign = "bad-resource-types"
        self.assertEqual(self.prepare(campaign).returncode, 0)
        resource_path = self.campaign(campaign) / "resource-preflight.json"
        payload = json.loads(resource_path.read_text())
        payload["source_bytes"] = True
        self.replace_immutable_output(
            campaign,
            "resource-preflight.json",
            slice0.canonical_bytes(payload),
        )
        self.assertEqual(
            self.run_cli(
                "validate",
                "--campaign-dir",
                str(self.campaign(campaign)),
            ).returncode,
            2,
        )

    def test_lock_contention_returns_three_without_owner(self) -> None:
        directory = self.campaign("locked")
        directory.mkdir()
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(directory_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            (directory / "run.lock").write_text("replacement", encoding="utf-8")
            result = self.prepare("locked")
        finally:
            os.close(directory_fd)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertFalse((directory / "stage-receipts").exists())
        self.assertFalse((directory / "run-owner.json").exists())
        self.assertFalse((directory / "progress.jsonl").exists())

    def test_status_absent_is_read_only(self) -> None:
        missing = self.state / "missing"
        result = self.run_cli("status", "--campaign-dir", str(missing))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["state"], "absent")
        self.assertFalse(missing.exists())

    def test_category_fallback_and_occurrence_golden(self) -> None:
        low = finding("LOW-1", severity="LOW", title="Unmatched prose")
        high = finding("HIGH-1", severity="HIGH", title="Unmatched prose")
        low["location"] = low["missed_angle"] = "unmatched"
        high["location"] = high["missed_angle"] = "unmatched"
        self.assertEqual(slice0.derive_categories(low), ["other"])
        self.assertEqual(slice0.derive_categories(high), ["correctness"])
        value = slice0.occurrence_id("b" * 64, "CASPAR", 7, "F-9")
        self.assertEqual(
            value,
            hashlib.sha256(
                (
                    "deja-review-slice0-occurrence-v1\0"
                    + "b" * 64
                    + "\0CASPAR\0"
                    + "7"
                    + "\0F-9"
                ).encode()
            ).hexdigest(),
        )

    def test_normalizer_sha_covers_script_and_slice_schemas(self) -> None:
        original = slice0.implementation_sha()
        script_bytes = SCRIPT.read_bytes()
        expected = hashlib.sha256()
        for path in (SCRIPT.resolve(), slice0.RECORD_SCHEMA_PATH, slice0.MANIFEST_SCHEMA_PATH):
            expected.update(path.name.encode())
            expected.update(b"\0")
            expected.update(path.read_bytes())
            expected.update(b"\0")
        self.assertEqual(original, expected.hexdigest())
        self.assertIn(script_bytes[:20], SCRIPT.read_bytes())

    def test_validate_and_status_reject_normalizer_implementation_drift(self) -> None:
        campaign = "normalizer-drift"
        self.assertEqual(self.prepare(campaign).returncode, 0)
        directory = self.campaign(campaign)
        fds = slice0.open_campaign(str(self.state), campaign, create=False)
        try:
            with mock.patch.object(
                slice0,
                "implementation_sha",
                return_value="b" * 64,
            ):
                with self.assertRaises(slice0.Slice0Error) as caught:
                    slice0.validate_campaign_dir(fds)
        finally:
            fds.close()
        self.assertEqual(caught.exception.reason, "invalid-output")
        self.assertIn("current bytes", str(caught.exception))

        stdout = io.StringIO()
        args = slice0.argparse.Namespace(campaign_dir=str(directory))
        with (
            mock.patch.object(
                slice0,
                "implementation_sha",
                return_value="b" * 64,
            ),
            redirect_stdout(stdout),
        ):
            self.assertEqual(slice0.status_command(args), 2)
        self.assertEqual(json.loads(stdout.getvalue())["state"], "invalid")

        prepare_args = slice0.argparse.Namespace(
            campaign_id=campaign,
            state_root=str(self.state),
            source=[str(self.source)],
        )
        with mock.patch.object(
            slice0,
            "implementation_sha",
            return_value="b" * 64,
        ):
            with self.assertRaises(slice0.Slice0Error) as mismatch:
                slice0.prepare(prepare_args)
        self.assertEqual(mismatch.exception.exit_code, 4)
        self.assertEqual(mismatch.exception.reason, "immutable-input-mismatch")

    def test_preflight_formula_includes_path_records_and_snapshot_count(self) -> None:
        meta = slice0.SourceMeta(
            path="/x/" + "p" * 200,
            dev=1,
            ino=2,
            size=1000,
            mtime_ns=3,
        )
        projection = slice0.resource_projection([meta], self.state, retained_bytes=123)
        path_bytes = len(meta.path.encode())
        records = slice0.MAX_FINDINGS * (
            path_bytes * 6 + slice0.MAX_REVIEWER_BYTES * 6 + 4096
        )
        self.assertEqual(
            projection["per_source_projection"][0]["projected_incremental_memory"],
            1000 * 6 + records * 2 + 1024 * 1024,
        )
        expected_disk = (
            123
            + projection["artifact_spool_bytes_upper_bound"] * 2
            + projection["manifest_and_receipt_temporary_bytes"]
            + projection["projected_progress_append_bytes"]
            + projection["atomic_snapshot_output_count"] * 4096
        )
        self.assertEqual(projection["projected_simultaneous_peak_bytes"], expected_disk)

    def test_reviewer_amplification_is_bounded_and_projected(self) -> None:
        payload = artifact()
        payload["reviewer"] = "R" * (slice0.MAX_REVIEWER_BYTES + 1)
        with self.assertRaises(slice0.Slice0Error) as caught:
            slice0.validate_artifact(payload)
        self.assertIn("reviewer exceeds byte ceiling", str(caught.exception))

        meta = slice0.SourceMeta(
            path="/review.json",
            dev=1,
            ino=2,
            size=1024,
            mtime_ns=3,
        )
        projection = slice0.resource_projection([meta], self.state, retained_bytes=0)
        reviewer_expansion = (
            slice0.MAX_FINDINGS * slice0.MAX_REVIEWER_BYTES * 6
        )
        self.assertGreaterEqual(
            projection["per_source_projection"][0]["normalized_record_expansion"],
            reviewer_expansion,
        )

    def test_exact_reuse_skips_generation_resource_preflight(self) -> None:
        campaign = "reuse-without-generation-preflight"
        self.assertEqual(self.prepare(campaign).returncode, 0)
        args = slice0.argparse.Namespace(
            campaign_id=campaign,
            state_root=str(self.state),
            source=[str(self.source)],
        )
        with mock.patch.object(
            slice0,
            "resource_projection",
            side_effect=AssertionError("generation preflight must not run"),
        ):
            self.assertEqual(slice0.prepare(args), 0)

    def test_cgroup_probe_failure_fails_closed(self) -> None:
        with (
            mock.patch.object(slice0.os, "stat", return_value=object()),
            mock.patch.object(Path, "read_text", side_effect=PermissionError("denied")),
        ):
            with self.assertRaises(slice0.Slice0Error) as caught:
                slice0.cgroup_headroom()
        self.assertEqual(caught.exception.reason, "memory-preflight-unavailable")

    def test_stale_owner_is_reclaimed_but_live_and_remote_are_refused(self) -> None:
        directory = self.campaign()
        (directory / "stage-receipts").mkdir(parents=True)
        old = "2000-01-01T00:00:00Z"

        def write_owner(hostname: str, pid: int, ticks: str) -> None:
            write_json(
                directory / "run-owner.json",
                {
                    "schema_version": "deja-review-slice0-owner/v1",
                    "run_id": "old",
                    "campaign_id": directory.name,
                    "hostname": hostname,
                    "pid": pid,
                    "process_start_ticks": ticks,
                    "started_at": old,
                    "input_intent_digest": "c" * 64,
                },
            )
            write_json(
                directory / "heartbeat.json",
                {
                    "schema_version": "deja-review-slice0-heartbeat/v1",
                    "run_id": "old",
                    "campaign_id": directory.name,
                    "stage": "normalize",
                    "completed": 0,
                    "total": 1,
                    "last_progress_at": old,
                    "recorded_at": old,
                },
            )

        write_owner(slice0.socket.gethostname(), 99999999, "1")
        reclaimed = self.prepare()
        self.assertEqual(reclaimed.returncode, 0, reclaimed.stderr)
        events = [
            json.loads(line)
            for line in (directory / "progress.jsonl").read_text().splitlines()
        ]
        self.assertTrue(any(item["reason_code"] == "stale-lock-reclaimed" for item in events))

        # Remove the completed campaign and exercise unsafe incomplete-owner
        # states under fresh campaign IDs.
        live_directory = self.campaign("live-owner")
        (live_directory / "stage-receipts").mkdir(parents=True)
        directory = live_directory
        write_owner(
            slice0.socket.gethostname(),
            os.getpid(),
            slice0.process_start_ticks(os.getpid()),
        )
        live = self.prepare("live-owner")
        self.assertEqual(live.returncode, 2)
        self.assertIn("owner-still-live", live.stderr)

        remote_directory = self.campaign("remote-owner")
        (remote_directory / "stage-receipts").mkdir(parents=True)
        directory = remote_directory
        write_owner("remote.invalid", 99999999, "1")
        remote = self.prepare("remote-owner")
        self.assertEqual(remote.returncode, 2)
        self.assertIn("owner-unverifiable", remote.stderr)

    def test_stale_owner_rejects_coercible_or_nonpositive_pid(self) -> None:
        old = "2000-01-01T00:00:00Z"
        for index, pid in enumerate((True, 1.9, "1", 0, -1)):
            with self.subTest(pid=pid):
                campaign = f"bad-owner-pid-{index}"
                directory = self.campaign(campaign)
                (directory / "stage-receipts").mkdir(parents=True)
                write_json(
                    directory / "run-owner.json",
                    {
                        "schema_version": "deja-review-slice0-owner/v1",
                        "run_id": "old",
                        "campaign_id": campaign,
                        "hostname": slice0.socket.gethostname(),
                        "pid": pid,
                        "process_start_ticks": "1",
                        "started_at": old,
                        "input_intent_digest": "c" * 64,
                    },
                )
                write_json(
                    directory / "heartbeat.json",
                    {
                        "schema_version": "deja-review-slice0-heartbeat/v1",
                        "run_id": "old",
                        "campaign_id": campaign,
                        "stage": "normalize",
                        "completed": 0,
                        "total": 1,
                        "last_progress_at": old,
                        "recorded_at": old,
                    },
                )
                result = self.prepare(campaign)
                self.assertEqual(result.returncode, 2)
                self.assertIn("owner-unverifiable", result.stderr)
                self.assertEqual(
                    json.loads((directory / "run-owner.json").read_text())["pid"],
                    pid,
                )
                self.assertFalse(
                    (directory / "stage-receipts" / "prepare.json").exists()
                )

    def test_stale_owner_recovery_requires_correlated_heartbeat(self) -> None:
        campaign = "stale-owner-heartbeat"
        directory = self.campaign(campaign)
        (directory / "stage-receipts").mkdir(parents=True)
        old = "2000-01-01T00:00:00Z"
        write_json(
            directory / "run-owner.json",
            {
                "schema_version": "deja-review-slice0-owner/v1",
                "run_id": "owner-run",
                "campaign_id": campaign,
                "hostname": slice0.socket.gethostname(),
                "pid": 99999999,
                "process_start_ticks": "1",
                "started_at": old,
                "input_intent_digest": "c" * 64,
            },
        )
        write_json(
            directory / "heartbeat.json",
            {
                "schema_version": "deja-review-slice0-heartbeat/v1",
                "run_id": "different-run",
                "campaign_id": campaign,
                "stage": "normalize",
                "completed": 0,
                "total": 1,
                "last_progress_at": old,
                "recorded_at": old,
            },
        )

        result = self.prepare(campaign)

        self.assertEqual(result.returncode, 2)
        self.assertIn("owner-unverifiable", result.stderr)
        self.assertTrue((directory / "run-owner.json").exists())

    def test_status_reports_stalled_from_progress_age_not_heartbeat_age(self) -> None:
        directory = self.campaign("stalled")
        (directory / "stage-receipts").mkdir(parents=True)
        old = "2000-01-01T00:00:00Z"
        now = slice0.utc_now()
        write_json(
            directory / "run-owner.json",
            {
                "schema_version": "deja-review-slice0-owner/v1",
                "run_id": "run",
                "campaign_id": "stalled",
                "hostname": slice0.socket.gethostname(),
                "pid": os.getpid(),
                "process_start_ticks": slice0.process_start_ticks(os.getpid()),
                "started_at": old,
                "input_intent_digest": "d" * 64,
            },
        )
        write_json(
            directory / "heartbeat.json",
            {
                "schema_version": "deja-review-slice0-heartbeat/v1",
                "run_id": "run",
                "campaign_id": "stalled",
                "stage": "normalize",
                "completed": 0,
                "total": 2,
                "last_progress_at": old,
                "recorded_at": now,
            },
        )
        result = self.run_cli("status", "--campaign-dir", str(directory))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["state"], "stalled")

    def test_state_root_and_campaign_symlinks_are_rejected(self) -> None:
        root_link = self.base / "state-link"
        root_link.symlink_to(self.state, target_is_directory=True)
        result = self.run_cli(
            "prepare",
            "--campaign-id",
            "linked-root",
            "--state-root",
            str(root_link),
            "--source",
            str(self.source),
        )
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.state / "linked-root").exists())

        target = self.base / "outside"
        target.mkdir()
        (self.state / "linked-campaign").symlink_to(target, target_is_directory=True)
        result = self.prepare("linked-campaign")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(list(target.iterdir()), [])

    def test_source_swap_after_metadata_is_rejected(self) -> None:
        metas = slice0.source_metadata([str(self.source)])
        replacement = self.sources / "replacement.json"
        write_json(replacement, artifact(finding("R-2")))
        self.source.unlink()
        self.source.symlink_to(replacement)
        with self.assertRaises(slice0.Slice0Error) as caught:
            slice0.read_source(metas[0])
        self.assertEqual(caught.exception.reason, "source-race")

    def test_resource_projection_can_fail_closed(self) -> None:
        meta = slice0.SourceMeta(
            path=str(self.source),
            dev=1,
            ino=2,
            size=1024,
            mtime_ns=3,
        )
        with (
            mock.patch.object(slice0, "host_available_memory", return_value=1),
            mock.patch.object(slice0, "cgroup_headroom", return_value=None),
            mock.patch.object(slice0, "rlimit_headroom", return_value=None),
        ):
            projection = slice0.resource_projection([meta], self.state, retained_bytes=0)
        self.assertFalse(projection["memory_pass"])

    def test_rlimit_headroom_uses_virtual_memory_not_rss(self) -> None:
        with (
            mock.patch.object(
                slice0.resource,
                "getrlimit",
                return_value=(10_000, 10_000),
            ),
            mock.patch.object(
                Path,
                "read_text",
                return_value="2 1 0 0 0 0 0\n",
            ),
            mock.patch.object(slice0.os, "sysconf", return_value=1_000),
            mock.patch.object(
                slice0,
                "current_rss_bytes",
                side_effect=AssertionError("RSS is not address-space usage"),
            ),
        ):
            self.assertEqual(slice0.rlimit_headroom(), 8_000)

    def test_resource_projection_uses_one_rss_sample(self) -> None:
        meta = slice0.SourceMeta(
            path=str(self.source),
            dev=1,
            ino=2,
            size=1024,
            mtime_ns=3,
        )
        with mock.patch.object(
            slice0,
            "current_rss_bytes",
            side_effect=[123_456, AssertionError("second RSS sample")],
        ):
            projection = slice0.resource_projection(
                [meta],
                self.state,
                retained_bytes=0,
            )
        self.assertEqual(projection["current_rss_bytes"], 123_456)
        self.assertEqual(
            projection["projected_peak_rss_bytes"],
            123_456 + projection["maximum_projected_incremental_memory_bytes"],
        )

    def test_memory_headroom_is_rechecked_before_each_artifact_read(self) -> None:
        args = slice0.argparse.Namespace(
            campaign_id="per-artifact-memory",
            state_root=str(self.state),
            source=[str(self.source)],
        )
        with (
            mock.patch.object(
                slice0,
                "effective_memory_headroom",
                side_effect=[2**63, 0],
            ),
            mock.patch.object(
                slice0,
                "read_source",
                side_effect=AssertionError("source read before re-admission"),
            ),
        ):
            with self.assertRaises(slice0.Slice0Error) as caught:
                slice0.prepare(args)
        self.assertEqual(caught.exception.reason, "memory-preflight-failed")

    def test_file_count_ceiling_is_enforced_before_owner_publish(self) -> None:
        second = self.sources / "second.json"
        write_json(second, artifact(finding("F-2")))
        with mock.patch.object(slice0, "MAX_FILES", 1):
            with self.assertRaises(slice0.Slice0Error) as caught:
                slice0.source_metadata([str(self.source), str(second)])
        self.assertEqual(caught.exception.reason, "file-count-limit")

    def test_empty_state_root_is_rejected_before_write(self) -> None:
        before = set(Path.cwd().iterdir())
        result = self.run_cli(
            "prepare",
            "--campaign-id",
            "must-not-appear",
            "--state-root",
            "",
            "--source",
            str(self.source),
        )
        self.assertEqual(result.returncode, 64)
        self.assertEqual(before, set(Path.cwd().iterdir()))
        self.assertFalse((Path.cwd() / "must-not-appear").exists())

    def test_environment_does_not_override_fixed_limits(self) -> None:
        for value in ("x", "0", "-1", "257"):
            with self.subTest(value=value):
                result = self.run_cli(
                    "--help",
                    env={"DEJA_SLICE0_MAX_FILES": value},
                )
                self.assertEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stderr)

    def test_lone_surrogate_is_rejected_without_traceback(self) -> None:
        bad = artifact(finding())
        bad["findings"][0]["title"] = "\ud800"
        source = self.sources / "surrogate.json"
        # ensure_ascii is required to materialize a JSON escape rather than
        # asking the filesystem encoder to represent the surrogate.
        source.write_text(json.dumps(bad, ensure_ascii=True), encoding="utf-8")
        result = self.prepare("surrogate", source)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("invalid Unicode surrogate", result.stderr)

    def test_status_rejects_malformed_heartbeat_and_corrupt_receipt(self) -> None:
        self.assertEqual(self.prepare().returncode, 0)
        directory = self.campaign()
        receipt_path = directory / "stage-receipts" / "prepare.json"
        valid_receipt = receipt_path.read_bytes()
        receipt_path.unlink()
        write_json(
            directory / "run-owner.json",
            {
                "schema_version": "deja-review-slice0-owner/v1",
                "run_id": "bad",
                "campaign_id": "fixture",
                "hostname": slice0.socket.gethostname(),
                "pid": os.getpid(),
                "process_start_ticks": slice0.process_start_ticks(os.getpid()),
                "started_at": slice0.utc_now(),
                "input_intent_digest": "e" * 64,
            },
        )
        write_json(directory / "heartbeat.json", {"recorded_at": None})
        status = self.run_cli("status", "--campaign-dir", str(directory))
        self.assertEqual(status.returncode, 2)
        payload = json.loads(status.stdout)
        self.assertEqual(payload["state"], "invalid")
        self.assertEqual(payload["reason_code"], "invalid-heartbeat")

        (directory / "run-owner.json").unlink()
        receipt_path.write_bytes(valid_receipt)
        receipt_path.write_text("{", encoding="utf-8")
        status = self.run_cli("status", "--campaign-dir", str(directory))
        self.assertEqual(status.returncode, 2)
        payload = json.loads(status.stdout)
        self.assertEqual(payload["state"], "invalid")
        self.assertEqual(payload["reason_code"], "invalid-receipt-or-output")

    def test_valid_receipt_is_complete_even_with_leftover_owner(self) -> None:
        self.assertEqual(self.prepare().returncode, 0)
        directory = self.campaign()
        old = "2000-01-01T00:00:00Z"
        write_json(
            directory / "run-owner.json",
            {
                "schema_version": "deja-review-slice0-owner/v1",
                "run_id": "crashed-after-receipt",
                "campaign_id": "fixture",
                "hostname": slice0.socket.gethostname(),
                "pid": 99999999,
                "process_start_ticks": "1",
                "started_at": old,
                "input_intent_digest": "f" * 64,
            },
        )
        write_json(
            directory / "heartbeat.json",
            {
                "schema_version": "deja-review-slice0-heartbeat/v1",
                "run_id": "crashed-after-receipt",
                "campaign_id": "fixture",
                "stage": "publish",
                "completed": len(slice0.IMMUTABLE_OUTPUTS),
                "total": len(slice0.IMMUTABLE_OUTPUTS),
                "last_progress_at": old,
                "recorded_at": old,
            },
        )
        result = self.run_cli("status", "--campaign-dir", str(directory))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["state"], "complete")
        reuse = self.prepare()
        self.assertEqual(reuse.returncode, 0, reuse.stderr)
        progress = [
            json.loads(line)
            for line in (directory / "progress.jsonl").read_text().splitlines()
        ]
        self.assertEqual(progress[-1]["reason_code"], "post-publish-recovery")

    def test_completed_reuse_refuses_unverifiable_leftover_owner(self) -> None:
        campaign = "completed-owner-refusal"
        self.assertEqual(self.prepare(campaign).returncode, 0)
        directory = self.campaign(campaign)
        owner = {
            "schema_version": "deja-review-slice0-owner/v1",
            "run_id": "unverifiable",
            "campaign_id": campaign,
            "hostname": "remote.invalid",
            "pid": 99999999,
            "process_start_ticks": "1",
            "started_at": slice0.utc_now(),
            "input_intent_digest": "f" * 64,
        }
        write_json(directory / "run-owner.json", owner)

        reuse = self.prepare(campaign)

        self.assertEqual(reuse.returncode, 2)
        self.assertIn("owner-unverifiable", reuse.stderr)
        self.assertEqual(
            json.loads((directory / "run-owner.json").read_text()),
            owner,
        )

    def test_status_surfaces_unsafe_campaign_instead_of_absent(self) -> None:
        target = self.base / "outside-status"
        target.mkdir()
        link = self.state / "unsafe-status"
        link.symlink_to(target, target_is_directory=True)
        result = self.run_cli("status", "--campaign-dir", str(link))
        self.assertEqual(result.returncode, 2)
        self.assertIn("unsafe-campaign-path", result.stderr)

    def test_status_structures_campaign_missing_receipt_directory(self) -> None:
        directory = self.campaign("interrupted-before-receipt-dir")
        directory.mkdir()

        result = self.run_cli("status", "--campaign-dir", str(directory))

        self.assertEqual(result.returncode, 2, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["state"], "invalid")
        self.assertEqual(payload["reason_code"], "campaign-incomplete")

    def test_state_root_identity_swap_is_rejected(self) -> None:
        admitted = self.base / "admitted"
        replacement = self.base / "replacement-root"
        old = self.base / "old-root"
        admitted.mkdir()
        replacement.mkdir()
        original_open = slice0.os.open
        swapped = False

        def racing_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if not swapped and Path(path) == admitted:
                admitted.rename(old)
                replacement.rename(admitted)
                swapped = True
            return original_open(path, flags, *args, **kwargs)

        with mock.patch.object(slice0.os, "open", side_effect=racing_open):
            with self.assertRaises(slice0.Slice0Error) as caught:
                slice0.open_campaign(str(admitted), "race", create=True)
        self.assertEqual(caught.exception.reason, "unsafe-state-root")
        self.assertEqual(list(admitted.iterdir()), [])

    def test_campaign_swap_after_first_binding_check_removes_receipt(self) -> None:
        original_verify = slice0.verify_campaign_binding
        calls = 0
        detached = self.state / "detached"

        def racing_verify(fds):
            nonlocal calls
            calls += 1
            original_verify(fds)
            if calls == 1:
                (self.state / "race-receipt").rename(detached)
                (self.state / "race-receipt").mkdir()

        args = slice0.argparse.Namespace(
            campaign_id="race-receipt",
            state_root=str(self.state),
            source=[str(self.source)],
        )
        with mock.patch.object(slice0, "verify_campaign_binding", side_effect=racing_verify):
            with self.assertRaises(slice0.Slice0Error) as caught:
                slice0.prepare(args)
        self.assertEqual(caught.exception.reason, "campaign-path-race")
        self.assertFalse((detached / "stage-receipts" / "prepare.json").exists())

    def test_receipt_directory_swap_before_publication_is_rejected(self) -> None:
        original_verify = slice0.verify_campaign_binding
        calls = 0
        detached = self.base / "detached-receipts"

        def racing_verify(fds):
            nonlocal calls
            calls += 1
            original_verify(fds)
            if calls == 1:
                directory = self.campaign("receipt-dir-race")
                (directory / "stage-receipts").rename(detached)
                (directory / "stage-receipts").mkdir()

        args = slice0.argparse.Namespace(
            campaign_id="receipt-dir-race",
            state_root=str(self.state),
            source=[str(self.source)],
        )
        with mock.patch.object(
            slice0,
            "verify_campaign_binding",
            side_effect=racing_verify,
        ):
            with self.assertRaises(slice0.Slice0Error) as caught:
                slice0.prepare(args)
        self.assertEqual(caught.exception.reason, "receipt-path-race")
        self.assertFalse((detached / "prepare.json").exists())
        self.assertFalse(
            (self.campaign("receipt-dir-race") / "stage-receipts" / "prepare.json").exists()
        )

    def test_atomic_write_validates_persisted_temporary_bytes(self) -> None:
        directory_fd = os.open(self.state, os.O_RDONLY | os.O_DIRECTORY)
        original_read = slice0.read_at

        def corrupt_temp(dir_fd, name, **kwargs):
            data = original_read(dir_fd, name, **kwargs)
            if name.endswith(".tmp"):
                return data + b"x"
            return data

        try:
            with mock.patch.object(slice0, "read_at", side_effect=corrupt_temp):
                with self.assertRaises(slice0.Slice0Error) as caught:
                    slice0.atomic_write_at(
                        directory_fd,
                        "snapshot.json",
                        b'{"ok":true}\n',
                        "run",
                        validator=lambda data: json.loads(data),
                    )
        finally:
            os.close(directory_fd)
        self.assertEqual(caught.exception.reason, "snapshot-race")
        self.assertFalse((self.state / "snapshot.json").exists())

    def test_atomic_write_fails_if_write_makes_no_progress(self) -> None:
        directory_fd = os.open(self.state, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with mock.patch.object(slice0.os, "write", return_value=0):
                with self.assertRaises(slice0.Slice0Error) as caught:
                    slice0.atomic_write_at(
                        directory_fd,
                        "snapshot.json",
                        b'{"ok":true}\n',
                        "zero-write",
                    )
        finally:
            os.close(directory_fd)
        self.assertEqual(caught.exception.reason, "snapshot-write-failed")
        self.assertFalse((self.state / "snapshot.json").exists())
        self.assertFalse((self.state / ".zero-write.snapshot.json.tmp").exists())

    def test_status_progress_oversize_remains_structured(self) -> None:
        directory = self.campaign("large-progress")
        (directory / "stage-receipts").mkdir(parents=True)
        write_json(
            directory / "run-owner.json",
            {
                "schema_version": "deja-review-slice0-owner/v1",
                "run_id": "large",
                "campaign_id": "large-progress",
                "hostname": slice0.socket.gethostname(),
                "pid": os.getpid(),
                "process_start_ticks": slice0.process_start_ticks(os.getpid()),
                "started_at": slice0.utc_now(),
                "input_intent_digest": "1" * 64,
            },
        )
        now = slice0.utc_now()
        write_json(
            directory / "heartbeat.json",
            {
                "schema_version": "deja-review-slice0-heartbeat/v1",
                "run_id": "large",
                "campaign_id": "large-progress",
                "stage": "normalize",
                "completed": 1,
                "total": 2,
                "last_progress_at": now,
                "recorded_at": now,
            },
        )
        (directory / "progress.jsonl").write_bytes(b" " * (16 * 1024 * 1024 + 1))
        result = self.run_cli("status", "--campaign-dir", str(directory))
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["state"], "invalid")
        self.assertEqual(payload["reason_code"], "progress-too-large")

    def test_state_root_swap_after_first_binding_check_removes_receipt(self) -> None:
        original_verify = slice0.verify_campaign_binding
        calls = 0
        detached_root = self.base / "detached-state"

        def racing_verify(fds):
            nonlocal calls
            calls += 1
            original_verify(fds)
            if calls == 1:
                self.state.rename(detached_root)
                self.state.mkdir()

        args = slice0.argparse.Namespace(
            campaign_id="root-race-receipt",
            state_root=str(self.state),
            source=[str(self.source)],
        )
        with mock.patch.object(slice0, "verify_campaign_binding", side_effect=racing_verify):
            with self.assertRaises(slice0.Slice0Error) as caught:
                slice0.prepare(args)
        self.assertEqual(caught.exception.reason, "state-root-race")
        self.assertFalse(
            (
                detached_root
                / "root-race-receipt"
                / "stage-receipts"
                / "prepare.json"
            ).exists()
        )

    def test_deep_json_recursion_fails_cleanly_and_removes_owner(self) -> None:
        deep = self.sources / "deep.json"
        deep.write_text("[" * 3000 + "0" + "]" * 3000, encoding="utf-8")
        result = self.prepare("deep", deep)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)
        directory = self.campaign("deep")
        self.assertFalse((directory / "run-owner.json").exists())
        progress = [
            json.loads(line)
            for line in (directory / "progress.jsonl").read_text().splitlines()
        ]
        self.assertEqual(progress[-1]["outcome"], "failure")

    def test_deep_receipt_and_progress_remain_structured(self) -> None:
        campaign = "deep-controls"
        self.assertEqual(self.prepare(campaign).returncode, 0)
        directory = self.campaign(campaign)
        receipt = directory / "stage-receipts" / "prepare.json"
        receipt.write_text("[" * 3000 + "0" + "]" * 3000, encoding="utf-8")

        validate = self.run_cli("validate", "--campaign-dir", str(directory))
        self.assertEqual(validate.returncode, 2)
        self.assertNotIn("Traceback", validate.stderr)
        status = self.run_cli("status", "--campaign-dir", str(directory))
        self.assertEqual(status.returncode, 2)
        self.assertEqual(json.loads(status.stdout)["reason_code"], "invalid-receipt-or-output")

        receipt.unlink()
        (directory / "progress.jsonl").write_text(
            "[" * 3000 + "0" + "]" * 3000 + "\n",
            encoding="utf-8",
        )
        status = self.run_cli("status", "--campaign-dir", str(directory))
        self.assertEqual(status.returncode, 2)
        self.assertEqual(json.loads(status.stdout)["reason_code"], "invalid-progress")

    def test_non_object_immutable_metadata_fails_without_traceback(self) -> None:
        for target in (
            "prepare.json",
            "campaign.json",
            "source-digests.json",
            "normalizer-manifest.json",
        ):
            with self.subTest(target=target):
                campaign = "container-" + target.replace(".", "-")
                self.assertEqual(self.prepare(campaign).returncode, 0)
                directory = self.campaign(campaign)
                receipt_path = directory / "stage-receipts" / "prepare.json"
                if target == "prepare.json":
                    receipt_path.write_text("null\n", encoding="utf-8")
                else:
                    output_path = directory / target
                    output_path.write_text("null\n", encoding="utf-8")
                    receipt = json.loads(receipt_path.read_text())
                    receipt["outputs"][target] = hashlib.sha256(b"null\n").hexdigest()
                    write_json(receipt_path, receipt)
                validate = self.run_cli("validate", "--campaign-dir", str(directory))
                self.assertEqual(validate.returncode, 2)
                self.assertNotIn("Traceback", validate.stderr)
                status = self.run_cli("status", "--campaign-dir", str(directory))
                self.assertEqual(status.returncode, 2)
                self.assertEqual(json.loads(status.stdout)["state"], "invalid")

    def test_jsonl_validation_digest_must_match_receipt(self) -> None:
        campaign = "jsonl-validation-digest"
        self.assertEqual(self.prepare(campaign).returncode, 0)
        fds = slice0.open_campaign(str(self.state), campaign, create=False)
        original_validate = slice0.stream_validate_jsonl_at

        def wrong_digest(dir_fd, name):
            _, size, count, references = original_validate(dir_fd, name)
            return "0" * 64, size, count, references

        try:
            with mock.patch.object(
                slice0,
                "stream_validate_jsonl_at",
                side_effect=wrong_digest,
            ):
                with self.assertRaises(slice0.Slice0Error) as caught:
                    slice0.validate_campaign_dir(fds)
        finally:
            fds.close()
        self.assertIn("receipt digest mismatch", str(caught.exception))

    def test_wrong_typed_progress_is_invalid(self) -> None:
        directory = self.campaign("bad-progress")
        (directory / "stage-receipts").mkdir(parents=True)
        now = slice0.utc_now()
        write_json(
            directory / "run-owner.json",
            {
                "schema_version": "deja-review-slice0-owner/v1",
                "run_id": "run",
                "campaign_id": "bad-progress",
                "hostname": slice0.socket.gethostname(),
                "pid": os.getpid(),
                "process_start_ticks": slice0.process_start_ticks(os.getpid()),
                "started_at": now,
                "input_intent_digest": "d" * 64,
            },
        )
        write_json(
            directory / "heartbeat.json",
            {
                "schema_version": "deja-review-slice0-heartbeat/v1",
                "run_id": "run",
                "campaign_id": "bad-progress",
                "stage": "normalize",
                "completed": 0,
                "total": 1,
                "recorded_at": now,
                "last_progress_at": now,
            },
        )
        bad_records = (
            {"stage": [], "completed": None, "total": "1", "outcome": False},
            {"stage": "normalize", "completed": -1, "total": 1, "outcome": "running"},
            {"stage": "unknown", "completed": 0, "total": 1, "outcome": "running"},
        )
        for index, overrides in enumerate(bad_records):
            record = slice0.progress_event(
                "run",
                "bad-progress",
                "normalize",
                0,
                1,
                started=time.monotonic(),
                outcome="running",
            )
            record.update(overrides)
            write_json(directory / "progress.jsonl", record)
            result = self.run_cli("status", "--campaign-dir", str(directory))
            self.assertEqual(result.returncode, 2, (index, result.stderr))
            payload = json.loads(result.stdout)
            self.assertEqual(payload["reason_code"], "invalid-progress")

    def test_unknown_progress_reason_codes_are_invalid(self) -> None:
        directory = self.campaign("progress-reason")
        (directory / "stage-receipts").mkdir(parents=True)
        now = slice0.utc_now()
        write_json(
            directory / "run-owner.json",
            {
                "schema_version": "deja-review-slice0-owner/v1",
                "run_id": "run",
                "campaign_id": "progress-reason",
                "hostname": slice0.socket.gethostname(),
                "pid": os.getpid(),
                "process_start_ticks": slice0.process_start_ticks(os.getpid()),
                "started_at": now,
                "input_intent_digest": "d" * 64,
            },
        )
        write_json(
            directory / "heartbeat.json",
            {"recorded_at": now, "last_progress_at": now},
        )
        for reason in ("", "future-reason", "x" * 1024):
            with self.subTest(reason=reason[:20]):
                record = slice0.progress_event(
                    "run",
                    "progress-reason",
                    "discover",
                    0,
                    1,
                    started=time.monotonic(),
                    outcome="running",
                )
                record["reason_code"] = reason
                write_json(directory / "progress.jsonl", record)
                result = self.run_cli("status", "--campaign-dir", str(directory))
                self.assertEqual(result.returncode, 2)
                self.assertEqual(
                    json.loads(result.stdout)["reason_code"],
                    "invalid-progress",
                )

    def test_noncanonical_progress_line_endings_are_invalid(self) -> None:
        directory = self.campaign("progress-boundary")
        (directory / "stage-receipts").mkdir(parents=True)
        now = slice0.utc_now()
        write_json(
            directory / "run-owner.json",
            {
                "schema_version": "deja-review-slice0-owner/v1",
                "run_id": "run",
                "campaign_id": "progress-boundary",
                "hostname": slice0.socket.gethostname(),
                "pid": os.getpid(),
                "process_start_ticks": slice0.process_start_ticks(os.getpid()),
                "started_at": now,
                "input_intent_digest": "d" * 64,
            },
        )
        write_json(
            directory / "heartbeat.json",
            {"recorded_at": now, "last_progress_at": now},
        )
        record = slice0.canonical_bytes(
            slice0.progress_event(
                "run",
                "progress-boundary",
                "discover",
                1,
                1,
                started=time.monotonic(),
                outcome="running",
            )
        )
        for content in (
            record.rstrip(b"\n"),
            record.rstrip(b"\n") + b"\r\n",
            record.rstrip(b"\n") + b"\r" + record,
        ):
            with self.subTest(content=content[-2:]):
                (directory / "progress.jsonl").write_bytes(content)
                result = self.run_cli(
                    "status",
                    "--campaign-dir",
                    str(directory),
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout)["reason_code"], "invalid-progress")

    def test_status_does_not_echo_unvalidated_immutable_content(self) -> None:
        directory = self.campaign("status-untrusted-immutable")
        (directory / "stage-receipts").mkdir(parents=True)
        secret = "UNTRUSTED_IMMUTABLE_CONTENT"
        write_json(
            directory / "campaign.json",
            {"immutable_input_digest": secret},
        )

        result = self.run_cli("status", "--campaign-dir", str(directory))

        self.assertEqual(result.returncode, 2, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIsNone(payload["immutable_input_digest"])
        self.assertNotIn(secret, result.stdout + result.stderr)

    def test_progress_append_retries_partial_writes(self) -> None:
        fds = slice0.open_campaign(str(self.state), "partial-progress", create=True)
        original_write = slice0.os.write
        calls = 0

        def partial_write(fd, data):
            nonlocal calls
            calls += 1
            raw = bytes(data)
            if calls == 1 and len(raw) > 1:
                return original_write(fd, raw[:-1])
            return original_write(fd, raw)

        event = slice0.progress_event(
            "run",
            "partial-progress",
            "discover",
            1,
            1,
            started=time.monotonic(),
            outcome="running",
            digest="a" * 64,
        )
        try:
            with mock.patch.object(slice0.os, "write", side_effect=partial_write):
                slice0.append_progress(fds, event)
        finally:
            fds.close()
        self.assertGreaterEqual(calls, 2)
        lines = (self.campaign("partial-progress") / "progress.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0]), event)

    def test_progress_reader_waits_across_partial_append(self) -> None:
        fds = slice0.open_campaign(str(self.state), "progress-lock", create=True)
        original_write = slice0.os.write
        prefix_written = slice0.threading.Event()
        release_writer = slice0.threading.Event()
        reader_done = slice0.threading.Event()
        observed: list[bytes] = []
        calls = 0
        event = slice0.progress_event(
            "run",
            "progress-lock",
            "discover",
            1,
            1,
            started=time.monotonic(),
            outcome="running",
        )

        def paused_partial_write(fd, data):
            nonlocal calls
            calls += 1
            raw = bytes(data)
            if calls == 1:
                split = max(1, len(raw) // 2)
                written = original_write(fd, raw[:split])
                prefix_written.set()
                release_writer.wait(timeout=5)
                return written
            return original_write(fd, raw)

        def write_event():
            slice0.append_progress(fds, event)

        def read_event():
            observed.append(slice0.read_progress_at(fds, limit=1024 * 1024))
            reader_done.set()

        try:
            with mock.patch.object(
                slice0.os,
                "write",
                side_effect=paused_partial_write,
            ):
                writer = slice0.threading.Thread(target=write_event)
                writer.start()
                self.assertTrue(prefix_written.wait(timeout=2))
                reader = slice0.threading.Thread(target=read_event)
                reader.start()
                time.sleep(0.1)
                self.assertFalse(reader_done.is_set())
                release_writer.set()
                writer.join()
                reader.join()
        finally:
            release_writer.set()
            fds.close()
        self.assertEqual(observed, [slice0.canonical_bytes(event)])

    def test_jsonl_publication_fails_if_write_makes_no_progress(self) -> None:
        fds = slice0.open_campaign(str(self.state), "zero-jsonl", create=True)
        spool_name = ".run.artifact-0001.spool"
        (self.campaign("zero-jsonl") / spool_name).write_bytes(b"{}\n")
        try:
            with mock.patch.object(slice0.os, "write", return_value=0):
                with self.assertRaises(slice0.Slice0Error) as caught:
                    slice0.publish_jsonl_from_spools(
                        fds,
                        [("b" * 64, spool_name, 1)],
                        "run",
                    )
        finally:
            fds.close()
        self.assertEqual(caught.exception.reason, "snapshot-write-failed")
        self.assertFalse(
            (self.campaign("zero-jsonl") / ".run.normalized-findings.jsonl.tmp").exists()
        )

    def test_cleanup_failures_are_visible(self) -> None:
        args = slice0.argparse.Namespace(
            campaign_id="cleanup-failure",
            state_root=str(self.state),
            source=[str(self.source)],
        )
        original_append = slice0.append_progress
        append_calls = 0

        def fail_cleanup_append(fds, payload):
            nonlocal append_calls
            append_calls += 1
            if payload["outcome"] == "failure":
                raise OSError(errno.EIO, "injected progress cleanup failure")
            return original_append(fds, payload)

        original_unlink = slice0.os.unlink
        original_projection = slice0.resource_projection

        def fail_owner_unlink(path, *args, **kwargs):
            if path == "run-owner.json":
                raise OSError(errno.EPERM, "injected owner cleanup failure")
            return original_unlink(path, *args, **kwargs)

        def denied_projection(*args, **kwargs):
            result = original_projection(*args, **kwargs)
            result["disk_pass"] = False
            return result

        stderr = io.StringIO()
        with (
            mock.patch.object(slice0, "append_progress", side_effect=fail_cleanup_append),
            mock.patch.object(slice0.os, "unlink", side_effect=fail_owner_unlink),
            mock.patch.object(slice0, "resource_projection", side_effect=denied_projection),
            redirect_stderr(stderr),
        ):
            with self.assertRaises(slice0.Slice0Error):
                slice0.prepare(args)
        self.assertGreaterEqual(append_calls, 2)
        self.assertIn("cleanup-failure", stderr.getvalue())
        self.assertIn("terminal-progress failed (errno=5)", stderr.getvalue())
        self.assertIn("owner-removal failed (errno=1)", stderr.getvalue())

    def test_raw_oserror_after_owner_publication_runs_terminal_cleanup(self) -> None:
        args = slice0.argparse.Namespace(
            campaign_id="operational-failure",
            state_root=str(self.state),
            source=[str(self.source)],
        )
        original_append = slice0.append_progress
        calls = 0

        def fail_first_append(fds, payload):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError(errno.ENOSPC, "injected append failure")
            return original_append(fds, payload)

        with mock.patch.object(slice0, "append_progress", side_effect=fail_first_append):
            with self.assertRaises(slice0.Slice0Error) as caught:
                slice0.prepare(args)
        self.assertEqual(caught.exception.reason, "operation-failed")
        self.assertIn("prepare-operation failed (errno=28)", str(caught.exception))
        directory = self.campaign("operational-failure")
        self.assertFalse((directory / "run-owner.json").exists())
        events = [
            json.loads(line)
            for line in (directory / "progress.jsonl").read_text().splitlines()
        ]
        self.assertEqual(events[-1]["outcome"], "failure")
        self.assertEqual(events[-1]["reason_code"], "operation-failed")

    def test_pre_receipt_cross_check_failure_leaves_no_receipt(self) -> None:
        args = slice0.argparse.Namespace(
            campaign_id="pre-receipt-validation",
            state_root=str(self.state),
            source=[str(self.source)],
        )
        original_write = slice0.atomic_write_at

        def corrupt_resource(dir_fd, name, data, run_id, *, validator=None):
            if name == "resource-preflight.json":
                payload = json.loads(data)
                payload["disk_pass"] = False
                data = slice0.canonical_bytes(payload)
            return original_write(
                dir_fd,
                name,
                data,
                run_id,
                validator=validator,
            )

        with mock.patch.object(slice0, "atomic_write_at", side_effect=corrupt_resource):
            with self.assertRaises(slice0.Slice0Error) as caught:
                slice0.prepare(args)
        self.assertEqual(caught.exception.reason, "invalid-output")
        directory = self.campaign("pre-receipt-validation")
        self.assertFalse((directory / "stage-receipts" / "prepare.json").exists())
        self.assertFalse((directory / "run-owner.json").exists())

    def test_source_inside_campaign_state_is_rejected_without_overwrite(self) -> None:
        directory = self.campaign("source-overlap")
        directory.mkdir()
        overlapping_source = directory / "campaign.json"
        original = write_json(overlapping_source, artifact())

        result = self.prepare("source-overlap", overlapping_source)

        self.assertEqual(result.returncode, 2)
        self.assertIn("source-state-overlap", result.stderr)
        self.assertEqual(overlapping_source.read_bytes(), original)
        self.assertFalse((directory / "stage-receipts" / "prepare.json").exists())

    def test_watchdog_remains_active_through_receipt_and_owner_cleanup(self) -> None:
        args = slice0.argparse.Namespace(
            campaign_id="watchdog-terminal",
            state_root=str(self.state),
            source=[str(self.source)],
        )
        original_write = slice0.atomic_write_at
        original_cleanup = slice0.cleanup_owner_after_success
        original_stop = slice0.RunWatchdog.stop
        stop_calls: list[str] = []

        def observe_write(dir_fd, name, data, run_id, *, validator=None):
            if name == "prepare.json":
                self.assertFalse(stop_calls)
            return original_write(
                dir_fd,
                name,
                data,
                run_id,
                validator=validator,
            )

        def observe_cleanup(fds):
            self.assertFalse(stop_calls)
            return original_cleanup(fds)

        def observe_stop(watchdog):
            stop_calls.append("stop")
            return original_stop(watchdog)

        with (
            mock.patch.object(slice0, "atomic_write_at", side_effect=observe_write),
            mock.patch.object(
                slice0,
                "cleanup_owner_after_success",
                side_effect=observe_cleanup,
            ),
            mock.patch.object(slice0.RunWatchdog, "stop", new=observe_stop),
        ):
            self.assertEqual(slice0.prepare(args), 0)
        self.assertEqual(stop_calls, ["stop"])

    def test_post_receipt_owner_cleanup_failure_preserves_success(self) -> None:
        for operation in ("unlink", "fsync"):
            with self.subTest(operation=operation):
                campaign = f"post-receipt-{operation}"
                directory = self.campaign(campaign)
                args = slice0.argparse.Namespace(
                    campaign_id=campaign,
                    state_root=str(self.state),
                    source=[str(self.source)],
                )
                original_unlink = slice0.os.unlink
                original_fsync = slice0.os.fsync

                def fail_owner_unlink(path, *call_args, **call_kwargs):
                    if path == "run-owner.json":
                        raise OSError(errno.EIO, "injected owner unlink failure")
                    return original_unlink(path, *call_args, **call_kwargs)

                def fail_owner_directory_fsync(fd):
                    stat_result = os.fstat(fd)
                    progress = directory / "progress.jsonl"
                    if (
                        directory.exists()
                        and stat_result.st_ino == directory.stat().st_ino
                        and (directory / "stage-receipts" / "prepare.json").exists()
                        and progress.exists()
                        and json.loads(progress.read_text().splitlines()[-1])["outcome"]
                        == "success"
                    ):
                        raise OSError(errno.ENOSPC, "injected owner directory fsync failure")
                    return original_fsync(fd)

                patcher = (
                    mock.patch.object(
                        slice0.os,
                        "unlink",
                        side_effect=fail_owner_unlink,
                    )
                    if operation == "unlink"
                    else mock.patch.object(
                        slice0.os,
                        "fsync",
                        side_effect=fail_owner_directory_fsync,
                    )
                )
                stderr = io.StringIO()
                with patcher, redirect_stderr(stderr):
                    self.assertEqual(slice0.prepare(args), 0)
                self.assertIn("post-receipt-cleanup-warning", stderr.getvalue())
                self.assertTrue(
                    (directory / "stage-receipts" / "prepare.json").exists()
                )
                events = [
                    json.loads(line)
                    for line in (directory / "progress.jsonl").read_text().splitlines()
                ]
                self.assertEqual(events[-1]["outcome"], "success")
                status = self.run_cli("status", "--campaign-dir", str(directory))
                self.assertEqual(status.returncode, 0, status.stderr)
                self.assertEqual(json.loads(status.stdout)["state"], "complete")

    def test_snapshot_publication_error_has_safe_operation_context(self) -> None:
        fds = slice0.open_campaign(str(self.state), "publication-context", create=True)
        try:
            with mock.patch.object(
                slice0.os,
                "replace",
                side_effect=OSError(errno.EIO, "injected replace failure"),
            ):
                with self.assertRaises(slice0.Slice0Error) as caught:
                    slice0.atomic_write_at(
                        fds.campaign_fd,
                        "campaign.json",
                        b"{}\n",
                        "run",
                    )
        finally:
            fds.close()
        self.assertEqual(caught.exception.reason, "operation-failed")
        self.assertIn(
            "snapshot-publication:campaign.json failed (errno=5)",
            str(caught.exception),
        )

    def test_receipt_rollback_failures_preserve_binding_error_context(self) -> None:
        cases = (
            ("unlink", "receipt-unlink", errno.EIO),
            ("fsync", "receipt-directory-fsync", errno.ENOSPC),
        )
        for operation, label, error_number in cases:
            with self.subTest(operation=operation):
                campaign = f"receipt-rollback-{operation}"
                fds = slice0.open_campaign(str(self.state), campaign, create=True)
                receipt = self.campaign(campaign) / "stage-receipts" / "prepare.json"
                receipt.write_bytes(b"{}\n")
                binding = slice0.Slice0Error(
                    "campaign path changed before receipt",
                    reason="campaign-path-race",
                )
                try:
                    patcher = mock.patch.object(
                        slice0.os,
                        operation,
                        side_effect=OSError(error_number, f"injected {operation} failure"),
                    )
                    with patcher:
                        with self.assertRaises(slice0.Slice0Error) as caught:
                            slice0.rollback_receipt_after_binding_failure(fds, binding)
                finally:
                    fds.close()
                self.assertEqual(caught.exception.reason, "campaign-path-race")
                self.assertIn("campaign path changed before receipt", str(caught.exception))
                self.assertIn(f"{label} failed", str(caught.exception))
                self.assertIn(f"errno={error_number}", str(caught.exception))

    def test_retained_state_scan_fails_closed(self) -> None:
        with mock.patch.object(
            slice0.os,
            "listdir",
            side_effect=PermissionError("denied"),
        ):
            with self.assertRaises(slice0.Slice0Error) as caught:
                slice0.retained_campaign_bytes(0)
        self.assertEqual(caught.exception.reason, "retained-state-scan-failed")

    def test_retained_state_scan_stays_bound_to_campaign_descriptor(self) -> None:
        campaign = "retained-descriptor"
        directory = self.campaign(campaign)
        (directory / "stage-receipts").mkdir(parents=True)
        (directory / "held.bin").write_bytes(b"held")
        fds = slice0.open_campaign(str(self.state), campaign, create=False)
        detached = self.base / "retained-detached"
        try:
            directory.rename(detached)
            (directory / "stage-receipts").mkdir(parents=True)
            (directory / "replacement.bin").write_bytes(b"x" * 10_000)
            self.assertEqual(
                slice0.retained_campaign_bytes(fds.campaign_fd),
                len(b"held"),
            )
        finally:
            fds.close()

    def test_watchdog_refreshes_heartbeat_without_progress(self) -> None:
        fds = slice0.open_campaign(str(self.state), "watchdog-heartbeat", create=True)
        watchdog = slice0.RunWatchdog(fds, "watchdog")
        try:
            with (
                mock.patch.object(slice0, "STALL_SECONDS", 1),
                mock.patch.object(slice0, "DEADLINE_SECONDS", 10),
            ):
                watchdog.start()
                watchdog.update("normalize", 1, 3)
                first = json.loads(
                    (self.campaign("watchdog-heartbeat") / "heartbeat.json").read_text()
                )
                time.sleep(0.7)
                second = json.loads(
                    (self.campaign("watchdog-heartbeat") / "heartbeat.json").read_text()
                )
                self.assertNotEqual(first["recorded_at"], second["recorded_at"])
                self.assertEqual(first["last_progress_at"], second["last_progress_at"])
        finally:
            watchdog.stop()
            fds.close()

    def test_watchdog_start_publishes_initial_heartbeat_and_stop_disarms_signal(self) -> None:
        fds = slice0.open_campaign(str(self.state), "watchdog-start", create=True)
        watchdog = slice0.RunWatchdog(fds, "watchdog-start")
        try:
            watchdog.start()
            heartbeat = json.loads(
                (self.campaign("watchdog-start") / "heartbeat.json").read_text()
            )
            self.assertEqual(heartbeat["stage"], "discover")
            watchdog.stop()
            watchdog._alarm(slice0.WATCHDOG_SIGNAL, None)
        finally:
            watchdog.stop()
            fds.close()

    def test_watchdog_stop_waits_for_worker_termination(self) -> None:
        fds = slice0.open_campaign(str(self.state), "watchdog-barrier", create=True)
        watchdog = slice0.RunWatchdog(fds, "watchdog-barrier")
        entered = slice0.threading.Event()
        release = slice0.threading.Event()
        original_publish = watchdog._publish_locked
        calls = 0

        def blocking_publish():
            nonlocal calls
            calls += 1
            if calls > 1:
                entered.set()
                release.wait()
            return original_publish()

        def delayed_release():
            self.assertTrue(entered.wait(timeout=2))
            time.sleep(2.1)
            release.set()

        try:
            with (
                mock.patch.object(slice0, "STALL_SECONDS", 0.1),
                mock.patch.object(watchdog, "_publish_locked", side_effect=blocking_publish),
            ):
                watchdog.start()
                releaser = slice0.threading.Thread(target=delayed_release)
                releaser.start()
                self.assertTrue(entered.wait(timeout=2))
                started = time.monotonic()
                watchdog.stop()
                elapsed = time.monotonic() - started
                releaser.join()
            self.assertGreaterEqual(elapsed, 2)
            self.assertFalse(watchdog._thread.is_alive())
        finally:
            release.set()
            watchdog.stop()
            fds.close()

    def test_heartbeat_timeout_does_not_retain_authoritative_lock(self) -> None:
        fds = slice0.open_campaign(str(self.state), "heartbeat-reap", create=True)
        lock_fd = slice0.lock_campaign(fds)
        release_reaper = slice0.threading.Event()
        captured_pass_fd: list[int] = []

        class StuckProcess:
            returncode = None

            def communicate(self, input=None, timeout=None):
                raise slice0.subprocess.TimeoutExpired("heartbeat", timeout)

            def kill(self):
                return None

            def wait(self):
                release_reaper.wait(timeout=5)
                return -9

        def fake_popen(*args, **kwargs):
            captured_pass_fd.extend(kwargs["pass_fds"])
            return StuckProcess()

        watchdog = slice0.RunWatchdog(fds, "heartbeat-reap")
        started = time.monotonic()
        try:
            with (
                mock.patch.object(slice0.subprocess, "Popen", side_effect=fake_popen),
                mock.patch.object(slice0, "HEARTBEAT_IO_TIMEOUT_SECONDS", 0.05),
                mock.patch.object(slice0, "HEARTBEAT_REAP_TIMEOUT_SECONDS", 0.05),
            ):
                with self.assertRaises(slice0.Slice0Error) as caught:
                    watchdog._publish_locked()
            self.assertEqual(caught.exception.reason, "heartbeat-watchdog-failed")
            self.assertLess(time.monotonic() - started, 1)
            self.assertEqual(len(captured_pass_fd), 1)
            self.assertNotEqual(captured_pass_fd[0], fds.campaign_fd)
            with self.assertRaises(OSError):
                os.fstat(captured_pass_fd[0])
        finally:
            os.close(lock_fd)
            fds.close()

        replacement = slice0.open_campaign(str(self.state), "heartbeat-reap", create=False)
        replacement_lock = -1
        try:
            replacement_lock = slice0.lock_campaign(replacement)
        finally:
            release_reaper.set()
            if replacement_lock >= 0:
                os.close(replacement_lock)
            replacement.close()

    def test_prepare_cleanup_and_locks_survive_unreapable_heartbeat_helper(self) -> None:
        campaign = "heartbeat-prepare-reap"
        directory = self.campaign(campaign)
        args = slice0.argparse.Namespace(
            campaign_id=campaign,
            state_root=str(self.state),
            source=[str(self.source)],
        )
        helper_started = slice0.threading.Event()
        release_reaper = slice0.threading.Event()
        original_popen = slice0.subprocess.Popen
        original_projection = slice0.resource_projection

        class StuckProcess:
            returncode = None

            def communicate(self, input=None, timeout=None):
                raise slice0.subprocess.TimeoutExpired("heartbeat", timeout)

            def kill(self):
                return None

            def wait(self):
                release_reaper.wait(timeout=5)
                return -9

        def route_popen(*call_args, **call_kwargs):
            if slice0.threading.current_thread() is slice0.threading.main_thread():
                return original_popen(*call_args, **call_kwargs)
            helper_started.set()
            return StuckProcess()

        def wait_for_helper(*projection_args, **projection_kwargs):
            self.assertTrue(helper_started.wait(timeout=3))
            time.sleep(5)
            return original_projection(*projection_args, **projection_kwargs)

        try:
            with (
                mock.patch.object(
                    slice0.subprocess,
                    "Popen",
                    side_effect=route_popen,
                ),
                mock.patch.object(slice0, "STALL_SECONDS", 0.1),
                mock.patch.object(slice0, "HEARTBEAT_IO_TIMEOUT_SECONDS", 0.05),
                mock.patch.object(slice0, "HEARTBEAT_REAP_TIMEOUT_SECONDS", 0.05),
                mock.patch.object(
                    slice0,
                    "resource_projection",
                    side_effect=wait_for_helper,
                ),
            ):
                with self.assertRaises(slice0.Slice0Error) as caught:
                    slice0.prepare(args)
            self.assertEqual(caught.exception.reason, "heartbeat-watchdog-failed")
            self.assertFalse((directory / "run-owner.json").exists())
            self.assertFalse(
                (directory / "stage-receipts" / "prepare.json").exists()
            )

            replacement = slice0.open_campaign(str(self.state), campaign, create=False)
            replacement_lock = -1
            try:
                replacement_lock = slice0.lock_campaign(replacement)
            finally:
                if replacement_lock >= 0:
                    os.close(replacement_lock)
                replacement.close()
        finally:
            release_reaper.set()

    def test_deadline_cleanup_runs_while_heartbeat_publish_is_blocked(self) -> None:
        args = slice0.argparse.Namespace(
            campaign_id="watchdog-deadline-cleanup",
            state_root=str(self.state),
            source=[str(self.source)],
        )
        directory = self.campaign("watchdog-deadline-cleanup")
        entered = slice0.threading.Event()
        release = slice0.threading.Event()
        cleanup_observed = slice0.threading.Event()
        original_publish = slice0.RunWatchdog._publish_locked
        original_projection = slice0.resource_projection

        def blocking_background_publish(watchdog):
            if slice0.threading.current_thread() is not slice0.threading.main_thread():
                entered.set()
                release.wait(timeout=5)
            return original_publish(watchdog)

        def wait_past_deadline(*projection_args, **projection_kwargs):
            self.assertTrue(entered.wait(timeout=3))
            time.sleep(5)
            return original_projection(*projection_args, **projection_kwargs)

        def observe_cleanup():
            owner = directory / "run-owner.json"
            limit = time.monotonic() + 5
            while time.monotonic() < limit and not owner.exists():
                time.sleep(0.01)
            while time.monotonic() < limit and owner.exists():
                time.sleep(0.01)
            if not owner.exists():
                progress = directory / "progress.jsonl"
                if progress.exists():
                    events = [json.loads(line) for line in progress.read_text().splitlines()]
                    if events and events[-1]["reason_code"] == "foundation-deadline-exceeded":
                        cleanup_observed.set()
            release.set()

        observer = slice0.threading.Thread(target=observe_cleanup)
        observer.start()
        try:
            with (
                mock.patch.object(slice0, "STALL_SECONDS", 0.1),
                mock.patch.object(slice0, "DEADLINE_SECONDS", 1.5),
                mock.patch.object(
                    slice0.RunWatchdog,
                    "_publish_locked",
                    side_effect=blocking_background_publish,
                    autospec=True,
                ),
                mock.patch.object(
                    slice0,
                    "resource_projection",
                    side_effect=wait_past_deadline,
                ),
            ):
                with self.assertRaises(slice0.Slice0Error) as caught:
                    slice0.prepare(args)
            self.assertEqual(caught.exception.reason, "foundation-deadline-exceeded")
        finally:
            release.set()
            observer.join(timeout=5)
        self.assertTrue(cleanup_observed.is_set())
        self.assertFalse((directory / "stage-receipts" / "prepare.json").exists())

    def test_unprobeable_existing_process_is_not_declared_dead(self) -> None:
        directory = self.campaign("unprobeable-owner")
        (directory / "stage-receipts").mkdir(parents=True)
        now = slice0.utc_now()
        write_json(
            directory / "run-owner.json",
            {
                "schema_version": "deja-review-slice0-owner/v1",
                "run_id": "run",
                "campaign_id": "unprobeable-owner",
                "hostname": slice0.socket.gethostname(),
                "pid": os.getpid(),
                "process_start_ticks": "1",
                "started_at": now,
                "input_intent_digest": "d" * 64,
            },
        )
        write_json(
            directory / "heartbeat.json",
            {"recorded_at": now, "last_progress_at": now},
        )
        fds = slice0.open_campaign(
            str(self.state),
            "unprobeable-owner",
            create=False,
        )
        try:
            with (
                mock.patch.object(slice0, "process_start_ticks", return_value=None),
                mock.patch.object(slice0, "process_entry_exists", return_value=True),
            ):
                exists, reason, run_id = slice0.owner_liveness_at_status(fds)
        finally:
            fds.close()
        self.assertTrue(exists)
        self.assertEqual(reason, "owner-unverifiable")
        self.assertEqual(run_id, "run")

    def test_status_requires_closed_heartbeat_correlated_to_owner(self) -> None:
        now = slice0.utc_now()
        for index, mutate in enumerate(("wrong-run", "wrong-campaign", "extra")):
            with self.subTest(mutate=mutate):
                campaign = f"status-heartbeat-{index}"
                directory = self.campaign(campaign)
                (directory / "stage-receipts").mkdir(parents=True)
                write_json(
                    directory / "run-owner.json",
                    {
                        "schema_version": "deja-review-slice0-owner/v1",
                        "run_id": "owner-run",
                        "campaign_id": campaign,
                        "hostname": slice0.socket.gethostname(),
                        "pid": os.getpid(),
                        "process_start_ticks": slice0.process_start_ticks(os.getpid()),
                        "started_at": now,
                        "input_intent_digest": "d" * 64,
                    },
                )
                heartbeat = {
                    "schema_version": "deja-review-slice0-heartbeat/v1",
                    "run_id": "owner-run",
                    "campaign_id": campaign,
                    "stage": "normalize",
                    "completed": 0,
                    "total": 1,
                    "last_progress_at": now,
                    "recorded_at": now,
                }
                if mutate == "wrong-run":
                    heartbeat["run_id"] = "other-run"
                elif mutate == "wrong-campaign":
                    heartbeat["campaign_id"] = "other-campaign"
                else:
                    heartbeat["extra"] = True
                write_json(directory / "heartbeat.json", heartbeat)

                result = self.run_cli(
                    "status",
                    "--campaign-dir",
                    str(directory),
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["state"], "invalid")
                self.assertEqual(payload["reason_code"], "invalid-heartbeat")

    def test_status_requires_closed_owner_record(self) -> None:
        now = slice0.utc_now()
        variants = []
        base = {
            "schema_version": "deja-review-slice0-owner/v1",
            "run_id": "run",
            "campaign_id": "",
            "hostname": slice0.socket.gethostname(),
            "pid": os.getpid(),
            "process_start_ticks": slice0.process_start_ticks(os.getpid()),
            "started_at": now,
            "input_intent_digest": "d" * 64,
        }
        missing_schema = dict(base)
        missing_schema.pop("schema_version")
        variants.append(missing_schema)
        wrong_campaign = dict(base)
        wrong_campaign["campaign_id"] = "other"
        variants.append(wrong_campaign)
        extra_key = dict(base)
        extra_key["extra"] = True
        variants.append(extra_key)
        for pid in (True, "1"):
            bad_pid = dict(base)
            bad_pid["pid"] = pid
            variants.append(bad_pid)

        for index, owner in enumerate(variants):
            with self.subTest(index=index):
                campaign = f"status-owner-{index}"
                directory = self.campaign(campaign)
                (directory / "stage-receipts").mkdir(parents=True)
                if owner.get("campaign_id") == "":
                    owner["campaign_id"] = campaign
                write_json(directory / "run-owner.json", owner)
                write_json(
                    directory / "heartbeat.json",
                    {"recorded_at": now, "last_progress_at": now},
                )
                result = self.run_cli(
                    "status",
                    "--campaign-dir",
                    str(directory),
                )
                self.assertEqual(result.returncode, 2)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["state"], "invalid")
                self.assertEqual(payload["reason_code"], "invalid-owner")

    def test_watchdog_interrupts_inside_long_operation(self) -> None:
        fds = slice0.open_campaign(str(self.state), "watchdog-deadline", create=True)
        watchdog = slice0.RunWatchdog(
            fds,
            "deadline",
            deadline=time.monotonic() + 1,
        )
        try:
            watchdog.start()
            with self.assertRaises(slice0.Slice0Error) as caught:
                time.sleep(2)
            self.assertEqual(caught.exception.reason, "foundation-deadline-exceeded")
        finally:
            watchdog.stop()
            fds.close()

    def test_recent_artifact_progress_remains_running_across_old_run_window(self) -> None:
        directory = self.campaign("positive-progress")
        (directory / "stage-receipts").mkdir(parents=True)
        now = slice0.utc_now()
        write_json(
            directory / "run-owner.json",
            {
                "schema_version": "deja-review-slice0-owner/v1",
                "run_id": "run",
                "campaign_id": "positive-progress",
                "hostname": slice0.socket.gethostname(),
                "pid": os.getpid(),
                "process_start_ticks": slice0.process_start_ticks(os.getpid()),
                "started_at": now,
                "input_intent_digest": "d" * 64,
            },
        )
        event = slice0.progress_event(
            "run",
            "positive-progress",
            "normalize",
            2,
            3,
            started=time.monotonic() - 130,
            outcome="running",
            digest="2" * 64,
        )
        write_json(directory / "progress.jsonl", event)
        write_json(
            directory / "heartbeat.json",
            {
                "schema_version": "deja-review-slice0-heartbeat/v1",
                "run_id": "run",
                "campaign_id": "positive-progress",
                "stage": "normalize",
                "completed": 2,
                "total": 3,
                "recorded_at": now,
                "last_progress_at": now,
            },
        )
        result = self.run_cli("status", "--campaign-dir", str(directory))
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)["state"], "running")

    def test_orphan_temporaries_do_not_affect_validate_or_status(self) -> None:
        self.assertEqual(self.prepare().returncode, 0)
        directory = self.campaign()
        (directory / ".foreign.partial.tmp").write_text("{", encoding="utf-8")
        (directory / ".foreign.artifact-9999.spool").write_text("not-json", encoding="utf-8")
        validate = self.run_cli("validate", "--campaign-dir", str(directory))
        self.assertEqual(validate.returncode, 0, validate.stderr)
        status = self.run_cli("status", "--campaign-dir", str(directory))
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)["state"], "complete")


if __name__ == "__main__":
    unittest.main()
