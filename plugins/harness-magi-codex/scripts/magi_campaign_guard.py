#!/usr/bin/env python3
"""Own a canonical, bounded launch ledger for dual-magi campaigns."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import signal
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


DEFAULT_MAX_MODEL_LAUNCHES = 16
PHASE_WEIGHT = {"fanout": 3, "targeted": 1, "xfamily": 1}
FINAL_XFAMILY_RESERVE = PHASE_WEIGHT["xfamily"]
GLOBAL_MAX_MODEL_LAUNCHES = 16
TERMINAL_STATUSES = {
    "success",
    "failed",
    "abandoned",
    "superseded-by-requirement-revision",
}
NONTERMINAL_STATUSES = {"running", "cancellation_in_progress"}
VALID_STATUSES = TERMINAL_STATUSES | NONTERMINAL_STATUSES
PROTOCOL_FILES = (
    "schemas/finding.schema.json",
    "schemas/implementation-convergence.schema.json",
    "scripts/magi_campaign_guard.py",
    "scripts/magi_convergence_gate.py",
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
    return hashlib.sha256(str(doc).encode()).hexdigest()[:16]


def file_sha(doc: Path) -> str:
    return hashlib.sha256(doc.read_bytes()).hexdigest()


def protocol_sha() -> str:
    root = Path(__file__).resolve().parent.parent
    digest = hashlib.sha256()
    for relative_path in PROTOCOL_FILES:
        path = root / relative_path
        digest.update(relative_path.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


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
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
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
        if not isinstance(campaign, dict) or not isinstance(campaign.get("launches"), list):
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
    return payload


def active_campaign(ledger: dict[str, object]) -> dict[str, object]:
    campaign = ledger["campaigns"][-1]  # type: ignore[index]
    if not isinstance(campaign, dict):
        raise StateError("active campaign is malformed")
    expected = {"campaign_id", "started_at", "started_by", "reason", "launches"}
    if set(campaign) != expected or not isinstance(campaign.get("launches"), list):
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
    if status in {"failed", "abandoned"}:
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


def admission_decision(total_used: int, ceiling: int, phase: str) -> dict[str, object]:
    weight = PHASE_WEIGHT[phase]
    reserve = FINAL_XFAMILY_RESERVE if phase in {"fanout", "targeted"} else 0
    required = weight + reserve
    affordable = total_used + required <= ceiling
    reason = (
        f"global campaign history would require {total_used + required}/{ceiling} "
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


def model_launches(campaigns: list[object]) -> int:
    return sum(
        launch.get("model_launches", PHASE_WEIGHT.get(str(launch.get("phase")), 0))
        for campaign in campaigns
        if isinstance(campaign, dict)
        for launch in campaign.get("launches", [])
        if isinstance(launch, dict)
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
        ceiling = min(GLOBAL_MAX_MODEL_LAUNCHES, base_ceiling())
        last = launches[-1] if launches else None
        if (
            transition["kind"] == "transition-blocked"
            and isinstance(last, dict)
            and last.get("status") == "superseded-by-requirement-revision"
            and may_rollover(ledger, campaign, doc, 1, "fanout")
        ):
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
                "ceiling": ceiling,
            }
        admission = admission_decision(total_used, ceiling, str(transition["phase"]))
        return {
            **transition,
            **admission,
            "kind": "candidate" if admission["affordable"] else "budget-blocked",
            "ledger_sha": ledger_sha,
            "used": total_used,
            "ceiling": ceiling,
        }


def may_rollover(
    ledger: dict[str, object], campaign: dict[str, object], doc: Path, round_no: int, phase: str
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
        return phase == "fanout" and last.get("artifact_sha") != file_sha(doc)
    return (
        last.get("artifact_sha") != file_sha(doc)
        or last.get("protocol_sha") != protocol_sha()
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
        if expected_artifact_sha is not None and expected_artifact_sha != file_sha(doc):
            raise TransitionError("claim artifact changed after its authorization decision")
        ledger = load_ledger(doc, create=True)
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
        try:
            attempt = validate_transition(launches, round_no, phase)
        except TransitionError as exc:
            transition_error = exc
        if transition_error is not None:
            if not may_rollover(ledger, campaign, doc, round_no, phase):
                raise transition_error
            campaign = new_campaign(
                operator="automatic-rollover",
                reason="document or review protocol changed after prior campaign attempt",
            )
            launches = campaign["launches"]
            assert isinstance(launches, list)
            attempt = 1
            planned_rollover = True
        configured_ceiling = base_ceiling()
        total_used = model_launches(campaigns)
        global_ceiling = min(GLOBAL_MAX_MODEL_LAUNCHES, configured_ceiling)
        admission = admission_decision(total_used, global_ceiling, phase)
        if not admission["affordable"]:
            raise BudgetDenied(str(admission["reason"]))
        weight = int(admission["weight"])
        if planned_rollover:
            campaigns.append(campaign)
        claim_id = str(uuid.uuid4())
        launch_payload = {
            "claim_id": claim_id,
            "sequence": len(launches) + 1,
            "round": round_no,
            "phase": phase,
            "attempt": attempt,
            "model_launches": weight,
            "state_dir": str(state),
            "artifact_sha": file_sha(doc),
            "protocol_sha": protocol_sha(),
            "claimed_at": now(),
            "status": "running",
        }
        if owner is not None:
            launch_payload["owner"] = owner
        launches.append(launch_payload)
        atomic_json(ledger_path(doc), ledger)
    print(
        f"CAMPAIGN CLAIMED: {campaign['campaign_id']} global model launches "
        f"{total_used + weight}/{global_ceiling}, "
        f"round {round_no} {phase}, attempt {attempt}; CLAIM_ID={claim_id}"
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
