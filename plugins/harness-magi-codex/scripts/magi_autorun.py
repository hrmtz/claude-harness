#!/usr/bin/env python3
"""Opt-in Stop-hook controller that keeps a Magi campaign moving without user acknowledgement."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


MAX_NO_PROGRESS_STOPS = 2
DEFAULT_GLOBAL_CEILING = 16
PHASE_WEIGHT = {"fanout": 3, "xfamily": 1}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def document(raw: str) -> Path:
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"document not found: {path}")
    return path


def document_id(doc: Path) -> str:
    return hashlib.sha256(str(doc).encode()).hexdigest()[:16]


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def state_root() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    base = Path(configured).expanduser() if configured else Path.home() / ".local" / "state"
    root = base / "harness-magi-codex" / "autorun"
    root.mkdir(parents=True, exist_ok=True)
    return root


def registry_path(session_id: str) -> Path:
    safe = hashlib.sha256(session_id.encode()).hexdigest()[:24]
    return state_root() / f"{safe}.json"


def marker_path(doc: Path) -> Path:
    control = doc.parent / ".dual-magi"
    control.mkdir(parents=True, exist_ok=True)
    return control / f"AUTORUN.{document_id(doc)}.json"


def atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def persist(payload: dict[str, object]) -> None:
    session_id = str(payload["owner_session"])
    doc = Path(str(payload["doc_path"]))
    payload["updated_at"] = now()
    atomic_json(registry_path(session_id), payload)
    atomic_json(marker_path(doc), payload)


def arm(doc_raw: str, session_override: str | None = None) -> None:
    doc = document(doc_raw)
    session_id = session_override or os.environ.get("CODEX_THREAD_ID", "")
    if not session_id:
        raise ValueError("CODEX_THREAD_ID is required to bind autorun to one Codex session")
    path = registry_path(session_id)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("status") == "active" and existing.get("doc_path") != str(doc):
            raise ValueError("this Codex session already owns another active Magi campaign")
    payload: dict[str, object] = {
        "schema_version": 1,
        "owner_session": session_id,
        "doc_id": document_id(doc),
        "doc_path": str(doc),
        "status": "active",
        "reason": "",
        "started_at": now(),
        "updated_at": now(),
        "last_fingerprint": "",
        "no_progress_stops": 0,
    }
    persist(payload)
    print(f"MAGI AUTORUN ARMED: {doc} session={session_id}")


def load_registry(session_id: str) -> dict[str, object] | None:
    path = registry_path(session_id)
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema_version", "owner_session", "doc_id", "doc_path", "status", "reason",
        "started_at", "updated_at", "last_fingerprint", "no_progress_stops",
    }
    if set(payload) != required or payload.get("schema_version") != 1:
        raise ValueError("autorun registry is malformed")
    return payload


def set_terminal(doc_raw: str, status: str, reason: str, session_override: str | None) -> None:
    doc = document(doc_raw)
    session_id = session_override or os.environ.get("CODEX_THREAD_ID", "")
    if not session_id:
        raise ValueError("CODEX_THREAD_ID is required")
    payload = load_registry(session_id)
    if payload is None or payload.get("doc_path") != str(doc):
        raise ValueError("no matching autorun campaign is armed")
    if status == "complete" and not plateau_exists(doc):
        raise ValueError("complete requires an exact-revision plateau marker")
    payload["status"] = status
    payload["reason"] = reason
    persist(payload)
    print(f"MAGI AUTORUN {status.upper()}: {doc}: {reason}")


def plateau_exists(doc: Path) -> bool:
    prefix = file_sha(doc)[:16]
    return (doc.parent / ".dual-magi" / f"PLATEAU.{document_id(doc)}.{prefix}").is_file()


def ledger_state(doc: Path) -> tuple[str, int, int]:
    ledger = doc.parent / ".dual-magi" / f"CAMPAIGN.{document_id(doc)}.json"
    if not ledger.is_file():
        return "no-ledger", 0, 3
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    launches = [
        launch
        for campaign in payload.get("campaigns", [])
        for launch in campaign.get("launches", [])
        if isinstance(launch, dict)
    ]
    used = sum(
        launch.get("model_launches", PHASE_WEIGHT.get(str(launch.get("phase")), 0))
        for launch in launches
    )
    if not launches:
        next_weight = 3
    else:
        last = launches[-1]
        if last.get("status") in {"failed", "abandoned", "running"}:
            next_weight = PHASE_WEIGHT.get(str(last.get("phase")), 3)
        else:
            next_weight = 1 if last.get("phase") == "fanout" else 3
    return file_sha(ledger), int(used), next_weight


def hook() -> int:
    try:
        hook_input = json.load(sys.stdin)
        session_id = str(hook_input.get("session_id") or "")
        if not session_id:
            return 0
        payload = load_registry(session_id)
        if payload is None or payload.get("status") != "active":
            return 0
        doc = document(str(payload["doc_path"]))
        if plateau_exists(doc):
            payload["status"] = "complete"
            payload["reason"] = "exact-revision mechanical plateau gate passed"
            persist(payload)
            print(json.dumps({"systemMessage": "Magi autorun complete: exact-revision plateau passed."}))
            return 0
        ledger_sha, used, next_weight = ledger_state(doc)
        ceiling_raw = os.environ.get("MAGI_MAX_AUTONOMOUS_MODEL_LAUNCHES", "16")
        ceiling = min(DEFAULT_GLOBAL_CEILING, max(1, int(ceiling_raw)))
        if used + next_weight > ceiling:
            payload["status"] = "blocked"
            payload["reason"] = (
                f"fixed global fuse cannot fund the next phase: {used}+{next_weight}>{ceiling}"
            )
            persist(payload)
            print(json.dumps({"systemMessage": "Magi autorun reached a definitive NOT PLATEAU blocked state; no acknowledgement is requested."}))
            return 0
        fingerprint = hashlib.sha256(
            f"{file_sha(doc)}:{ledger_sha}:{used}:{next_weight}".encode()
        ).hexdigest()
        if payload.get("last_fingerprint") == fingerprint:
            payload["no_progress_stops"] = int(payload.get("no_progress_stops", 0)) + 1
        else:
            payload["last_fingerprint"] = fingerprint
            payload["no_progress_stops"] = 0
        if int(payload["no_progress_stops"]) >= MAX_NO_PROGRESS_STOPS:
            payload["status"] = "blocked"
            payload["reason"] = "orchestrator made no durable campaign progress across two continuations"
            persist(payload)
            print(json.dumps({"systemMessage": "Magi autorun stopped in a definitive no-progress blocked state; no acknowledgement is requested."}))
            return 0
        persist(payload)
        reason = (
            f"Magi autorun is active for {doc}. Continue the campaign now without asking for user "
            "acknowledgement: inspect durable state, apply in-scope fixes, run the next legal phase, "
            "and stop only after exact-revision PLATEAU or a definitive fixed-fuse BLOCKED state."
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        # Stop hooks must fail open. The campaign guard still prevents unbounded provider spend.
        return 0
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hook", action="store_true")
    commands = parser.add_subparsers(dest="command")
    arm_parser = commands.add_parser("arm")
    arm_parser.add_argument("doc")
    arm_parser.add_argument("--session")
    for name in ("complete", "blocked"):
        terminal = commands.add_parser(name)
        terminal.add_argument("doc")
        terminal.add_argument("--reason", required=True)
        terminal.add_argument("--session")
    args = parser.parse_args()
    try:
        if args.hook:
            return hook()
        if args.command == "arm":
            arm(args.doc, args.session)
        elif args.command in {"complete", "blocked"}:
            set_terminal(args.doc, args.command, args.reason, args.session)
        else:
            parser.error("a command or --hook is required")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"magi-autorun: {exc}", file=sys.stderr)
        return 64
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
