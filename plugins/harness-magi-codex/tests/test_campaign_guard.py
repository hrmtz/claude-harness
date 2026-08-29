#!/usr/bin/env python3
"""Regression tests for bounded campaigns and convergence classifications."""

from __future__ import annotations

import copy
import json
import hashlib
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock
from pathlib import Path

import jsonschema


HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
GUARD = PLUGIN / "scripts" / "magi_campaign_guard.py"
FANOUT = PLUGIN / "scripts" / "magi_fanout_codex.sh"
XFAMILY = PLUGIN / "scripts" / "magi_xfamily.sh"
VALIDATOR = PLUGIN / "scripts" / "magi_validate_findings.py"
SCHEMA = json.loads((PLUGIN / "schemas" / "finding.schema.json").read_text())
ISSUE_271_LEDGER_FIXTURE = (
    HERE / "fixtures" / "issue_271_actual_ledger_sanitized.json"
)
ISSUE_271_SOURCE_CLAIM = "b1dd62bd-1f56-4fbc-a3cc-d01bdcd2e845"
ISSUE_271_SANITIZED_HISTORY_SHA256 = (
    "a59c89ed8254ef49d2bfd1b9312f3620356fa65ca84af05dc094701bc65db174"
)
ISSUE_271_ATTESTATION = {
    "incident_id": "claude-harness-271-hippocampus-262-schema-startup",
    "issue": "hrmtz/claude-harness#271",
    "doc_id": "8fe2b3353e5e4a5b",
    "source_claim_id": ISSUE_271_SOURCE_CLAIM,
    "source_finished_at": "2026-07-30T06:13:21.767990+00:00",
    "artifact_sha": "34c165c9c5447fafc2b9e27cc119ef32721fa5022b6e137010d5d8cc131cf59a",
    "source_protocol_sha": "26d2f729c8b1639e28f176622424608f7c5e1c99e413584cf1953201c3473171",
    "history_launch_count": 6,
    "history_gross_model_launches": 14,
    "history_prefix_sha256": "3edb25a926a9bf6050cd263a0b2402ff5f0eadbd9b24c24a2b4d425e23f10fb7",
    "credited_model_launches": 3,
    "provider_stage": "codex-output-schema-validation-before-reviewer-turn",
    "reviewer_count": 3,
    "turn_observed": False,
    "legacy_classification": "provider-exit",
}
ISSUE_271_OLD_SOURCE_CLAIM = "0f0bd40b-f015-461d-8f44-fc7e47c4657a"
sys.path.insert(0, str(PLUGIN / "scripts"))
from magi_validate_findings import validate as validate_findings  # noqa: E402
import magi_campaign_guard as campaign_guard  # noqa: E402
from magi_campaign_guard import (  # noqa: E402
    DEFAULT_MAX_MODEL_LAUNCHES,
    GLOBAL_MAX_MODEL_LAUNCHES,
    PHASE_WEIGHT,
    SCOPED_GLOBAL_CEILING_OVERRIDES,
    global_ceiling_policy,
    scope_review_checkpoint_required,
    protocol_sha,
)


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
        "subsystem": "orchestration",
        "root_cause_id": "test.root",
        "affected_invariant": "test invariant",
        "changes_design_invariant": False,
        "relation_to_prior": "none",
    }
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


class ScopedFuseAuthorityTests(unittest.TestCase):
    def test_scoped_authority_extends_only_the_exact_slice_path(self) -> None:
        fi_path = Path(next(
            path for path in SCOPED_GLOBAL_CEILING_OVERRIDES if "TELEMETRY-FI" in path
        ))
        ceiling, authority = global_ceiling_policy(fi_path)
        self.assertEqual(ceiling, 36)
        self.assertIsNotNone(authority)
        assert authority is not None
        self.assertEqual(authority["default_ceiling"], GLOBAL_MAX_MODEL_LAUNCHES)
        self.assertEqual(authority["previous_scoped_ceiling"], 20)
        self.assertEqual(authority["intermediate_authorized_ceiling"], 24)
        self.assertEqual(authority["additional_slots"], 12)
        self.assertEqual(authority["doc_id"], campaign_guard.doc_id(fi_path))

        e2a_path = Path(next(
            path for path in SCOPED_GLOBAL_CEILING_OVERRIDES if "E2a3a1" in path
        ))
        e2a_ceiling, e2a_authority = global_ceiling_policy(e2a_path)
        self.assertEqual(e2a_ceiling, 34)
        self.assertIsNotNone(e2a_authority)
        assert e2a_authority is not None
        self.assertEqual(e2a_authority["authorized_max_ceiling"], 34)
        self.assertEqual(e2a_authority["previous_scoped_ceiling"], 30)
        self.assertEqual(e2a_authority["prior_usage"], 30)
        self.assertEqual(e2a_authority["additional_slots"], 4)
        self.assertFalse(scope_review_checkpoint_required(e2a_path, 33))
        self.assertTrue(scope_review_checkpoint_required(e2a_path, 34))
        historical_e2a = campaign_guard.HISTORICAL_SCOPED_GLOBAL_CEILING_AUTHORITIES[
            str(e2a_path)
        ]
        self.assertEqual(len(historical_e2a), 4)
        self.assertEqual(
            historical_e2a[0]["authority_id"],
            "ZN6-E2A3A1-USER-ACK-2026-08-22-CHECKPOINT-14-18",
        )
        self.assertEqual(historical_e2a[0]["new_ceiling"], 18)
        self.assertEqual(
            historical_e2a[1]["authority_id"],
            "ZN6-E2A3A1-USER-ACK-2026-08-22-CHECKPOINT-18-22",
        )
        self.assertEqual(historical_e2a[1]["new_ceiling"], 22)
        self.assertEqual(
            historical_e2a[2]["authority_id"],
            "ZN6-E2A3A1-USER-ACK-2026-08-22-CHECKPOINT-22-26",
        )
        self.assertEqual(historical_e2a[2]["new_ceiling"], 26)
        self.assertEqual(
            historical_e2a[3]["authority_id"],
            "ZN6-E2A3A1-USER-ACK-2026-08-22-CHECKPOINT-26-30",
        )
        self.assertEqual(historical_e2a[3]["new_ceiling"], 30)

        knock_f0_path = Path(next(
            path
            for path in SCOPED_GLOBAL_CEILING_OVERRIDES
            if "KNOCK_TELEMETRY_F0_OVERLAY" in path
        ))
        knock_f0_ceiling, knock_f0_authority = global_ceiling_policy(knock_f0_path)
        self.assertEqual(knock_f0_ceiling, 27)
        self.assertIsNotNone(knock_f0_authority)
        assert knock_f0_authority is not None
        self.assertEqual(
            knock_f0_authority["authority_id"],
            "ZN6-KNOCK-F0-USER-AUTH-2026-08-23-SEQ4689-SCOPE4708-4709-23-27",
        )
        self.assertEqual(knock_f0_authority["authority_reference_mailbox_seq"], 4689)
        self.assertEqual(knock_f0_authority["scope_correction_mailbox_seqs"], [4708, 4709])
        self.assertEqual(
            knock_f0_authority["prior_authority_id"],
            "ZN6-KNOCK-F0-USER-AUTH-2026-08-23-SEQ4689-19-23",
        )
        self.assertEqual(
            knock_f0_authority["scope_basis_campaign_id"],
            "e6348644-1bea-45cf-92c1-0192cf06cb5d",
        )
        self.assertEqual(
            knock_f0_authority["scope_basis_claim_id"],
            "396758f8-fbe5-4301-8998-c81da0a96b63",
        )
        self.assertEqual(
            knock_f0_authority["scope_basis_review_output_sha256"],
            "6f9af979bc9fb0c18b565cd03c17da962ded039d8c85e8e26846bd609af50721",
        )
        self.assertEqual(knock_f0_authority["scope_basis_finding_id"], "XF-R2-001")
        self.assertEqual(
            knock_f0_authority["scope_basis_root_cause_id"],
            "recorder.namespace_generation_unbound",
        )
        self.assertEqual(knock_f0_authority["scope_basis_reported_severity"], "HIGH")
        self.assertEqual(
            knock_f0_authority["scope_corrected_classification"],
            "OPTIONAL_OPERATIONAL_HARDENING",
        )
        self.assertIs(knock_f0_authority["scope_corrected_rom_admission_blocker"], False)
        self.assertIs(knock_f0_authority["scope_correction_is_safety_remediation"], False)
        self.assertNotIn("trigger_root_resolved", knock_f0_authority)
        self.assertEqual(
            knock_f0_authority["removed_transactional_recorder_gates"],
            ["REC_PATH_DURABLE", "REC_PROFILE_DURABLE"],
        )
        self.assertEqual(
            knock_f0_authority["retained_operational_requirements"],
            [
                "RAWOUT_ONLY_F0_ANALYSIS",
                "OUT_NON_ADMISSIBLE_UNDER_F0_SINGLE_PENDING_V1",
                "ACTIVE_MODE_01_SEPARATE_CAN_AUTHORITY",
            ],
        )
        self.assertEqual(
            knock_f0_authority["authorized_artifact_sha256"],
            "084a4c8dd7ea0fca25df7256039617b4defa337117a8366db1de073f45f16637",
        )
        self.assertEqual(knock_f0_authority["authorized_max_ceiling"], 27)
        self.assertEqual(knock_f0_authority["previous_scoped_ceiling"], 23)
        self.assertEqual(knock_f0_authority["prior_usage"], 23)
        self.assertEqual(knock_f0_authority["additional_slots"], 4)
        self.assertEqual(knock_f0_authority["authorized_cycle_weight"], 4)
        self.assertIs(knock_f0_authority["quota_conservation_constraints_removed"], True)
        self.assertEqual(
            knock_f0_authority["authority_continuation_kind"],
            "STANDING_FURTHER_CYCLE_AUTHORITY",
        )
        self.assertNotIn("valid_blocker_continuation", knock_f0_authority)
        self.assertEqual(
            knock_f0_authority["new_ceiling"],
            knock_f0_authority["prior_usage"] + knock_f0_authority["additional_slots"],
        )
        self.assertEqual(
            knock_f0_authority["authorized_cycle_weight"],
            knock_f0_authority["additional_slots"],
        )
        self.assertEqual(
            knock_f0_authority["authorized_phase_plan"],
            [
                {"phase": "fanout", "weight": 3, "family": "codex"},
                {"phase": "xfamily", "weight": 1, "family": "claude"},
            ],
        )
        self.assertEqual(
            set(knock_f0_authority),
            {
                "authority_id",
                "doc_id",
                "scope",
                "authority_reference_mailbox_seq",
                "scope_correction_mailbox_seqs",
                "prior_authority_id",
                "scope_basis_campaign_id",
                "scope_basis_claim_id",
                "scope_basis_review_output_sha256",
                "scope_basis_finding_id",
                "scope_basis_root_cause_id",
                "scope_basis_reported_severity",
                "scope_corrected_classification",
                "scope_corrected_rom_admission_blocker",
                "scope_correction_is_safety_remediation",
                "removed_transactional_recorder_gates",
                "retained_operational_requirements",
                "authorized_artifact_sha256",
                "default_ceiling",
                "previous_scoped_ceiling",
                "authorized_max_ceiling",
                "checkpoint_interval",
                "prior_usage",
                "additional_slots",
                "authorized_cycle_weight",
                "authorized_phase_plan",
                "quota_conservation_constraints_removed",
                "authority_continuation_kind",
                "new_ceiling",
            },
        )
        self.assertEqual(knock_f0_authority["doc_id"], campaign_guard.doc_id(knock_f0_path))
        self.assertFalse(scope_review_checkpoint_required(knock_f0_path, 26))
        self.assertTrue(scope_review_checkpoint_required(knock_f0_path, 27))

        historical_knock_f0 = (
            campaign_guard.HISTORICAL_SCOPED_GLOBAL_CEILING_AUTHORITIES[
                str(knock_f0_path)
            ]
        )
        self.assertEqual(len(historical_knock_f0), 2)
        self.assertEqual(
            historical_knock_f0[0]["authority_id"],
            "ZN6-KNOCK-F0-USER-AUTH-2026-08-23-SEQ4668-15-19",
        )
        self.assertEqual(historical_knock_f0[0]["prior_usage"], 15)
        self.assertEqual(historical_knock_f0[0]["new_ceiling"], 19)
        self.assertEqual(
            historical_knock_f0[0]["authorized_artifact_sha256"],
            "8620b3e52f64bdd9a7b7719bf335f5c0298e0349948a39ca0991a48c30df0d6e",
        )
        self.assertEqual(
            historical_knock_f0[1],
            {
                "authority_id": "ZN6-KNOCK-F0-USER-AUTH-2026-08-23-SEQ4689-19-23",
                "doc_id": "c2c6bef4d687838c",
                "scope": (
                    "F0 overlay scope-corrected exact WIRE revision; "
                    "one Codex fanout then one Claude xfamily"
                ),
                "authority_reference_mailbox_seq": 4689,
                "scope_correction_mailbox_seqs": [4691, 4692],
                "authorized_artifact_sha256": (
                    "d35f07c44c5e534e5985e4bda01501c2ec59fbd8e359b959bdf876356cdc2ff1"
                ),
                "default_ceiling": 16,
                "previous_scoped_ceiling": 19,
                "authorized_max_ceiling": 23,
                "checkpoint_interval": 4,
                "prior_usage": 19,
                "additional_slots": 4,
                "authorized_cycle_weight": 4,
                "authorized_phase_plan": [
                    {"phase": "fanout", "weight": 3, "family": "codex"},
                    {"phase": "xfamily", "weight": 1, "family": "claude"},
                ],
                "quota_conservation_constraints_removed": True,
                "valid_blocker_continuation": "FRESH_NARROW_COMPLETE_CYCLE_AUTHORITY",
                "new_ceiling": 23,
            },
        )
        self.assertEqual(
            historical_knock_f0[1]["new_ceiling"],
            knock_f0_authority["prior_usage"],
        )
        self.assertEqual(
            historical_knock_f0[0]["new_ceiling"],
            historical_knock_f0[1]["prior_usage"],
        )

        for field, malformed_value in (
            ("authority_id", "ZN6-KNOCK-F0-UNAUTHORIZED"),
            ("doc_id", "0" * 16),
            ("scope", "F0 overlay safety remediation"),
            ("authority_reference_mailbox_seq", 4688),
            ("scope_correction_mailbox_seqs", [4708]),
            ("prior_authority_id", "UNBOUND"),
            ("scope_basis_campaign_id", "00000000-0000-0000-0000-000000000000"),
            ("scope_basis_claim_id", "00000000-0000-0000-0000-000000000000"),
            ("scope_basis_review_output_sha256", "0" * 64),
            ("scope_basis_finding_id", "XF-R2-999"),
            ("scope_basis_root_cause_id", "recorder.root_resolved"),
            ("scope_basis_reported_severity", "MED"),
            ("scope_corrected_classification", "ROM_SAFETY_BLOCKER"),
            ("scope_corrected_rom_admission_blocker", True),
            ("scope_correction_is_safety_remediation", True),
            ("authorized_artifact_sha256", "0" * 64),
            ("default_ceiling", 15),
            ("previous_scoped_ceiling", 22),
            ("authorized_max_ceiling", 28),
            ("checkpoint_interval", 3),
            ("prior_usage", 22),
            ("additional_slots", 3),
            ("authorized_cycle_weight", 3),
            ("quota_conservation_constraints_removed", False),
            ("authority_continuation_kind", "SAFETY_REMEDIATION"),
            (
                "authorized_phase_plan",
                [
                    {"phase": "targeted", "weight": 1, "family": "codex"},
                    {"phase": "xfamily", "weight": 1, "family": "claude"},
                ],
            ),
            ("new_ceiling", 28),
        ):
            with self.subTest(malformed_cycle_field=field):
                malformed_authority = dict(knock_f0_authority)
                malformed_authority[field] = malformed_value
                with mock.patch.dict(
                    SCOPED_GLOBAL_CEILING_OVERRIDES,
                    {str(knock_f0_path): malformed_authority},
                ):
                    with self.assertRaises(campaign_guard.StateError):
                        global_ceiling_policy(knock_f0_path)

        for integer_field in (
            "authority_reference_mailbox_seq",
            "default_ceiling",
            "previous_scoped_ceiling",
            "authorized_max_ceiling",
            "checkpoint_interval",
            "prior_usage",
            "additional_slots",
            "authorized_cycle_weight",
            "new_ceiling",
        ):
            with self.subTest(equal_valued_float=integer_field):
                malformed_authority = dict(knock_f0_authority)
                malformed_authority[integer_field] = float(
                    malformed_authority[integer_field]
                )
                with mock.patch.dict(
                    SCOPED_GLOBAL_CEILING_OVERRIDES,
                    {str(knock_f0_path): malformed_authority},
                ):
                    with self.assertRaises(campaign_guard.StateError):
                        global_ceiling_policy(knock_f0_path)

        float_scope_correction_seq = dict(knock_f0_authority)
        float_scope_correction_seq["scope_correction_mailbox_seqs"] = [4708.0, 4709]
        with mock.patch.dict(
            SCOPED_GLOBAL_CEILING_OVERRIDES,
            {str(knock_f0_path): float_scope_correction_seq},
        ):
            with self.assertRaises(campaign_guard.StateError):
                global_ceiling_policy(knock_f0_path)

        malformed_phase_plans = {
            "reversed": [
                {"phase": "xfamily", "weight": 1, "family": "claude"},
                {"phase": "fanout", "weight": 3, "family": "codex"},
            ],
            "wrong_fanout_family": [
                {"phase": "fanout", "weight": 3, "family": "claude"},
                {"phase": "xfamily", "weight": 1, "family": "claude"},
            ],
            "wrong_xfamily_family": [
                {"phase": "fanout", "weight": 3, "family": "codex"},
                {"phase": "xfamily", "weight": 1, "family": "grok"},
            ],
        }
        for label, malformed_plan in malformed_phase_plans.items():
            with self.subTest(exact_phase_plan=label):
                malformed_authority = dict(knock_f0_authority)
                malformed_authority["authorized_phase_plan"] = malformed_plan
                with mock.patch.dict(
                    SCOPED_GLOBAL_CEILING_OVERRIDES,
                    {str(knock_f0_path): malformed_authority},
                ):
                    with self.assertRaises(campaign_guard.StateError):
                        global_ceiling_policy(knock_f0_path)

        for list_field in (
            "removed_transactional_recorder_gates",
            "retained_operational_requirements",
        ):
            values = list(knock_f0_authority[list_field])
            for omitted in values:
                with self.subTest(incomplete_semantic_field=list_field, omitted=omitted):
                    malformed_authority = dict(knock_f0_authority)
                    malformed_authority[list_field] = [
                        value for value in values if value != omitted
                    ]
                    with mock.patch.dict(
                        SCOPED_GLOBAL_CEILING_OVERRIDES,
                        {str(knock_f0_path): malformed_authority},
                    ):
                        with self.assertRaises(campaign_guard.StateError):
                            global_ceiling_policy(knock_f0_path)

        authority_with_false_resolution_claim = dict(knock_f0_authority)
        authority_with_false_resolution_claim["trigger_root_resolved"] = True
        with mock.patch.dict(
            SCOPED_GLOBAL_CEILING_OVERRIDES,
            {str(knock_f0_path): authority_with_false_resolution_claim},
        ):
            with self.assertRaises(campaign_guard.StateError):
                global_ceiling_policy(knock_f0_path)

        authority_missing_scope_classification = dict(knock_f0_authority)
        authority_missing_scope_classification.pop("scope_corrected_classification")
        with mock.patch.dict(
            SCOPED_GLOBAL_CEILING_OVERRIDES,
            {str(knock_f0_path): authority_missing_scope_classification},
        ):
            with self.assertRaises(campaign_guard.StateError):
                global_ceiling_policy(knock_f0_path)

        campaign_guard.enforce_scoped_artifact_sha(
            knock_f0_authority,
            str(knock_f0_authority["authorized_artifact_sha256"]),
        )
        with self.assertRaises(campaign_guard.StateError):
            campaign_guard.enforce_scoped_artifact_sha(
                knock_f0_authority,
                "0" * 64,
            )

        absent_path = Path("/nonexistent/harness-test/KNOCK_TELEMETRY_F0_OVERLAY_WIRE.md")
        absent_authority = dict(knock_f0_authority)
        absent_authority["doc_id"] = campaign_guard.doc_id(absent_path)
        with mock.patch.dict(
            SCOPED_GLOBAL_CEILING_OVERRIDES,
            {str(absent_path): absent_authority},
        ):
            absent_ceiling, loaded_absent_authority = global_ceiling_policy(absent_path)
        self.assertEqual(absent_ceiling, 27)
        self.assertEqual(loaded_absent_authority, absent_authority)

        default_ceiling, default_authority = global_ceiling_policy(
            fi_path.with_name("another-design.md")
        )
        self.assertEqual(default_ceiling, GLOBAL_MAX_MODEL_LAUNCHES)
        self.assertIsNone(default_authority)

    def test_abc_fp_orchestrator_audit_authorizes_one_exact_cycle(self) -> None:
        abc_path = Path(next(
            path
            for path in SCOPED_GLOBAL_CEILING_OVERRIDES
            if "ABC_FP_CALIBRATION_IMPLEMENTATION_REVIEW" in path
        ))
        ceiling, authority = global_ceiling_policy(abc_path)
        self.assertEqual(ceiling, 18)
        self.assertIsNotNone(authority)
        assert authority is not None
        self.assertEqual(
            authority["authority_id"],
            "ZN6-ABC-FP-ORCHESTRATOR-AUDIT-2026-08-24-14-18",
        )
        self.assertEqual(authority["doc_id"], campaign_guard.doc_id(abc_path))
        self.assertEqual(
            authority["authorized_artifact_sha256"],
            "e430616b0761f71a8a7c4a4c8775902d9b496895e08ef42c2d0b33861ec2e7cf",
        )
        self.assertEqual(authority["prior_usage"], 14)
        self.assertEqual(authority["additional_slots"], 4)
        self.assertEqual(
            authority["authority_continuation_kind"],
            "ONE_EXACT_REVISION_COMPLETE_CYCLE",
        )
        self.assertFalse(scope_review_checkpoint_required(abc_path, 17))
        self.assertTrue(scope_review_checkpoint_required(abc_path, 18))

        campaign_guard.enforce_scoped_artifact_sha(
            authority,
            "e430616b0761f71a8a7c4a4c8775902d9b496895e08ef42c2d0b33861ec2e7cf",
        )
        with self.assertRaises(campaign_guard.StateError):
            campaign_guard.enforce_scoped_artifact_sha(authority, "0" * 64)

    def test_abc_runtime_orchestrator_audit_authorizes_one_exact_cycle(self) -> None:
        abc_path = Path(next(
            path
            for path in SCOPED_GLOBAL_CEILING_OVERRIDES
            if "RACEROM_ABC_CHANGER_V1.md" in path
        ))
        ceiling, authority = global_ceiling_policy(abc_path)
        self.assertEqual(ceiling, 31)
        self.assertIsNotNone(authority)
        assert authority is not None
        artifact_sha = (
            "0ade043db0a7482d29762b0cbebb0d475d50e0c6103730a42a211e83eb14dcff"
        )
        protocol = "a" * 64

        self.assertEqual(
            authority["authority_id"],
            "ZN6-ABC-RUNTIME-I4-AUDIT-2026-08-25-27-31",
        )
        self.assertEqual(authority["doc_id"], campaign_guard.doc_id(abc_path))
        self.assertEqual(authority["authorized_artifact_sha256"], artifact_sha)
        self.assertEqual(authority["prior_usage"], 27)
        self.assertEqual(authority["additional_slots"], 4)
        self.assertEqual(authority["authorized_cycle_weight"], 4)
        self.assertEqual(authority["new_ceiling"], 31)
        self.assertIs(authority["quota_conservation_constraints_removed"], True)
        self.assertEqual(
            authority["authorized_phase_plan"],
            [
                {"phase": "fanout", "weight": 3, "family": "codex"},
                {"phase": "xfamily", "weight": 1, "family": "claude"},
            ],
        )
        self.assertFalse(scope_review_checkpoint_required(abc_path, 30))
        self.assertTrue(scope_review_checkpoint_required(abc_path, 31))

        historical = campaign_guard.HISTORICAL_SCOPED_GLOBAL_CEILING_AUTHORITIES[
            str(abc_path)
        ]
        self.assertEqual(len(historical), 3)
        self.assertEqual(
            historical[0]["authority_id"],
            "ZN6-ABC-RUNTIME-ORCHESTRATOR-AUDIT-2026-08-24-15-19",
        )
        self.assertEqual(historical[0]["prior_usage"], 15)
        self.assertEqual(historical[0]["new_ceiling"], 19)
        self.assertEqual(
            historical[1]["authority_id"],
            "ZN6-ABC-RUNTIME-ORCHESTRATOR-AUDIT-2026-08-24-19-23",
        )
        self.assertEqual(historical[1]["prior_usage"], 19)
        self.assertEqual(historical[1]["new_ceiling"], 23)
        self.assertEqual(
            historical[2]["authority_id"],
            "ZN6-ABC-RUNTIME-ORCHESTRATOR-AUDIT-2026-08-24-23-27",
        )
        self.assertEqual(historical[2]["prior_usage"], 23)
        self.assertEqual(historical[2]["new_ceiling"], 27)
        self.assertEqual(
            historical[2]["drift_audit"]["invalidated_historical_marker"],
            {
                "basename": "PLATEAU.12044291bf20ce79.2d5a41341dae37ef",
                "sha256": (
                    "60a95cde2b9f998f5dc9ba0792586a5a51b5b23511d0d617ea0174bd9a716d0a"
                ),
                "status": "INVALIDATED_G8_CARRIED_SEVERITY_DOWNGRADE",
                "shipping_authority": False,
            },
        )
        self.assertEqual(
            authority["drift_audit"]["v17_receipt_sha256"],
            "fe8887becdcbae537347e63432d3389f34712bf786aab8fcf74a71178c998f3e",
        )
        self.assertEqual(
            authority["drift_audit"]["v17_manifest_sha256"],
            "bc56ab8a4b979407f9bd874a5eaaa1206bd90cd59d2f3c027f0bff3f6f0cb807",
        )

        campaign_guard.enforce_scoped_artifact_sha(authority, artifact_sha)
        with self.assertRaises(campaign_guard.StateError):
            campaign_guard.enforce_scoped_artifact_sha(authority, "0" * 64)

        required = campaign_guard.enforce_scoped_launch_plan(
            authority,
            27,
            "fanout",
            [],
            artifact_sha,
            protocol,
            reviewer_family=None,
            require_reviewer_family=True,
        )
        self.assertEqual(required, "codex")
        with self.assertRaises(campaign_guard.TransitionError):
            campaign_guard.enforce_scoped_launch_plan(
                authority,
                27,
                "xfamily",
                [],
                artifact_sha,
                protocol,
                reviewer_family="claude",
                require_reviewer_family=True,
            )

        fanout = {
            "round": 1,
            "phase": "fanout",
            "model_launches": 3,
            "status": "success",
            "artifact_sha": artifact_sha,
            "protocol_sha": protocol,
            "global_fuse_authority": authority,
        }
        required = campaign_guard.enforce_scoped_launch_plan(
            authority,
            30,
            "xfamily",
            [fanout],
            artifact_sha,
            protocol,
            reviewer_family="claude",
            require_reviewer_family=True,
        )
        self.assertEqual(required, "claude")
        with self.assertRaises(campaign_guard.TransitionError):
            campaign_guard.enforce_scoped_launch_plan(
                authority,
                30,
                "xfamily",
                [fanout],
                artifact_sha,
                protocol,
                reviewer_family="grok",
                require_reviewer_family=True,
            )
        with self.assertRaises(campaign_guard.TransitionError):
            campaign_guard.enforce_scoped_launch_plan(
                authority,
                31,
                "fanout",
                [fanout],
                artifact_sha,
                protocol,
                reviewer_family=None,
                require_reviewer_family=True,
            )

        for changed_field, changed_value in (
            ("status", "failed"),
            ("artifact_sha", "0" * 64),
            ("protocol_sha", "b" * 64),
            ("global_fuse_authority", {}),
            ("model_launches", 1),
            ("phase", "targeted"),
            ("replacement_for", "prior-claim"),
        ):
            with self.subTest(mismatched_predecessor=changed_field):
                changed_fanout = dict(fanout)
                changed_fanout[changed_field] = changed_value
                with self.assertRaises(campaign_guard.TransitionError):
                    campaign_guard.enforce_scoped_launch_plan(
                        authority,
                        30,
                        "xfamily",
                        [changed_fanout],
                        artifact_sha,
                        protocol,
                        reviewer_family="claude",
                        require_reviewer_family=True,
                    )

        malformed_cases = {
            "wrong_artifact": {
                **authority,
                "authorized_artifact_sha256": "0" * 64,
            },
            "open_ended_ceiling": {**authority, "new_ceiling": 32},
            "wrong_prior_usage": {**authority, "prior_usage": 26},
            "quota_constraint_not_removed": {
                **authority,
                "quota_conservation_constraints_removed": False,
            },
            "rom_authority_drift": {
                **authority,
                "drift_audit": {
                    **authority["drift_audit"],
                    "rom_authority_changed": True,
                },
            },
            "consumer_authority_drift": {
                **authority,
                "drift_audit": {
                    **authority["drift_audit"],
                    "consumer_authority_changed": True,
                },
            },
            "mislabeled_receipt_as_manifest": {
                **authority,
                "drift_audit": {
                    **authority["drift_audit"],
                    "v17_receipt_sha256": (
                        authority["drift_audit"]["v17_manifest_sha256"]
                    ),
                },
            },
            "hardware_authority_drift": {
                **authority,
                "drift_audit": {
                    **authority["drift_audit"],
                    "hardware_authority_added": True,
                },
            },
            "reversed_phase_plan": {
                **authority,
                "authorized_phase_plan": list(
                    reversed(authority["authorized_phase_plan"])
                ),
            },
        }
        for label, malformed in malformed_cases.items():
            with self.subTest(malformed_abc_runtime_authority=label):
                with mock.patch.dict(
                    SCOPED_GLOBAL_CEILING_OVERRIDES,
                    {str(abc_path): malformed},
                ):
                    with self.assertRaises(campaign_guard.StateError):
                        global_ceiling_policy(abc_path)

    def test_s08a_rev20_orchestrator_audit_authorizes_one_exact_cycle(self) -> None:
        s08a_path = Path(next(
            path
            for path in SCOPED_GLOBAL_CEILING_OVERRIDES
            if "S08A-RELOCATABLE-SHADOW-PACK" in path
        ))
        ceiling, authority = global_ceiling_policy(s08a_path)
        self.assertEqual(ceiling, 80)
        self.assertIsNotNone(authority)
        assert authority is not None
        self.assertEqual(
            authority["authority_id"],
            "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-24-REV20-76-80",
        )
        self.assertEqual(authority["doc_id"], campaign_guard.doc_id(s08a_path))
        self.assertEqual(authority["prior_usage"], 76)
        self.assertEqual(authority["additional_slots"], 4)
        self.assertEqual(authority["new_ceiling"], 80)
        self.assertEqual(
            authority["drift_audit"]["result"],
            "PASS_BOUNDED_REMEDIATION_NOT_SCOPE_DRIFT",
        )
        self.assertIs(authority["drift_audit"]["outcome_changed"], False)
        self.assertIs(authority["drift_audit"]["rom_authority_changed"], False)
        self.assertEqual(authority["drift_audit"]["same_family_finding_count"], 9)
        self.assertEqual(authority["drift_audit"]["cross_family_finding_count"], 6)
        self.assertEqual(authority["drift_audit"]["blocking_root_count"], 2)
        self.assertEqual(
            authority["drift_audit"]["authoring_receipt_sha256"],
            "8b8f28e56b48f18e3374d72432a40aafdeb8c53fc8df4fb273a843fa89fc9b86",
        )
        self.assertEqual(
            authority["drift_audit"]["lifecycle_status_sha256"],
            "ef4fd5e5ceb3b639e658b20ea127f4a97be1d728de372f03d1975564bc284a9d",
        )
        self.assertEqual(authority["drift_audit"]["accepted_remediation_root_count"], 2)
        self.assertEqual(authority["drift_audit"]["parent_audit_same_root_count"], 0)
        self.assertEqual(authority["drift_audit"]["parent_audit_fix_passes"], 2)
        self.assertIs(authority["drift_audit"]["bounded_calibration_authorization_added"], False)
        self.assertFalse(scope_review_checkpoint_required(s08a_path, 79))
        self.assertTrue(scope_review_checkpoint_required(s08a_path, 80))

        historical = campaign_guard.HISTORICAL_SCOPED_GLOBAL_CEILING_AUTHORITIES[
            str(s08a_path)
        ]
        self.assertEqual(len(historical), 16)
        self.assertEqual(
            historical[0]["authority_id"],
            "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-13-17",
        )
        self.assertEqual(
            historical[1]["authority_id"],
            "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-17-21",
        )
        self.assertEqual(
            historical[2]["authority_id"],
            (
                "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-"
                "CAPACITY-RECOVERY-20-24"
            ),
        )
        self.assertEqual(
            historical[3]["authority_id"],
            "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-REV7-24-28",
        )
        self.assertEqual(
            historical[4]["authority_id"],
            "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-REV8-28-32",
        )
        self.assertEqual(
            historical[5]["authority_id"],
            "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-REV9-32-36",
        )
        self.assertEqual(
            historical[6]["authority_id"],
            "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-REV10-36-40",
        )
        self.assertEqual(
            historical[7]["authority_id"],
            "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-REV11-40-44",
        )
        self.assertEqual(
            historical[8]["authority_id"],
            "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-REV12-44-48",
        )
        self.assertEqual(
            historical[9]["authority_id"],
            "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-REV13-48-52",
        )
        self.assertEqual(
            historical[10]["authority_id"],
            "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-REV14-52-56",
        )
        self.assertEqual(
            historical[11]["authority_id"],
            "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-REV15-56-60",
        )
        self.assertEqual(
            historical[12]["authority_id"],
            "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-REV16-60-64",
        )
        self.assertEqual(
            historical[13]["authority_id"],
            "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-REV17-64-68",
        )
        self.assertEqual(
            historical[14]["authority_id"],
            "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-24-REV18-68-72",
        )
        self.assertEqual(
            historical[15]["authority_id"],
            "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-24-REV19-72-76",
        )

        artifact_sha = str(authority["authorized_artifact_sha256"])
        protocol = "a" * 64
        campaign_guard.enforce_scoped_artifact_sha(authority, artifact_sha)
        with self.assertRaises(campaign_guard.StateError):
            campaign_guard.enforce_scoped_artifact_sha(authority, "0" * 64)

        required = campaign_guard.enforce_scoped_launch_plan(
            authority,
            76,
            "fanout",
            [],
            artifact_sha,
            protocol,
            reviewer_family=None,
            require_reviewer_family=True,
        )
        self.assertEqual(required, "codex")
        with self.assertRaises(campaign_guard.TransitionError):
            campaign_guard.enforce_scoped_launch_plan(
                authority,
                76,
                "xfamily",
                [],
                artifact_sha,
                protocol,
                reviewer_family="claude",
                require_reviewer_family=True,
            )

        fanout = {
            "round": 1,
            "phase": "fanout",
            "model_launches": 3,
            "status": "success",
            "artifact_sha": artifact_sha,
            "protocol_sha": protocol,
            "global_fuse_authority": authority,
        }
        required = campaign_guard.enforce_scoped_launch_plan(
            authority,
            79,
            "xfamily",
            [fanout],
            artifact_sha,
            protocol,
            reviewer_family="claude",
            require_reviewer_family=True,
        )
        self.assertEqual(required, "claude")
        with self.assertRaises(campaign_guard.TransitionError):
            campaign_guard.enforce_scoped_launch_plan(
                authority,
                80,
                "fanout",
                [fanout],
                artifact_sha,
                protocol,
                reviewer_family=None,
                require_reviewer_family=True,
            )

        malformed_cases = {
            "wrong_artifact": {
                **authority,
                "authorized_artifact_sha256": "0" * 64,
            },
            "open_ended_ceiling": {**authority, "new_ceiling": 81},
            "wrong_prior_usage": {**authority, "prior_usage": 75},
            "wrong_lifecycle_status": {
                **authority,
                "drift_audit": {
                    **authority["drift_audit"],
                    "lifecycle_status_sha256": "0" * 64,
                },
            },
            "rom_authority_drift": {
                **authority,
                "drift_audit": {
                    **authority["drift_audit"],
                    "rom_authority_changed": True,
                },
            },
            "reversed_phase_plan": {
                **authority,
                "authorized_phase_plan": list(
                    reversed(authority["authorized_phase_plan"])
                ),
            },
        }
        for label, malformed in malformed_cases.items():
            with self.subTest(malformed_s08a_authority=label):
                with mock.patch.dict(
                    SCOPED_GLOBAL_CEILING_OVERRIDES,
                    {str(s08a_path): malformed},
                ):
                    with self.assertRaises(campaign_guard.StateError):
                        global_ceiling_policy(s08a_path)

    def test_scoped_phase_plan_is_exact_and_fail_closed(self) -> None:
        knock_f0_path = Path(next(
            path
            for path in SCOPED_GLOBAL_CEILING_OVERRIDES
            if "KNOCK_TELEMETRY_F0_OVERLAY" in path
        ))
        _, authority = global_ceiling_policy(knock_f0_path)
        assert authority is not None
        artifact_sha = str(authority["authorized_artifact_sha256"])
        protocol = "a" * 64

        required = campaign_guard.enforce_scoped_launch_plan(
            authority,
            23,
            "fanout",
            [],
            artifact_sha,
            protocol,
            reviewer_family=None,
            require_reviewer_family=True,
        )
        self.assertEqual(required, "codex")
        for invalid_total in (22, 24, 25):
            with self.subTest(non_boundary_usage=invalid_total):
                with self.assertRaises(campaign_guard.StateError):
                    campaign_guard.enforce_scoped_launch_plan(
                        authority,
                        invalid_total,
                        "fanout",
                        [],
                        artifact_sha,
                        protocol,
                        reviewer_family=None,
                        require_reviewer_family=True,
                    )
        for forbidden_phase in ("targeted", "xfamily"):
            with self.subTest(forbidden_phase=forbidden_phase):
                with self.assertRaises(campaign_guard.TransitionError):
                    campaign_guard.enforce_scoped_launch_plan(
                        authority,
                        23,
                        forbidden_phase,
                        [],
                        artifact_sha,
                        protocol,
                        reviewer_family="claude",
                        require_reviewer_family=True,
                    )

        fanout = {
            "round": 1,
            "phase": "fanout",
            "model_launches": 3,
            "status": "success",
            "artifact_sha": artifact_sha,
            "protocol_sha": protocol,
            "global_fuse_authority": authority,
        }
        required = campaign_guard.enforce_scoped_launch_plan(
            authority,
            26,
            "xfamily",
            [fanout],
            artifact_sha,
            protocol,
            reviewer_family="claude",
            require_reviewer_family=True,
        )
        self.assertEqual(required, "claude")

        with self.assertRaises(campaign_guard.TransitionError):
            campaign_guard.enforce_scoped_launch_plan(
                authority,
                26,
                "xfamily",
                [],
                artifact_sha,
                protocol,
                reviewer_family="claude",
                require_reviewer_family=True,
            )

        with self.assertRaises(campaign_guard.TransitionError):
            campaign_guard.enforce_scoped_launch_plan(
                authority,
                26,
                "xfamily",
                [fanout],
                artifact_sha,
                protocol,
                reviewer_family="grok",
                require_reviewer_family=True,
            )
        for changed_field, changed_value in (
            ("status", "failed"),
            ("artifact_sha", "0" * 64),
            ("protocol_sha", "b" * 64),
            ("global_fuse_authority", {}),
            ("model_launches", 1),
            ("phase", "targeted"),
            ("replacement_for", "prior-claim"),
        ):
            with self.subTest(mismatched_predecessor=changed_field):
                changed_fanout = dict(fanout)
                changed_fanout[changed_field] = changed_value
                with self.assertRaises(campaign_guard.TransitionError):
                    campaign_guard.enforce_scoped_launch_plan(
                        authority,
                        26,
                        "xfamily",
                        [changed_fanout],
                        artifact_sha,
                        protocol,
                        reviewer_family="claude",
                        require_reviewer_family=True,
                    )

        type_confused_authority = copy.deepcopy(authority)
        type_confused_authority["scope_correction_is_safety_remediation"] = 0
        type_confused_fanout = dict(fanout)
        type_confused_fanout["global_fuse_authority"] = type_confused_authority
        with self.assertRaises(campaign_guard.TransitionError):
            campaign_guard.enforce_scoped_launch_plan(
                authority,
                26,
                "xfamily",
                [type_confused_fanout],
                artifact_sha,
                protocol,
                reviewer_family="claude",
                require_reviewer_family=True,
            )

        with self.assertRaises(campaign_guard.TransitionError):
            campaign_guard.enforce_scoped_launch_plan(
                authority,
                27,
                "xfamily",
                [fanout],
                artifact_sha,
                protocol,
                reviewer_family="claude",
                require_reviewer_family=True,
            )

        adapter = XFAMILY.read_text()
        self.assertIn('--reviewer-family "$REVIEWER"', adapter)
        self.assertIn('--expected-artifact-sha "$ARTIFACT_SHA"', adapter)


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
        """Claim alternating rounds until the default ceiling is consumed.

        Derived from the guard's own ceiling rather than a fixed round count, so
        the helper keeps meaning "budget is now full" when that ceiling moves.
        """
        spent = 0
        round_no = 0
        while spent < DEFAULT_MAX_MODEL_LAUNCHES:
            round_no += 1
            phase = "fanout" if round_no % 2 else "xfamily"
            result = self.claim(round_no, phase)
            self.assertEqual(result.returncode, 0, result.stderr)
            spent += PHASE_WEIGHT[phase]
        self.filled_rounds = round_no

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

    def recoverable_launch(self) -> dict[str, object]:
        claim_id = "11111111-1111-4111-8111-111111111111"
        return {
            "claim_id": claim_id,
            "sequence": 1,
            "round": 1,
            "phase": "fanout",
            "attempt": 1,
            "model_launches": 3,
            "state_dir": str(self.state.resolve()),
            "artifact_sha": hashlib.sha256(self.doc.read_bytes()).hexdigest(),
            "protocol_sha": protocol_sha(),
            "claimed_at": "2026-01-01T00:00:00Z",
            "finished_at": "2026-01-01T00:00:01Z",
            "status": "startup-failed-recoverable",
            "recovery": {
                "kind": "claim-scoped-credit",
                "reason_code": "PROVIDER_SCHEMA_STARTUP_REJECTION",
                "requested_at": "2026-01-01T00:00:01Z",
                "evidence_path": str(
                    self.state / f"round_1_fanout.{claim_id}.FAILED.json"
                ),
                "evidence_sha256": "1" * 64,
                "adapter_script_sha256": "2" * 64,
                "process_cleanup": "verified-no-descendants",
                "reviewers": [
                    {
                        "reviewer": reviewer,
                        "classification": "provider-schema-startup-rejection",
                        "provider_exit_code": 1,
                        "output_bytes": 0,
                        "input_bytes": 0,
                        "turn_observed": False,
                    }
                    for reviewer in ("MELCHIOR", "BALTHASAR", "CASPAR")
                ],
            },
        }

    def historical_incident(self) -> tuple[Path, str, dict[str, object]]:
        """Seed the observed 14/16 shape and its closed compatibility attestation."""
        artifact_sha = hashlib.sha256(self.doc.read_bytes()).hexdigest()
        source_claim = "10000000-0000-4000-8000-000000000001"

        def launch(
            claim_id: str,
            *,
            campaign_round: int,
            phase: str,
            attempt: int,
            status: str,
            artifact: str,
            protocol: str,
        ) -> dict[str, object]:
            return {
                "claim_id": claim_id,
                "sequence": campaign_round,
                "round": campaign_round,
                "phase": phase,
                "attempt": attempt,
                "model_launches": PHASE_WEIGHT[phase],
                "state_dir": str(self.state.resolve()),
                "artifact_sha": artifact,
                "protocol_sha": protocol,
                "claimed_at": f"2026-01-01T00:00:{campaign_round:02d}Z",
                "finished_at": f"2026-01-01T00:01:{campaign_round:02d}Z",
                "status": status,
            }

        first = [
            launch(
                source_claim,
                campaign_round=1,
                phase="fanout",
                attempt=1,
                status="failed",
                artifact=artifact_sha,
                protocol="1" * 64,
            ),
            launch(
                "10000000-0000-4000-8000-000000000002",
                campaign_round=1,
                phase="fanout",
                attempt=2,
                status="failed",
                artifact=artifact_sha,
                protocol="1" * 64,
            ),
        ]
        first[1]["sequence"] = 2
        later = [
            launch(
                "10000000-0000-4000-8000-000000000003",
                campaign_round=1,
                phase="fanout",
                attempt=1,
                status="success",
                artifact="2" * 64,
                protocol="2" * 64,
            ),
            launch(
                "10000000-0000-4000-8000-000000000004",
                campaign_round=2,
                phase="xfamily",
                attempt=1,
                status="failed",
                artifact="3" * 64,
                protocol="2" * 64,
            ),
            launch(
                "10000000-0000-4000-8000-000000000005",
                campaign_round=2,
                phase="xfamily",
                attempt=2,
                status="failed",
                artifact="3" * 64,
                protocol="2" * 64,
            ),
        ]
        final = [
            launch(
                "10000000-0000-4000-8000-000000000006",
                campaign_round=1,
                phase="fanout",
                attempt=1,
                status="success",
                artifact="4" * 64,
                protocol="2" * 64,
            )
        ]
        ledger_path = self.seed_ledger(first)
        ledger = json.loads(ledger_path.read_text())
        for index, launches in enumerate((later, final), start=2):
            ledger["campaigns"].append(
                {
                    "campaign_id": f"seed-{index}",
                    "started_at": f"2026-01-01T00:0{index}:00Z",
                    "started_by": "test",
                    "reason": "historical incident fixture",
                    "launches": launches,
                }
            )
        ledger_path.write_text(json.dumps(ledger))
        history = [
            launch
            for campaign in ledger["campaigns"]
            for launch in campaign["launches"]
        ]
        incident = {
            "incident_id": "test-closed-schema-startup",
            "issue": "test#271",
            "doc_id": hashlib.sha256(
                str(self.doc.resolve()).encode()
            ).hexdigest()[:16],
            "source_claim_id": source_claim,
            "source_finished_at": first[0]["finished_at"],
            "artifact_sha": artifact_sha,
            "source_protocol_sha": "1" * 64,
            "history_launch_count": len(history),
            "history_gross_model_launches": 14,
            "history_prefix_sha256": campaign_guard.canonical_sha256(history),
            "credited_model_launches": 3,
            "provider_stage": "codex-output-schema-validation-before-reviewer-turn",
            "reviewer_count": 3,
            "turn_observed": False,
            "legacy_classification": "provider-exit",
        }
        return ledger_path, source_claim, incident

    def issue_271_fixture(
        self,
    ) -> tuple[Path, dict[str, object], dict[str, object]]:
        """Install the sanitized incident ledger without consulting production constants."""
        ledger = json.loads(ISSUE_271_LEDGER_FIXTURE.read_text())
        ledger["doc_path"] = str(self.doc.resolve())
        control = self.doc.parent / ".dual-magi"
        control.mkdir(exist_ok=True)
        path = control / f"CAMPAIGN.{ISSUE_271_ATTESTATION['doc_id']}.json"
        path.write_text(json.dumps(ledger))
        incident = {
            **ISSUE_271_ATTESTATION,
            "history_prefix_sha256": ISSUE_271_SANITIZED_HISTORY_SHA256,
        }
        return path, ledger, incident

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

    def revision_state(self, suffix: str = "") -> Path:
        digest = hashlib.sha256(self.doc.read_bytes()).hexdigest()[:16]
        return self.root / "revisions" / f"{digest}{suffix}"

    def test_exact_current_and_historical_scoped_authorities_load(self) -> None:
        knock_f0_path = Path(next(
            path
            for path in SCOPED_GLOBAL_CEILING_OVERRIDES
            if "KNOCK_TELEMETRY_F0_OVERLAY" in path
        ))
        _, current_authority = global_ceiling_policy(knock_f0_path)
        assert current_authority is not None
        historical_authorities = (
            campaign_guard.HISTORICAL_SCOPED_GLOBAL_CEILING_AUTHORITIES[
                str(knock_f0_path)
            ]
        )

        for label, candidate in (
            ("current_23_to_27", current_authority),
            ("historical_15_to_19", historical_authorities[0]),
            ("historical_19_to_23", historical_authorities[1]),
        ):
            with self.subTest(exact_authority=label):
                launch = {
                    "claim_id": "11111111-1111-4111-8111-111111111111",
                    "round": 1,
                    "phase": "fanout",
                    "model_launches": 3,
                    "state_dir": str(self.state.resolve()),
                    "artifact_sha": "a" * 64,
                    "protocol_sha": "b" * 64,
                    "status": "success",
                    "global_fuse_authority": candidate,
                    "owner": {
                        "pid": 1,
                        "start_ticks": 1,
                        "ppid": 0,
                        "pgid": 1,
                        "adapter_kind": "fanout",
                    },
                }
                self.seed_ledger([launch])
                with mock.patch.object(
                    campaign_guard,
                    "global_ceiling_policy",
                    return_value=(27, current_authority),
                ), mock.patch.dict(
                    campaign_guard.HISTORICAL_SCOPED_GLOBAL_CEILING_AUTHORITIES,
                    {str(self.doc): historical_authorities},
                ):
                    loaded = campaign_guard.load_ledger(self.doc, create=False)
                loaded_campaign = loaded["campaigns"][0]
                loaded_authority = loaded_campaign["launches"][0][
                    "global_fuse_authority"
                ]
                self.assertTrue(
                    campaign_guard.exact_json_equal(loaded_authority, candidate)
                )

    def test_abc_runtime_current_and_historical_authorities_load(self) -> None:
        runtime_path = Path(next(
            path
            for path in SCOPED_GLOBAL_CEILING_OVERRIDES
            if "RACEROM_ABC_CHANGER_V1.md" in path
        ))
        ceiling, current_authority = global_ceiling_policy(runtime_path)
        assert current_authority is not None
        historical = campaign_guard.HISTORICAL_SCOPED_GLOBAL_CEILING_AUTHORITIES[
            str(runtime_path)
        ]
        self.assertEqual(ceiling, 31)
        self.assertEqual(len(historical), 3)

        for candidate in (current_authority, *historical):
            launch = {
                "claim_id": "11111111-1111-4111-8111-111111111111",
                "round": 1,
                "phase": "fanout",
                "model_launches": 3,
                "state_dir": str(self.state.resolve()),
                "artifact_sha": "a" * 64,
                "protocol_sha": "b" * 64,
                "status": "success",
                "global_fuse_authority": candidate,
                "owner": {
                    "pid": 1,
                    "start_ticks": 1,
                    "ppid": 0,
                    "pgid": 1,
                    "adapter_kind": "fanout",
                },
            }
            self.seed_ledger([launch])
            with mock.patch.object(
                campaign_guard,
                "global_ceiling_policy",
                return_value=(ceiling, current_authority),
            ), mock.patch.dict(
                campaign_guard.HISTORICAL_SCOPED_GLOBAL_CEILING_AUTHORITIES,
                {str(self.doc): historical},
            ):
                loaded = campaign_guard.load_ledger(self.doc, create=False)
            loaded_authority = loaded["campaigns"][0]["launches"][0][
                "global_fuse_authority"
            ]
            self.assertTrue(
                campaign_guard.exact_json_equal(loaded_authority, candidate)
            )

    def test_ledger_authority_comparison_is_recursively_type_strict(self) -> None:
        knock_f0_path = Path(next(
            path
            for path in SCOPED_GLOBAL_CEILING_OVERRIDES
            if "KNOCK_TELEMETRY_F0_OVERLAY" in path
        ))
        _, current_authority = global_ceiling_policy(knock_f0_path)
        assert current_authority is not None
        historical_authority = (
            campaign_guard.HISTORICAL_SCOPED_GLOBAL_CEILING_AUTHORITIES[
                str(knock_f0_path)
            ][1]
        )

        type_confused_authorities: list[tuple[str, dict[str, object]]] = []

        false_as_zero = copy.deepcopy(current_authority)
        false_as_zero["scope_correction_is_safety_remediation"] = 0
        type_confused_authorities.append(("current_false_as_zero", false_as_zero))

        true_as_one = copy.deepcopy(current_authority)
        true_as_one["quota_conservation_constraints_removed"] = 1
        type_confused_authorities.append(("current_true_as_one", true_as_one))

        nested_one_as_true = copy.deepcopy(current_authority)
        nested_plan = nested_one_as_true["authorized_phase_plan"]
        assert isinstance(nested_plan, list)
        assert isinstance(nested_plan[1], dict)
        nested_plan[1]["weight"] = True
        type_confused_authorities.append(("nested_one_as_true", nested_one_as_true))

        historical_true_as_one = copy.deepcopy(historical_authority)
        historical_true_as_one["quota_conservation_constraints_removed"] = 1
        type_confused_authorities.append(
            ("historical_true_as_one", historical_true_as_one)
        )

        for label, candidate in type_confused_authorities:
            with self.subTest(type_confused_authority=label):
                launch = {
                    "claim_id": "11111111-1111-4111-8111-111111111111",
                    "round": 1,
                    "phase": "fanout",
                    "model_launches": 3,
                    "state_dir": str(self.state.resolve()),
                    "artifact_sha": "a" * 64,
                    "protocol_sha": "b" * 64,
                    "status": "success",
                    "global_fuse_authority": candidate,
                    "owner": {
                        "pid": 1,
                        "start_ticks": 1,
                        "ppid": 0,
                        "pgid": 1,
                        "adapter_kind": "fanout",
                    },
                }
                self.seed_ledger([launch])
                with mock.patch.object(
                    campaign_guard,
                    "global_ceiling_policy",
                    return_value=(27, current_authority),
                ), mock.patch.dict(
                    campaign_guard.HISTORICAL_SCOPED_GLOBAL_CEILING_AUTHORITIES,
                    {str(self.doc): (historical_authority,)},
                ):
                    with self.assertRaises(campaign_guard.StateError):
                        campaign_guard.load_ledger(self.doc, create=False)

    def test_per_campaign_ceiling_requires_revision(self) -> None:
        self.fill_default_campaign()
        denied = self.claim(self.filled_rounds + 1, "fanout")
        self.assertEqual(denied.returncode, 4)
        self.assertIn("NOT PLATEAU", denied.stderr)
        self.assertIn("MAGI_BUDGET_EXHAUSTED", denied.stderr)
        self.assertIn("active campaign", denied.stderr)
        self.assertFalse(any((self.doc.parent / ".dual-magi").glob("PLATEAU*")))

    def test_requirement_revision_can_reach_but_not_exceed_global_fuse(self) -> None:
        self.fill_default_campaign()
        self.doc.write_text("# revised requirement\n")
        rollover = self.claim(1, "fanout", self.revision_state())
        self.assertEqual(rollover.returncode, 0, rollover.stderr)
        self.assertIn(
            f"global model launches 15/{GLOBAL_MAX_MODEL_LAUNCHES}",
            rollover.stdout,
        )
        self.assertIn(
            f"campaign model launches 3/{DEFAULT_MAX_MODEL_LAUNCHES}",
            rollover.stdout,
        )
        diverse = self.claim(2, "xfamily", self.revision_state())
        self.assertEqual(diverse.returncode, 0, diverse.stderr)
        self.assertIn(
            f"global model launches 16/{GLOBAL_MAX_MODEL_LAUNCHES}",
            diverse.stdout,
        )

        self.doc.write_text("# second revised requirement\n")
        denied = self.claim(1, "fanout", self.revision_state())
        self.assertEqual(denied.returncode, 4)
        self.assertIn("global campaign history", denied.stderr)
        self.assertIn(
            f"20/{GLOBAL_MAX_MODEL_LAUNCHES} model launches",
            denied.stderr,
        )

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

    def test_non_adapter_owner_cannot_authorize_startup_recovery(self) -> None:
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
        claim_id = claimed.stdout.strip().rsplit("CLAIM_ID=", 1)[-1]
        evidence = self.state / f"round_1_fanout.{claim_id}.FAILED.json"
        evidence.write_text("{}")
        ledger_path = next((self.doc.parent / ".dual-magi").glob("CAMPAIGN.*.json"))
        before = ledger_path.read_bytes()
        denied = self.guard(
            "recover-startup", str(self.doc), claim_id, str(evidence)
        )
        self.assertEqual(denied.returncode, 64)
        self.assertIn("official shell adapter", denied.stderr)
        self.assertEqual(ledger_path.read_bytes(), before)

    def test_concurrent_replacement_claim_has_one_consumer(self) -> None:
        source = self.recoverable_launch()
        ledger_path = self.seed_ledger([source])
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    str(GUARD),
                    "claim",
                    str(self.doc),
                    "1",
                    "fanout",
                    str(self.state),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for _ in range(8)
        ]
        results = [process.communicate(timeout=10) + (process.returncode,) for process in processes]
        self.assertEqual(sum(returncode == 0 for _, _, returncode in results), 1)
        ledger = json.loads(ledger_path.read_text())
        launches = ledger["campaigns"][0]["launches"]
        self.assertEqual(len(launches), 2)
        self.assertEqual(launches[1]["replacement_for"], source["claim_id"])
        self.assertEqual(launches[1]["model_launches"], 3)

    def test_replacement_source_is_shared_admission_signal(self) -> None:
        source = self.recoverable_launch()
        self.assertIs(
            campaign_guard.replacement_source([source], "fanout"), source
        )
        self.assertIsNone(campaign_guard.replacement_source([source], "targeted"))
        source["status"] = "failed"
        self.assertIsNone(campaign_guard.replacement_source([source], "fanout"))

    def test_changed_artifact_after_recovery_rolls_over_without_replacement(self) -> None:
        source = self.recoverable_launch()
        ledger_path = self.seed_ledger([source])
        self.doc.write_text("# revised after startup failure\n")

        admission = campaign_guard.campaign_admission_status(self.doc)
        self.assertEqual(admission["weight"], 3)
        self.assertEqual(admission["required"], 4)

        claimed = self.guard(
            "claim", str(self.doc), "1", "fanout", str(self.revision_state())
        )
        self.assertEqual(claimed.returncode, 0, claimed.stderr)
        ledger = json.loads(ledger_path.read_text())
        self.assertEqual(len(ledger["campaigns"]), 2)
        replacement = ledger["campaigns"][1]["launches"][0]
        self.assertNotIn("replacement_for", replacement)
        self.assertEqual(replacement["model_launches"], 3)
        self.assertEqual(
            replacement["artifact_sha"],
            hashlib.sha256(self.doc.read_bytes()).hexdigest(),
        )
        status = self.guard("claim-status", str(self.doc), replacement["claim_id"])
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(status.stdout.strip(), "running")

    def test_protocol_fix_can_consume_replacement_for_same_artifact(self) -> None:
        source = self.recoverable_launch()
        source["protocol_sha"] = "provider-schema-before-fix"
        ledger_path = self.seed_ledger([source])

        claimed = self.guard(
            "claim", str(self.doc), "1", "fanout", str(self.state)
        )
        self.assertEqual(claimed.returncode, 0, claimed.stderr)
        launches = json.loads(ledger_path.read_text())["campaigns"][0]["launches"]
        self.assertEqual(launches[1]["replacement_for"], source["claim_id"])
        self.assertNotEqual(launches[1]["protocol_sha"], source["protocol_sha"])
        self.assertEqual(launches[1]["artifact_sha"], source["artifact_sha"])

    def test_document_change_after_claim_snapshot_cannot_corrupt_replacement(self) -> None:
        source = self.recoverable_launch()
        ledger_path = self.seed_ledger([source])
        real_file_sha = campaign_guard.file_sha
        calls = 0

        def mutate_after_snapshot(path: Path) -> str:
            nonlocal calls
            calls += 1
            digest = real_file_sha(path)
            self.doc.write_text("# changed after the guarded snapshot\n")
            return digest

        with (
            mock.patch.object(
                campaign_guard, "file_sha", side_effect=mutate_after_snapshot
            ),
            mock.patch("builtins.print"),
        ):
            campaign_guard.claim(
                str(self.doc), "1", "fanout", str(self.state)
            )

        self.assertEqual(calls, 1)
        launches = json.loads(ledger_path.read_text())["campaigns"][0]["launches"]
        self.assertEqual(launches[1]["replacement_for"], source["claim_id"])
        self.assertEqual(launches[1]["artifact_sha"], source["artifact_sha"])
        status = self.guard("claim-status", str(self.doc), launches[1]["claim_id"])
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(status.stdout.strip(), "running")

    def test_changed_artifact_after_recovery_rejects_noninitial_round(self) -> None:
        source = self.recoverable_launch()
        ledger_path = self.seed_ledger([source])
        self.doc.write_text("# revised after startup failure\n")
        before = ledger_path.read_bytes()

        denied = self.guard(
            "claim", str(self.doc), "2", "fanout", str(self.state)
        )
        self.assertEqual(denied.returncode, 64)
        self.assertIn("requires round 1 fanout", denied.stderr)
        self.assertEqual(ledger_path.read_bytes(), before)

    def test_malformed_replacement_link_fails_closed(self) -> None:
        source = self.recoverable_launch()
        consumer = {
            **source,
            "claim_id": "22222222-2222-4222-8222-222222222222",
            "sequence": 2,
            "attempt": 2,
            "status": "failed",
            "claimed_at": "2026-01-01T00:00:02Z",
            "finished_at": "2026-01-01T00:00:03Z",
            "replacement_for": source["claim_id"],
        }
        consumer.pop("recovery")
        consumer["artifact_sha"] = "0" * 64
        self.seed_ledger([source, consumer])
        denied = self.guard("claim-status", str(self.doc), source["claim_id"])
        self.assertEqual(denied.returncode, 2)
        self.assertIn("replacement does not match", denied.stderr)

    def test_historical_repair_makes_observed_14_of_16_final_sequence_affordable(
        self,
    ) -> None:
        ledger_path, source_claim, incident = self.historical_incident()
        with (
            mock.patch.object(
                campaign_guard, "HISTORICAL_STARTUP_INCIDENTS", (incident,)
            ),
            mock.patch("builtins.print"),
        ):
            campaign_guard.repair_historical_startup(str(self.doc), source_claim)
            ledger = json.loads(ledger_path.read_text())
            gross = sum(
                launch["model_launches"]
                for campaign in ledger["campaigns"]
                for launch in campaign["launches"]
            )
            self.assertEqual(gross, 14)
            repairs = [
                repair
                for campaign in ledger["campaigns"]
                for repair in campaign.get("repairs", [])
            ]
            self.assertEqual(len(repairs), 1)
            self.assertEqual(repairs[0]["source_claim_id"], source_claim)
            self.assertEqual(repairs[0]["credited_model_launches"], 3)
            self.assertEqual(campaign_guard.model_launches(ledger["campaigns"]), 11)

            new_state = self.revision_state()
            campaign_guard.claim(str(self.doc), "1", "fanout", str(new_state))
            ledger = json.loads(ledger_path.read_text())
            fanout_claim = ledger["campaigns"][-1]["launches"][-1]["claim_id"]
            campaign_guard.finish(str(self.doc), fanout_claim, "success")
            campaign_guard.claim(str(self.doc), "2", "xfamily", str(new_state))
            ledger = json.loads(ledger_path.read_text())
            xfamily_claim = ledger["campaigns"][-1]["launches"][-1]["claim_id"]
            campaign_guard.finish(str(self.doc), xfamily_claim, "success")

            completed = json.loads(ledger_path.read_text())
            gross = sum(
                launch["model_launches"]
                for campaign in completed["campaigns"]
                for launch in campaign["launches"]
            )
            self.assertEqual(gross, 18)
            self.assertEqual(
                campaign_guard.model_launches(completed["campaigns"]), 15
            )

    def test_issue_271_closed_attestation_matches_independent_expected_values(
        self,
    ) -> None:
        self.assertEqual(len(campaign_guard.HISTORICAL_STARTUP_INCIDENTS), 1)
        installed = campaign_guard.HISTORICAL_STARTUP_INCIDENTS[0]
        self.assertEqual(set(installed), set(ISSUE_271_ATTESTATION))
        for field, expected in ISSUE_271_ATTESTATION.items():
            with self.subTest(field=field):
                self.assertEqual(installed[field], expected)

        fixture = json.loads(ISSUE_271_LEDGER_FIXTURE.read_text())
        history = [
            launch
            for campaign in fixture["campaigns"]
            for launch in campaign["launches"]
        ]
        self.assertEqual(fixture["doc_id"], "8fe2b3353e5e4a5b")
        self.assertEqual(len(history), 6)
        self.assertEqual(sum(item["model_launches"] for item in history), 14)
        self.assertEqual(history[0]["claim_id"], ISSUE_271_SOURCE_CLAIM)
        self.assertEqual(
            history[0]["finished_at"], "2026-07-30T06:13:21.767990+00:00"
        )
        self.assertEqual(
            history[0]["artifact_sha"],
            "34c165c9c5447fafc2b9e27cc119ef32721fa5022b6e137010d5d8cc131cf59a",
        )
        self.assertEqual(
            history[0]["protocol_sha"],
            "26d2f729c8b1639e28f176622424608f7c5e1c99e413584cf1953201c3473171",
        )
        self.assertEqual(
            campaign_guard.canonical_sha256(history),
            ISSUE_271_SANITIZED_HISTORY_SHA256,
        )

    def test_issue_271_fixture_repair_preserves_gross_and_affords_final_reviews(
        self,
    ) -> None:
        ledger_path, _, incident = self.issue_271_fixture()
        with (
            mock.patch.object(
                campaign_guard, "doc_id", return_value=ISSUE_271_ATTESTATION["doc_id"]
            ),
            mock.patch.object(
                campaign_guard, "HISTORICAL_STARTUP_INCIDENTS", (incident,)
            ),
            mock.patch("builtins.print"),
        ):
            campaign_guard.repair_historical_startup(
                str(self.doc), ISSUE_271_SOURCE_CLAIM
            )
            repaired = json.loads(ledger_path.read_text())
            gross = sum(
                launch["model_launches"]
                for campaign in repaired["campaigns"]
                for launch in campaign["launches"]
            )
            self.assertEqual(gross, 14)
            self.assertEqual(campaign_guard.model_launches(repaired["campaigns"]), 11)
            repairs = [
                repair
                for campaign in repaired["campaigns"]
                for repair in campaign.get("repairs", [])
            ]
            self.assertEqual(len(repairs), 1)
            self.assertEqual(
                repairs[0]["history_prefix_sha256"],
                ISSUE_271_SANITIZED_HISTORY_SHA256,
            )

            campaign_guard.claim(str(self.doc), "1", "fanout", str(self.state))
            ledger = json.loads(ledger_path.read_text())
            fanout_claim = ledger["campaigns"][-1]["launches"][-1]["claim_id"]
            campaign_guard.finish(str(self.doc), fanout_claim, "success")
            campaign_guard.claim(str(self.doc), "2", "xfamily", str(self.state))
            ledger = json.loads(ledger_path.read_text())
            xfamily_claim = ledger["campaigns"][-1]["launches"][-1]["claim_id"]
            campaign_guard.finish(str(self.doc), xfamily_claim, "success")

            completed = json.loads(ledger_path.read_text())
            final_gross = sum(
                launch["model_launches"]
                for campaign in completed["campaigns"]
                for launch in campaign["launches"]
            )
            self.assertEqual(final_gross, 18)
            self.assertEqual(
                campaign_guard.model_launches(completed["campaigns"]), 15
            )

    def test_installed_attestation_rejects_old_identity_and_accepts_actual(
        self,
    ) -> None:
        artifact_id = str(ISSUE_271_ATTESTATION["doc_id"])
        with self.assertRaisesRegex(
            campaign_guard.TransitionError,
            "no closed historical startup attestation",
        ):
            campaign_guard.historical_incident(
                artifact_id,
                ISSUE_271_OLD_SOURCE_CLAIM,
            )
        accepted = campaign_guard.historical_incident(
            artifact_id,
            ISSUE_271_SOURCE_CLAIM,
        )
        self.assertIs(accepted, campaign_guard.HISTORICAL_STARTUP_INCIDENTS[0])

    def test_issue_271_fixture_one_field_history_mutations_fail_closed(self) -> None:
        ledger_path, baseline, incident = self.issue_271_fixture()
        mutations = (
            (0, 0, "claim_id", "00000000-0000-4000-8000-000000000000"),
            (0, 0, "finished_at", "2026-07-30T06:13:21+00:00"),
            (0, 0, "artifact_sha", "0" * 64),
            (0, 0, "protocol_sha", "0" * 64),
            (0, 0, "status", "success"),
            (0, 0, "attempt", 2),
            (0, 0, "model_launches", 1),
            (2, 1, "finished_at", "2026-07-30T07:06:01+00:00"),
        )
        with (
            mock.patch.object(
                campaign_guard, "doc_id", return_value=ISSUE_271_ATTESTATION["doc_id"]
            ),
            mock.patch("builtins.print"),
        ):
            for campaign_index, launch_index, field, value in mutations:
                with self.subTest(field=field, value=value):
                    changed = json.loads(json.dumps(baseline))
                    changed["campaigns"][campaign_index]["launches"][launch_index][
                        field
                    ] = value
                    ledger_path.write_text(json.dumps(changed))
                    with self.assertRaises(
                        (
                            campaign_guard.UsageError,
                            campaign_guard.TransitionError,
                            campaign_guard.StateError,
                        )
                    ):
                        campaign_guard.repair_historical_startup(
                            str(self.doc),
                            ISSUE_271_SOURCE_CLAIM,
                            (incident,),
                        )

    def test_historical_repair_refuses_unattested_runtime_input(self) -> None:
        ledger_path, source_claim, _ = self.historical_incident()
        before = ledger_path.read_bytes()
        denied = self.guard(
            "repair-historical-startup",
            str(self.doc),
            source_claim,
        )
        self.assertEqual(denied.returncode, 64)
        self.assertIn("no closed historical startup attestation", denied.stderr)
        self.assertEqual(ledger_path.read_bytes(), before)

    def test_historical_repair_refuses_changed_history_turn_and_attempt_two(
        self,
    ) -> None:
        ledger_path, source_claim, incident = self.historical_incident()
        before = ledger_path.read_bytes()
        changed_history = {**incident, "history_prefix_sha256": "0" * 64}
        with (
            self.assertRaisesRegex(campaign_guard.TransitionError, "does not match"),
            mock.patch("builtins.print"),
        ):
            campaign_guard.repair_historical_startup(
                str(self.doc), source_claim, (changed_history,)
            )
        self.assertEqual(ledger_path.read_bytes(), before)

        turn_started = {**incident, "turn_observed": True}
        with (
            self.assertRaisesRegex(campaign_guard.TransitionError, "does not match"),
            mock.patch("builtins.print"),
        ):
            campaign_guard.repair_historical_startup(
                str(self.doc), source_claim, (turn_started,)
            )
        self.assertEqual(ledger_path.read_bytes(), before)

        ledger = json.loads(ledger_path.read_text())
        attempt_two = "10000000-0000-4000-8000-000000000002"
        attempt_two_incident = {
            **incident,
            "source_claim_id": attempt_two,
            "source_finished_at": ledger["campaigns"][0]["launches"][1][
                "finished_at"
            ],
        }
        with (
            self.assertRaisesRegex(campaign_guard.TransitionError, "does not match"),
            mock.patch("builtins.print"),
        ):
            campaign_guard.repair_historical_startup(
                str(self.doc), attempt_two, (attempt_two_incident,)
            )
        self.assertEqual(ledger_path.read_bytes(), before)

    def test_historical_repair_is_single_use_and_tamper_evident(self) -> None:
        ledger_path, source_claim, incident = self.historical_incident()
        with (
            mock.patch.object(
                campaign_guard, "HISTORICAL_STARTUP_INCIDENTS", (incident,)
            ),
            mock.patch("builtins.print"),
        ):
            campaign_guard.repair_historical_startup(str(self.doc), source_claim)
            with self.assertRaisesRegex(
                campaign_guard.TransitionError, "already consumed"
            ):
                campaign_guard.repair_historical_startup(
                    str(self.doc), source_claim
                )
            with mock.patch.object(
                campaign_guard, "HISTORICAL_STARTUP_INCIDENTS", ()
            ):
                campaign_guard.load_ledger(self.doc.resolve(), create=False)
            ledger = json.loads(ledger_path.read_text())
            ledger["campaigns"][0]["repairs"][0]["credited_model_launches"] = 6
            ledger_path.write_text(json.dumps(ledger))
            with self.assertRaisesRegex(campaign_guard.StateError, "does not match"):
                campaign_guard.load_ledger(self.doc.resolve(), create=False)

    def test_historical_repair_at_rest_rejects_self_consistent_forgery(self) -> None:
        ledger_path, source_claim, incident = self.historical_incident()
        with (
            mock.patch.object(
                campaign_guard, "HISTORICAL_STARTUP_INCIDENTS", (incident,)
            ),
            mock.patch("builtins.print"),
        ):
            campaign_guard.repair_historical_startup(str(self.doc), source_claim)
        baseline = ledger_path.read_bytes()

        def mutate(**changes: object) -> None:
            ledger = json.loads(baseline)
            repair = ledger["campaigns"][0]["repairs"][0]
            for field, value in changes.items():
                if field in repair:
                    repair[field] = value
            repair["attestation"].update(changes)
            repair["attestation_sha256"] = campaign_guard.canonical_sha256(
                repair["attestation"]
            )
            ledger_path.write_text(json.dumps(ledger))

        cases = (
            {"credited_model_launches": 14},
            {
                "history_launch_count": 0,
                "history_prefix_sha256": campaign_guard.canonical_sha256([]),
                "history_gross_model_launches": 0,
            },
            {"credited_model_launches": "3"},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                mutate(**changes)
                with self.assertRaisesRegex(
                    campaign_guard.StateError, "does not match"
                ):
                    campaign_guard.load_ledger(self.doc.resolve(), create=False)

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
        rollover = self.claim(1, "fanout", self.revision_state())
        self.assertEqual(rollover.returncode, 0, rollover.stderr)
        self.assertIn(
            f"global model launches 6/{GLOBAL_MAX_MODEL_LAUNCHES}", rollover.stdout
        )
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
        self.assertEqual(
            self.claim(1, "fanout", self.revision_state()).returncode, 0
        )
        ledger = json.loads(next((self.doc.parent / ".dual-magi").glob("CAMPAIGN.*.json")).read_text())
        self.assertEqual(len(ledger["campaigns"]), 2)
        self.assertEqual(ledger["campaigns"][-1]["started_by"], "automatic-rollover")

    def test_cross_family_claim_requires_preceding_exact_revision(self) -> None:
        first = self.claim(1, "fanout")
        self.assertEqual(first.returncode, 0, first.stderr)
        ledger_path = next((self.doc.parent / ".dual-magi").glob("CAMPAIGN.*.json"))
        before = ledger_path.read_bytes()

        self.doc.write_text("# revised before mandatory cross-family\n")
        denied = self.claim(2, "xfamily", finish=None)
        self.assertEqual(denied.returncode, 64)
        self.assertIn("requires round 1 fanout", denied.stderr)
        self.assertEqual(ledger_path.read_bytes(), before)

        reused_state = self.claim(1, "fanout", finish=None)
        self.assertEqual(reused_state.returncode, 64)
        self.assertIn("revision-scoped state directory", reused_state.stderr)
        self.assertEqual(ledger_path.read_bytes(), before)

        revision_state = self.root / "revisions" / hashlib.sha256(
            self.doc.read_bytes()
        ).hexdigest()[:16]
        rollover = self.claim(1, "fanout", revision_state)
        self.assertEqual(rollover.returncode, 0, rollover.stderr)
        ledger = json.loads(ledger_path.read_text())
        self.assertEqual(len(ledger["campaigns"]), 2)
        self.assertEqual(
            ledger["campaigns"][-1]["launches"][0]["artifact_sha"],
            hashlib.sha256(self.doc.read_bytes()).hexdigest(),
        )

        before_xfamily = ledger_path.read_bytes()
        wrong_state = self.claim(2, "xfamily", self.state, finish=None)
        self.assertEqual(wrong_state.returncode, 64)
        self.assertIn("revision-scoped state directory", wrong_state.stderr)
        self.assertEqual(ledger_path.read_bytes(), before_xfamily)
        xfamily = self.claim(2, "xfamily", revision_state)
        self.assertEqual(xfamily.returncode, 0, xfamily.stderr)

    def test_stranded_cross_revision_xfamily_retries_allow_round_one_rollover(self) -> None:
        current_sha = hashlib.sha256(self.doc.read_bytes()).hexdigest()
        current_protocol = protocol_sha()

        def launch(
            claim_id: str,
            *,
            sequence: int,
            round_no: int,
            phase: str,
            attempt: int,
            artifact_sha: str,
            launch_protocol: str,
            status: str,
        ) -> dict[str, object]:
            return {
                "claim_id": claim_id,
                "sequence": sequence,
                "round": round_no,
                "phase": phase,
                "attempt": attempt,
                "model_launches": PHASE_WEIGHT[phase],
                "state_dir": str(self.state.resolve()),
                "artifact_sha": artifact_sha,
                "protocol_sha": launch_protocol,
                "claimed_at": f"2026-01-01T00:00:0{sequence}Z",
                "finished_at": f"2026-01-01T00:01:0{sequence}Z",
                "status": status,
            }

        ledger_path = self.seed_ledger(
            [
                launch(
                    "10000000-0000-4000-8000-000000000001",
                    sequence=1,
                    round_no=1,
                    phase="fanout",
                    attempt=1,
                    artifact_sha="a" * 64,
                    launch_protocol=current_protocol,
                    status="success",
                ),
                launch(
                    "10000000-0000-4000-8000-000000000002",
                    sequence=2,
                    round_no=2,
                    phase="xfamily",
                    attempt=1,
                    artifact_sha=current_sha,
                    launch_protocol="b" * 64,
                    status="failed",
                ),
                launch(
                    "10000000-0000-4000-8000-000000000003",
                    sequence=3,
                    round_no=2,
                    phase="xfamily",
                    attempt=2,
                    artifact_sha=current_sha,
                    launch_protocol="b" * 64,
                    status="failed",
                ),
            ]
        )

        admission = campaign_guard.campaign_admission_status(self.doc)
        self.assertEqual(admission["kind"], "candidate")
        self.assertEqual(admission["round"], 1)
        self.assertEqual(admission["phase"], "fanout")
        self.assertEqual(admission["weight"], PHASE_WEIGHT["fanout"])

        revision_state = self.root / "revisions" / current_sha[:16]
        rollover = self.claim(1, "fanout", revision_state)
        self.assertEqual(rollover.returncode, 0, rollover.stderr)
        self.assertIn(
            f"global model launches 8/{GLOBAL_MAX_MODEL_LAUNCHES}",
            rollover.stdout,
        )
        ledger = json.loads(ledger_path.read_text())
        self.assertEqual(len(ledger["campaigns"]), 2)
        replacement = ledger["campaigns"][-1]["launches"][0]
        self.assertNotIn("replacement_for", replacement)
        self.assertEqual(replacement["artifact_sha"], current_sha)

    def test_same_revision_xfamily_retry_exhaustion_remains_blocked(self) -> None:
        current_sha = hashlib.sha256(self.doc.read_bytes()).hexdigest()
        current_protocol = protocol_sha()
        launches = []
        for sequence, attempt, phase, status in (
            (1, 1, "fanout", "success"),
            (2, 1, "xfamily", "failed"),
            (3, 2, "xfamily", "failed"),
        ):
            launches.append(
                {
                    "claim_id": f"10000000-0000-4000-8000-{sequence:012d}",
                    "sequence": sequence,
                    "round": 1 if phase == "fanout" else 2,
                    "phase": phase,
                    "attempt": attempt,
                    "model_launches": PHASE_WEIGHT[phase],
                    "state_dir": str(self.state.resolve()),
                    "artifact_sha": current_sha,
                    "protocol_sha": current_protocol,
                    "claimed_at": f"2026-01-01T00:00:0{sequence}Z",
                    "finished_at": f"2026-01-01T00:01:0{sequence}Z",
                    "status": status,
                }
            )
        ledger_path = self.seed_ledger(launches)
        before = ledger_path.read_bytes()

        admission = campaign_guard.campaign_admission_status(self.doc)
        self.assertEqual(admission["kind"], "transition-blocked")
        denied = self.claim(1, "fanout", finish=None)
        self.assertEqual(denied.returncode, 64)
        self.assertEqual(ledger_path.read_bytes(), before)

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
        denied = self.guard(
            "claim", str(self.doc), str(self.filled_rounds + 1), "xfamily",
            str(self.state),
        )
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
        denied = self.guard(
            "claim", str(self.doc), "1", "fanout", str(self.revision_state())
        )
        self.assertEqual(denied.returncode, 4, denied.stderr)
        ledger = json.loads(ledger_path.read_text())
        self.assertEqual(len(ledger["campaigns"]), 1)
        self.assertEqual(len(ledger["campaigns"][0]["launches"]), len(history))

    def test_stray_authorization_file_cannot_extend_fuse(self) -> None:
        self.fill_default_campaign()
        control = self.doc.parent / ".dual-magi"
        approval = control / "CAMPAIGN_CONTINUE.untrusted.json"
        approval.write_text('{"schema_version": 1')
        self.assertEqual(
            self.claim(self.filled_rounds + 1, "fanout").returncode, 4
        )

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
        self.assertIn(
            f"model launches 4/{DEFAULT_MAX_MODEL_LAUNCHES}", claimed.stdout
        )
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
        # The prior artifact must belong to the round just before the one being
        # claimed, so it follows the filled campaign rather than a fixed number.
        prior_round = self.filled_rounds
        prior_source = self.state / f"round_{prior_round}_source.json"
        prior_source.write_text(
            json.dumps(empty_review(self.doc, prior_round, "SOURCE"))
        )
        self.prior = self.state / f"round_{prior_round}_codex.json"
        prior_payload = empty_review(self.doc, prior_round, "SYNTHESIS")
        prior_payload["source_artifacts"] = [
            {
                "path": prior_source.name,
                "sha256": hashlib.sha256(prior_source.read_bytes()).hexdigest(),
            }
        ]
        self.prior.write_text(json.dumps(prior_payload))
        stub_bin = self.root / "bin"
        stub_bin.mkdir()
        marker = self.root / "provider-started"
        codex_stub = stub_bin / "codex"
        codex_stub.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = exec ] && [ \"$2\" = --help ]; then\n"
            "  echo '--output-schema --output-last-message --ephemeral --json'\n"
            "  exit 0\n"
            "fi\n"
            f"touch {marker}\n"
            "exit 70\n"
        )
        codex_stub.chmod(0o755)
        fanout = run(
            str(FANOUT),
            str(self.doc),
            str(self.filled_rounds + 1),
            str(self.state),
            "--prior",
            str(self.prior),
            env={"PATH": f"{stub_bin}:{os.environ['PATH']}"},
        )
        self.assertEqual(fanout.returncode, 4)
        self.assertIn("NOT PLATEAU", fanout.stderr)
        self.assertFalse(marker.exists())

    def test_changed_artifact_can_roll_into_weight_one_targeted_pair(self) -> None:
        self.assertEqual(self.claim(1, "fanout").returncode, 0)
        self.assertEqual(self.claim(2, "xfamily").returncode, 0)
        self.doc.write_text("# fixture\n\nsmall fix\n")

        new_state = self.revision_state()
        targeted = self.claim(1, "targeted", new_state)
        self.assertEqual(targeted.returncode, 0, targeted.stderr)
        final = self.claim(2, "xfamily", new_state)
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

        admission = campaign_guard.campaign_admission_status(self.doc)
        self.assertEqual(admission["kind"], "candidate")
        self.assertEqual(admission["round"], 1)
        self.assertEqual(admission["phase"], "fanout")

        targeted = self.claim(1, "targeted", finish=None)
        self.assertEqual(targeted.returncode, 64)
        fanout = self.claim(1, "fanout", self.revision_state())
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
        fanout = self.claim(1, "fanout", self.revision_state())
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
            with self.assertRaisesRegex(ValueError, "blocking finding requires"):
                validate_findings(payload, SCHEMA)

        payload = finding("new", "MED")
        for optional in (
            "subsystem",
            "root_cause_id",
            "affected_invariant",
            "changes_design_invariant",
            "relation_to_prior",
        ):
            payload["findings"][0].pop(optional)
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
