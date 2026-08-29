#!/usr/bin/env python3
"""Own a canonical, bounded launch ledger for dual-magi campaigns."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import signal
import stat
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import magi_convergence_kernel as kernel
from magi_protocol import (
    protocol_sha as closed_protocol_sha,
    sha256_file,
    strict_json_loads,
)


# Per-campaign autonomous ceiling: 3 full rounds (fanout + its mandatory
# cross-family review, 4 launches each). A campaign that needs more must earn it
# through a real requirement revision, which rolls over into the global fuse
# below; it cannot simply keep re-reviewing the same unchanged target.
#
# Measured 2026-07-27 across 28 multi-round campaigns / 1,084 round artifacts:
# the median campaign stops producing NEW CRITICAL/HIGH findings at artifact
# round 6 (= 3 full rounds), and past roughly that point the findings are
# predominantly residue of the previous round's own fix ("the r34 fix was r35's
# CRITICAL") rather than pre-existing defects. Three rounds covers the median
# campaign whole; a smaller default would cut below it.
#
# For a target far below design scale — one function, one guard clause — tighten
# further per run with MAGI_MAX_AUTONOMOUS_MODEL_LAUNCHES rather than relying on
# this ceiling. A flat allowance is what let a one-function shell guard consume
# 14 of 16 launches over four rounds.
DEFAULT_MAX_MODEL_LAUNCHES = 12
PHASE_WEIGHT = {"fanout": 3, "targeted": 1, "xfamily": 1}
STARTUP_REVIEWER_SETS = {
    "fanout": (
        frozenset({"MELCHIOR", "BALTHASAR", "CASPAR"}),
        frozenset({"HORNET", "GNAT", "WASP"}),
    ),
    "targeted": (
        frozenset({"HORNET"}),
        frozenset({"GNAT"}),
        frozenset({"WASP"}),
    ),
}
FINAL_XFAMILY_RESERVE = PHASE_WEIGHT["xfamily"]
# Hard fuse across all revision campaigns for one target. Deliberately NOT cut:
# in the same corpus five campaigns were still surfacing new CRITICAL/HIGH at
# this boundary, and one shipped-blocking CRITICAL appeared at the 4th
# cross-family round. Depth stays available; it just has to be earned.
GLOBAL_MAX_MODEL_LAUNCHES = 16
SCOPED_GLOBAL_CEILING_OVERRIDES: dict[str, dict[str, object]] = {
    "/home/hrmtz/projects/ZN6/ecu-re-abc-calibration-bind-20260824/docs/re/"
    "RACEROM_ABC_CHANGER_V1.md": {
        "authority_id": "ZN6-ABC-RUNTIME-I4-AUDIT-2026-08-25-27-31",
        "doc_id": "12044291bf20ce79",
        "scope": (
            "ABC runtime I4-pinned v17 exact revision after bounded status admission, "
            "RESYNC rearm, status egress, and feature-disable completion remediation; "
            "one Codex fanout then one Claude xfamily"
        ),
        "authority_reference": (
            "parent scoped drift audit authorized one bounded exact-revision cycle "
            "from canonical ledger usage 27 on 2026-08-25"
        ),
        "drift_audit": {
            "result": "PASS_BOUNDED_REMEDIATION_NOT_SCOPE_DRIFT",
            "prior_review_artifact_sha256": (
                "6e116691a6b4a5be6ad5de308a06697a14f28943bf956aa5150b720259fe1b60"
            ),
            "authorized_artifact_sha256": (
                "0ade043db0a7482d29762b0cbebb0d475d50e0c6103730a42a211e83eb14dcff"
            ),
            "prior_review_finding_count": 4,
            "remediated_roots": [
                "status_admission_projection",
                "resync_corrupt_active_rearm",
                "status_egress_private_buffer_generation_protocol",
                "feature_disable_actual_a_vs_old_bc_completion_semantics",
            ],
            "frozen_implementation_commit": "847856de48f21ff6a5b299a57e4c563b8a8af375",
            "v17_contract_sha256": (
                "5960f8b96a53e27f8c411a6df00721a6640febbe70b7fb5a731b6c52b38ef4e3"
            ),
            "v17_receipt_sha256": (
                "fe8887becdcbae537347e63432d3389f34712bf786aab8fcf74a71178c998f3e"
            ),
            "v17_manifest_sha256": (
                "bc56ab8a4b979407f9bd874a5eaaa1206bd90cd59d2f3c027f0bff3f6f0cb807"
            ),
            "outcome_changed": False,
            "calibration_values_changed": False,
            "implementation_authority_changed": False,
            "consumer_authority_changed": False,
            "rom_authority_changed": False,
            "flash_authority_added": False,
            "hardware_authority_added": False,
            "classification": "BOUNDED_FINDING_REMEDIATION",
        },
        "authorized_artifact_sha256": (
            "0ade043db0a7482d29762b0cbebb0d475d50e0c6103730a42a211e83eb14dcff"
        ),
        "default_ceiling": 16,
        "previous_scoped_ceiling": 27,
        "authorized_max_ceiling": 31,
        "checkpoint_interval": 4,
        "prior_usage": 27,
        "additional_slots": 4,
        "authorized_cycle_weight": 4,
        "authorized_phase_plan": [
            {"phase": "fanout", "weight": 3, "family": "codex"},
            {"phase": "xfamily", "weight": 1, "family": "claude"},
        ],
        "quota_conservation_constraints_removed": True,
        "authority_continuation_kind": "ONE_EXACT_REVISION_COMPLETE_CYCLE",
        "new_ceiling": 31,
    },
    "/home/hrmtz/projects/ZN6/ecu-tuning-abc-fp-20260824/docs/designs/"
    "ABC_FP_CALIBRATION_IMPLEMENTATION_REVIEW.md": {
        "authority_id": "ZN6-ABC-FP-ORCHESTRATOR-AUDIT-2026-08-24-14-18",
        "doc_id": "5e25a5a6211c2849",
        "scope": (
            "ABC FP calibration exact revision after replay shared-oracle remediation; "
            "one Codex fanout then one Claude xfamily"
        ),
        "authority_reference": (
            "user explicitly reset review limits and delegated autonomous completion "
            "on 2026-08-24"
        ),
        "drift_audit": {
            "result": "PASS_BOUNDED_REMEDIATION_NOT_SCOPE_DRIFT",
            "prior_artifact_sha256": (
                "0ffafbfa8b07a9f21f0110aef921aa4609e39f9a472cc931d504c0b15cc10df2"
            ),
            "authorized_artifact_sha256": (
                "e430616b0761f71a8a7c4a4c8775902d9b496895e08ef42c2d0b33861ec2e7cf"
            ),
            "prior_lines": 153,
            "authorized_lines": 158,
            "blocking_root_count": 1,
            "blocking_root": "replay.shared_oracle",
            "independent_oracle_sha256": (
                "6807b6c414ce7873f943aa9ef51a63716d5bf95c4181119eec46782a49e809a7"
            ),
            "oracle_tests_sha256": (
                "6ea6791da0e45d142a70d78cc642bb03874224d3bd43289453c9e0929f45cbc2"
            ),
            "executable_evidence_sha256": (
                "ef85505c0f0e899f1e939ef367f92986097cc3c655872ce3fb8f4b0f7c8ff8c0"
            ),
            "dedicated_tests": 37,
            "full_tests": 399,
            "outcome_changed": False,
            "calibration_values_changed": False,
            "implementation_authority_changed": False,
            "rom_authority_changed": False,
            "flash_authority_added": False,
            "classification": "BOUNDED_FINDING_REMEDIATION",
        },
        "authorized_artifact_sha256": (
            "e430616b0761f71a8a7c4a4c8775902d9b496895e08ef42c2d0b33861ec2e7cf"
        ),
        "default_ceiling": 16,
        "previous_scoped_ceiling": 16,
        "authorized_max_ceiling": 18,
        "checkpoint_interval": 4,
        "prior_usage": 14,
        "additional_slots": 4,
        "authorized_cycle_weight": 4,
        "authorized_phase_plan": [
            {"phase": "fanout", "weight": 3, "family": "codex"},
            {"phase": "xfamily", "weight": 1, "family": "claude"},
        ],
        "authority_continuation_kind": "ONE_EXACT_REVISION_COMPLETE_CYCLE",
        "new_ceiling": 18,
    },
    "/home/hrmtz/projects/ZN6/ecu-tuning/docs/designs/"
    "TELEMETRY-FI-CALIBRATION-HARDENING/01a-canonical-inventory-publication.md": {
        "authority_id": "ZN6-01A-USER-ACK-2026-08-22-TO-36",
        "doc_id": "af61a5fa1b729d66",
        "scope": "Slice 01a exact-revision design review only",
        "authorized_at": "2026-08-22T01:02:12Z",
        "default_ceiling": 16,
        "previous_scoped_ceiling": 20,
        "intermediate_authorized_ceiling": 24,
        "additional_slots": 12,
        "new_ceiling": 36,
    },
    "/home/hrmtz/projects/ZN6/ecu-re/docs/designs/TORQUE-CONTROL-REHOME/"
    "E2a3a1-publisher-hardening-worktree.md": {
        "authority_id": "ZN6-E2A3A1-USER-PASS-2026-08-23-CHECKPOINT-30-34",
        "doc_id": "a3be751c394d935f",
        "scope": "E2a3a1 final exact cycle after zero-writer risk acceptance",
        "authority_reference": "user accepted zero-writer threat model and said pass on 2026-08-23",
        "default_ceiling": 16,
        "previous_scoped_ceiling": 30,
        "authorized_max_ceiling": 34,
        "checkpoint_interval": 4,
        "prior_usage": 30,
        "additional_slots": 4,
        "new_ceiling": 34,
    },
    "/home/hrmtz/projects/ZN6/ecu-re/docs/designs/TORQUE-AR-ROM-INTEGRATION/"
    "S08A-RELOCATABLE-SHADOW-PACK.md": {
        "authority_id": "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-24-REV20-76-80",
        "doc_id": "a4cb34578437a22f",
        "scope": (
            "S08A Claude-authored revision 20 exact design review; "
            "one Codex fanout then one Claude xfamily"
        ),
        "authority_reference": (
            "user defined the global fuse as an orchestrator drift-audit "
            "checkpoint and delegated bounded continuation on 2026-08-23"
        ),
        "drift_audit": {
            "result": "PASS_BOUNDED_REMEDIATION_NOT_SCOPE_DRIFT",
            "revision_19_artifact_sha256": (
                "792d9e230ceda396c33c03d4081b27943a6b5003accb1c811c3f0c6998aec11e"
            ),
            "revision_20_artifact_sha256": (
                "3239fd7debc143ec32de23c88a56bf6f73ca6b5ac81d6e1ceed4884c9d01b9c6"
            ),
            "revision_19_lines": 5694,
            "revision_20_lines": 5793,
            "same_family_finding_count": 9,
            "cross_family_finding_count": 6,
            "blocking_root_count": 2,
            "authoring_receipt_sha256": (
                "8b8f28e56b48f18e3374d72432a40aafdeb8c53fc8df4fb273a843fa89fc9b86"
            ),
            "lifecycle_status_sha256": (
                "ef4fd5e5ceb3b639e658b20ea127f4a97be1d728de372f03d1975564bc284a9d"
            ),
            "epic_sha256": (
                "93ae8e3cacf296beb7dbe331b17eee5abd94544078da5e3b983f0fb4d2b46725"
            ),
            "accepted_remediation_root_count": 2,
            "parent_audit_same_root_count": 0,
            "parent_audit_fix_passes": 2,
            "bounded_calibration_authorization_added": False,
            "rejected_scope_expansions": [
                "bootstrap script implementation",
                "new artifact or acknowledgement type",
                "daemon, service, catalog, or lock",
                "ROM or hardware authorization",
            ],
            "outcome_changed": False,
            "component_inventory_changed": False,
            "implementation_authority_changed": False,
            "rom_authority_changed": False,
            "classification": "BOUNDED_FINDING_REMEDIATION",
        },
        "authorized_artifact_sha256": (
            "3239fd7debc143ec32de23c88a56bf6f73ca6b5ac81d6e1ceed4884c9d01b9c6"
        ),
        "default_ceiling": 16,
        "previous_scoped_ceiling": 76,
        "authorized_max_ceiling": 80,
        "checkpoint_interval": 4,
        "prior_usage": 76,
        "additional_slots": 4,
        "authorized_cycle_weight": 4,
        "authorized_phase_plan": [
            {"phase": "fanout", "weight": 3, "family": "codex"},
            {"phase": "xfamily", "weight": 1, "family": "claude"},
        ],
        "authority_continuation_kind": "ONE_EXACT_REVISION_COMPLETE_CYCLE",
        "new_ceiling": 80,
    },
    "/home/hrmtz/projects/ZN6/ecu-re-knock-telemetry-f0-overlay/docs/re/"
    "KNOCK_TELEMETRY_F0_OVERLAY_WIRE.md": {
        "authority_id": (
            "ZN6-KNOCK-F0-USER-AUTH-2026-08-23-"
            "SEQ4689-SCOPE4708-4709-23-27"
        ),
        "doc_id": "c2c6bef4d687838c",
        "scope": (
            "F0 overlay scope-corrected proportional operational-boundary "
            "exact WIRE review; "
            "one Codex fanout then one Claude xfamily"
        ),
        "authority_reference_mailbox_seq": 4689,
        "scope_correction_mailbox_seqs": [4708, 4709],
        "prior_authority_id": (
            "ZN6-KNOCK-F0-USER-AUTH-2026-08-23-SEQ4689-19-23"
        ),
        "scope_basis_campaign_id": "e6348644-1bea-45cf-92c1-0192cf06cb5d",
        "scope_basis_claim_id": "396758f8-fbe5-4301-8998-c81da0a96b63",
        "scope_basis_review_output_sha256": (
            "6f9af979bc9fb0c18b565cd03c17da962ded039d8c85e8e26846bd609af50721"
        ),
        "scope_basis_finding_id": "XF-R2-001",
        "scope_basis_root_cause_id": "recorder.namespace_generation_unbound",
        "scope_basis_reported_severity": "HIGH",
        "scope_corrected_classification": "OPTIONAL_OPERATIONAL_HARDENING",
        "scope_corrected_rom_admission_blocker": False,
        "scope_correction_is_safety_remediation": False,
        "removed_transactional_recorder_gates": [
            "REC_PATH_DURABLE",
            "REC_PROFILE_DURABLE",
        ],
        "retained_operational_requirements": [
            "RAWOUT_ONLY_F0_ANALYSIS",
            "OUT_NON_ADMISSIBLE_UNDER_F0_SINGLE_PENDING_V1",
            "ACTIVE_MODE_01_SEPARATE_CAN_AUTHORITY",
        ],
        "authorized_artifact_sha256": (
            "084a4c8dd7ea0fca25df7256039617b4defa337117a8366db1de073f45f16637"
        ),
        "default_ceiling": 16,
        "previous_scoped_ceiling": 23,
        "authorized_max_ceiling": 27,
        "checkpoint_interval": 4,
        "prior_usage": 23,
        "additional_slots": 4,
        "authorized_cycle_weight": 4,
        "authorized_phase_plan": [
            {"phase": "fanout", "weight": 3, "family": "codex"},
            {"phase": "xfamily", "weight": 1, "family": "claude"},
        ],
        "quota_conservation_constraints_removed": True,
        "authority_continuation_kind": "STANDING_FURTHER_CYCLE_AUTHORITY",
        "new_ceiling": 27,
    },
}
HISTORICAL_SCOPED_GLOBAL_CEILING_AUTHORITIES: dict[str, tuple[dict[str, object], ...]] = {
    "/home/hrmtz/projects/ZN6/ecu-re-abc-calibration-bind-20260824/docs/re/"
    "RACEROM_ABC_CHANGER_V1.md": (
        {
            "authority_id": "ZN6-ABC-RUNTIME-ORCHESTRATOR-AUDIT-2026-08-24-15-19",
            "doc_id": "12044291bf20ce79",
            "scope": (
                "ABC runtime frozen post-I3 v10 exact revision after bounded v7 "
                "finding remediation; one Codex fanout then one Claude xfamily"
            ),
            "authority_reference": (
                "user explicitly reset review limits, removed quota-conservation "
                "restrictions, and delegated autonomous completion on 2026-08-24"
            ),
            "drift_audit": {
                "result": "PASS_BOUNDED_REMEDIATION_NOT_SCOPE_DRIFT",
                "prior_review_artifact_sha256": (
                    "2f8c4f284fc207d54becfee05cad1591c1fac56a755f910e3aef2653697cacf7"
                ),
                "authorized_artifact_sha256": (
                    "bc21ae94bb2a19ce06bb559cc8593695b0ecf89659b24c3af8d4759e1ad7ef99"
                ),
                "prior_review_finding_count": 3,
                "frozen_implementation_commit": "76c3507e8684882780d1536b6b02cb2aee558144",
                "contract_sha256": (
                    "90531015c60e9fbe14d3bd6f7f3e5cd50fd52658d6f3c1d64cdfca093709007c"
                ),
                "v10_manifest_sha256": (
                    "45947bd0a56d472d2f680b5560e3b9d93808833aedad3490d56c4d0da61d6525"
                ),
                "outcome_changed": False,
                "calibration_values_changed": False,
                "implementation_authority_changed": False,
                "rom_authority_changed": False,
                "flash_authority_added": False,
                "classification": "BOUNDED_FINDING_REMEDIATION",
            },
            "authorized_artifact_sha256": (
                "bc21ae94bb2a19ce06bb559cc8593695b0ecf89659b24c3af8d4759e1ad7ef99"
            ),
            "default_ceiling": 16,
            "previous_scoped_ceiling": 16,
            "authorized_max_ceiling": 19,
            "checkpoint_interval": 4,
            "prior_usage": 15,
            "additional_slots": 4,
            "authorized_cycle_weight": 4,
            "authorized_phase_plan": [
                {"phase": "fanout", "weight": 3, "family": "codex"},
                {"phase": "xfamily", "weight": 1, "family": "claude"},
            ],
            "quota_conservation_constraints_removed": True,
            "authority_continuation_kind": "ONE_EXACT_REVISION_COMPLETE_CYCLE",
            "new_ceiling": 19,
        },
        {
            "authority_id": "ZN6-ABC-RUNTIME-ORCHESTRATOR-AUDIT-2026-08-24-19-23",
            "doc_id": "12044291bf20ce79",
            "scope": (
                "ABC runtime exact revision after bounded writer inventory/proof, "
                "decoder exact-type, strict duplicate-JSON, and actual reviewer-"
                "provenance remediation; one Codex fanout then one Claude xfamily"
            ),
            "authority_reference": (
                "user explicitly reset review limits, removed quota-conservation "
                "restrictions, and delegated autonomous completion on 2026-08-24"
            ),
            "drift_audit": {
                "result": "PASS_BOUNDED_REMEDIATION_NOT_SCOPE_DRIFT",
                "prior_review_artifact_sha256": (
                    "bc21ae94bb2a19ce06bb559cc8593695b0ecf89659b24c3af8d4759e1ad7ef99"
                ),
                "authorized_artifact_sha256": (
                    "2d5a41341dae37ef67dcf05bad26f0e1cf18b6deed279e152a5e859d101e3e98"
                ),
                "prior_review_finding_count": 5,
                "remediated_roots": [
                    "effective_valid_writer_inventory_and_structural_proof",
                    "command_decoder_exact_input_types",
                    "strict_duplicate_json_rejection",
                    "actual_cross_family_reviewer_provenance",
                ],
                "accepted_nonblocking_risks": [
                    "oversized_input",
                    "sparse_file",
                    "symlink_input",
                ],
                "frozen_implementation_commit": (
                    "76c3507e8684882780d1536b6b02cb2aee558144"
                ),
                "contract_sha256": (
                    "90531015c60e9fbe14d3bd6f7f3e5cd50fd52658d6f3c1d64cdfca093709007c"
                ),
                "v10_manifest_sha256": (
                    "45947bd0a56d472d2f680b5560e3b9d93808833aedad3490d56c4d0da61d6525"
                ),
                "outcome_changed": False,
                "calibration_values_changed": False,
                "implementation_authority_changed": False,
                "rom_authority_changed": False,
                "flash_authority_added": False,
                "classification": "BOUNDED_FINDING_REMEDIATION",
            },
            "authorized_artifact_sha256": (
                "2d5a41341dae37ef67dcf05bad26f0e1cf18b6deed279e152a5e859d101e3e98"
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
            "authority_continuation_kind": "ONE_EXACT_REVISION_COMPLETE_CYCLE",
            "new_ceiling": 23,
        },
        {
            "authority_id": "ZN6-ABC-RUNTIME-ORCHESTRATOR-AUDIT-2026-08-24-23-27",
            "doc_id": "12044291bf20ce79",
            "scope": (
                "ABC runtime exact revision after bounded non-finite JSON rejection, "
                "typed nested authority/requirements, and visible-ASCII reviewer-model "
                "remediation; one Codex fanout then one Claude xfamily"
            ),
            "authority_reference": (
                "parent audit authorized bounded exact-revision completion after the "
                "invalidated G8 marker on 2026-08-24"
            ),
            "drift_audit": {
                "result": "PASS_BOUNDED_REMEDIATION_NOT_SCOPE_DRIFT",
                "prior_review_artifact_sha256": (
                    "2d5a41341dae37ef67dcf05bad26f0e1cf18b6deed279e152a5e859d101e3e98"
                ),
                "authorized_artifact_sha256": (
                    "6e116691a6b4a5be6ad5de308a06697a14f28943bf956aa5150b720259fe1b60"
                ),
                "prior_review_finding_count": 4,
                "remediated_roots": [
                    "strict_json_nonfinite_constants",
                    "typed_nested_authority_and_requirements",
                    "visible_ascii_reviewer_model",
                ],
                "accepted_nonblocking_risks": [
                    "trusted_local_oversized_sparse_symlink_input",
                    "self_declared_reviewer_provenance",
                ],
                "invalidated_historical_marker": {
                    "basename": "PLATEAU.12044291bf20ce79.2d5a41341dae37ef",
                    "sha256": (
                        "60a95cde2b9f998f5dc9ba0792586a5a51b5b23511d0d617ea0174bd9a716d0a"
                    ),
                    "status": "INVALIDATED_G8_CARRIED_SEVERITY_DOWNGRADE",
                    "shipping_authority": False,
                },
                "frozen_implementation_commit": (
                    "76c3507e8684882780d1536b6b02cb2aee558144"
                ),
                "v13_contract_sha256": (
                    "61366e48a8e87e7e328c6119b023dedcf3e133c8c214d391ff3edd8fe1d0e058"
                ),
                "v13_receipt_sha256": (
                    "c5732fc03288b87a37d20330ca8f496ffe77c2ae79cc9de49f152875e23acea7"
                ),
                "v13_manifest_sha256": (
                    "8f037e9592ce499fadd080c70c54ce604041c8d03c176b0234f820c702770b88"
                ),
                "outcome_changed": False,
                "calibration_values_changed": False,
                "implementation_authority_changed": False,
                "rom_authority_changed": False,
                "flash_authority_added": False,
                "classification": "BOUNDED_FINDING_REMEDIATION",
            },
            "authorized_artifact_sha256": (
                "6e116691a6b4a5be6ad5de308a06697a14f28943bf956aa5150b720259fe1b60"
            ),
            "default_ceiling": 16,
            "previous_scoped_ceiling": 23,
            "authorized_max_ceiling": 27,
            "checkpoint_interval": 4,
            "prior_usage": 23,
            "additional_slots": 4,
            "authorized_cycle_weight": 4,
            "authorized_phase_plan": [
                {"phase": "fanout", "weight": 3, "family": "codex"},
                {"phase": "xfamily", "weight": 1, "family": "claude"},
            ],
            "quota_conservation_constraints_removed": True,
            "authority_continuation_kind": "ONE_EXACT_REVISION_COMPLETE_CYCLE",
            "new_ceiling": 27,
        },
    ),
    "/home/hrmtz/projects/ZN6/ecu-tuning/docs/designs/"
    "TELEMETRY-FI-CALIBRATION-HARDENING/01a-canonical-inventory-publication.md": (
        {
            "authority_id": "ZN6-01A-USER-ACK-2026-08-22",
            "doc_id": "af61a5fa1b729d66",
            "scope": "Slice 01a exact-revision design review only",
            "authorized_at": "2026-08-22T00:34:33Z",
            "old_ceiling": 16,
            "new_ceiling": 20,
        },
    ),
    "/home/hrmtz/projects/ZN6/ecu-re/docs/designs/TORQUE-CONTROL-REHOME/"
    "E2a3a1-publisher-hardening-worktree.md": (
        {
            "authority_id": "ZN6-E2A3A1-USER-ACK-2026-08-22-CHECKPOINT-14-18",
            "doc_id": "a3be751c394d935f",
            "scope": "E2a3a1 material successor cycle; torque-integrator review required at 18",
            "authority_reference_mailbox_seq": 4401,
            "default_ceiling": 16,
            "previous_scoped_ceiling": 16,
            "authorized_max_ceiling": 32,
            "checkpoint_interval": 4,
            "prior_usage": 14,
            "additional_slots": 2,
            "new_ceiling": 18,
        },
        {
            "authority_id": "ZN6-E2A3A1-USER-ACK-2026-08-22-CHECKPOINT-18-22",
            "doc_id": "a3be751c394d935f",
            "scope": "E2a3a1 exact repair recheck; torque-integrator review required at 22",
            "authority_reference_mailbox_seq": 4401,
            "default_ceiling": 16,
            "previous_scoped_ceiling": 18,
            "authorized_max_ceiling": 32,
            "checkpoint_interval": 4,
            "prior_usage": 18,
            "additional_slots": 4,
            "new_ceiling": 22,
        },
        {
            "authority_id": "ZN6-E2A3A1-USER-ACK-2026-08-22-CHECKPOINT-22-26",
            "doc_id": "a3be751c394d935f",
            "scope": "E2a3a1 immutable snapshot repair recheck; torque-integrator review required at 26",
            "authority_reference_mailbox_seq": 4401,
            "default_ceiling": 16,
            "previous_scoped_ceiling": 22,
            "authorized_max_ceiling": 32,
            "checkpoint_interval": 4,
            "prior_usage": 22,
            "additional_slots": 4,
            "new_ceiling": 26,
        },
        {
            "authority_id": "ZN6-E2A3A1-USER-ACK-2026-08-22-CHECKPOINT-26-30",
            "doc_id": "a3be751c394d935f",
            "scope": "E2a3a1 recovery and provider repair recheck; torque-integrator review required at 30",
            "authority_reference_mailbox_seq": 4401,
            "default_ceiling": 16,
            "previous_scoped_ceiling": 26,
            "authorized_max_ceiling": 32,
            "checkpoint_interval": 4,
            "prior_usage": 26,
            "additional_slots": 4,
            "new_ceiling": 30,
        },
    ),
    "/home/hrmtz/projects/ZN6/ecu-re/docs/designs/TORQUE-AR-ROM-INTEGRATION/"
    "S08A-RELOCATABLE-SHADOW-PACK.md": (
        {
            "authority_id": "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-13-17",
            "doc_id": "a4cb34578437a22f",
            "scope": (
                "S08A Claude-authored revision 5 exact design review; "
                "one Codex fanout then one Claude xfamily"
            ),
            "authority_reference": (
                "user defined the global fuse as an orchestrator drift-audit "
                "checkpoint and delegated bounded continuation on 2026-08-23"
            ),
            "drift_audit": {
                "result": "PASS_BOUNDED_REMEDIATION_NOT_SCOPE_DRIFT",
                "revision_4_artifact_sha256": (
                    "a138a3494b56d17bfe93fe98bb4a2debe6d72e67d822409af5cafc36ebdbaa36"
                ),
                "revision_5_artifact_sha256": (
                    "2cb92a25b2349fc88c31bd5a345eda7325a79a3d03dc87793076f8cef9196e5c"
                ),
                "revision_4_lines": 2287,
                "revision_5_lines": 2669,
                "synthesis_finding_count": 9,
                "outcome_changed": False,
                "component_inventory_changed": False,
                "implementation_authority_changed": False,
                "rom_authority_changed": False,
                "classification": "BOUNDED_FINDING_REMEDIATION",
            },
            "authorized_artifact_sha256": (
                "2cb92a25b2349fc88c31bd5a345eda7325a79a3d03dc87793076f8cef9196e5c"
            ),
            "default_ceiling": 16,
            "previous_scoped_ceiling": 16,
            "authorized_max_ceiling": 17,
            "checkpoint_interval": 4,
            "prior_usage": 13,
            "additional_slots": 4,
            "authorized_cycle_weight": 4,
            "authorized_phase_plan": [
                {"phase": "fanout", "weight": 3, "family": "codex"},
                {"phase": "xfamily", "weight": 1, "family": "claude"},
            ],
            "authority_continuation_kind": "ONE_EXACT_REVISION_COMPLETE_CYCLE",
            "new_ceiling": 17,
        },
        {
            "authority_id": "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-17-21",
            "doc_id": "a4cb34578437a22f",
            "scope": (
                "S08A Claude-authored revision 6 exact design review; "
                "one Codex fanout then one Claude xfamily"
            ),
            "authority_reference": (
                "user defined the global fuse as an orchestrator drift-audit "
                "checkpoint and delegated bounded continuation on 2026-08-23"
            ),
            "drift_audit": {
                "result": "PASS_BOUNDED_REMEDIATION_NOT_SCOPE_DRIFT",
                "revision_5_artifact_sha256": (
                    "2cb92a25b2349fc88c31bd5a345eda7325a79a3d03dc87793076f8cef9196e5c"
                ),
                "revision_6_artifact_sha256": (
                    "b9508e6c508c5ce155c88620e832e7bc6901cb47e4204c78cd5da937b9870b11"
                ),
                "revision_5_lines": 2669,
                "revision_6_lines": 3095,
                "same_family_finding_count": 7,
                "cross_family_finding_count": 10,
                "blocking_root_count": 7,
                "authoring_receipt_sha256": (
                    "3d70029496ebb8b466aefb36e1e5d12fdeeddaf80944fd48e10e71bb8347eb13"
                ),
                "rejected_scope_expansions": [
                    "NON_PUBLISH_CALIBRATION_SUBSYSTEM",
                    "POINTER_TARGET_RELEASE_DESCRIPTOR_REPLACEMENT",
                ],
                "outcome_changed": False,
                "component_inventory_changed": False,
                "implementation_authority_changed": False,
                "rom_authority_changed": False,
                "classification": "BOUNDED_FINDING_REMEDIATION",
            },
            "authorized_artifact_sha256": (
                "b9508e6c508c5ce155c88620e832e7bc6901cb47e4204c78cd5da937b9870b11"
            ),
            "default_ceiling": 16,
            "previous_scoped_ceiling": 17,
            "authorized_max_ceiling": 21,
            "checkpoint_interval": 4,
            "prior_usage": 17,
            "additional_slots": 4,
            "authorized_cycle_weight": 4,
            "authorized_phase_plan": [
                {"phase": "fanout", "weight": 3, "family": "codex"},
                {"phase": "xfamily", "weight": 1, "family": "claude"},
            ],
            "authority_continuation_kind": "ONE_EXACT_REVISION_COMPLETE_CYCLE",
            "new_ceiling": 21,
        },
        {
            "authority_id": (
                "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-"
                "CAPACITY-RECOVERY-20-24"
            ),
            "doc_id": "a4cb34578437a22f",
            "scope": (
                "S08A revision 6 exact design review after a zero-evidence "
                "Codex usage-limit fanout failure; one replacement Codex fanout "
                "then one Claude xfamily"
            ),
            "authority_reference": (
                "user defined the global fuse as an orchestrator drift-audit "
                "checkpoint and delegated bounded continuation on 2026-08-23"
            ),
            "drift_audit": {
                "result": "PASS_BOUNDED_REMEDIATION_NOT_SCOPE_DRIFT",
                "revision_5_artifact_sha256": (
                    "2cb92a25b2349fc88c31bd5a345eda7325a79a3d03dc87793076f8cef9196e5c"
                ),
                "revision_6_artifact_sha256": (
                    "b9508e6c508c5ce155c88620e832e7bc6901cb47e4204c78cd5da937b9870b11"
                ),
                "revision_5_lines": 2669,
                "revision_6_lines": 3095,
                "same_family_finding_count": 7,
                "cross_family_finding_count": 10,
                "blocking_root_count": 7,
                "authoring_receipt_sha256": (
                    "3d70029496ebb8b466aefb36e1e5d12fdeeddaf80944fd48e10e71bb8347eb13"
                ),
                "rejected_scope_expansions": [
                    "NON_PUBLISH_CALIBRATION_SUBSYSTEM",
                    "POINTER_TARGET_RELEASE_DESCRIPTOR_REPLACEMENT",
                ],
                "outcome_changed": False,
                "component_inventory_changed": False,
                "implementation_authority_changed": False,
                "rom_authority_changed": False,
                "classification": "BOUNDED_FINDING_REMEDIATION",
                "provider_failure_classification": "provider-exit",
                "failed_reviewer_turns": 3,
                "valid_review_artifacts_produced": 0,
                "failure_receipt_sha256": (
                    "7111be87184c994243e459bf8d8c64396331e3fc69609e4582e15a884df1dac9"
                ),
                "capacity_reset_redeemed": True,
                "recovery_classification": (
                    "EXTERNAL_CAPACITY_FAILURE_NOT_SCOPE_DRIFT"
                ),
            },
            "authorized_artifact_sha256": (
                "b9508e6c508c5ce155c88620e832e7bc6901cb47e4204c78cd5da937b9870b11"
            ),
            "default_ceiling": 16,
            "previous_scoped_ceiling": 21,
            "authorized_max_ceiling": 24,
            "checkpoint_interval": 4,
            "prior_usage": 20,
            "additional_slots": 4,
            "authorized_cycle_weight": 4,
            "authorized_phase_plan": [
                {"phase": "fanout", "weight": 3, "family": "codex"},
                {"phase": "xfamily", "weight": 1, "family": "claude"},
            ],
            "authority_continuation_kind": (
                "ONE_EXACT_REVISION_CAPACITY_RECOVERY_CYCLE"
            ),
            "new_ceiling": 24,
        },
        {
            "authority_id": "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-REV7-24-28",
            "doc_id": "a4cb34578437a22f",
            "scope": (
                "S08A Claude-authored revision 7 exact design review; "
                "one Codex fanout then one Claude xfamily"
            ),
            "authority_reference": (
                "user defined the global fuse as an orchestrator drift-audit "
                "checkpoint and delegated bounded continuation on 2026-08-23"
            ),
            "drift_audit": {
                "result": "PASS_BOUNDED_REMEDIATION_NOT_SCOPE_DRIFT",
                "revision_6_artifact_sha256": (
                    "b9508e6c508c5ce155c88620e832e7bc6901cb47e4204c78cd5da937b9870b11"
                ),
                "revision_7_artifact_sha256": (
                    "ec1e3b58982bd750e1b2e9a852d6a44cad4d2767ed3533f6534e0959b1fe0db8"
                ),
                "revision_6_lines": 3095,
                "revision_7_lines": 3406,
                "same_family_finding_count": 7,
                "cross_family_finding_count": 8,
                "blocking_root_count": 8,
                "authoring_receipt_sha256": (
                    "003c2b5033ec31170cc023cabbd34de74a6902f956d1525999af14b47193459d"
                ),
                "lifecycle_status_sha256": (
                    "4fefb66721e61ce4815958bf2ef3e8220f1143cdfe71cc1f22d1cf8b7f9abcba"
                ),
                "epic_sha256": (
                    "f7dbba10f06700424bcabab4dfb6848dcd47ba1fb4bf2647017ed58457b925b6"
                ),
                "accepted_remediation_root_count": 8,
                "rejected_scope_expansions": [],
                "outcome_changed": False,
                "component_inventory_changed": False,
                "implementation_authority_changed": False,
                "rom_authority_changed": False,
                "classification": "BOUNDED_FINDING_REMEDIATION",
            },
            "authorized_artifact_sha256": (
                "ec1e3b58982bd750e1b2e9a852d6a44cad4d2767ed3533f6534e0959b1fe0db8"
            ),
            "default_ceiling": 16,
            "previous_scoped_ceiling": 24,
            "authorized_max_ceiling": 28,
            "checkpoint_interval": 4,
            "prior_usage": 24,
            "additional_slots": 4,
            "authorized_cycle_weight": 4,
            "authorized_phase_plan": [
                {"phase": "fanout", "weight": 3, "family": "codex"},
                {"phase": "xfamily", "weight": 1, "family": "claude"},
            ],
            "authority_continuation_kind": "ONE_EXACT_REVISION_COMPLETE_CYCLE",
            "new_ceiling": 28,
        },
        {
            "authority_id": "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-REV8-28-32",
            "doc_id": "a4cb34578437a22f",
            "scope": (
                "S08A Claude-authored revision 8 exact design review; "
                "one Codex fanout then one Claude xfamily"
            ),
            "authority_reference": (
                "user defined the global fuse as an orchestrator drift-audit "
                "checkpoint and delegated bounded continuation on 2026-08-23"
            ),
            "drift_audit": {
                "result": "PASS_BOUNDED_REMEDIATION_NOT_SCOPE_DRIFT",
                "revision_7_artifact_sha256": (
                    "ec1e3b58982bd750e1b2e9a852d6a44cad4d2767ed3533f6534e0959b1fe0db8"
                ),
                "revision_8_artifact_sha256": (
                    "1f35249cde256e16175084d9e1538ed13aa6c0c647e13c5396705e3b68f4a1fd"
                ),
                "revision_7_lines": 3406,
                "revision_8_lines": 3592,
                "same_family_finding_count": 7,
                "cross_family_finding_count": 8,
                "blocking_root_count": 7,
                "authoring_receipt_sha256": (
                    "b774782dee8cc52efb4e75b06637d04e986ef2d238d646c380dbe0f51f7fd7b4"
                ),
                "lifecycle_status_sha256": (
                    "0a14ab69e3fe1b60795c8e2dc16e9848fbef411bad98d9581a7a57851b56e34b"
                ),
                "epic_sha256": (
                    "5906ed92fa5ed08b101b59e5c85be298f0d78e9b9e2b4a0d8faabd3b486bea85"
                ),
                "accepted_remediation_root_count": 7,
                "orchestrator_process_root_count": 1,
                "orchestrator_process_roots_closed": 1,
                "rejected_scope_expansions": [],
                "outcome_changed": False,
                "component_inventory_changed": False,
                "implementation_authority_changed": False,
                "rom_authority_changed": False,
                "classification": "BOUNDED_FINDING_REMEDIATION",
            },
            "authorized_artifact_sha256": (
                "1f35249cde256e16175084d9e1538ed13aa6c0c647e13c5396705e3b68f4a1fd"
            ),
            "default_ceiling": 16,
            "previous_scoped_ceiling": 28,
            "authorized_max_ceiling": 32,
            "checkpoint_interval": 4,
            "prior_usage": 28,
            "additional_slots": 4,
            "authorized_cycle_weight": 4,
            "authorized_phase_plan": [
                {"phase": "fanout", "weight": 3, "family": "codex"},
                {"phase": "xfamily", "weight": 1, "family": "claude"},
            ],
            "authority_continuation_kind": "ONE_EXACT_REVISION_COMPLETE_CYCLE",
            "new_ceiling": 32,
        },
        {
            "authority_id": "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-REV9-32-36",
            "doc_id": "a4cb34578437a22f",
            "scope": (
                "S08A Claude-authored revision 9 exact design review; "
                "one Codex fanout then one Claude xfamily"
            ),
            "authority_reference": (
                "user defined the global fuse as an orchestrator drift-audit "
                "checkpoint and delegated bounded continuation on 2026-08-23"
            ),
            "drift_audit": {
                "result": "PASS_BOUNDED_REMEDIATION_NOT_SCOPE_DRIFT",
                "revision_8_artifact_sha256": (
                    "1f35249cde256e16175084d9e1538ed13aa6c0c647e13c5396705e3b68f4a1fd"
                ),
                "revision_9_artifact_sha256": (
                    "214a06f2501c02d612957dbfc0ed603d44406ac22c9c5aac10316191e4fee94d"
                ),
                "revision_8_lines": 3592,
                "revision_9_lines": 3949,
                "same_family_finding_count": 7,
                "cross_family_finding_count": 8,
                "blocking_root_count": 8,
                "authoring_receipt_sha256": (
                    "78c8192a341b0ac799679041d5474341daeff8b92eb468e82d8032c8009c7b02"
                ),
                "lifecycle_status_sha256": (
                    "b258ebfcf146f326d843f30c14f82b42619d94fde6cb16a777ffae58f535f07f"
                ),
                "epic_sha256": (
                    "0dc1ac48a4c79b2e458a0200c3c3486644426e66be759a26bcbd9fec03ebdf37"
                ),
                "accepted_remediation_root_count": 11,
                "parent_audit_same_root_count": 4,
                "parent_audit_fix_passes": 2,
                "bounded_calibration_authorization_added": True,
                "rejected_scope_expansions": [],
                "outcome_changed": False,
                "component_inventory_changed": False,
                "implementation_authority_changed": False,
                "rom_authority_changed": False,
                "classification": "BOUNDED_FINDING_REMEDIATION",
            },
            "authorized_artifact_sha256": (
                "214a06f2501c02d612957dbfc0ed603d44406ac22c9c5aac10316191e4fee94d"
            ),
            "default_ceiling": 16,
            "previous_scoped_ceiling": 32,
            "authorized_max_ceiling": 36,
            "checkpoint_interval": 4,
            "prior_usage": 32,
            "additional_slots": 4,
            "authorized_cycle_weight": 4,
            "authorized_phase_plan": [
                {"phase": "fanout", "weight": 3, "family": "codex"},
                {"phase": "xfamily", "weight": 1, "family": "claude"},
            ],
            "authority_continuation_kind": "ONE_EXACT_REVISION_COMPLETE_CYCLE",
            "new_ceiling": 36,
        },
        {
            "authority_id": "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-REV10-36-40",
            "doc_id": "a4cb34578437a22f",
            "scope": "S08A Claude-authored revision 10 exact design review; one Codex fanout then one Claude xfamily",
            "authority_reference": "user defined the global fuse as an orchestrator drift-audit checkpoint and delegated bounded continuation on 2026-08-23",
            "drift_audit": {
                "result": "PASS_BOUNDED_REMEDIATION_NOT_SCOPE_DRIFT",
                "revision_9_artifact_sha256": "214a06f2501c02d612957dbfc0ed603d44406ac22c9c5aac10316191e4fee94d",
                "revision_10_artifact_sha256": "abdd11dca80eab7fc6be24134cee03781a4de4e5a367f6dbc25ca16c2f56423e",
                "revision_9_lines": 3949,
                "revision_10_lines": 4199,
                "same_family_finding_count": 7,
                "cross_family_finding_count": 8,
                "blocking_root_count": 6,
                "authoring_receipt_sha256": "c278cc7b0e5add1d5020dac63bb0f874ee76c433108831104e28714165220d57",
                "lifecycle_status_sha256": "f7bd043b0091e55982ad8320fdcb43b8109e657fcff26cd03c2249d7149a677b",
                "epic_sha256": "0b357c8a2170df6a78b30b2248a8c2c240a890ef458120b1b870c981dd8c823b",
                "accepted_remediation_root_count": 7,
                "parent_audit_same_root_count": 1,
                "parent_audit_fix_passes": 1,
                "bounded_calibration_authorization_added": False,
                "rejected_scope_expansions": [],
                "outcome_changed": False,
                "component_inventory_changed": False,
                "implementation_authority_changed": False,
                "rom_authority_changed": False,
                "classification": "BOUNDED_FINDING_REMEDIATION",
            },
            "authorized_artifact_sha256": "abdd11dca80eab7fc6be24134cee03781a4de4e5a367f6dbc25ca16c2f56423e",
            "default_ceiling": 16,
            "previous_scoped_ceiling": 36,
            "authorized_max_ceiling": 40,
            "checkpoint_interval": 4,
            "prior_usage": 36,
            "additional_slots": 4,
            "authorized_cycle_weight": 4,
            "authorized_phase_plan": [
                {"phase": "fanout", "weight": 3, "family": "codex"},
                {"phase": "xfamily", "weight": 1, "family": "claude"},
            ],
            "authority_continuation_kind": "ONE_EXACT_REVISION_COMPLETE_CYCLE",
            "new_ceiling": 40,
        },
        {
            "authority_id": "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-REV11-40-44",
            "doc_id": "a4cb34578437a22f",
            "scope": (
                "S08A Claude-authored revision 11 exact design review; "
                "one Codex fanout then one Claude xfamily"
            ),
            "authority_reference": (
                "user defined the global fuse as an orchestrator drift-audit "
                "checkpoint and delegated bounded continuation on 2026-08-23"
            ),
            "drift_audit": {
                "result": "PASS_BOUNDED_REMEDIATION_NOT_SCOPE_DRIFT",
                "revision_10_artifact_sha256": "abdd11dca80eab7fc6be24134cee03781a4de4e5a367f6dbc25ca16c2f56423e",
                "revision_11_artifact_sha256": "89364f8b09da4b36f309ea973599305cec6ed30dc6ab6d779626b1ebc5bf2227",
                "revision_10_lines": 4199,
                "revision_11_lines": 4339,
                "same_family_finding_count": 8,
                "cross_family_finding_count": 11,
                "blocking_root_count": 4,
                "authoring_receipt_sha256": "d3663826b70bf64459dfb85d93b986468e4df3960e5d8fec4df8b4c014d71ce8",
                "lifecycle_status_sha256": "965d98a384c5d47f7cbff34585d3639acfd3b0c8f2e0fe8923f898a45ffe7538",
                "epic_sha256": "61303866f9f0a7e7ade6d59acd881f2f98e7017fe4a53072f4a004a9c60e390e",
                "accepted_remediation_root_count": 8,
                "parent_audit_same_root_count": 0,
                "parent_audit_fix_passes": 0,
                "bounded_calibration_authorization_added": False,
                "rejected_scope_expansions": [],
                "outcome_changed": False,
                "component_inventory_changed": False,
                "implementation_authority_changed": False,
                "rom_authority_changed": False,
                "classification": "BOUNDED_FINDING_REMEDIATION",
            },
            "authorized_artifact_sha256": "89364f8b09da4b36f309ea973599305cec6ed30dc6ab6d779626b1ebc5bf2227",
            "default_ceiling": 16,
            "previous_scoped_ceiling": 40,
            "authorized_max_ceiling": 44,
            "checkpoint_interval": 4,
            "prior_usage": 40,
            "additional_slots": 4,
            "authorized_cycle_weight": 4,
            "authorized_phase_plan": [
                {"phase": "fanout", "weight": 3, "family": "codex"},
                {"phase": "xfamily", "weight": 1, "family": "claude"},
            ],
            "authority_continuation_kind": "ONE_EXACT_REVISION_COMPLETE_CYCLE",
            "new_ceiling": 44,
        },
        {
            "authority_id": "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-REV12-44-48",
            "doc_id": "a4cb34578437a22f",
            "scope": (
                "S08A Claude-authored revision 12 exact design review; "
                "one Codex fanout then one Claude xfamily"
            ),
            "authority_reference": (
                "user defined the global fuse as an orchestrator drift-audit "
                "checkpoint and delegated bounded continuation on 2026-08-23"
            ),
            "drift_audit": {
                "result": "PASS_BOUNDED_REMEDIATION_NOT_SCOPE_DRIFT",
                "revision_11_artifact_sha256": (
                    "89364f8b09da4b36f309ea973599305cec6ed30dc6ab6d779626b1ebc5bf2227"
                ),
                "revision_12_artifact_sha256": (
                    "5f6efc22ed994c10954892f11c2c01da11cddb37adf06f215862bc7f0603ac3f"
                ),
                "revision_11_lines": 4339,
                "revision_12_lines": 4546,
                "same_family_finding_count": 9,
                "cross_family_finding_count": 9,
                "blocking_root_count": 5,
                "authoring_receipt_sha256": (
                    "252ba0993540f20e86f01288f7024efb7347eecac886a7c596274281137323ca"
                ),
                "lifecycle_status_sha256": (
                    "bc5f99c68b9d4e438d14b3795cf49b432a2b92ed1e6f388cb3ad4ceadff3bcf5"
                ),
                "epic_sha256": (
                    "379a8afdcc00ff2622f20b67cdf7b2a30c34c9c51a0625b96d3bcd43bb908a8a"
                ),
                "accepted_remediation_root_count": 6,
                "parent_audit_same_root_count": 2,
                "parent_audit_fix_passes": 3,
                "bounded_calibration_authorization_added": False,
                "rejected_scope_expansions": [],
                "outcome_changed": False,
                "component_inventory_changed": False,
                "implementation_authority_changed": False,
                "rom_authority_changed": False,
                "classification": "BOUNDED_FINDING_REMEDIATION",
            },
            "authorized_artifact_sha256": (
                "5f6efc22ed994c10954892f11c2c01da11cddb37adf06f215862bc7f0603ac3f"
            ),
            "default_ceiling": 16,
            "previous_scoped_ceiling": 44,
            "authorized_max_ceiling": 48,
            "checkpoint_interval": 4,
            "prior_usage": 44,
            "additional_slots": 4,
            "authorized_cycle_weight": 4,
            "authorized_phase_plan": [
                {"phase": "fanout", "weight": 3, "family": "codex"},
                {"phase": "xfamily", "weight": 1, "family": "claude"},
            ],
            "authority_continuation_kind": "ONE_EXACT_REVISION_COMPLETE_CYCLE",
            "new_ceiling": 48,
        },
        {
            "authority_id": "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-REV13-48-52",
            "doc_id": "a4cb34578437a22f",
            "scope": (
                "S08A Claude-authored revision 13 exact design review; "
                "one Codex fanout then one Claude xfamily"
            ),
            "authority_reference": (
                "user defined the global fuse as an orchestrator drift-audit "
                "checkpoint and delegated bounded continuation on 2026-08-23"
            ),
            "drift_audit": {
                "result": "PASS_BOUNDED_REMEDIATION_NOT_SCOPE_DRIFT",
                "revision_12_artifact_sha256": (
                    "5f6efc22ed994c10954892f11c2c01da11cddb37adf06f215862bc7f0603ac3f"
                ),
                "revision_13_artifact_sha256": (
                    "fbf8edcaf1cb90e40fc0646a77adc6b5da817031a695906df85795c09a95c80b"
                ),
                "revision_12_lines": 4546,
                "revision_13_lines": 4706,
                "same_family_finding_count": 8,
                "cross_family_finding_count": 11,
                "blocking_root_count": 6,
                "authoring_receipt_sha256": (
                    "0cc83de38bec1b5d72ed368367574ffb7946d8aa2baa485721dc9a061eed2f91"
                ),
                "lifecycle_status_sha256": (
                    "7ff4de7cacacd1d8d7f4e143ceb345e83cd577fa6adcde0e030c5300baee7e00"
                ),
                "epic_sha256": (
                    "be3247cecda90587e2a99bb53e1b4a0d802eb5c96aed0cd06968098163382403"
                ),
                "accepted_remediation_root_count": 6,
                "parent_audit_same_root_count": 1,
                "parent_audit_fix_passes": 1,
                "bounded_calibration_authorization_added": False,
                "rejected_scope_expansions": ["off-host checkpoint replication"],
                "outcome_changed": False,
                "component_inventory_changed": False,
                "implementation_authority_changed": False,
                "rom_authority_changed": False,
                "classification": "BOUNDED_FINDING_REMEDIATION",
            },
            "authorized_artifact_sha256": (
                "fbf8edcaf1cb90e40fc0646a77adc6b5da817031a695906df85795c09a95c80b"
            ),
            "default_ceiling": 16,
            "previous_scoped_ceiling": 48,
            "authorized_max_ceiling": 52,
            "checkpoint_interval": 4,
            "prior_usage": 48,
            "additional_slots": 4,
            "authorized_cycle_weight": 4,
            "authorized_phase_plan": [
                {"phase": "fanout", "weight": 3, "family": "codex"},
                {"phase": "xfamily", "weight": 1, "family": "claude"},
            ],
            "authority_continuation_kind": "ONE_EXACT_REVISION_COMPLETE_CYCLE",
            "new_ceiling": 52,
        },
        {
            "authority_id": "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-REV14-52-56",
            "doc_id": "a4cb34578437a22f",
            "scope": (
                "S08A Claude-authored revision 14 exact design review; "
                "one Codex fanout then one Claude xfamily"
            ),
            "authority_reference": (
                "user defined the global fuse as an orchestrator drift-audit "
                "checkpoint and delegated bounded continuation on 2026-08-23"
            ),
            "drift_audit": {
                "result": "PASS_BOUNDED_REMEDIATION_NOT_SCOPE_DRIFT",
                "revision_13_artifact_sha256": (
                    "fbf8edcaf1cb90e40fc0646a77adc6b5da817031a695906df85795c09a95c80b"
                ),
                "revision_14_artifact_sha256": (
                    "f3b17f2e7387ab5f239de3eca23e206ce291c3aab27448cb0e1931818be9bf80"
                ),
                "revision_13_lines": 4706,
                "revision_14_lines": 4896,
                "same_family_finding_count": 7,
                "cross_family_finding_count": 8,
                "blocking_root_count": 4,
                "authoring_receipt_sha256": (
                    "139fb3c667ad46f25bcc041beebc8909a5a8fdff70e69101d54178b704f227d3"
                ),
                "lifecycle_status_sha256": (
                    "61a10e6c136ec4b88315f78b84ffae3a6fd3cea4c3d4a85546b49e64b94bbbc5"
                ),
                "epic_sha256": (
                    "d88ed43cd153f916381b16037d745fef8ffc6007143296d152709bee126c8bbe"
                ),
                "accepted_remediation_root_count": 4,
                "parent_audit_same_root_count": 1,
                "parent_audit_fix_passes": 1,
                "bounded_calibration_authorization_added": False,
                "rejected_scope_expansions": ["design-layer review-spend budgeting"],
                "outcome_changed": False,
                "component_inventory_changed": False,
                "implementation_authority_changed": False,
                "rom_authority_changed": False,
                "classification": "BOUNDED_FINDING_REMEDIATION",
            },
            "authorized_artifact_sha256": (
                "f3b17f2e7387ab5f239de3eca23e206ce291c3aab27448cb0e1931818be9bf80"
            ),
            "default_ceiling": 16,
            "previous_scoped_ceiling": 52,
            "authorized_max_ceiling": 56,
            "checkpoint_interval": 4,
            "prior_usage": 52,
            "additional_slots": 4,
            "authorized_cycle_weight": 4,
            "authorized_phase_plan": [
                {"phase": "fanout", "weight": 3, "family": "codex"},
                {"phase": "xfamily", "weight": 1, "family": "claude"},
            ],
            "authority_continuation_kind": "ONE_EXACT_REVISION_COMPLETE_CYCLE",
            "new_ceiling": 56,
        },
        {
            "authority_id": "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-REV15-56-60",
            "doc_id": "a4cb34578437a22f",
            "scope": (
                "S08A Claude-authored revision 15 exact design review; "
                "one Codex fanout then one Claude xfamily"
            ),
            "authority_reference": (
                "user defined the global fuse as an orchestrator drift-audit "
                "checkpoint and delegated bounded continuation on 2026-08-23"
            ),
            "drift_audit": {
                "result": "PASS_BOUNDED_REMEDIATION_NOT_SCOPE_DRIFT",
                "revision_14_artifact_sha256": (
                    "f3b17f2e7387ab5f239de3eca23e206ce291c3aab27448cb0e1931818be9bf80"
                ),
                "revision_15_artifact_sha256": (
                    "c0ac043ef86e4319c23409a601afc72d25c87ff4bb28bc157ba3a34dd2af4533"
                ),
                "revision_14_lines": 4896,
                "revision_15_lines": 5156,
                "same_family_finding_count": 8,
                "cross_family_finding_count": 9,
                "blocking_root_count": 4,
                "authoring_receipt_sha256": (
                    "8664ecc7b5eda41a8480debe6296d48b7617ef576389bb15d1e28070c62ae37c"
                ),
                "lifecycle_status_sha256": (
                    "cc54726f4b988b8698a3085dcb2bdf10c491be5e603c4067564892a6cf73fdf2"
                ),
                "epic_sha256": (
                    "2d87b5be495d6a5711166162202dc8aeb00ebd20b7bcdad7915089824918e615"
                ),
                "accepted_remediation_root_count": 4,
                "parent_audit_same_root_count": 3,
                "parent_audit_fix_passes": 3,
                "bounded_calibration_authorization_added": False,
                "rejected_scope_expansions": [
                    "process monitor",
                    "second lock",
                    "supervisor IPC",
                ],
                "outcome_changed": False,
                "component_inventory_changed": False,
                "implementation_authority_changed": False,
                "rom_authority_changed": False,
                "classification": "BOUNDED_FINDING_REMEDIATION",
            },
            "authorized_artifact_sha256": (
                "c0ac043ef86e4319c23409a601afc72d25c87ff4bb28bc157ba3a34dd2af4533"
            ),
            "default_ceiling": 16,
            "previous_scoped_ceiling": 56,
            "authorized_max_ceiling": 60,
            "checkpoint_interval": 4,
            "prior_usage": 56,
            "additional_slots": 4,
            "authorized_cycle_weight": 4,
            "authorized_phase_plan": [
                {"phase": "fanout", "weight": 3, "family": "codex"},
                {"phase": "xfamily", "weight": 1, "family": "claude"},
            ],
            "authority_continuation_kind": "ONE_EXACT_REVISION_COMPLETE_CYCLE",
            "new_ceiling": 60,
        },
        {
            "authority_id": "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-REV16-60-64",
            "doc_id": "a4cb34578437a22f",
            "scope": (
                "S08A Claude-authored revision 16 exact design review; "
                "one Codex fanout then one Claude xfamily"
            ),
            "authority_reference": (
                "user defined the global fuse as an orchestrator drift-audit "
                "checkpoint and delegated bounded continuation on 2026-08-23"
            ),
            "drift_audit": {
                "result": "PASS_BOUNDED_REMEDIATION_NOT_SCOPE_DRIFT",
                "revision_15_artifact_sha256": (
                    "c0ac043ef86e4319c23409a601afc72d25c87ff4bb28bc157ba3a34dd2af4533"
                ),
                "revision_16_artifact_sha256": (
                    "d322fccdf093322cab1d62b37d19c4b31dfd6ceb0d444ddc9ff8e1688f9dd0d6"
                ),
                "revision_15_lines": 5156,
                "revision_16_lines": 5266,
                "same_family_finding_count": 8,
                "cross_family_finding_count": 9,
                "blocking_root_count": 2,
                "authoring_receipt_sha256": (
                    "baa9406dc4df78c2a2a7389cca2d88833dae7b22dc54cfed39cf13297f69cae0"
                ),
                "lifecycle_status_sha256": (
                    "d9cdd52ed03f69e7f9910848ef26fbf7c7868a3f5cd3e2a048e5700a05db57a8"
                ),
                "epic_sha256": (
                    "c33b7ed677b846120407fd5c10aae16e10d7fc334852367de575d8aa25de6f1e"
                ),
                "accepted_remediation_root_count": 3,
                "parent_audit_same_root_count": 3,
                "parent_audit_fix_passes": 4,
                "bounded_calibration_authorization_added": False,
                "rejected_scope_expansions": [
                    "process monitor",
                    "second lock",
                    "fencing token",
                    "coordination daemon",
                ],
                "outcome_changed": False,
                "component_inventory_changed": False,
                "implementation_authority_changed": False,
                "rom_authority_changed": False,
                "classification": "BOUNDED_FINDING_REMEDIATION",
            },
            "authorized_artifact_sha256": (
                "d322fccdf093322cab1d62b37d19c4b31dfd6ceb0d444ddc9ff8e1688f9dd0d6"
            ),
            "default_ceiling": 16,
            "previous_scoped_ceiling": 60,
            "authorized_max_ceiling": 64,
            "checkpoint_interval": 4,
            "prior_usage": 60,
            "additional_slots": 4,
            "authorized_cycle_weight": 4,
            "authorized_phase_plan": [
                {"phase": "fanout", "weight": 3, "family": "codex"},
                {"phase": "xfamily", "weight": 1, "family": "claude"},
            ],
            "authority_continuation_kind": "ONE_EXACT_REVISION_COMPLETE_CYCLE",
            "new_ceiling": 64,
        },
        {
            "authority_id": "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-23-REV17-64-68",
            "doc_id": "a4cb34578437a22f",
            "scope": (
                "S08A Claude-authored revision 17 exact design review; "
                "one Codex fanout then one Claude xfamily"
            ),
            "authority_reference": (
                "user defined the global fuse as an orchestrator drift-audit "
                "checkpoint and delegated bounded continuation on 2026-08-23"
            ),
            "drift_audit": {
                "result": "PASS_BOUNDED_REMEDIATION_NOT_SCOPE_DRIFT",
                "revision_16_artifact_sha256": (
                    "d322fccdf093322cab1d62b37d19c4b31dfd6ceb0d444ddc9ff8e1688f9dd0d6"
                ),
                "revision_17_artifact_sha256": (
                    "a6d56327a05b465a0a29e649e3e9ff1715540c28b9855d7c5086e3d2219ce499"
                ),
                "revision_16_lines": 5266,
                "revision_17_lines": 5425,
                "same_family_finding_count": 7,
                "cross_family_finding_count": 5,
                "blocking_root_count": 1,
                "authoring_receipt_sha256": (
                    "4a4457e3ff6519a1c363e4f1df7b9d2d339741e5269824cb12e0fd3fb5b1c2ce"
                ),
                "lifecycle_status_sha256": (
                    "bb8eb906c4d1d292f048f1cc0ec7cda6cc392948247ac3bbe6836dc50d5b678b"
                ),
                "epic_sha256": (
                    "1586602f0659289362e11d67b44ff204ce3c3f321739b9d15d6270960cbcba43"
                ),
                "accepted_remediation_root_count": 2,
                "parent_audit_same_root_count": 2,
                "parent_audit_fix_passes": 2,
                "bounded_calibration_authorization_added": False,
                "rejected_scope_expansions": [
                    "global mutable catalog",
                    "second lock",
                    "fencing token",
                    "service or daemon",
                ],
                "outcome_changed": False,
                "component_inventory_changed": False,
                "implementation_authority_changed": False,
                "rom_authority_changed": False,
                "classification": "BOUNDED_FINDING_REMEDIATION",
            },
            "authorized_artifact_sha256": (
                "a6d56327a05b465a0a29e649e3e9ff1715540c28b9855d7c5086e3d2219ce499"
            ),
            "default_ceiling": 16,
            "previous_scoped_ceiling": 64,
            "authorized_max_ceiling": 68,
            "checkpoint_interval": 4,
            "prior_usage": 64,
            "additional_slots": 4,
            "authorized_cycle_weight": 4,
            "authorized_phase_plan": [
                {"phase": "fanout", "weight": 3, "family": "codex"},
                {"phase": "xfamily", "weight": 1, "family": "claude"},
            ],
            "authority_continuation_kind": "ONE_EXACT_REVISION_COMPLETE_CYCLE",
            "new_ceiling": 68,
        },
        {
            "authority_id": "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-24-REV18-68-72",
            "doc_id": "a4cb34578437a22f",
            "scope": (
                "S08A Claude-authored revision 18 exact design review; "
                "one Codex fanout then one Claude xfamily"
            ),
            "authority_reference": (
                "user defined the global fuse as an orchestrator drift-audit "
                "checkpoint and delegated bounded continuation on 2026-08-23"
            ),
            "drift_audit": {
                "result": "PASS_BOUNDED_REMEDIATION_NOT_SCOPE_DRIFT",
                "revision_17_artifact_sha256": (
                    "a6d56327a05b465a0a29e649e3e9ff1715540c28b9855d7c5086e3d2219ce499"
                ),
                "revision_18_artifact_sha256": (
                    "e3f3f2290025fce79bd2d1b2d2fca7096370f6670744241416de559545c643a2"
                ),
                "revision_17_lines": 5425,
                "revision_18_lines": 5566,
                "same_family_finding_count": 9,
                "cross_family_finding_count": 7,
                "blocking_root_count": 3,
                "authoring_receipt_sha256": (
                    "fc19120770f2f5bbf145c995e014acd7ee45b423a4ff78eecb8c6166e81e5619"
                ),
                "lifecycle_status_sha256": (
                    "2eb2fd316f81a5b0ddbd4687c9fa6dbddcd1520042b537bcb5a6dbb4326935da"
                ),
                "epic_sha256": (
                    "a8e6be91621c4a7516a31814297b44a03d970a79a8370d03cfaefb4f3d4952a2"
                ),
                "accepted_remediation_root_count": 5,
                "parent_audit_same_root_count": 1,
                "parent_audit_fix_passes": 1,
                "bounded_calibration_authorization_added": False,
                "rejected_scope_expansions": [
                    "pointer-removal transaction",
                    "global all-shard readability requirement",
                    "checkpointed incremental GC or persistent catalog",
                    "daemon or second coordinator",
                ],
                "outcome_changed": False,
                "component_inventory_changed": False,
                "implementation_authority_changed": False,
                "rom_authority_changed": False,
                "classification": "BOUNDED_FINDING_REMEDIATION",
            },
            "authorized_artifact_sha256": (
                "e3f3f2290025fce79bd2d1b2d2fca7096370f6670744241416de559545c643a2"
            ),
            "default_ceiling": 16,
            "previous_scoped_ceiling": 68,
            "authorized_max_ceiling": 72,
            "checkpoint_interval": 4,
            "prior_usage": 68,
            "additional_slots": 4,
            "authorized_cycle_weight": 4,
            "authorized_phase_plan": [
                {"phase": "fanout", "weight": 3, "family": "codex"},
                {"phase": "xfamily", "weight": 1, "family": "claude"},
            ],
            "authority_continuation_kind": "ONE_EXACT_REVISION_COMPLETE_CYCLE",
            "new_ceiling": 72,
        },
        {
            "authority_id": "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-24-REV19-72-76",
            "doc_id": "a4cb34578437a22f",
            "scope": (
                "S08A Claude-authored revision 19 exact design review; "
                "one Codex fanout then one Claude xfamily"
            ),
            "authority_reference": (
                "user defined the global fuse as an orchestrator drift-audit "
                "checkpoint and delegated bounded continuation on 2026-08-23"
            ),
            "drift_audit": {
                "result": "PASS_BOUNDED_REMEDIATION_NOT_SCOPE_DRIFT",
                "revision_18_artifact_sha256": (
                    "e3f3f2290025fce79bd2d1b2d2fca7096370f6670744241416de559545c643a2"
                ),
                "revision_19_artifact_sha256": (
                    "792d9e230ceda396c33c03d4081b27943a6b5003accb1c811c3f0c6998aec11e"
                ),
                "revision_18_lines": 5566,
                "revision_19_lines": 5694,
                "same_family_finding_count": 7,
                "cross_family_finding_count": 7,
                "blocking_root_count": 2,
                "authoring_receipt_sha256": (
                    "d3170a544a26abeaacd83978415664ee5763ced1262fa8735f0c82cb44995d7d"
                ),
                "lifecycle_status_sha256": (
                    "f693e9a712ec75fda6c942a0c8921a1e40e8718814982b010363096fd7d69ce3"
                ),
                "epic_sha256": (
                    "df7231534fc4987e43d171f1c6d087f4ebd61947fb8a818edf75a131b17cce45"
                ),
                "accepted_remediation_root_count": 2,
                "parent_audit_same_root_count": 0,
                "parent_audit_fix_passes": 2,
                "bounded_calibration_authorization_added": False,
                "rejected_scope_expansions": [
                    "owner acknowledgement release gate",
                    "global all-shard genesis admission scan",
                    "persistent catalog or second lock",
                    "daemon or new service",
                ],
                "outcome_changed": False,
                "component_inventory_changed": False,
                "implementation_authority_changed": False,
                "rom_authority_changed": False,
                "classification": "BOUNDED_FINDING_REMEDIATION",
            },
            "authorized_artifact_sha256": (
                "792d9e230ceda396c33c03d4081b27943a6b5003accb1c811c3f0c6998aec11e"
            ),
            "default_ceiling": 16,
            "previous_scoped_ceiling": 72,
            "authorized_max_ceiling": 76,
            "checkpoint_interval": 4,
            "prior_usage": 72,
            "additional_slots": 4,
            "authorized_cycle_weight": 4,
            "authorized_phase_plan": [
                {"phase": "fanout", "weight": 3, "family": "codex"},
                {"phase": "xfamily", "weight": 1, "family": "claude"},
            ],
            "authority_continuation_kind": "ONE_EXACT_REVISION_COMPLETE_CYCLE",
            "new_ceiling": 76,
        },
    ),
    "/home/hrmtz/projects/ZN6/ecu-re-knock-telemetry-f0-overlay/docs/re/"
    "KNOCK_TELEMETRY_F0_OVERLAY_WIRE.md": (
        {
            "authority_id": "ZN6-KNOCK-F0-USER-AUTH-2026-08-23-SEQ4668-15-19",
            "doc_id": "c2c6bef4d687838c",
            "scope": "F0 overlay exact WIRE revision; one Codex fanout then one Claude xfamily",
            "authority_reference_mailbox_seq": 4668,
            "authority_guard_correction_mailbox_seq": 4669,
            "authorized_artifact_sha256": (
                "8620b3e52f64bdd9a7b7719bf335f5c0298e0349948a39ca0991a48c30df0d6e"
            ),
            "default_ceiling": 16,
            "previous_scoped_ceiling": 16,
            "authorized_max_ceiling": 19,
            "checkpoint_interval": 4,
            "prior_usage": 15,
            "additional_slots": 4,
            "authorized_cycle_weight": 4,
            "authorized_phase_plan": [
                {"phase": "fanout", "weight": 3, "family": "codex"},
                {"phase": "xfamily", "weight": 1, "family": "claude"},
            ],
            "new_ceiling": 19,
        },
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
    ),
}
FINAL_CYCLE_CLOSED_DOC = (
    "/home/hrmtz/projects/ZN6/ecu-tuning/docs/designs/"
    "TELEMETRY-FI-CALIBRATION-HARDENING/01a-canonical-inventory-publication.md"
)
FINAL_CYCLE_CLOSED_RECEIPT = Path(
    "/home/hrmtz/projects/ZN6/ecu-tuning/docs/designs/"
    "TELEMETRY-FI-CALIBRATION-HARDENING/.dual-magi-01a/"
    "FINAL-CYCLE-CLOSED.ZN6-01A-2026-08-22.json"
)
FINAL_CYCLE_CLOSED_RECEIPT_SHA256 = (
    "cc1d5503c427e83edbd5792cd7798b4d7df6b977511c5a386fa893e3d1dcf857"
)
FINAL_CYCLE_CLOSED_USAGE = 32
TERMINAL_STATUSES = {
    "success",
    "failed",
    "abandoned",
    "startup-failed-recoverable",
    "superseded-by-requirement-revision",
}
NONTERMINAL_STATUSES = {"running", "cancellation_in_progress"}
VALID_STATUSES = TERMINAL_STATUSES | NONTERMINAL_STATUSES
PROTOCOL_FILES = (
    "schemas/finding.schema.json",
    "schemas/finding.codex.schema.json",
    "schemas/implementation-convergence.schema.json",
    "scripts/magi_campaign_guard.py",
    "scripts/magi_classify_failure.py",
    "scripts/magi_codex_schema_preflight.py",
    "scripts/magi_convergence_gate.py",
    "scripts/magi_convergence_kernel.py",
    "scripts/magi_design_convergence_gate.py",
    "scripts/magi_fanout_codex.sh",
    "scripts/magi_git.py",
    "scripts/magi_lock.sh",
    "scripts/magi_plateau_gate.sh",
    "scripts/magi_review_packet.py",
    "scripts/magi_scrub.py",
    "scripts/magi_target_root.sh",
    "scripts/magi_validate_findings.py",
    "scripts/magi_verify_round.py",
    "scripts/magi_xfamily.sh",
    "scripts/magi_xfamily_claude.sh",
)
# Closed one-time compatibility attestations for incidents that predate the
# claim-scoped recovery state.  Runtime/operator-authored evidence is
# deliberately unsupported: adding an incident requires a reviewed protocol
# change, so an arbitrary failed ledger cannot mint credit.  The selected
# attestation is copied into the durable repair transition: later cleanup of
# this allowlist must not make an already-repaired ledger unreadable.
HISTORICAL_STARTUP_INCIDENTS: tuple[dict[str, object], ...] = (
    {
        "incident_id": "claude-harness-271-hippocampus-262-schema-startup",
        "issue": "hrmtz/claude-harness#271",
        "doc_id": "8fe2b3353e5e4a5b",
        "source_claim_id": "b1dd62bd-1f56-4fbc-a3cc-d01bdcd2e845",
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
    },
)


class UsageError(ValueError):
    """Invalid operator input (exit 64)."""


class BudgetDenied(RuntimeError):
    """Campaign may not launch another reviewer (exit 4)."""


class StateError(RuntimeError):
    """Canonical accounting state is unreadable or internally inconsistent (exit 2)."""


class TransitionError(ValueError):
    """The caller requested an illegal phase transition (exit 64)."""


class CancellationBlocked(RuntimeError):
    """Requirement-revision cleanup could not prove every owner exited (exit 2)."""


def positive_int(raw: str, label: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise UsageError(f"{label} must be an integer: {raw!r}") from exc
    if value < 1:
        raise UsageError(f"{label} must be at least 1: {value}")
    return value


def canonical_doc(raw: str) -> Path:
    doc = Path(raw).expanduser().resolve()
    if not doc.is_file():
        raise UsageError(f"document not found: {doc}")
    return doc


def doc_id(doc: Path) -> str:
    return hashlib.sha256(os.fsencode(doc)).hexdigest()[:16]


def global_ceiling_policy(doc: Path) -> tuple[int, dict[str, object] | None]:
    authority = SCOPED_GLOBAL_CEILING_OVERRIDES.get(str(doc))
    if authority is None:
        return GLOBAL_MAX_MODEL_LAUNCHES, None
    cycle_weight = authority.get("authorized_cycle_weight")
    if cycle_weight is None:
        cycle_arithmetic_valid = True
    else:
        prior_usage = authority.get("prior_usage")
        additional_slots = authority.get("additional_slots")
        new_ceiling = authority.get("new_ceiling")
        phase_plan = authority.get("authorized_phase_plan")
        phase_plan_valid = (
            isinstance(phase_plan, list)
            and bool(phase_plan)
            and all(
                isinstance(step, dict)
                and set(step) == {"phase", "weight", "family"}
                and step.get("phase") in PHASE_WEIGHT
                and type(step.get("weight")) is int
                and step.get("weight") == PHASE_WEIGHT[step["phase"]]
                and step.get("family") in {"codex", "claude", "grok"}
                for step in phase_plan
            )
        )
        cycle_arithmetic_valid = (
            type(cycle_weight) is int
            and type(prior_usage) is int
            and type(additional_slots) is int
            and type(new_ceiling) is int
            and phase_plan_valid
            and cycle_weight > 0
            and additional_slots > 0
            and new_ceiling == prior_usage + additional_slots
            and cycle_weight == additional_slots
            and cycle_weight == sum(step["weight"] for step in phase_plan)
        )
    authorized_artifact_sha = authority.get("authorized_artifact_sha256")
    artifact_scope_valid = (
        authorized_artifact_sha is None
        or is_sha256(authorized_artifact_sha)
    )
    common_valid = (
        authority.get("doc_id") == doc_id(doc)
        and authority.get("default_ceiling") == GLOBAL_MAX_MODEL_LAUNCHES
        and cycle_arithmetic_valid
        and artifact_scope_valid
    )
    if authority.get("authority_id") == "ZN6-01A-USER-ACK-2026-08-22-TO-36":
        specific_valid = (
            authority.get("previous_scoped_ceiling") == 20
            and authority.get("intermediate_authorized_ceiling") == 24
            and authority.get("additional_slots") == 12
            and authority.get("new_ceiling") == 36
        )
    elif authority.get("authority_id") == "ZN6-E2A3A1-USER-PASS-2026-08-23-CHECKPOINT-30-34":
        specific_valid = (
            authority.get("authority_reference")
            == "user accepted zero-writer threat model and said pass on 2026-08-23"
            and authority.get("previous_scoped_ceiling") == 30
            and authority.get("authorized_max_ceiling") == 34
            and authority.get("checkpoint_interval") == 4
            and authority.get("prior_usage") == 30
            and authority.get("additional_slots") == 4
            and authority.get("new_ceiling") == 34
        )
    elif (
        authority.get("authority_id")
        == "ZN6-ABC-RUNTIME-I4-AUDIT-2026-08-25-27-31"
    ):
        exact_keys = {
            "authority_id",
            "doc_id",
            "scope",
            "authority_reference",
            "drift_audit",
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
        }
        exact_integer_fields = {
            "default_ceiling",
            "previous_scoped_ceiling",
            "authorized_max_ceiling",
            "checkpoint_interval",
            "prior_usage",
            "additional_slots",
            "authorized_cycle_weight",
            "new_ceiling",
        }
        specific_valid = (
            set(authority) == exact_keys
            and all(
                type(authority.get(field)) is int for field in exact_integer_fields
            )
            and authority.get("scope")
            == (
                "ABC runtime I4-pinned v17 exact revision after bounded status admission, "
                "RESYNC rearm, status egress, and feature-disable completion remediation; "
                "one Codex fanout then one Claude xfamily"
            )
            and authority.get("authority_reference")
            == (
                "parent scoped drift audit authorized one bounded exact-revision cycle "
                "from canonical ledger usage 27 on 2026-08-25"
            )
            and exact_json_equal(
                authority.get("drift_audit"),
                {
                    "result": "PASS_BOUNDED_REMEDIATION_NOT_SCOPE_DRIFT",
                    "prior_review_artifact_sha256": (
                        "6e116691a6b4a5be6ad5de308a06697a14f28943bf956aa5150b720259fe1b60"
                    ),
                    "authorized_artifact_sha256": (
                        "0ade043db0a7482d29762b0cbebb0d475d50e0c6103730a42a211e83eb14dcff"
                    ),
                    "prior_review_finding_count": 4,
                    "remediated_roots": [
                        "status_admission_projection",
                        "resync_corrupt_active_rearm",
                        "status_egress_private_buffer_generation_protocol",
                        "feature_disable_actual_a_vs_old_bc_completion_semantics",
                    ],
                    "frozen_implementation_commit": (
                        "847856de48f21ff6a5b299a57e4c563b8a8af375"
                    ),
                    "v17_contract_sha256": (
                        "5960f8b96a53e27f8c411a6df00721a6640febbe70b7fb5a731b6c52b38ef4e3"
                    ),
                    "v17_receipt_sha256": (
                        "fe8887becdcbae537347e63432d3389f34712bf786aab8fcf74a71178c998f3e"
                    ),
                    "v17_manifest_sha256": (
                        "bc56ab8a4b979407f9bd874a5eaaa1206bd90cd59d2f3c027f0bff3f6f0cb807"
                    ),
                    "outcome_changed": False,
                    "calibration_values_changed": False,
                    "implementation_authority_changed": False,
                    "consumer_authority_changed": False,
                    "rom_authority_changed": False,
                    "flash_authority_added": False,
                    "hardware_authority_added": False,
                    "classification": "BOUNDED_FINDING_REMEDIATION",
                },
            )
            and authority.get("authorized_artifact_sha256")
            == "0ade043db0a7482d29762b0cbebb0d475d50e0c6103730a42a211e83eb14dcff"
            and authority.get("previous_scoped_ceiling") == 27
            and authority.get("authorized_max_ceiling") == 31
            and authority.get("checkpoint_interval") == 4
            and authority.get("prior_usage") == 27
            and authority.get("additional_slots") == 4
            and authority.get("authorized_cycle_weight") == 4
            and exact_json_equal(
                authority.get("authorized_phase_plan"),
                [
                    {"phase": "fanout", "weight": 3, "family": "codex"},
                    {"phase": "xfamily", "weight": 1, "family": "claude"},
                ],
            )
            and authority.get("quota_conservation_constraints_removed") is True
            and authority.get("authority_continuation_kind")
            == "ONE_EXACT_REVISION_COMPLETE_CYCLE"
            and authority.get("new_ceiling") == 31
        )
    elif (
        authority.get("authority_id")
        == "ZN6-ABC-FP-ORCHESTRATOR-AUDIT-2026-08-24-14-18"
    ):
        exact_keys = {
            "authority_id", "doc_id", "scope", "authority_reference",
            "drift_audit", "authorized_artifact_sha256", "default_ceiling",
            "previous_scoped_ceiling", "authorized_max_ceiling",
            "checkpoint_interval", "prior_usage", "additional_slots",
            "authorized_cycle_weight", "authorized_phase_plan",
            "authority_continuation_kind", "new_ceiling",
        }
        exact_integer_fields = {
            "default_ceiling", "previous_scoped_ceiling", "authorized_max_ceiling",
            "checkpoint_interval", "prior_usage", "additional_slots",
            "authorized_cycle_weight", "new_ceiling",
        }
        specific_valid = (
            set(authority) == exact_keys
            and all(type(authority.get(field)) is int for field in exact_integer_fields)
            and authority.get("scope")
            == (
                "ABC FP calibration exact revision after replay shared-oracle remediation; "
                "one Codex fanout then one Claude xfamily"
            )
            and authority.get("authority_reference")
            == (
                "user explicitly reset review limits and delegated autonomous completion "
                "on 2026-08-24"
            )
            and exact_json_equal(
                authority.get("drift_audit"),
                {
                    "result": "PASS_BOUNDED_REMEDIATION_NOT_SCOPE_DRIFT",
                    "prior_artifact_sha256": (
                        "0ffafbfa8b07a9f21f0110aef921aa4609e39f9a472cc931d504c0b15cc10df2"
                    ),
                    "authorized_artifact_sha256": (
                        "e430616b0761f71a8a7c4a4c8775902d9b496895e08ef42c2d0b33861ec2e7cf"
                    ),
                    "prior_lines": 153,
                    "authorized_lines": 158,
                    "blocking_root_count": 1,
                    "blocking_root": "replay.shared_oracle",
                    "independent_oracle_sha256": (
                        "6807b6c414ce7873f943aa9ef51a63716d5bf95c4181119eec46782a49e809a7"
                    ),
                    "oracle_tests_sha256": (
                        "6ea6791da0e45d142a70d78cc642bb03874224d3bd43289453c9e0929f45cbc2"
                    ),
                    "executable_evidence_sha256": (
                        "ef85505c0f0e899f1e939ef367f92986097cc3c655872ce3fb8f4b0f7c8ff8c0"
                    ),
                    "dedicated_tests": 37,
                    "full_tests": 399,
                    "outcome_changed": False,
                    "calibration_values_changed": False,
                    "implementation_authority_changed": False,
                    "rom_authority_changed": False,
                    "flash_authority_added": False,
                    "classification": "BOUNDED_FINDING_REMEDIATION",
                },
            )
            and authority.get("authorized_artifact_sha256")
            == "e430616b0761f71a8a7c4a4c8775902d9b496895e08ef42c2d0b33861ec2e7cf"
            and authority.get("previous_scoped_ceiling") == 16
            and authority.get("authorized_max_ceiling") == 18
            and authority.get("checkpoint_interval") == 4
            and authority.get("prior_usage") == 14
            and authority.get("additional_slots") == 4
            and authority.get("authorized_cycle_weight") == 4
            and exact_json_equal(
                authority.get("authorized_phase_plan"),
                [
                    {"phase": "fanout", "weight": 3, "family": "codex"},
                    {"phase": "xfamily", "weight": 1, "family": "claude"},
                ],
            )
            and authority.get("authority_continuation_kind")
            == "ONE_EXACT_REVISION_COMPLETE_CYCLE"
            and authority.get("new_ceiling") == 18
        )
    elif (
        authority.get("authority_id")
        == "ZN6-S08A-ORCHESTRATOR-AUDIT-2026-08-24-REV20-76-80"
    ):
        exact_keys = {
            "authority_id",
            "doc_id",
            "scope",
            "authority_reference",
            "drift_audit",
            "authorized_artifact_sha256",
            "default_ceiling",
            "previous_scoped_ceiling",
            "authorized_max_ceiling",
            "checkpoint_interval",
            "prior_usage",
            "additional_slots",
            "authorized_cycle_weight",
            "authorized_phase_plan",
            "authority_continuation_kind",
            "new_ceiling",
        }
        exact_integer_fields = {
            "default_ceiling",
            "previous_scoped_ceiling",
            "authorized_max_ceiling",
            "checkpoint_interval",
            "prior_usage",
            "additional_slots",
            "authorized_cycle_weight",
            "new_ceiling",
        }
        specific_valid = (
            set(authority) == exact_keys
            and all(
                type(authority.get(field)) is int for field in exact_integer_fields
            )
            and authority.get("scope")
            == (
                "S08A Claude-authored revision 20 exact design review; "
                "one Codex fanout then one Claude xfamily"
            )
            and authority.get("authority_reference")
            == (
                "user defined the global fuse as an orchestrator drift-audit "
                "checkpoint and delegated bounded continuation on 2026-08-23"
            )
            and exact_json_equal(
                authority.get("drift_audit"),
                {
                    "result": "PASS_BOUNDED_REMEDIATION_NOT_SCOPE_DRIFT",
                    "revision_19_artifact_sha256": (
                        "792d9e230ceda396c33c03d4081b27943a6b5003accb1c811c3f0c6998aec11e"
                    ),
                    "revision_20_artifact_sha256": (
                        "3239fd7debc143ec32de23c88a56bf6f73ca6b5ac81d6e1ceed4884c9d01b9c6"
                    ),
                    "revision_19_lines": 5694,
                    "revision_20_lines": 5793,
                    "same_family_finding_count": 9,
                    "cross_family_finding_count": 6,
                    "blocking_root_count": 2,
                    "authoring_receipt_sha256": (
                        "8b8f28e56b48f18e3374d72432a40aafdeb8c53fc8df4fb273a843fa89fc9b86"
                    ),
                    "lifecycle_status_sha256": (
                        "ef4fd5e5ceb3b639e658b20ea127f4a97be1d728de372f03d1975564bc284a9d"
                    ),
                    "epic_sha256": (
                        "93ae8e3cacf296beb7dbe331b17eee5abd94544078da5e3b983f0fb4d2b46725"
                    ),
                    "accepted_remediation_root_count": 2,
                    "parent_audit_same_root_count": 0,
                    "parent_audit_fix_passes": 2,
                    "bounded_calibration_authorization_added": False,
                    "rejected_scope_expansions": [
                        "bootstrap script implementation",
                        "new artifact or acknowledgement type",
                        "daemon, service, catalog, or lock",
                        "ROM or hardware authorization",
                    ],
                    "outcome_changed": False,
                    "component_inventory_changed": False,
                    "implementation_authority_changed": False,
                    "rom_authority_changed": False,
                    "classification": "BOUNDED_FINDING_REMEDIATION",
                },
            )
            and authority.get("authorized_artifact_sha256")
            == "3239fd7debc143ec32de23c88a56bf6f73ca6b5ac81d6e1ceed4884c9d01b9c6"
            and authority.get("previous_scoped_ceiling") == 76
            and authority.get("authorized_max_ceiling") == 80
            and authority.get("checkpoint_interval") == 4
            and authority.get("prior_usage") == 76
            and authority.get("additional_slots") == 4
            and authority.get("authorized_cycle_weight") == 4
            and exact_json_equal(
                authority.get("authorized_phase_plan"),
                [
                    {"phase": "fanout", "weight": 3, "family": "codex"},
                    {"phase": "xfamily", "weight": 1, "family": "claude"},
                ],
            )
            and authority.get("authority_continuation_kind")
            == "ONE_EXACT_REVISION_COMPLETE_CYCLE"
            and authority.get("new_ceiling") == 80
        )
    elif (
        authority.get("authority_id")
        == "ZN6-KNOCK-F0-USER-AUTH-2026-08-23-SEQ4689-SCOPE4708-4709-23-27"
    ):
        exact_keys = {
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
        }
        exact_integer_fields = {
            "authority_reference_mailbox_seq",
            "default_ceiling",
            "previous_scoped_ceiling",
            "authorized_max_ceiling",
            "checkpoint_interval",
            "prior_usage",
            "additional_slots",
            "authorized_cycle_weight",
            "new_ceiling",
        }
        specific_valid = (
            set(authority) == exact_keys
            and all(
                type(authority.get(field)) is int for field in exact_integer_fields
            )
            and authority.get("scope")
            == (
                "F0 overlay scope-corrected proportional operational-boundary "
                "exact WIRE review; one Codex fanout then one Claude xfamily"
            )
            and authority.get("authority_reference_mailbox_seq") == 4689
            and exact_json_equal(
                authority.get("scope_correction_mailbox_seqs"), [4708, 4709]
            )
            and authority.get("prior_authority_id")
            == "ZN6-KNOCK-F0-USER-AUTH-2026-08-23-SEQ4689-19-23"
            and authority.get("scope_basis_campaign_id")
            == "e6348644-1bea-45cf-92c1-0192cf06cb5d"
            and authority.get("scope_basis_claim_id")
            == "396758f8-fbe5-4301-8998-c81da0a96b63"
            and authority.get("scope_basis_review_output_sha256")
            == "6f9af979bc9fb0c18b565cd03c17da962ded039d8c85e8e26846bd609af50721"
            and authority.get("scope_basis_finding_id") == "XF-R2-001"
            and authority.get("scope_basis_root_cause_id")
            == "recorder.namespace_generation_unbound"
            and authority.get("scope_basis_reported_severity") == "HIGH"
            and authority.get("scope_corrected_classification")
            == "OPTIONAL_OPERATIONAL_HARDENING"
            and authority.get("scope_corrected_rom_admission_blocker") is False
            and authority.get("scope_correction_is_safety_remediation") is False
            and exact_json_equal(
                authority.get("removed_transactional_recorder_gates"),
                ["REC_PATH_DURABLE", "REC_PROFILE_DURABLE"],
            )
            and exact_json_equal(
                authority.get("retained_operational_requirements"),
                [
                    "RAWOUT_ONLY_F0_ANALYSIS",
                    "OUT_NON_ADMISSIBLE_UNDER_F0_SINGLE_PENDING_V1",
                    "ACTIVE_MODE_01_SEPARATE_CAN_AUTHORITY",
                ],
            )
            and authority.get("authorized_artifact_sha256")
            == "084a4c8dd7ea0fca25df7256039617b4defa337117a8366db1de073f45f16637"
            and authority.get("previous_scoped_ceiling") == 23
            and authority.get("authorized_max_ceiling") == 27
            and authority.get("checkpoint_interval") == 4
            and authority.get("prior_usage") == 23
            and authority.get("additional_slots") == 4
            and authority.get("authorized_cycle_weight") == 4
            and exact_json_equal(
                authority.get("authorized_phase_plan"),
                [
                    {"phase": "fanout", "weight": 3, "family": "codex"},
                    {"phase": "xfamily", "weight": 1, "family": "claude"},
                ],
            )
            and authority.get("quota_conservation_constraints_removed") is True
            and authority.get("authority_continuation_kind")
            == "STANDING_FURTHER_CYCLE_AUTHORITY"
            and authority.get("new_ceiling") == 27
        )
    else:
        specific_valid = False
    if not common_valid or not specific_valid:
        raise StateError("scoped global fuse authority is malformed")
    return int(authority["new_ceiling"]), dict(authority)


def enforce_scoped_artifact_sha(
    authority: dict[str, object] | None, current_artifact_sha: str
) -> None:
    """Bind a structurally valid scoped authority to live document bytes."""
    if authority is None:
        return
    expected = authority.get("authorized_artifact_sha256")
    if expected is not None and expected != current_artifact_sha:
        raise StateError("scoped fuse authority belongs to another exact artifact")


def exact_json_equal(left: object, right: object) -> bool:
    """Compare JSON-shaped authority values without bool/int coercion."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        assert isinstance(right, dict)
        return set(left) == set(right) and all(
            exact_json_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        assert isinstance(right, list)
        return len(left) == len(right) and all(
            exact_json_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return left == right


def scoped_reviewer_families(
    authority: dict[str, object], phase: str
) -> set[str]:
    phase_plan = authority.get("authorized_phase_plan")
    if phase_plan is None:
        return set()
    if not isinstance(phase_plan, list):
        raise StateError("scoped launch plan is malformed")
    return {
        str(step["family"])
        for step in phase_plan
        if isinstance(step, dict) and step.get("phase") == phase
    }


def scoped_launch_plan_step(
    authority: dict[str, object], total_used: int
) -> tuple[int, dict[str, object]] | None:
    """Resolve one exact authorized phase at a model-launch boundary."""
    phase_plan = authority.get("authorized_phase_plan")
    if phase_plan is None:
        return None
    if not isinstance(phase_plan, list):
        raise StateError("scoped launch plan is malformed")
    prior_usage = authority.get("prior_usage")
    if type(prior_usage) is not int or total_used < prior_usage:
        raise StateError("scoped launch usage predates its authority")
    offset = total_used - prior_usage
    consumed = 0
    for index, step in enumerate(phase_plan):
        if not isinstance(step, dict) or type(step.get("weight")) is not int:
            raise StateError("scoped launch plan step is malformed")
        if offset == consumed:
            return index, dict(step)
        consumed += int(step["weight"])
        if offset < consumed:
            raise StateError("scoped launch usage falls inside a phase weight")
    if offset == consumed:
        return None
    raise StateError("scoped launch usage exceeds its authorized phase plan")


def enforce_scoped_launch_plan(
    authority: dict[str, object],
    total_used: int,
    phase: str,
    launches: list[object],
    artifact_sha: str,
    review_protocol_sha: str,
    *,
    reviewer_family: str | None,
    require_reviewer_family: bool,
) -> str | None:
    """Fail closed unless the requested claim is the next exact scoped phase."""
    phase_plan = authority.get("authorized_phase_plan")
    if phase_plan is None:
        return None
    resolved = scoped_launch_plan_step(authority, total_used)
    if resolved is None:
        raise TransitionError("scoped launch plan is exhausted")
    index, expected = resolved
    expected_phase = expected.get("phase")
    expected_family = expected.get("family")
    if phase != expected_phase:
        raise TransitionError(
            f"scoped launch plan requires {expected_phase}, not {phase}"
        )
    actual_family = "codex" if phase in {"fanout", "targeted"} else reviewer_family
    if require_reviewer_family and actual_family != expected_family:
        raise TransitionError(
            f"scoped launch plan requires reviewer family {expected_family}"
        )
    if index:
        if not isinstance(phase_plan, list) or len(launches) < index:
            raise TransitionError("scoped launch plan lacks its preceding claims")
        prior_launches = launches[-index:]
        for expected_prior, prior_launch in zip(phase_plan[:index], prior_launches):
            if (
                not isinstance(expected_prior, dict)
                or not isinstance(prior_launch, dict)
                or prior_launch.get("phase") != expected_prior.get("phase")
                or prior_launch.get("model_launches") != expected_prior.get("weight")
                or prior_launch.get("status") != "success"
                or prior_launch.get("artifact_sha") != artifact_sha
                or prior_launch.get("protocol_sha") != review_protocol_sha
                or not exact_json_equal(
                    prior_launch.get("global_fuse_authority"), authority
                )
                or prior_launch.get("replacement_for") is not None
            ):
                raise TransitionError(
                    "scoped launch plan preceding claim is absent or mismatched"
                )
    return str(expected_family) if expected_family is not None else None


def scope_review_checkpoint_required(doc: Path, used: int) -> bool:
    authority = SCOPED_GLOBAL_CEILING_OVERRIDES.get(str(doc))
    return bool(
        authority
        and authority.get("checkpoint_interval") == 4
        and type(authority.get("new_ceiling")) is int
        and used >= int(authority["new_ceiling"])
    )


def final_cycle_is_closed(doc: Path, used: int) -> bool:
    """Hard-stop the exact user-scoped target after its one final review pair."""
    if str(doc) != FINAL_CYCLE_CLOSED_DOC or used < FINAL_CYCLE_CLOSED_USAGE:
        return False
    if (
        not FINAL_CYCLE_CLOSED_RECEIPT.is_file()
        or sha256_file(FINAL_CYCLE_CLOSED_RECEIPT)
        != FINAL_CYCLE_CLOSED_RECEIPT_SHA256
    ):
        raise StateError("final-cycle closure receipt is missing or changed")
    return True


def file_sha(doc: Path) -> str:
    return sha256_file(doc)


def protocol_sha() -> str:
    return closed_protocol_sha()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def proc_identity(pid: int) -> dict[str, object] | None:
    """Read one Linux process identity without trusting caller-supplied start metadata."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except (FileNotFoundError, ProcessLookupError):
        return None
    except OSError as exc:
        raise StateError(f"cannot inspect process {pid}: {exc}") from exc
    close = raw.rfind(")")
    if close < 0:
        raise StateError(f"malformed /proc identity for process {pid}")
    fields = raw[close + 2 :].split()
    if len(fields) < 20:
        raise StateError(f"incomplete /proc identity for process {pid}")
    try:
        return {
            "pid": pid,
            "state": fields[0],
            "ppid": int(fields[1]),
            "pgid": int(fields[2]),
            "start_ticks": int(fields[19]),
        }
    except ValueError as exc:
        raise StateError(f"invalid /proc identity for process {pid}") from exc


def matching_process(identity: dict[str, object]) -> bool:
    pid = identity.get("pid")
    start_ticks = identity.get("start_ticks")
    if type(pid) is not int or type(start_ticks) is not int:
        return False
    current = proc_identity(pid)
    return (
        current is not None
        and current["start_ticks"] == start_ticks
        and current["state"] != "Z"
    )


def process_cmdline(pid: int) -> list[str]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, ProcessLookupError):
        return []
    except OSError as exc:
        raise StateError(f"cannot inspect process {pid} command line: {exc}") from exc
    try:
        return [
            field.decode("utf-8")
            for field in raw.rstrip(b"\0").split(b"\0")
            if field
        ]
    except UnicodeDecodeError as exc:
        raise StateError(f"process {pid} command line is not UTF-8") from exc


def process_snapshot() -> dict[int, dict[str, object]]:
    snapshot: dict[int, dict[str, object]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        identity = proc_identity(int(entry.name))
        if identity is not None:
            snapshot[identity["pid"]] = identity
    return snapshot


def owned_processes(owner: dict[str, object]) -> list[dict[str, object]]:
    """Return the current owner tree deepest-first, with the owner last."""
    owner_pid = owner.get("pid")
    owner_start = owner.get("start_ticks")
    if type(owner_pid) is not int or type(owner_start) is not int:
        return []
    snapshot = process_snapshot()
    current = snapshot.get(owner_pid)
    if current is None or current["start_ticks"] != owner_start:
        return []
    depths = {owner_pid: 0}
    changed = True
    while changed:
        changed = False
        for pid, identity in snapshot.items():
            parent = int(identity["ppid"])
            if pid not in depths and parent in depths:
                depths[pid] = depths[parent] + 1
                changed = True
    return [
        snapshot[pid]
        for pid in sorted(depths, key=lambda item: (depths[item], item), reverse=True)
    ]


def signal_identity(identity: dict[str, object], sig: signal.Signals) -> str:
    """Signal only the process whose start identity still matches."""
    if not matching_process(identity):
        return "not-live"
    pid = int(identity["pid"])
    pidfd = None
    try:
        if hasattr(os, "pidfd_open"):
            pidfd = os.pidfd_open(pid)
            if not matching_process(identity):
                return "identity-changed"
            if hasattr(signal, "pidfd_send_signal"):
                signal.pidfd_send_signal(pidfd, sig)
            else:
                os.kill(pid, sig)
        else:
            if not matching_process(identity):
                return "identity-changed"
            os.kill(pid, sig)
    except ProcessLookupError:
        return "not-live"
    except PermissionError as exc:
        return f"permission-denied: {exc}"
    finally:
        if pidfd is not None:
            os.close(pidfd)
    return "signaled"


def wait_for_exit(
    identities: list[dict[str, object]], timeout_seconds: int
) -> list[dict[str, object]]:
    deadline = time.monotonic() + timeout_seconds
    survivors = [identity for identity in identities if matching_process(identity)]
    while survivors and time.monotonic() < deadline:
        time.sleep(0.02)
        survivors = [identity for identity in survivors if matching_process(identity)]
    return survivors


def control_dir(doc: Path) -> Path:
    path = doc.parent / ".dual-magi"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ledger_path(doc: Path) -> Path:
    return control_dir(doc) / f"CAMPAIGN.{doc_id(doc)}.json"


@contextmanager
def document_lock(doc: Path) -> Iterator[None]:
    lock_path = control_dir(doc) / f".campaign.{doc_id(doc)}.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def atomic_json(path: Path, payload: object) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def new_campaign(*, operator: str, reason: str) -> dict[str, object]:
    return {
        "campaign_id": str(uuid.uuid4()),
        "started_at": now(),
        "started_by": operator,
        "reason": reason,
        "launches": [],
    }


def is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def canonical_sha256(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def historical_incident(
    artifact_id: str,
    claim_id: str,
    incidents: tuple[dict[str, object], ...] | None = None,
) -> dict[str, object]:
    if incidents is None:
        incidents = HISTORICAL_STARTUP_INCIDENTS
    matches = [
        incident
        for incident in incidents
        if incident.get("doc_id") == artifact_id
        and incident.get("source_claim_id") == claim_id
    ]
    if len(matches) != 1:
        raise TransitionError(
            "no closed historical startup attestation matches this document and claim"
        )
    return matches[0]


def new_ledger(doc: Path) -> dict[str, object]:
    return {
        "schema_version": 1,
        "doc_id": doc_id(doc),
        "doc_path": str(doc),
        "campaigns": [
            new_campaign(operator="automatic-initial-campaign", reason="first guarded launch")
        ],
    }


def load_ledger(doc: Path, *, create: bool) -> dict[str, object]:
    path = ledger_path(doc)
    if not path.exists():
        if not create:
            raise UsageError(f"no campaign ledger exists for {doc}")
        return new_ledger(doc)
    try:
        payload = strict_json_loads(path.read_bytes())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise StateError(f"campaign ledger is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise StateError("campaign ledger must be a JSON object")
    expected = {"schema_version", "doc_id", "doc_path", "campaigns"}
    if set(payload) != expected or payload.get("schema_version") != 1:
        raise StateError("campaign ledger fields do not match schema version 1")
    if payload.get("doc_id") != doc_id(doc) or payload.get("doc_path") != str(doc):
        raise StateError("campaign ledger belongs to another document")
    campaigns = payload.get("campaigns")
    if not isinstance(campaigns, list) or not campaigns:
        raise StateError("campaign ledger has no active campaign")
    for campaign in campaigns:
        campaign_fields = {
            "campaign_id",
            "started_at",
            "started_by",
            "reason",
            "launches",
        }
        if (
            not isinstance(campaign, dict)
            or set(campaign) not in (campaign_fields, campaign_fields | {"repairs"})
            or not isinstance(campaign.get("launches"), list)
            or (
                "repairs" in campaign
                and not isinstance(campaign.get("repairs"), list)
            )
        ):
            raise StateError("campaign ledger contains a malformed campaign")
        for index, launch in enumerate(campaign["launches"], start=1):
            if not isinstance(launch, dict):
                raise StateError("campaign ledger contains a malformed launch")
            phase = launch.get("phase")
            round_no = launch.get("round")
            if phase not in PHASE_WEIGHT or not isinstance(round_no, int) or round_no < 1:
                raise StateError("legacy launch cannot be safely weighted")
            launch.setdefault("model_launches", PHASE_WEIGHT[phase])
            if (
                type(launch.get("model_launches")) is not int
                or launch.get("model_launches") != PHASE_WEIGHT[phase]
            ):
                raise StateError(
                    f"launch weight does not match phase {phase!r}: "
                    f"{launch.get('model_launches')!r}"
                )
            launch.setdefault(
                "claim_id",
                str(uuid.uuid5(uuid.NAMESPACE_URL, f"{campaign.get('campaign_id')}:{index}")),
            )
            launch.setdefault("protocol_sha", "legacy-unknown")
            if "status" not in launch:
                state = Path(str(launch.get("state_dir", "")))
                if phase == "fanout":
                    completed = any(
                        all(
                            (state / f"round_{round_no}_{persona}.json").is_file()
                            for persona in persona_set
                        )
                        for persona_set in (
                            ("melchior", "balthasar", "caspar"),
                            ("hornet", "gnat", "wasp"),
                        )
                    )
                else:
                    completed = (state / f"round_{round_no}_xfamily.json").is_file()
                launch["status"] = "success" if completed else "failed"
            if launch.get("status") not in VALID_STATUSES:
                raise StateError("campaign launch has an invalid status")
            fuse_authority = launch.get("global_fuse_authority")
            if fuse_authority is not None:
                _, expected_authority = global_ceiling_policy(doc)
                accepted_authorities = (
                    expected_authority,
                    *HISTORICAL_SCOPED_GLOBAL_CEILING_AUTHORITIES.get(str(doc), ()),
                )
                if not any(
                    exact_json_equal(fuse_authority, accepted)
                    for accepted in accepted_authorities
                ):
                    raise StateError("campaign launch has invalid global fuse authority")
            reviewer_family = launch.get("reviewer_family")
            if reviewer_family is not None and (
                phase != "xfamily" or reviewer_family not in {"claude", "grok"}
            ):
                raise StateError("campaign launch has an invalid reviewer family")
            scoped_plan = (
                fuse_authority is not None
                and fuse_authority.get("authorized_phase_plan") is not None
            )
            if scoped_plan and phase == "xfamily" and reviewer_family not in (
                scoped_reviewer_families(fuse_authority, "xfamily")
            ):
                raise StateError(
                    "scoped xfamily launch is missing its authorized reviewer family"
                )
            owner = launch.get("owner")
            if scoped_plan and owner is None:
                raise StateError("scoped launch is missing its registered adapter owner")
            if owner is not None:
                required_owner = {"pid", "start_ticks", "ppid", "pgid", "adapter_kind"}
                if (
                    not isinstance(owner, dict)
                    or set(owner) != required_owner
                    or any(type(owner.get(key)) is not int for key in required_owner - {"adapter_kind"})
                    or owner.get("adapter_kind") not in PHASE_WEIGHT
                    or owner.get("adapter_kind") != phase
                ):
                    raise StateError("campaign launch has an invalid owner identity")
            cancellation = launch.get("cancellation")
            if launch.get("status") in {
                "cancellation_in_progress",
                "superseded-by-requirement-revision",
            }:
                if not isinstance(cancellation, dict):
                    raise StateError("cancelled campaign launch lacks cancellation state")
                required_cancellation = {
                    "expected_artifact_sha",
                    "reason",
                    "requested_at",
                    "term_timeout_s",
                    "kill_timeout_s",
                    "inventory",
                    "cleanup",
                    "cleanup_detail",
                }
                if (
                    set(cancellation) - (required_cancellation | {"completed_at"})
                    or not required_cancellation <= set(cancellation)
                    or cancellation.get("expected_artifact_sha") != launch.get("artifact_sha")
                    or cancellation.get("cleanup") not in {"pending", "blocked", "complete"}
                    or not isinstance(cancellation.get("inventory"), list)
                ):
                    raise StateError("campaign launch has malformed cancellation state")
            recovery = launch.get("recovery")
            if launch.get("status") == "startup-failed-recoverable":
                required_recovery = {
                    "kind",
                    "reason_code",
                    "requested_at",
                    "evidence_path",
                    "evidence_sha256",
                    "adapter_script_sha256",
                    "process_cleanup",
                    "reviewers",
                }
                recovery_reviewers = (
                    recovery.get("reviewers") if isinstance(recovery, dict) else None
                )
                recovery_names = (
                    frozenset(
                        item.get("reviewer")
                        for item in recovery_reviewers
                        if isinstance(item, dict)
                    )
                    if isinstance(recovery_reviewers, list)
                    else frozenset()
                )
                recovery_shape_valid = isinstance(recovery_reviewers, list) and any(
                    len(recovery_reviewers) == len(expected)
                    and recovery_names == expected
                    for expected in STARTUP_REVIEWER_SETS.get(str(phase), ())
                )
                if (
                    not isinstance(recovery, dict)
                    or set(recovery) != required_recovery
                    or recovery.get("kind") != "claim-scoped-credit"
                    or recovery.get("reason_code")
                    != "PROVIDER_SCHEMA_STARTUP_REJECTION"
                    or recovery.get("process_cleanup") != "verified-no-descendants"
                    or not isinstance(recovery.get("requested_at"), str)
                    or not recovery.get("requested_at")
                    or not isinstance(recovery.get("evidence_path"), str)
                    or not recovery.get("evidence_path")
                    or any(
                        not is_sha256(recovery.get(field))
                        for field in ("evidence_sha256", "adapter_script_sha256")
                    )
                    or not isinstance(recovery_reviewers, list)
                    or not recovery_shape_valid
                    or any(
                        not isinstance(item, dict)
                        or set(item)
                        != {
                            "reviewer",
                            "classification",
                            "provider_exit_code",
                            "output_bytes",
                            "input_bytes",
                            "turn_observed",
                        }
                        or item.get("classification")
                        != "provider-schema-startup-rejection"
                        or type(item.get("provider_exit_code")) is not int
                        or item.get("provider_exit_code") in {0, 124, 137}
                        or type(item.get("output_bytes")) is not int
                        or item.get("output_bytes") != 0
                        or type(item.get("input_bytes")) is not int
                        or item.get("input_bytes") != 0
                        or item.get("turn_observed") is not False
                        for item in recovery_reviewers
                    )
                ):
                    raise StateError("recoverable campaign launch has malformed recovery state")
            elif recovery is not None:
                raise StateError("non-recoverable campaign launch unexpectedly has recovery state")
    claims: dict[str, tuple[str, int, dict[str, object]]] = {}
    consumers: set[str] = set()
    historical_repair_sources: set[str] = set()
    ordered_launches = [
        launch
        for campaign in campaigns
        if isinstance(campaign, dict)
        for launch in campaign.get("launches", [])
        if isinstance(launch, dict)
    ]
    for campaign in campaigns:
        assert isinstance(campaign, dict)
        campaign_id = str(campaign.get("campaign_id"))
        launches = campaign["launches"]
        assert isinstance(launches, list)
        for index, launch in enumerate(launches):
            assert isinstance(launch, dict)
            claim_id = str(launch["claim_id"])
            if claim_id in claims:
                raise StateError("campaign ledger contains a duplicate claim_id")
            replacement_for = launch.get("replacement_for")
            if replacement_for is not None:
                if not isinstance(replacement_for, str) or replacement_for in consumers:
                    raise StateError("campaign replacement credit is duplicated or malformed")
                source_record = claims.get(replacement_for)
                if source_record is None:
                    raise StateError("campaign replacement source does not precede its consumer")
                source_campaign, source_index, source = source_record
                if (
                    source_campaign != campaign_id
                    or source_index + 1 != index
                    or source.get("status") != "startup-failed-recoverable"
                    or source.get("attempt") != 1
                    or launch.get("attempt") != 2
                    or any(
                        source.get(field) != launch.get(field)
                        for field in ("round", "phase", "artifact_sha", "model_launches")
                    )
                ):
                    raise StateError("campaign replacement does not match its recovery source")
                consumers.add(replacement_for)
            claims[claim_id] = (campaign_id, index, launch)
        repairs = campaign.get("repairs", [])
        assert isinstance(repairs, list)
        for repair in repairs:
            required_repair = {
                "kind",
                "repair_id",
                "incident_id",
                "source_claim_id",
                "reason_code",
                "recorded_at",
                "source_finished_at",
                "artifact_sha",
                "source_protocol_sha",
                "repair_protocol_sha",
                "attestation",
                "attestation_sha256",
                "history_prefix_sha256",
                "history_launch_count",
                "credited_model_launches",
            }
            if not isinstance(repair, dict) or set(repair) != required_repair:
                raise StateError("campaign historical repair is malformed")
            source_claim_id = repair.get("source_claim_id")
            source_record = claims.get(str(source_claim_id))
            incident = repair.get("attestation")
            incident_fields = {
                "incident_id",
                "issue",
                "doc_id",
                "source_claim_id",
                "source_finished_at",
                "artifact_sha",
                "source_protocol_sha",
                "history_launch_count",
                "history_gross_model_launches",
                "history_prefix_sha256",
                "credited_model_launches",
                "provider_stage",
                "reviewer_count",
                "turn_observed",
                "legacy_classification",
            }
            if not isinstance(incident, dict) or set(incident) != incident_fields:
                raise StateError(
                    "campaign historical repair lacks its immutable attestation"
                )
            history_count = incident.get("history_launch_count")
            credited_model_launches = repair.get("credited_model_launches")
            history_prefix = (
                ordered_launches[:history_count]
                if type(history_count) is int and history_count >= 1
                else []
            )
            source_global_index = (
                next(
                    (
                        index
                        for index, item in enumerate(ordered_launches)
                        if source_record is not None and item is source_record[2]
                    ),
                    -1,
                )
            )
            try:
                uuid.UUID(str(repair.get("repair_id")))
            except (ValueError, AttributeError):
                raise StateError("campaign historical repair has an invalid repair_id")
            if (
                repair.get("kind") != "historical-startup-credit"
                or repair.get("incident_id") != incident.get("incident_id")
                or incident.get("doc_id") != payload.get("doc_id")
                or repair.get("reason_code")
                != "PROVIDER_SCHEMA_STARTUP_REJECTION"
                or not isinstance(source_claim_id, str)
                or source_claim_id in historical_repair_sources
                or source_claim_id in consumers
                or source_record is None
                or source_record[0] != campaign_id
                or source_record[2].get("status") != "failed"
                or source_record[2].get("phase") != "fanout"
                or source_record[2].get("attempt") != 1
                or type(credited_model_launches) is not int
                or credited_model_launches != PHASE_WEIGHT["fanout"]
                or credited_model_launches
                != source_record[2].get("model_launches")
                or type(history_count) is not int
                or history_count < 1
                or source_global_index < 0
                or source_global_index >= history_count
                or source_record[2].get("finished_at")
                != repair.get("source_finished_at")
                or source_record[2].get("artifact_sha")
                != repair.get("artifact_sha")
                or source_record[2].get("protocol_sha")
                != repair.get("source_protocol_sha")
                or repair.get("source_protocol_sha")
                == repair.get("repair_protocol_sha")
                or repair.get("source_claim_id")
                != incident.get("source_claim_id")
                or repair.get("source_finished_at")
                != incident.get("source_finished_at")
                or repair.get("artifact_sha") != incident.get("artifact_sha")
                or repair.get("source_protocol_sha")
                != incident.get("source_protocol_sha")
                or repair.get("credited_model_launches")
                != incident.get("credited_model_launches")
                or repair.get("history_launch_count") != history_count
                or repair.get("history_prefix_sha256")
                != incident.get("history_prefix_sha256")
                or canonical_sha256(history_prefix)
                != incident.get("history_prefix_sha256")
                or sum(
                    int(item.get("model_launches", 0))
                    for item in history_prefix
                )
                != incident.get("history_gross_model_launches")
                or repair.get("attestation_sha256")
                != canonical_sha256(incident)
                or not isinstance(repair.get("recorded_at"), str)
                or not repair.get("recorded_at")
                or any(
                    not is_sha256(repair.get(field))
                    for field in (
                        "artifact_sha",
                        "source_protocol_sha",
                        "repair_protocol_sha",
                        "attestation_sha256",
                        "history_prefix_sha256",
                    )
                )
            ):
                raise StateError("campaign historical repair does not match its source")
            historical_repair_sources.add(source_claim_id)
    if len(historical_repair_sources) > 1:
        raise StateError("campaign ledger contains more than one historical startup repair")
    return payload


def active_campaign(ledger: dict[str, object]) -> dict[str, object]:
    campaign = ledger["campaigns"][-1]  # type: ignore[index]
    if not isinstance(campaign, dict):
        raise StateError("active campaign is malformed")
    expected = {"campaign_id", "started_at", "started_by", "reason", "launches"}
    if (
        set(campaign) not in (expected, expected | {"repairs"})
        or not isinstance(campaign.get("launches"), list)
        or ("repairs" in campaign and not isinstance(campaign.get("repairs"), list))
    ):
        raise StateError("active campaign fields do not match schema version 1")
    return campaign


def base_ceiling() -> int:
    raw = os.environ.get(
        "MAGI_MAX_AUTONOMOUS_MODEL_LAUNCHES", str(DEFAULT_MAX_MODEL_LAUNCHES)
    )
    value = positive_int(raw, "MAGI_MAX_AUTONOMOUS_MODEL_LAUNCHES")
    if value > DEFAULT_MAX_MODEL_LAUNCHES:
        raise UsageError(
            "MAGI_MAX_AUTONOMOUS_MODEL_LAUNCHES may only tighten the default ceiling of "
            f"{DEFAULT_MAX_MODEL_LAUNCHES}; the global fuse cannot be extended"
        )
    return value


def next_transition(launches: list[object]) -> dict[str, object]:
    if not launches:
        return {
            "kind": "candidate",
            "round": 1,
            "phase": "fanout",
            "attempt": 1,
        }
    last = launches[-1]
    if not isinstance(last, dict):
        raise StateError("campaign launch ledger contains a malformed entry")
    last_round, last_phase = last.get("round"), last.get("phase")
    if not isinstance(last_round, int) or last_phase not in PHASE_WEIGHT:
        raise StateError("campaign launch ledger contains an invalid transition entry")
    same_attempts = sum(
        1
        for launch in launches
        if isinstance(launch, dict)
        and launch.get("round") == last_round
        and launch.get("phase") == last_phase
    )
    status = last.get("status")
    if status in NONTERMINAL_STATUSES:
        return {
            "kind": "cancellation-in-progress"
            if status == "cancellation_in_progress"
            else "running",
            "round": last_round,
            "phase": last_phase,
            "attempt": same_attempts,
            "reason": (
                f"round {last_round} {last_phase} requirement-revision cleanup is incomplete"
                if status == "cancellation_in_progress"
                else f"round {last_round} {last_phase} is not terminal"
            ),
        }
    if status == "superseded-by-requirement-revision":
        return {
            "kind": "transition-blocked",
            "round": last_round,
            "phase": last_phase,
            "attempt": same_attempts,
            "reason": "requirement-revision supersession requires a changed artifact",
        }
    if status in {"failed", "abandoned", "startup-failed-recoverable"}:
        if same_attempts >= 2:
            return {
                "kind": "transition-blocked",
                "round": last_round,
                "phase": last_phase,
                "attempt": same_attempts,
                "reason": f"retry budget exhausted for round {last_round} {last_phase}",
            }
        return {
            "kind": "candidate",
            "round": last_round,
            "phase": last_phase,
            "attempt": same_attempts + 1,
        }
    if status != "success":
        raise StateError(f"campaign launch has an invalid status: {status!r}")
    expected_phase = "xfamily" if last_phase in {"fanout", "targeted"} else "fanout"
    return {
        "kind": "candidate",
        "round": last_round + 1,
        "phase": expected_phase,
        "attempt": 1,
    }


def replacement_source(
    launches: list[object], phase: str
) -> dict[str, object] | None:
    """Return the one claim whose immediately-next phase carries zero weight."""
    transition = next_transition(launches)
    if (
        transition.get("kind") != "candidate"
        or transition.get("phase") != phase
        or transition.get("attempt") != 2
        or not launches
        or not isinstance(launches[-1], dict)
        or launches[-1].get("status") != "startup-failed-recoverable"
    ):
        return None
    return launches[-1]


def exact_revision_matches(
    launch: dict[str, object], artifact_sha: str, review_protocol_sha: str
) -> bool:
    return (
        launch.get("artifact_sha") == artifact_sha
        and launch.get("protocol_sha") == review_protocol_sha
    )


def xfamily_source_revision_matches(
    launch: dict[str, object], artifact_sha: str, review_protocol_sha: str
) -> bool:
    """Keep the pre-protocol-ledger migration path artifact-bound."""
    return (
        launch.get("artifact_sha") == artifact_sha
        and launch.get("protocol_sha") in {review_protocol_sha, "legacy-unknown"}
    )


def xfamily_revision_source(
    launches: list[object], round_no: int
) -> tuple[dict[str, object], list[dict[str, object]]] | None:
    """Return the successful same-family source and trailing xfamily attempts."""
    trailing: list[dict[str, object]] = []
    index = len(launches) - 1
    while index >= 0:
        launch = launches[index]
        if not isinstance(launch, dict):
            return None
        if launch.get("phase") != "xfamily" or launch.get("round") != round_no:
            break
        trailing.append(launch)
        index -= 1
    if index < 0:
        return None
    source = launches[index]
    if (
        not isinstance(source, dict)
        or source.get("phase") not in {"fanout", "targeted"}
        or source.get("status") != "success"
        or source.get("round") != round_no - 1
    ):
        return None
    trailing.reverse()
    return source, trailing


def stranded_cross_revision_xfamily(
    launches: list[object], artifact_sha: str
) -> bool:
    """Recognize one exact new revision stranded behind invalid xfamily failures."""
    if not launches or not isinstance(launches[-1], dict):
        return False
    last = launches[-1]
    round_no = last.get("round")
    if type(round_no) is not int or last.get("phase") != "xfamily":
        return False
    source_and_attempts = xfamily_revision_source(launches, round_no)
    if source_and_attempts is None:
        return False
    source, attempts = source_and_attempts
    attempt_protocols = {attempt.get("protocol_sha") for attempt in attempts}
    if len(attempt_protocols) != 1:
        return False
    attempt_protocol = next(iter(attempt_protocols))
    source_is_distinct = source.get("artifact_sha") != artifact_sha or (
        source.get("protocol_sha") != "legacy-unknown"
        and source.get("protocol_sha") != attempt_protocol
    )
    return (
        bool(attempts)
        and all(
            attempt.get("status") in {"failed", "abandoned"}
            and attempt.get("artifact_sha") == artifact_sha
            for attempt in attempts
        )
        and source_is_distinct
    )


def reused_revision_state(
    campaigns: list[object],
    state: Path,
    artifact_sha: str,
    review_protocol_sha: str,
    *,
    allowed_claim_id: object = None,
) -> dict[str, object] | None:
    """Return a prior launch that binds this state directory to another revision."""
    for campaign in campaigns:
        if not isinstance(campaign, dict):
            continue
        launches = campaign.get("launches")
        if not isinstance(launches, list):
            continue
        for launch in launches:
            if (
                isinstance(launch, dict)
                and launch.get("claim_id") != allowed_claim_id
                and launch.get("state_dir") == str(state)
                and not xfamily_source_revision_matches(
                    launch, artifact_sha, review_protocol_sha
                )
            ):
                return launch
    return None


def validate_transition(launches: list[object], round_no: int, phase: str) -> int:
    transition = next_transition(launches)
    if (
        transition["kind"] == "candidate"
        and transition["round"] == round_no
        and transition["phase"] == phase
    ):
        return int(transition["attempt"])
    if transition["kind"] in {"running", "cancellation-in-progress"}:
        raise TransitionError(str(transition["reason"]))
    if transition["kind"] == "transition-blocked":
        raise TransitionError(str(transition["reason"]))
    if not launches:
        raise TransitionError("a campaign must start at round 1 fanout")
    last = launches[-1]
    assert isinstance(last, dict)
    last_round, last_phase = last["round"], last["phase"]
    if round_no == last_round and phase == last_phase and last.get("status") == "success":
        raise TransitionError(
            f"round {round_no} {phase} already succeeded; retry would duplicate providers"
        )
    if last.get("status") != "success":
        raise TransitionError(
            f"round {last_round} {last_phase} did not succeed; next phase cannot start"
        )
    raise TransitionError(
        f"illegal campaign transition: after round {last_round} {last_phase}, expected "
        f"round {transition['round']} {transition['phase']}"
    )


def admission_decision(
    total_used: int,
    ceiling: int,
    phase: str,
    *,
    launch_weight: int | None = None,
    scope: str = "global campaign history",
) -> dict[str, object]:
    weight = PHASE_WEIGHT[phase] if launch_weight is None else launch_weight
    reserve = FINAL_XFAMILY_RESERVE if phase in {"fanout", "targeted"} else 0
    arithmetic = kernel.launch_affordability(
        total_used,
        ceiling,
        launch_weight=weight,
        reserved_weight=reserve,
    )
    required = int(arithmetic["required"])
    affordable = bool(arithmetic["affordable"])
    reason = (
        f"{scope} would require {total_used + required}/{ceiling} "
        f"model launches ({weight} for {phase}"
        + (f" plus {reserve} reserved for mandatory xfamily)" if reserve else ")")
    )
    return {
        "weight": weight,
        "reserve": reserve,
        "required": required,
        "affordable": affordable,
        "reason": reason,
    }


def bounded_admission_decision(
    campaign_used: int,
    campaign_ceiling: int,
    total_used: int,
    global_ceiling: int,
    phase: str,
    *,
    launch_weight: int | None = None,
) -> dict[str, object]:
    """Require both the per-campaign allowance and the task-global fuse."""
    campaign = admission_decision(
        campaign_used,
        campaign_ceiling,
        phase,
        launch_weight=launch_weight,
        scope="active campaign",
    )
    global_history = admission_decision(
        total_used, global_ceiling, phase, launch_weight=launch_weight
    )
    affordable = bool(campaign["affordable"]) and bool(global_history["affordable"])
    if not campaign["affordable"]:
        reason = str(campaign["reason"])
    else:
        reason = str(global_history["reason"])
    return {
        "weight": campaign["weight"],
        "reserve": campaign["reserve"],
        "required": campaign["required"],
        "affordable": affordable,
        "reason": reason,
        "campaign_used": campaign_used,
        "campaign_ceiling": campaign_ceiling,
        "global_used": total_used,
        "global_ceiling": global_ceiling,
    }


def model_launches(campaigns: list[object]) -> int:
    gross = sum(
        0
        if launch.get("replacement_for") is not None
        else launch.get("model_launches", PHASE_WEIGHT.get(str(launch.get("phase")), 0))
        for campaign in campaigns
        if isinstance(campaign, dict)
        for launch in campaign.get("launches", [])
        if isinstance(launch, dict)
    )
    historical_credits = sum(
        repair.get("credited_model_launches", 0)
        for campaign in campaigns
        if isinstance(campaign, dict)
        for repair in campaign.get("repairs", [])
        if isinstance(repair, dict)
    )
    return int(gross) - int(historical_credits)


def verified_recovery_adapter(
    owner: dict[str, object], phase: str
) -> tuple[Path, str]:
    """Bind startup credit to the closed, official fanout adapter process."""
    if phase not in {"fanout", "targeted"}:
        raise TransitionError("startup credit is only supported for Codex fanout")
    pid = owner.get("pid")
    if type(pid) is not int or not matching_process(owner):
        raise TransitionError("claim owner is not live with its registered identity")
    cmdline = process_cmdline(pid)
    if len(cmdline) < 2 or Path(cmdline[0]).name not in {"bash", "sh"}:
        raise TransitionError("claim owner is not the official shell adapter")
    if cmdline[1] == "-c":
        raise TransitionError("inline shell owners cannot request startup credit")
    candidate = Path(cmdline[1]).expanduser().resolve()
    expected = Path(__file__).resolve().with_name("magi_fanout_codex.sh")
    try:
        candidate_sha = sha256_file(candidate)
        expected_sha = sha256_file(expected)
    except OSError as exc:
        raise TransitionError(f"cannot verify recovery adapter identity: {exc}") from exc
    if candidate_sha != expected_sha:
        raise TransitionError("claim owner does not match the closed fanout adapter")
    return candidate, candidate_sha


def load_startup_evidence(
    evidence_raw: str, launch: dict[str, object], expected_artifact_id: str
) -> tuple[Path, dict[str, object], str]:
    state = Path(str(launch.get("state_dir"))).resolve()
    evidence = Path(evidence_raw).expanduser().resolve()
    expected_name = (
        f"round_{launch.get('round')}_fanout.{launch.get('claim_id')}.FAILED.json"
    )
    if evidence.parent != state or evidence.name != expected_name:
        raise TransitionError("startup evidence is not the claim-scoped failure artifact")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(evidence, flags)
    except OSError as exc:
        raise TransitionError(f"cannot open startup evidence: {exc}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size < 1 or info.st_size > 65536:
            raise TransitionError("startup evidence is not a bounded regular file")
        with os.fdopen(fd, "rb", closefd=False) as fh:
            raw = fh.read(65537)
    finally:
        os.close(fd)
    try:
        payload = strict_json_loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise TransitionError(f"startup evidence is invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise TransitionError("startup evidence must be an object")
    expected_top = {
        "status",
        "classification",
        "round",
        "claim_id",
        "artifact_id",
        "artifact_sha",
        "reviewers",
    }
    if set(payload) != expected_top:
        raise TransitionError("startup evidence fields do not match the closed contract")
    if (
        payload.get("status") != "failed"
        or payload.get("classification") != "reviewer-fanout-failure"
        or payload.get("round") != launch.get("round")
        or payload.get("claim_id") != launch.get("claim_id")
        or payload.get("artifact_id") != expected_artifact_id
        or payload.get("artifact_sha") != launch.get("artifact_sha")
    ):
        raise TransitionError("startup evidence identity does not match the claim")
    reviewers = payload.get("reviewers")
    phase = launch.get("phase")
    reviewer_sets = STARTUP_REVIEWER_SETS.get(str(phase))
    if reviewer_sets is None:
        raise TransitionError("startup evidence phase is not recoverable")
    expected_count = len(reviewer_sets[0])
    if not isinstance(reviewers, list) or len(reviewers) != expected_count:
        raise TransitionError(
            f"startup evidence must cover exactly {expected_count} reviewer(s)"
        )
    names = frozenset(
        item.get("reviewer") for item in reviewers if isinstance(item, dict)
    )
    if names not in reviewer_sets:
        raise TransitionError("startup evidence reviewer set is invalid")
    required_item = {
        "reviewer",
        "round",
        "classification",
        "provider_exit_code",
        "scrubber_exit_code",
        "output_bytes",
        "log_bytes",
        "input_bytes",
        "input_parsed_json",
        "redactions",
        "turn_observed",
    }
    for item in reviewers:
        if not isinstance(item, dict) or set(item) != required_item:
            raise TransitionError("startup reviewer evidence fields are invalid")
        provider_exit = item.get("provider_exit_code")
        if (
            item.get("round") != launch.get("round")
            or item.get("classification") != "provider-schema-startup-rejection"
            or type(provider_exit) is not int
            or provider_exit in {0, 124, 137}
            or item.get("scrubber_exit_code") != 0
            or type(item.get("output_bytes")) is not int
            or item.get("output_bytes") != 0
            or type(item.get("input_bytes")) is not int
            or item.get("input_bytes") != 0
            or item.get("input_parsed_json") is not False
            or item.get("turn_observed") is not False
            or type(item.get("log_bytes")) is not int
            or item.get("log_bytes") < 1
            or type(item.get("redactions")) is not int
            or item.get("redactions") < 0
        ):
            raise TransitionError("reviewer evidence does not prove a startup rejection")
    return evidence, payload, hashlib.sha256(raw).hexdigest()


def recover_startup(doc_raw: str, claim_id: str, evidence_raw: str) -> None:
    """Credit one claim only when the closed adapter proves no reviewer turn began."""
    doc = canonical_doc(doc_raw)
    with document_lock(doc):
        ledger = load_ledger(doc, create=False)
        matches = [
            launch
            for campaign in ledger["campaigns"]  # type: ignore[index]
            if isinstance(campaign, dict)
            for launch in campaign.get("launches", [])
            if isinstance(launch, dict) and launch.get("claim_id") == claim_id
        ]
        if len(matches) != 1:
            raise UsageError(f"claim_id resolves to {len(matches)} launches")
        launch = matches[0]
        if launch.get("status") == "startup-failed-recoverable":
            print(f"CAMPAIGN STARTUP CREDIT CONFIRMED: CLAIM_ID={claim_id}")
            return
        if launch.get("status") != "running":
            raise TransitionError(
                f"claim {claim_id} is not running and cannot receive startup credit"
            )
        owner = launch.get("owner")
        if not isinstance(owner, dict) or owner.get("pid") != os.getppid():
            raise TransitionError("startup credit caller is not the registered claim owner")
        _, adapter_sha = verified_recovery_adapter(owner, str(launch.get("phase")))
        live_tree = owned_processes(owner)
        unexpected = [
            item
            for item in live_tree
            if item.get("pid") not in {owner.get("pid"), os.getpid()}
        ]
        if unexpected:
            raise TransitionError("provider process tree is still live")
        evidence, evidence_payload, evidence_sha = load_startup_evidence(
            evidence_raw, launch, doc_id(doc)
        )
        if launch.get("attempt") != 1:
            raise TransitionError(
                "only the first attempt can authorize one replacement launch"
            )
        launch["status"] = "startup-failed-recoverable"
        launch["finished_at"] = now()
        launch["recovery"] = {
            "kind": "claim-scoped-credit",
            "reason_code": "PROVIDER_SCHEMA_STARTUP_REJECTION",
            "requested_at": now(),
            "evidence_path": str(evidence),
            "evidence_sha256": evidence_sha,
            "adapter_script_sha256": adapter_sha,
            "process_cleanup": "verified-no-descendants",
            "reviewers": [
                {
                    key: item[key]
                    for key in (
                        "reviewer",
                        "classification",
                        "provider_exit_code",
                        "output_bytes",
                        "input_bytes",
                        "turn_observed",
                    )
                }
                for item in evidence_payload["reviewers"]  # type: ignore[index]
            ],
        }
        atomic_json(ledger_path(doc), ledger)
    print(
        f"CAMPAIGN STARTUP RECOVERY AUTHORIZED: CLAIM_ID={claim_id} "
        f"replacement weight={launch['model_launches']}"
    )


def repair_historical_startup(
    doc_raw: str,
    claim_id: str,
    incidents: tuple[dict[str, object], ...] | None = None,
) -> None:
    """Apply one reviewed, closed attestation for a pre-recovery incident."""
    doc = canonical_doc(doc_raw)
    with document_lock(doc):
        path = ledger_path(doc)
        if not path.is_file():
            raise UsageError(f"no campaign ledger exists for {doc}")
        try:
            original_payload = strict_json_loads(path.read_bytes())
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise StateError(f"campaign ledger is unreadable: {path}: {exc}") from exc
        ledger = load_ledger(doc, create=False)
        if not isinstance(original_payload, dict) or original_payload.get(
            "campaigns"
        ) != ledger.get("campaigns"):
            raise TransitionError(
                "historical repair requires fully materialized immutable launch history"
            )
        campaigns = ledger["campaigns"]
        assert isinstance(campaigns, list)
        incident = historical_incident(doc_id(doc), claim_id, incidents)
        flattened: list[tuple[int, int, dict[str, object]]] = [
            (campaign_index, launch_index, launch)
            for campaign_index, campaign in enumerate(campaigns)
            if isinstance(campaign, dict)
            for launch_index, launch in enumerate(campaign.get("launches", []))
            if isinstance(launch, dict)
        ]
        matches = [
            (campaign_index, launch_index, launch)
            for campaign_index, launch_index, launch in flattened
            if launch.get("claim_id") == claim_id
        ]
        if len(matches) != 1:
            raise UsageError(f"claim_id resolves to {len(matches)} launches")
        campaign_index, launch_index, launch = matches[0]
        if any(
            isinstance(campaign, dict) and campaign.get("repairs")
            for campaign in campaigns
        ):
            raise TransitionError(
                "historical startup repair was already consumed for this ledger"
            )
        if any(
            item.get("status") in NONTERMINAL_STATUSES
            for _, _, item in flattened
        ):
            raise TransitionError("historical repair requires a fully terminal ledger")
        history_count = incident.get("history_launch_count")
        history_launches = [item for _, _, item in flattened]
        if (
            type(history_count) is not int
            or history_count < 1
            or len(history_launches) != history_count
            or canonical_sha256(history_launches)
            != incident.get("history_prefix_sha256")
            or sum(int(item.get("model_launches", 0)) for item in history_launches)
            != incident.get("history_gross_model_launches")
            or launch.get("status") != "failed"
            or launch.get("phase") != "fanout"
            or launch.get("attempt") != 1
            or launch.get("model_launches") != PHASE_WEIGHT["fanout"]
            or launch.get("claim_id") != incident.get("source_claim_id")
            or launch.get("finished_at") != incident.get("source_finished_at")
            or launch.get("artifact_sha") != incident.get("artifact_sha")
            or launch.get("protocol_sha") != incident.get("source_protocol_sha")
            or launch.get("model_launches")
            != incident.get("credited_model_launches")
            or incident.get("provider_stage")
            != "codex-output-schema-validation-before-reviewer-turn"
            or incident.get("reviewer_count") != 3
            or incident.get("turn_observed") is not False
            or incident.get("legacy_classification") != "provider-exit"
        ):
            raise TransitionError(
                "historical ledger does not match its closed incident attestation"
            )
        if any(
            item.get("replacement_for") == claim_id for _, _, item in flattened
        ):
            raise TransitionError("startup recovery for this claim was already consumed")
        repair_protocol_sha = protocol_sha()
        if launch.get("protocol_sha") == repair_protocol_sha:
            raise TransitionError(
                "historical repair requires a changed review protocol"
            )
        campaign = campaigns[campaign_index]
        assert isinstance(campaign, dict)
        repairs = campaign.setdefault("repairs", [])
        assert isinstance(repairs, list)
        repair = {
            "kind": "historical-startup-credit",
            "repair_id": str(uuid.uuid4()),
            "incident_id": incident["incident_id"],
            "source_claim_id": claim_id,
            "reason_code": "PROVIDER_SCHEMA_STARTUP_REJECTION",
            "recorded_at": now(),
            "source_finished_at": launch["finished_at"],
            "artifact_sha": launch["artifact_sha"],
            "source_protocol_sha": launch["protocol_sha"],
            "repair_protocol_sha": repair_protocol_sha,
            "attestation": incident,
            "attestation_sha256": canonical_sha256(incident),
            "history_prefix_sha256": incident["history_prefix_sha256"],
            "history_launch_count": history_count,
            "credited_model_launches": launch["model_launches"],
        }
        repairs.append(repair)
        atomic_json(ledger_path(doc), ledger)
    print(
        f"CAMPAIGN HISTORICAL STARTUP REPAIR RECORDED: CLAIM_ID={claim_id} "
        f"REPAIR_ID={repair['repair_id']} credit={repair['credited_model_launches']}"
    )


def campaign_admission_status(doc: Path) -> dict[str, object]:
    """Read the active campaign under its lock without changing ledger state."""
    with document_lock(doc):
        path = ledger_path(doc)
        if path.is_file():
            ledger = load_ledger(doc, create=False)
            ledger_sha = file_sha(path)
        else:
            ledger = new_ledger(doc)
            ledger_sha = "no-ledger"
        campaign = active_campaign(ledger)
        launches = campaign["launches"]
        campaigns = ledger["campaigns"]
        assert isinstance(launches, list)
        assert isinstance(campaigns, list)
        transition = next_transition(launches)
        total_used = model_launches(campaigns)
        campaign_ceiling = base_ceiling()
        global_ceiling, fuse_authority = global_ceiling_policy(doc)
        last = launches[-1] if launches else None
        current_artifact_sha = file_sha(doc)
        current_protocol_sha = protocol_sha()
        enforce_scoped_artifact_sha(fuse_authority, current_artifact_sha)
        if scope_review_checkpoint_required(doc, total_used):
            return {
                "kind": "budget-blocked",
                "reason": "SCOPE_REVIEW_CHECKPOINT_REQUIRED",
                "ledger_sha": ledger_sha,
                "used": total_used,
                "ceiling": total_used,
                "campaign_used": model_launches([campaign]),
                "campaign_ceiling": campaign_ceiling,
                "global_fuse_authority": fuse_authority,
            }
        if final_cycle_is_closed(doc, total_used):
            return {
                "kind": "budget-blocked",
                "reason": "USER_AUTHORIZED_FINAL_CYCLE_EXHAUSTED",
                "ledger_sha": ledger_sha,
                "used": total_used,
                "ceiling": total_used,
                "campaign_used": model_launches([campaign]),
                "campaign_ceiling": campaign_ceiling,
                "global_fuse_authority": fuse_authority,
            }
        rollover_available = (
            isinstance(last, dict)
            and last.get("status") not in NONTERMINAL_STATUSES
            and may_rollover(
                ledger,
                campaign,
                1,
                "fanout",
                artifact_sha=current_artifact_sha,
                review_protocol_sha=current_protocol_sha,
            )
        )
        if rollover_available:
            transition = {
                "kind": "candidate",
                "round": 1,
                "phase": "fanout",
                "attempt": 1,
            }
        if transition["kind"] != "candidate":
            return {
                **transition,
                "ledger_sha": ledger_sha,
                "used": total_used,
                "ceiling": global_ceiling,
                "campaign_used": model_launches([campaign]),
                "campaign_ceiling": campaign_ceiling,
                "global_fuse_authority": fuse_authority,
            }
        campaign_used = 0 if rollover_available else model_launches([campaign])
        replacement = (
            None
            if rollover_available
            else replacement_source(launches, str(transition["phase"]))
        )
        required_reviewer_family = None
        if fuse_authority is not None:
            try:
                required_reviewer_family = enforce_scoped_launch_plan(
                    fuse_authority,
                    total_used,
                    str(transition["phase"]),
                    launches,
                    current_artifact_sha,
                    current_protocol_sha,
                    reviewer_family=None,
                    require_reviewer_family=False,
                )
            except TransitionError as exc:
                return {
                    "kind": "transition-blocked",
                    "reason": str(exc),
                    "ledger_sha": ledger_sha,
                    "used": total_used,
                    "ceiling": global_ceiling,
                    "global_fuse_authority": fuse_authority,
                }
        admission = bounded_admission_decision(
            campaign_used,
            campaign_ceiling,
            total_used,
            global_ceiling,
            str(transition["phase"]),
            launch_weight=0 if replacement is not None else None,
        )
        return {
            **transition,
            **admission,
            "kind": "candidate" if admission["affordable"] else "budget-blocked",
            "ledger_sha": ledger_sha,
            "used": total_used,
            "ceiling": global_ceiling,
            "global_fuse_authority": fuse_authority,
            "required_reviewer_family": required_reviewer_family,
        }


def may_rollover(
    ledger: dict[str, object],
    campaign: dict[str, object],
    round_no: int,
    phase: str,
    *,
    artifact_sha: str,
    review_protocol_sha: str,
) -> bool:
    campaigns = ledger["campaigns"]
    assert isinstance(campaigns, list)
    launches = campaign["launches"]
    assert isinstance(launches, list)
    if round_no != 1 or phase not in {"fanout", "targeted"} or not launches:
        return False
    last = launches[-1]
    if not isinstance(last, dict):
        return False
    if last.get("status") in NONTERMINAL_STATUSES:
        return False
    if (
        phase == "fanout"
        and stranded_cross_revision_xfamily(launches, artifact_sha)
    ):
        return True
    if last.get("status") == "superseded-by-requirement-revision":
        return phase == "fanout" and last.get("artifact_sha") != artifact_sha
    if phase == "targeted":
        return last.get("artifact_sha") != artifact_sha
    return (
        last.get("artifact_sha") != artifact_sha
        or last.get("protocol_sha") != review_protocol_sha
    )


def claim(
    doc_raw: str,
    round_raw: str,
    phase: str,
    state_raw: str,
    owner_pid: int | None = None,
    adapter_kind: str | None = None,
    expected_artifact_sha: str | None = None,
    reviewer_family: str | None = None,
) -> None:
    doc = canonical_doc(doc_raw)
    round_no = positive_int(round_raw, "round")
    if (owner_pid is None) != (adapter_kind is None):
        raise UsageError("--owner-pid and --adapter-kind must be supplied together")
    if reviewer_family is not None and phase != "xfamily":
        raise UsageError("--reviewer-family is valid only for xfamily claims")
    owner: dict[str, object] | None = None
    if owner_pid is not None:
        if owner_pid != os.getppid():
            raise UsageError("--owner-pid must identify the campaign guard's parent process")
        if adapter_kind != phase:
            raise UsageError("--adapter-kind must match the claimed phase")
        identity = proc_identity(owner_pid)
        if identity is None:
            raise UsageError("--owner-pid is not live")
        owner = {
            key: identity[key]
            for key in ("pid", "start_ticks", "ppid", "pgid")
        }
        owner["adapter_kind"] = adapter_kind
    state = Path(state_raw).expanduser().resolve()
    state.mkdir(parents=True, exist_ok=True)
    with document_lock(doc):
        current_artifact_sha = file_sha(doc)
        if (
            expected_artifact_sha is not None
            and expected_artifact_sha != current_artifact_sha
        ):
            raise TransitionError("claim artifact changed after its authorization decision")
        ledger = load_ledger(doc, create=True)
        current_protocol_sha = protocol_sha()
        nonterminal = [
            launch
            for existing_campaign in ledger["campaigns"]  # type: ignore[index]
            if isinstance(existing_campaign, dict)
            for launch in existing_campaign.get("launches", [])
            if isinstance(launch, dict) and launch.get("status") in NONTERMINAL_STATUSES
        ]
        if nonterminal:
            launch = nonterminal[0]
            raise TransitionError(
                f"claim {launch.get('claim_id')} is still {launch.get('status')}"
            )
        campaign = active_campaign(ledger)
        launches = campaign["launches"]
        assert isinstance(launches, list)
        campaigns = ledger["campaigns"]
        assert isinstance(campaigns, list)
        transition_error = None
        planned_rollover = False
        last_launch = launches[-1] if launches else None
        recovery_revision_changed = (
            isinstance(last_launch, dict)
            and last_launch.get("status") == "startup-failed-recoverable"
            and last_launch.get("artifact_sha") != current_artifact_sha
        )
        if recovery_revision_changed:
            transition_error = TransitionError(
                "startup replacement belongs to the prior artifact revision"
            )
        else:
            try:
                attempt = validate_transition(launches, round_no, phase)
            except TransitionError as exc:
                transition_error = exc
            else:
                if phase == "xfamily":
                    source_and_attempts = xfamily_revision_source(launches, round_no)
                    if (
                        source_and_attempts is None
                        or not xfamily_source_revision_matches(
                            source_and_attempts[0],
                            current_artifact_sha,
                            current_protocol_sha,
                        )
                        or any(
                            not exact_revision_matches(
                                launch, current_artifact_sha, current_protocol_sha
                            )
                            for launch in source_and_attempts[1]
                        )
                    ):
                        transition_error = TransitionError(
                            "current exact revision requires round 1 fanout before xfamily"
                        )
        if transition_error is not None:
            if not may_rollover(
                ledger,
                campaign,
                round_no,
                phase,
                artifact_sha=current_artifact_sha,
                review_protocol_sha=current_protocol_sha,
            ):
                if recovery_revision_changed:
                    raise TransitionError(
                        "changed artifact after startup failure requires round 1 fanout"
                    )
                raise transition_error
            campaign = new_campaign(
                operator="automatic-rollover",
                reason="document or review protocol changed after prior campaign attempt",
            )
            launches = campaign["launches"]
            assert isinstance(launches, list)
            attempt = 1
            planned_rollover = True
        replacement = replacement_source(launches, phase)
        reused = reused_revision_state(
            campaigns,
            state,
            current_artifact_sha,
            current_protocol_sha,
            allowed_claim_id=(
                replacement.get("claim_id") if replacement is not None else None
            ),
        )
        if reused is not None:
            raise TransitionError(
                "exact revision requires a revision-scoped state directory; "
                f"state is already bound to claim {reused.get('claim_id')}"
            )
        campaign_ceiling = base_ceiling()
        campaign_used = model_launches([campaign])
        total_used = model_launches(campaigns)
        if scope_review_checkpoint_required(doc, total_used):
            raise BudgetDenied("SCOPE_REVIEW_CHECKPOINT_REQUIRED")
        if final_cycle_is_closed(doc, total_used):
            raise BudgetDenied("USER_AUTHORIZED_FINAL_CYCLE_EXHAUSTED")
        global_ceiling, fuse_authority = global_ceiling_policy(doc)
        enforce_scoped_artifact_sha(fuse_authority, current_artifact_sha)
        if fuse_authority is not None:
            if fuse_authority.get("authorized_phase_plan") is not None and owner is None:
                raise TransitionError(
                    "scoped launch plan requires a registered official adapter owner"
                )
            enforce_scoped_launch_plan(
                fuse_authority,
                total_used,
                phase,
                launches,
                current_artifact_sha,
                current_protocol_sha,
                reviewer_family=reviewer_family,
                require_reviewer_family=True,
            )
        admission = bounded_admission_decision(
            campaign_used,
            campaign_ceiling,
            total_used,
            global_ceiling,
            phase,
            launch_weight=0 if replacement is not None else None,
        )
        if not admission["affordable"]:
            raise BudgetDenied(str(admission["reason"]))
        charged_weight = int(admission["weight"])
        weight = PHASE_WEIGHT[phase]
        if planned_rollover:
            campaigns.append(campaign)
        claim_id = str(uuid.uuid4())
        claim_protocol_sha = current_protocol_sha
        launch_payload = {
            "claim_id": claim_id,
            "sequence": len(launches) + 1,
            "round": round_no,
            "phase": phase,
            "attempt": attempt,
            "model_launches": weight,
            "state_dir": str(state),
            "artifact_sha": current_artifact_sha,
            "protocol_sha": claim_protocol_sha,
            "claimed_at": now(),
            "status": "running",
        }
        if owner is not None:
            launch_payload["owner"] = owner
        if replacement is not None:
            launch_payload["replacement_for"] = replacement["claim_id"]
        if fuse_authority is not None:
            launch_payload["global_fuse_authority"] = fuse_authority
        if reviewer_family is not None:
            launch_payload["reviewer_family"] = reviewer_family
        launches.append(launch_payload)
        atomic_json(ledger_path(doc), ledger)
    print(
        f"CAMPAIGN CLAIMED: {campaign['campaign_id']} global model launches "
        f"{total_used + charged_weight}/{global_ceiling}, "
        f"campaign model launches {campaign_used + charged_weight}/{campaign_ceiling}, "
        f"round {round_no} {phase}, attempt {attempt}; "
        + (
            f"FUSE_AUTHORITY={fuse_authority['authority_id']} "
            f"old={fuse_authority['previous_scoped_ceiling']} "
            f"new={fuse_authority['new_ceiling']}; "
            if fuse_authority is not None
            else ""
        )
        + f"PROTOCOL_SHA={claim_protocol_sha}; CLAIM_ID={claim_id}"
    )


def finish(doc_raw: str, claim_id: str, status: str) -> None:
    doc = canonical_doc(doc_raw)
    with document_lock(doc):
        ledger = load_ledger(doc, create=False)
        matches = [
            launch
            for campaign in ledger["campaigns"]  # type: ignore[index]
            if isinstance(campaign, dict)
            for launch in campaign.get("launches", [])
            if isinstance(launch, dict) and launch.get("claim_id") == claim_id
        ]
        if len(matches) != 1:
            raise UsageError(f"claim_id resolves to {len(matches)} launches")
        launch = matches[0]
        if launch.get("status") in {
            "cancellation_in_progress",
            "superseded-by-requirement-revision",
        }:
            if status == "failed":
                print(
                    f"CAMPAIGN FINISH CONFIRMED: CLAIM_ID={claim_id} "
                    f"status={launch.get('status')}"
                )
                return
            raise TransitionError(
                f"claim {claim_id} is under requirement-revision cancellation"
            )
        if launch.get("status") == "startup-failed-recoverable" and status == "failed":
            print(
                f"CAMPAIGN FINISH CONFIRMED: CLAIM_ID={claim_id} "
                "status=startup-failed-recoverable"
            )
            return
        if launch.get("status") != "running":
            raise TransitionError(
                f"claim {claim_id} is already terminal with status {launch.get('status')!r}"
            )
        if status == "success":
            if launch.get("artifact_sha") != file_sha(doc):
                raise TransitionError(
                    f"claim {claim_id} artifact changed before successful finish"
                )
            if launch.get("protocol_sha") != protocol_sha():
                raise TransitionError(
                    f"claim {claim_id} review protocol changed before successful finish"
                )
        launch["status"] = status
        launch["finished_at"] = now()
        atomic_json(ledger_path(doc), ledger)
    print(f"CAMPAIGN FINISHED: CLAIM_ID={claim_id} status={status}")


def claim_status(doc_raw: str, claim_id: str) -> None:
    doc = canonical_doc(doc_raw)
    with document_lock(doc):
        ledger = load_ledger(doc, create=False)
        matches = [
            launch
            for campaign in ledger["campaigns"]  # type: ignore[index]
            if isinstance(campaign, dict)
            for launch in campaign.get("launches", [])
            if isinstance(launch, dict) and launch.get("claim_id") == claim_id
        ]
        if len(matches) != 1:
            raise UsageError(f"claim_id resolves to {len(matches)} launches")
        print(str(matches[0]["status"]))


def review_lock_available(doc: Path) -> bool:
    lock_path = control_dir(doc) / f".review.{doc_id(doc)}.lock"
    fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    finally:
        os.close(fd)


def revoke_plateau_markers(doc: Path) -> None:
    for marker in control_dir(doc).glob(f"PLATEAU.{doc_id(doc)}.*"):
        try:
            marker.unlink()
        except FileNotFoundError:
            pass
        except IsADirectoryError as exc:
            raise StateError(f"plateau marker path is a directory: {marker}") from exc


def cancellation_matches(
    launch: dict[str, object], expected_artifact_sha: str
) -> bool:
    cancellation = launch.get("cancellation")
    return (
        launch.get("artifact_sha") == expected_artifact_sha
        and isinstance(cancellation, dict)
        and cancellation.get("expected_artifact_sha") == expected_artifact_sha
    )


def merge_inventory(
    stored: list[object], discovered: list[dict[str, object]]
) -> list[dict[str, object]]:
    merged: dict[tuple[int, int], dict[str, object]] = {}
    for identity in [*stored, *discovered]:
        if not isinstance(identity, dict):
            raise StateError("cancellation inventory contains a malformed identity")
        pid = identity.get("pid")
        start_ticks = identity.get("start_ticks")
        if type(pid) is not int or type(start_ticks) is not int:
            raise StateError("cancellation inventory contains an invalid identity")
        merged[(pid, start_ticks)] = {"pid": pid, "start_ticks": start_ticks}
    # Descendants are already discovered deepest-first. Stored reparented survivors have no
    # trustworthy current depth, so signal them before the registered owner below.
    return list(merged.values())


def cancel_revision(
    doc_raw: str,
    expected_artifact_sha: str,
    reason: str,
    term_timeout_s: int,
    kill_timeout_s: int,
) -> None:
    doc = canonical_doc(doc_raw)
    if (
        len(expected_artifact_sha) != 64
        or any(char not in "0123456789abcdef" for char in expected_artifact_sha)
    ):
        raise UsageError("--expected-artifact-sha must be 64 lowercase hex characters")
    if not reason.strip():
        raise UsageError("--reason must be non-empty")
    if not 1 <= term_timeout_s <= 5:
        raise UsageError("--term-timeout-s must be in 1..5")
    if not 1 <= kill_timeout_s <= 2:
        raise UsageError("--kill-timeout-s must be in 1..2")

    with document_lock(doc):
        ledger = load_ledger(doc, create=False)
        launches = [
            launch
            for campaign in ledger["campaigns"]  # type: ignore[index]
            if isinstance(campaign, dict)
            for launch in campaign.get("launches", [])
            if isinstance(launch, dict)
        ]
        terminal = [
            launch
            for launch in launches
            if launch.get("status") == "superseded-by-requirement-revision"
            and cancellation_matches(launch, expected_artifact_sha)
        ]
        if len(terminal) == 1:
            print(
                "CAMPAIGN CANCELLATION CONFIRMED: "
                f"CLAIM_ID={terminal[0].get('claim_id')} status="
                "superseded-by-requirement-revision"
            )
            return
        candidates = [
            launch
            for launch in launches
            if launch.get("status") in {"running", "cancellation_in_progress"}
            and launch.get("artifact_sha") == expected_artifact_sha
        ]
        if len(candidates) != 1:
            raise TransitionError(
                f"expected exactly one live claim for artifact, found {len(candidates)}"
            )
        launch = candidates[0]
        owner = launch.get("owner")
        if launch.get("status") == "running":
            inventory = owned_processes(owner) if isinstance(owner, dict) else []
            launch["status"] = "cancellation_in_progress"
            launch["cancellation"] = {
                "expected_artifact_sha": expected_artifact_sha,
                "reason": reason.strip(),
                "requested_at": now(),
                "term_timeout_s": term_timeout_s,
                "kill_timeout_s": kill_timeout_s,
                "inventory": [
                    {
                        "pid": identity["pid"],
                        "start_ticks": identity["start_ticks"],
                    }
                    for identity in inventory
                ],
                "cleanup": "pending",
                "cleanup_detail": "",
            }
            revoke_plateau_markers(doc)
        else:
            cancellation = launch.get("cancellation")
            assert isinstance(cancellation, dict)
            if cancellation.get("expected_artifact_sha") != expected_artifact_sha:
                raise TransitionError("cancellation artifact identity changed")
            inventory = merge_inventory(
                list(cancellation.get("inventory", [])),
                owned_processes(owner) if isinstance(owner, dict) else [],
            )
            cancellation["inventory"] = inventory
        cancellation = launch["cancellation"]
        assert isinstance(cancellation, dict)
        atomic_json(ledger_path(doc), ledger)
        claim_id = str(launch.get("claim_id"))

    inventory = merge_inventory(
        list(cancellation["inventory"]),
        owned_processes(owner) if isinstance(owner, dict) else [],
    )
    owner_key = None
    if isinstance(owner, dict):
        owner_key = (owner.get("pid"), owner.get("start_ticks"))
    inventory = [
        identity
        for identity in inventory
        if (identity.get("pid"), identity.get("start_ticks")) != owner_key
    ] + [
        identity
        for identity in inventory
        if (identity.get("pid"), identity.get("start_ticks")) == owner_key
    ]
    for identity in inventory:
        signal_identity(identity, signal.SIGTERM)
    survivors = wait_for_exit(inventory, term_timeout_s)
    for identity in survivors:
        signal_identity(identity, signal.SIGKILL)
    survivors = wait_for_exit(survivors, kill_timeout_s)
    lock_available = review_lock_available(doc)

    with document_lock(doc):
        ledger = load_ledger(doc, create=False)
        matches = [
            launch
            for campaign in ledger["campaigns"]  # type: ignore[index]
            if isinstance(campaign, dict)
            for launch in campaign.get("launches", [])
            if isinstance(launch, dict) and launch.get("claim_id") == claim_id
        ]
        if len(matches) != 1:
            raise StateError(f"cancelled claim_id resolves to {len(matches)} launches")
        launch = matches[0]
        if launch.get("status") == "superseded-by-requirement-revision":
            print(
                "CAMPAIGN CANCELLATION CONFIRMED: "
                f"CLAIM_ID={claim_id} status=superseded-by-requirement-revision"
            )
            return
        if launch.get("status") != "cancellation_in_progress":
            raise StateError(f"cancelled claim changed to incompatible status {launch.get('status')}")
        cancellation = launch.get("cancellation")
        assert isinstance(cancellation, dict)
        cancellation["inventory"] = inventory
        if survivors or not lock_available or not isinstance(owner, dict):
            reasons = []
            if not isinstance(owner, dict):
                reasons.append("claim has no verified owner identity")
            if survivors:
                reasons.append(
                    "matching survivors remain: "
                    + ",".join(str(identity["pid"]) for identity in survivors)
                )
            if not lock_available:
                reasons.append("canonical review lock remains held")
            cancellation["cleanup"] = "blocked"
            cancellation["cleanup_detail"] = "; ".join(reasons)
            atomic_json(ledger_path(doc), ledger)
            raise CancellationBlocked(str(cancellation["cleanup_detail"]))
        cancellation["cleanup"] = "complete"
        cancellation["cleanup_detail"] = ""
        cancellation["completed_at"] = now()
        launch["status"] = "superseded-by-requirement-revision"
        launch["finished_at"] = cancellation["completed_at"]
        atomic_json(ledger_path(doc), ledger)
    print(
        "CAMPAIGN CANCELLED: "
        f"CLAIM_ID={claim_id} status=superseded-by-requirement-revision"
    )


def start_new(doc_raw: str, operator: str, reason: str) -> None:
    doc = canonical_doc(doc_raw)
    if os.environ.get("MAGI_TEST_ALLOW_NEW_CAMPAIGN") != "1":
        raise UsageError("new-campaign is disabled outside deterministic test fixtures")
    if not operator.strip() or not reason.strip():
        raise UsageError("--operator and --reason must be non-empty")
    with document_lock(doc):
        ledger = load_ledger(doc, create=False)
        campaigns = ledger["campaigns"]
        assert isinstance(campaigns, list)
        campaign = new_campaign(operator=operator.strip(), reason=reason.strip())
        campaigns.append(campaign)
        atomic_json(ledger_path(doc), ledger)
    print(f"NEW CAMPAIGN AUTHORIZED: {campaign['campaign_id']} -> {ledger_path(doc)}")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Own a bounded dual-magi campaign launch ledger")
    commands = root.add_subparsers(dest="command", required=True)
    claim_parser = commands.add_parser("claim")
    claim_parser.add_argument("doc")
    claim_parser.add_argument("round")
    claim_parser.add_argument("phase", choices=("fanout", "targeted", "xfamily"))
    claim_parser.add_argument("state_dir")
    claim_parser.add_argument("--owner-pid", type=int)
    claim_parser.add_argument("--adapter-kind", choices=("fanout", "targeted", "xfamily"))
    claim_parser.add_argument("--expected-artifact-sha")
    claim_parser.add_argument("--reviewer-family", choices=("claude", "grok"))
    finish_parser = commands.add_parser("finish")
    finish_parser.add_argument("doc")
    finish_parser.add_argument("claim_id")
    finish_parser.add_argument("status", choices=("success", "failed"))
    recover_parser = commands.add_parser("recover-startup")
    recover_parser.add_argument("doc")
    recover_parser.add_argument("claim_id")
    recover_parser.add_argument("evidence")
    historical_parser = commands.add_parser("repair-historical-startup")
    historical_parser.add_argument("doc")
    historical_parser.add_argument("claim_id")
    status_parser = commands.add_parser("claim-status")
    status_parser.add_argument("doc")
    status_parser.add_argument("claim_id")
    cancel_parser = commands.add_parser("cancel-revision")
    cancel_parser.add_argument("doc")
    cancel_parser.add_argument("--expected-artifact-sha", required=True)
    cancel_parser.add_argument("--reason", required=True)
    cancel_parser.add_argument("--term-timeout-s", type=int, default=5)
    cancel_parser.add_argument("--kill-timeout-s", type=int, default=2)
    new_parser = commands.add_parser("new-campaign")
    new_parser.add_argument("doc")
    new_parser.add_argument("--operator", required=True)
    new_parser.add_argument("--reason", required=True)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "claim":
            claim(
                args.doc,
                args.round,
                args.phase,
                args.state_dir,
                args.owner_pid,
                args.adapter_kind,
                args.expected_artifact_sha,
                args.reviewer_family,
            )
        elif args.command == "finish":
            finish(args.doc, args.claim_id, args.status)
        elif args.command == "recover-startup":
            recover_startup(args.doc, args.claim_id, args.evidence)
        elif args.command == "repair-historical-startup":
            repair_historical_startup(args.doc, args.claim_id)
        elif args.command == "claim-status":
            claim_status(args.doc, args.claim_id)
        elif args.command == "cancel-revision":
            cancel_revision(
                args.doc,
                args.expected_artifact_sha,
                args.reason,
                args.term_timeout_s,
                args.kill_timeout_s,
            )
        else:
            start_new(args.doc, args.operator, args.reason)
    except UsageError as exc:
        print(f"MAGI_USAGE_ERROR: {exc}", file=sys.stderr)
        return 64
    except TransitionError as exc:
        print(f"MAGI_TRANSITION_ERROR: {exc}", file=sys.stderr)
        return 64
    except StateError as exc:
        print(f"MAGI_STATE_CORRUPTION — FAIL CLOSED: {exc}", file=sys.stderr)
        return 2
    except CancellationBlocked as exc:
        print(f"REQUIREMENT_REVISION_CLEANUP_BLOCKED: {exc}", file=sys.stderr)
        return 2
    except BudgetDenied as exc:
        print(
            "CAMPAIGN BUDGET EXHAUSTED — NOT PLATEAU\n"
            f"MAGI_BUDGET_EXHAUSTED: {exc}\n"
            "autonomous decision required: reduce scope, replace the primitive, or emit a "
            "definitive blocked result; do not pause for acknowledgement",
            file=sys.stderr,
        )
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
