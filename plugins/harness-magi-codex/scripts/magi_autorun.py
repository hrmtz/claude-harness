#!/usr/bin/env python3
"""Opt-in Stop-hook controller that keeps a Magi campaign moving without user acknowledgement."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from magi_protocol import protocol_sha, sha256_file, strict_json_loads

from magi_campaign_guard import StateError, campaign_admission_status


MAX_NO_PROGRESS_STOPS = 2
MAX_MARKER_BYTES = 64 * 1024
MAX_REGISTRY_BYTES = 128 * 1024


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def document(raw: str) -> Path:
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"document not found: {path}")
    return path


def document_id(doc: Path) -> str:
    return hashlib.sha256(os.fsencode(doc)).hexdigest()[:16]


def file_sha(path: Path) -> str:
    return sha256_file(path)


def state_root() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    base = Path(configured).expanduser() if configured else Path.home() / ".local" / "state"
    root = base / "harness-magi-codex" / "autorun"
    root.mkdir(parents=True, exist_ok=True)
    return root


def registry_path(session_id: str) -> Path:
    safe = hashlib.sha256(session_id.encode()).hexdigest()[:24]
    return state_root() / f"{safe}.json"


def acquire_registry_lock(session_id: str):
    handle = registry_path(session_id).with_suffix(".lock").open("a+")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


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
    registry_lock = acquire_registry_lock(session_id)
    try:
        existing = load_registry(session_id)
        if existing is not None:
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
            "completed_artifact_sha": "",
        }
        persist(payload)
    finally:
        registry_lock.close()
    print(f"MAGI AUTORUN ARMED: {doc} session={session_id}")


def load_registry(session_id: str) -> dict[str, object] | None:
    path = registry_path(session_id)
    if not os.path.lexists(path):
        return None
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("autorun registry must be a regular file")
    if info.st_size > MAX_REGISTRY_BYTES:
        raise ValueError("autorun registry exceeds the size limit")
    payload = strict_json_loads(path.read_bytes())
    if not isinstance(payload, dict):
        raise ValueError("autorun registry must be a JSON object")
    required = {
        "schema_version", "owner_session", "doc_id", "doc_path", "status", "reason",
        "started_at", "updated_at", "last_fingerprint", "no_progress_stops",
        "completed_artifact_sha",
    }
    legacy_required = required - {"completed_artifact_sha"}
    if set(payload) == legacy_required and payload.get("schema_version") == 1:
        payload["completed_artifact_sha"] = ""
    elif set(payload) != required or payload.get("schema_version") != 1:
        raise ValueError("autorun registry is malformed")
    return payload


def set_terminal(doc_raw: str, status: str, reason: str, session_override: str | None) -> None:
    doc = document(doc_raw)
    session_id = session_override or os.environ.get("CODEX_THREAD_ID", "")
    if not session_id:
        raise ValueError("CODEX_THREAD_ID is required")
    with acquire_registry_lock(session_id):
        payload = load_registry(session_id)
        if payload is None or payload.get("doc_path") != str(doc):
            raise ValueError("no matching autorun campaign is armed")
        if status == "complete":
            marker_status, detail, artifact_sha = plateau_status(doc)
            if marker_status != "VALID":
                raise ValueError(
                    f"complete requires a valid exact-revision plateau marker ({detail})"
                )
            payload["completed_artifact_sha"] = artifact_sha
        payload["status"] = status
        payload["reason"] = reason
        persist(payload)
        if status == "complete" and file_sha(doc) != payload["completed_artifact_sha"]:
            payload["status"] = "blocked"
            payload["reason"] = "document changed while committing exact-revision completion"
            persist(payload)
            print(f"MAGI AUTORUN BLOCKED: {doc}: {payload['reason']}")
            return
        print(f"MAGI AUTORUN {status.upper()}: {doc}: {reason}")


def plateau_status(doc: Path) -> tuple[str, str, str]:
    initial_sha = file_sha(doc)
    prefix = initial_sha[:16]
    marker = doc.parent / ".dual-magi" / f"PLATEAU.{document_id(doc)}.{prefix}"
    if not os.path.lexists(marker):
        return "ABSENT", "exact-revision marker is absent", ""
    try:
        marker_info = marker.lstat()
        if not stat.S_ISREG(marker_info.st_mode):
            return "INVALID", "marker is not a regular file", ""
        if marker_info.st_size > MAX_MARKER_BYTES:
            return "INVALID", "marker exceeds the size limit", ""
        payload = strict_json_loads(marker.read_bytes())
        current_protocol = protocol_sha()
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return "INVALID", "marker JSON is malformed", ""
    except OSError as exc:
        return "INVALID", f"marker/protocol I/O failed ({type(exc).__name__})", ""
    except (RuntimeError, ValueError) as exc:
        detail = str(exc).replace("\n", " ")[:240]
        return (
            "INVALID",
            f"protocol validation failed ({type(exc).__name__}): {detail}",
            "",
        )
    if not isinstance(payload, dict):
        return "INVALID", "marker payload is not an object", ""
    if payload.get("artifact_sha") != initial_sha or file_sha(doc) != initial_sha:
        return "INVALID", "marker artifact identity does not match", ""
    if payload.get("protocol_sha") != current_protocol:
        return "INVALID", "marker protocol identity does not match", ""
    if payload.get("reviewer_family") not in {"claude", "grok"}:
        return "INVALID", "marker reviewer family is unsupported", ""
    if payload.get("asserts_passed") != [
        "G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9"
    ]:
        return "INVALID", "marker G1-G9 assertion set is incomplete", ""
    return "VALID", "exact-revision marker is valid", initial_sha


def plateau_exists(doc: Path) -> bool:
    return plateau_status(doc)[0] == "VALID"


def hook() -> int:
    try:
        hook_input = json.load(sys.stdin)
        if not isinstance(hook_input, dict):
            print(
                json.dumps(
                    {
                        "decision": "block",
                        "reason": "Magi autorun hook input is malformed: expected an object.",
                    }
                )
            )
            return 0
        raw_session_id = hook_input.get("session_id")
        if not isinstance(raw_session_id, str) or not raw_session_id.strip():
            print(
                json.dumps(
                    {
                        "decision": "block",
                        "reason": (
                            "Magi autorun hook input is malformed: "
                            "session_id must be a non-empty string."
                        ),
                    }
                )
            )
            return 0
        session_id = raw_session_id
        hook_lock = acquire_registry_lock(session_id)
        payload = load_registry(session_id)
        if payload is None:
            return 0
        doc = document(str(payload["doc_path"]))
        if payload.get("status") == "complete":
            if payload.get("completed_artifact_sha") == file_sha(doc):
                return 0
            payload["status"] = "blocked"
            payload["reason"] = "document changed after exact-revision completion"
            persist(payload)
            print(
                json.dumps(
                    {
                        "decision": "block",
                        "reason": "Magi autorun blocked: completed artifact changed.",
                    }
                )
            )
            return 0
        if payload.get("status") != "active":
            return 0
        marker_status, marker_detail, artifact_sha = plateau_status(doc)
        if marker_status == "INVALID":
            payload["status"] = "blocked"
            payload["reason"] = f"plateau boundary invalid: {marker_detail}"
            persist(payload)
            print(
                json.dumps(
                    {
                        "decision": "block",
                        "reason": (
                            "Magi autorun blocked: plateau marker or protocol validation failed; "
                            f"no plateau was accepted ({marker_detail})."
                        )
                    }
                )
            )
            return 0
        if marker_status == "VALID":
            if file_sha(doc) != artifact_sha:
                payload["status"] = "blocked"
                payload["reason"] = "document changed while confirming exact-revision plateau"
                persist(payload)
                print(
                    json.dumps(
                        {
                            "decision": "block",
                            "reason": f"Magi autorun blocked: {payload['reason']}.",
                        }
                    )
                )
                return 0
            payload["status"] = "complete"
            payload["reason"] = "exact-revision mechanical plateau gate passed"
            payload["completed_artifact_sha"] = artifact_sha
            persist(payload)
            if file_sha(doc) != artifact_sha:
                payload["status"] = "blocked"
                payload["reason"] = "document changed while committing exact-revision completion"
                persist(payload)
                print(
                    json.dumps(
                        {
                            "decision": "block",
                            "reason": f"Magi autorun blocked: {payload['reason']}.",
                        }
                    )
                )
                return 0
            print(json.dumps({"systemMessage": "Magi autorun complete: exact-revision plateau passed."}))
            return 0
        campaign = campaign_admission_status(doc)
        ledger_sha = str(campaign["ledger_sha"])
        used = int(campaign["used"])
        if campaign["kind"] in {"budget-blocked", "transition-blocked"}:
            payload["status"] = "blocked"
            payload["reason"] = str(campaign["reason"])
            persist(payload)
            print(json.dumps({"systemMessage": "Magi autorun reached a definitive NOT PLATEAU blocked state; no acknowledgement is requested."}))
            return 0
        fingerprint = hashlib.sha256(
            (
                f"{file_sha(doc)}:{ledger_sha}:{used}:{campaign['kind']}:"
                f"{campaign.get('round')}:{campaign.get('phase')}:{campaign.get('attempt')}"
            ).encode()
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
    except (OSError, ValueError, TypeError, RecursionError, json.JSONDecodeError, StateError) as exc:
        # Hook protocol keeps exit 0, but campaign-state corruption must remain visible and
        # fail-closed instead of looking like an ordinary completed Stop event.
        detail = str(exc).replace("\n", " ")[:240]
        reason = (
            f"Magi autorun blocked at a hook boundary: {type(exc).__name__}: {detail}. "
            "Inspect the campaign ledger and autorun registry."
        )
        if "payload" in locals() and isinstance(payload, dict):
            payload["status"] = "blocked"
            payload["reason"] = reason
            try:
                persist(payload)
            except (OSError, ValueError, TypeError) as persist_exc:
                reason += (
                    " Blocked-state persistence failed: "
                    f"{type(persist_exc).__name__}."
                )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0
    finally:
        if "hook_lock" in locals():
            hook_lock.close()
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
