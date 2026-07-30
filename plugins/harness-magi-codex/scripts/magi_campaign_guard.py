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
FINAL_XFAMILY_RESERVE = PHASE_WEIGHT["xfamily"]
# Hard fuse across all revision campaigns for one target. Deliberately NOT cut:
# in the same corpus five campaigns were still surfacing new CRITICAL/HIGH at
# this boundary, and one shipped-blocking CRITICAL appeared at the 4th
# cross-family round. Depth stays available; it just has to be earned.
GLOBAL_MAX_MODEL_LAUNCHES = 16
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
        "doc_id": "5c8e85fd709b23cc",
        "source_claim_id": "0f0bd40b-f015-461d-8f44-fc7e47c4657a",
        "source_finished_at": "2026-07-30T01:52:24.251065+00:00",
        "artifact_sha": "f04d2112d4fa3b6c3c1e4dd7cff1fd94778954a7d242a73ab3673678486084a6",
        "source_protocol_sha": "26d2f729c8b1639e28f176622424608f7c5e1c99e413584cf1953201c3473171",
        "history_launch_count": 6,
        "history_gross_model_launches": 14,
        "history_prefix_sha256": "ab3881716d5efd2e359f069bb37a98e4321e0d554b54a3b74fd3316eea4ff770",
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
            owner = launch.get("owner")
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
                    or len(recovery_reviewers) != 3
                    or {
                        item.get("reviewer")
                        for item in recovery_reviewers
                        if isinstance(item, dict)
                    }
                    not in (
                        {"MELCHIOR", "BALTHASAR", "CASPAR"},
                        {"HORNET", "GNAT", "WASP"},
                    )
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
    if not isinstance(reviewers, list) or len(reviewers) != 3:
        raise TransitionError("startup evidence must cover exactly three reviewers")
    names = {item.get("reviewer") for item in reviewers if isinstance(item, dict)}
    if names not in (
        {"MELCHIOR", "BALTHASAR", "CASPAR"},
        {"HORNET", "GNAT", "WASP"},
    ):
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
        global_ceiling = GLOBAL_MAX_MODEL_LAUNCHES
        last = launches[-1] if launches else None
        current_artifact_sha = file_sha(doc)
        current_protocol_sha = protocol_sha()
        rollover_available = (
            isinstance(last, dict)
            and last.get("artifact_sha") != current_artifact_sha
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
            }
        campaign_used = 0 if rollover_available else model_launches([campaign])
        replacement = (
            None
            if rollover_available
            else replacement_source(launches, str(transition["phase"]))
        )
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
) -> None:
    doc = canonical_doc(doc_raw)
    round_no = positive_int(round_raw, "round")
    if (owner_pid is None) != (adapter_kind is None):
        raise UsageError("--owner-pid and --adapter-kind must be supplied together")
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
        campaign_ceiling = base_ceiling()
        campaign_used = model_launches([campaign])
        total_used = model_launches(campaigns)
        global_ceiling = GLOBAL_MAX_MODEL_LAUNCHES
        replacement = replacement_source(launches, phase)
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
        launches.append(launch_payload)
        atomic_json(ledger_path(doc), ledger)
    print(
        f"CAMPAIGN CLAIMED: {campaign['campaign_id']} global model launches "
        f"{total_used + charged_weight}/{global_ceiling}, "
        f"campaign model launches {campaign_used + charged_weight}/{campaign_ceiling}, "
        f"round {round_no} {phase}, attempt {attempt}; "
        f"PROTOCOL_SHA={claim_protocol_sha}; CLAIM_ID={claim_id}"
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
