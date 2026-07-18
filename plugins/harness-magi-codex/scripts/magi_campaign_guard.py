#!/usr/bin/env python3
"""Own a canonical, bounded launch ledger for dual-magi campaigns."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


DEFAULT_MAX_MODEL_LAUNCHES = 16
PHASE_WEIGHT = {"fanout": 3, "xfamily": 1}
GLOBAL_MAX_MODEL_LAUNCHES = 16


class UsageError(ValueError):
    """Invalid operator input (exit 64)."""


class BudgetDenied(RuntimeError):
    """Campaign may not launch another reviewer (exit 4)."""


class StateError(RuntimeError):
    """Canonical accounting state is unreadable or internally inconsistent (exit 2)."""


class TransitionError(ValueError):
    """The caller requested an illegal phase transition (exit 64)."""


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
    paths = [
        root / "schemas" / "finding.schema.json",
        root / "scripts" / "magi_campaign_guard.py",
        root / "scripts" / "magi_validate_findings.py",
        root / "scripts" / "magi_fanout_codex.sh",
        root / "scripts" / "magi_xfamily.sh",
    ]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
            if launch.get("status") not in {"running", "success", "failed", "abandoned"}:
                raise StateError("campaign launch has an invalid status")
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


def validate_transition(launches: list[object], round_no: int, phase: str) -> int:
    if not launches:
        if round_no != 1 or phase != "fanout":
            raise TransitionError("a campaign must start at round 1 fanout")
        return 1
    last = launches[-1]
    if not isinstance(last, dict):
        raise StateError("campaign launch ledger contains a malformed entry")
    last_round, last_phase = last.get("round"), last.get("phase")
    same_attempts = sum(
        1
        for launch in launches
        if isinstance(launch, dict)
        and launch.get("round") == round_no
        and launch.get("phase") == phase
    )
    if round_no == last_round and phase == last_phase:
        if last.get("status") == "success":
            raise TransitionError(
                f"round {round_no} {phase} already succeeded; retry would duplicate providers"
            )
        if last.get("status") not in {"failed", "abandoned"}:
            raise TransitionError(f"round {round_no} {phase} is not terminal")
        if same_attempts >= 2:
            raise TransitionError(
                f"retry budget exhausted for round {round_no} {phase}"
            )
        return same_attempts + 1
    if last.get("status") != "success":
        raise TransitionError(
            f"round {last_round} {last_phase} did not succeed; next phase cannot start"
        )
    expected_phase = "xfamily" if last_phase == "fanout" else "fanout"
    if round_no != last_round + 1 or phase != expected_phase:
        raise TransitionError(
            f"illegal campaign transition: after round {last_round} {last_phase}, expected "
            f"round {last_round + 1} {expected_phase}"
        )
    return 1


def model_launches(campaigns: list[object]) -> int:
    return sum(
        launch.get("model_launches", PHASE_WEIGHT.get(str(launch.get("phase")), 0))
        for campaign in campaigns
        if isinstance(campaign, dict)
        for launch in campaign.get("launches", [])
        if isinstance(launch, dict)
    )


def may_rollover(
    ledger: dict[str, object], campaign: dict[str, object], doc: Path, round_no: int, phase: str
) -> bool:
    campaigns = ledger["campaigns"]
    assert isinstance(campaigns, list)
    launches = campaign["launches"]
    assert isinstance(launches, list)
    if round_no != 1 or phase != "fanout" or not launches:
        return False
    last = launches[-1]
    if not isinstance(last, dict):
        return False
    return (
        last.get("artifact_sha") != file_sha(doc)
        or last.get("protocol_sha") != protocol_sha()
    )


def claim(doc_raw: str, round_raw: str, phase: str, state_raw: str) -> None:
    doc = canonical_doc(doc_raw)
    round_no = positive_int(round_raw, "round")
    state = Path(state_raw).expanduser().resolve()
    state.mkdir(parents=True, exist_ok=True)
    with document_lock(doc):
        ledger = load_ledger(doc, create=True)
        campaign = active_campaign(ledger)
        launches = campaign["launches"]
        assert isinstance(launches, list)
        if launches and isinstance(launches[-1], dict) and launches[-1].get("status") == "running":
            # Callers hold the canonical phase execution lock before claim. If a later caller owns
            # that lock, the previous running owner is gone and its conservative charge is retained.
            launches[-1]["status"] = "abandoned"
        campaigns = ledger["campaigns"]
        assert isinstance(campaigns, list)
        configured_ceiling = base_ceiling()
        weight = PHASE_WEIGHT[phase]
        total_used = model_launches(campaigns)
        global_ceiling = min(GLOBAL_MAX_MODEL_LAUNCHES, configured_ceiling)
        if total_used + weight > global_ceiling:
            raise BudgetDenied(
                f"global campaign history would use {total_used + weight}/"
                f"{global_ceiling} model launches"
            )
        transition_error = None
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
            campaigns.append(campaign)
            launches = campaign["launches"]
            assert isinstance(launches, list)
            attempt = 1
        claim_id = str(uuid.uuid4())
        launches.append(
            {
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
        )
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
        if launch.get("status") != "running":
            raise TransitionError(
                f"claim {claim_id} is already terminal with status {launch.get('status')!r}"
            )
        launch["status"] = status
        launch["finished_at"] = now()
        atomic_json(ledger_path(doc), ledger)
    print(f"CAMPAIGN FINISHED: CLAIM_ID={claim_id} status={status}")


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
    claim_parser.add_argument("phase", choices=("fanout", "xfamily"))
    claim_parser.add_argument("state_dir")
    finish_parser = commands.add_parser("finish")
    finish_parser.add_argument("doc")
    finish_parser.add_argument("claim_id")
    finish_parser.add_argument("status", choices=("success", "failed"))
    new_parser = commands.add_parser("new-campaign")
    new_parser.add_argument("doc")
    new_parser.add_argument("--operator", required=True)
    new_parser.add_argument("--reason", required=True)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "claim":
            claim(args.doc, args.round, args.phase, args.state_dir)
        elif args.command == "finish":
            finish(args.doc, args.claim_id, args.status)
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
