#!/usr/bin/env python3
"""Build a deterministic, read-only-derived Deja Review Slice 0 corpus.

Linux-only v1: source and state safety rely on O_NOFOLLOW, O_DIRECTORY,
descriptor-relative mutation, flock, and /proc process identity.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
import re
import resource
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
SOURCE_SCHEMA_PATH = ROOT / "schemas" / "finding.schema.json"
RECORD_SCHEMA_PATH = ROOT / "schemas" / "deja-review-slice0-record.schema.json"
MANIFEST_SCHEMA_PATH = ROOT / "schemas" / "deja-review-slice0-manifest.schema.json"

SCHEMA_VERSION = "deja-review-slice0-record/v1"
CAMPAIGN_SCHEMA_VERSION = "deja-review-slice0-campaign/v1"
NORMALIZER_VERSION = "deja-review-slice0-normalizer/v1"
CATEGORY_VERSION = "magi-category-v1"
TRUST = "untrusted-review-content"
WATCHDOG_SIGNAL = signal.SIGUSR1
CAMPAIGN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
HEX64_RE = re.compile(r"^[a-f0-9]{64}$")

MAX_FILES = 256
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_FINDINGS = 1024
MAX_TEXT_BYTES = 64 * 1024
MAX_REVIEWER_BYTES = 4 * 1024
MAX_ARTIFACT_TEXT_BYTES = 8 * 1024 * 1024
STALL_SECONDS = 120
DEADLINE_SECONDS = 900
HEARTBEAT_IO_TIMEOUT_SECONDS = 10
HEARTBEAT_REAP_TIMEOUT_SECONDS = 1
RESOURCE_MARGIN = 1.5
PROGRESS_REASON_CODES = frozenset(
    {
        None,
        "campaign-lock-held",
        "campaign-not-found",
        "campaign-path-race",
        "disk-preflight-failed",
        "duplicate-occurrence",
        "duplicate-source-content",
        "exact-reuse",
        "file-byte-limit",
        "file-count-limit",
        "finding-count-limit",
        "foundation-deadline-exceeded",
        "heartbeat-watchdog-failed",
        "heartbeat-write-failed",
        "immutable-input-mismatch",
        "invalid-heartbeat",
        "invalid-input",
        "invalid-output",
        "invalid-owner",
        "invalid-progress",
        "memory-preflight-failed",
        "memory-preflight-unavailable",
        "operation-failed",
        "owner-identity-unavailable",
        "owner-still-live",
        "owner-unverifiable",
        "post-publish-recovery",
        "progress-too-large",
        "receipt-path-race",
        "retained-state-scan-failed",
        "snapshot-race",
        "snapshot-write-failed",
        "source-race",
        "stale-lock-reclaimed",
        "state-root-race",
        "total-byte-limit",
        "unsafe-campaign-path",
        "unsafe-state-root",
        "watchdog-unavailable",
    }
)

CATEGORY_ORDER = (
    "correctness",
    "rollback",
    "security",
    "data-integrity",
    "performance",
    "operability",
    "maintainability",
    "api-design",
    "testing",
    "cost",
    "privacy",
    "other",
)
CATEGORY_LEXICON = {
    "rollback": ("rollback", "revert", "restore", "recovery", "backup", "resume", "checkpoint"),
    "security": ("security", "injection", "credential", "secret", "auth", "permission", "acl", "exfil"),
    "data-integrity": (
        "data loss", "corrupt", "digest", "identity", "atomic", "transaction",
        "migration", "constraint", "idempotent", "lineage", "supersession",
    ),
    "performance": (
        "performance", "latency", "timeout", "memory", "cpu", "disk", "scale",
        "batch", "index", "hnsw", "resource",
    ),
    "operability": (
        "operability", "monitor", "alert", "heartbeat", "runbook", "maintenance",
        "deploy", "rollout", "scheduler",
    ),
    "maintainability": (
        "maintainability", "drift", "duplication", "complexity", "coupling", "refactor",
    ),
    "api-design": ("api", "contract", "request", "response", "compatibility", "versioning"),
    "testing": ("test", "fixture", "coverage", "verification", "preflight", "gate"),
    "cost": ("roi", "cost", "spend", "payback", "commercial", "operator-hour"),
    "privacy": ("privacy", "tenant", "visibility", "disclosure"),
}

IMMUTABLE_OUTPUTS = (
    "campaign.json",
    "source-digests.json",
    "normalizer-manifest.json",
    "normalized-findings.jsonl",
    "resource-preflight.json",
)
TEXT_FIELDS = ("title", "location", "rationale", "required_fix", "missed_angle")
OPTIONAL_TEXT_FIELDS = ("subsystem", "root_cause_id", "affected_invariant")
OPTIONAL_PROTOCOL_FIELDS = (
    "subsystem",
    "root_cause_id",
    "affected_invariant",
    "changes_design_invariant",
    "relation_to_prior",
)


class Slice0Error(RuntimeError):
    def __init__(self, message: str, *, exit_code: int = 2, reason: str = "invalid-input"):
        super().__init__(message)
        self.exit_code = exit_code
        self.reason = reason


@dataclass(frozen=True)
class SourceMeta:
    path: str
    dev: int
    ino: int
    size: int
    mtime_ns: int


@dataclass
class CampaignFDs:
    root_fd: int
    campaign_fd: int
    receipt_fd: int
    root_path: Path
    lexical_root_path: Path
    root_dev: int
    root_ino: int
    campaign_id: str
    campaign_dev: int
    campaign_ino: int
    receipt_dev: int
    receipt_ino: int

    def close(self) -> None:
        for fd in (self.receipt_fd, self.campaign_fd, self.root_fd):
            with contextlib.suppress(OSError):
                os.close(fd)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_bytes(payload: Any) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def require_utf8(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise Slice0Error(f"{label} must be a string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise Slice0Error(f"{label} contains an invalid Unicode surrogate") from exc
    return value


def safe_json_loads(data: str | bytes, *, reason: str = "invalid-output") -> Any:
    try:
        return json.loads(data)
    except (json.JSONDecodeError, UnicodeError, RecursionError, MemoryError) as exc:
        raise Slice0Error("JSON cannot be safely decoded", reason=reason) from exc


def safe_oserror_context(operation: str, exc: OSError) -> str:
    errno = exc.errno if isinstance(exc.errno, int) else "unknown"
    return f"{operation} failed (errno={errno})"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_schema(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


SOURCE_SCHEMA = load_schema(SOURCE_SCHEMA_PATH)
RECORD_SCHEMA = load_schema(RECORD_SCHEMA_PATH)
MANIFEST_SCHEMA = load_schema(MANIFEST_SCHEMA_PATH)


def implementation_sha() -> str:
    digest = hashlib.sha256()
    for path in (Path(__file__).resolve(), RECORD_SCHEMA_PATH, MANIFEST_SCHEMA_PATH):
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def process_start_ticks(pid: int) -> str | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        comm_end = raw.rfind(")")
        if comm_end < 0:
            return None
        fields_after_comm = raw[comm_end + 1 :].split()
        # fields_after_comm[0] is field 3 (state); starttime is field 22.
        return fields_after_comm[19]
    except (OSError, IndexError):
        return None


def process_entry_exists(pid: int) -> bool:
    try:
        os.stat(f"/proc/{pid}")
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return True


@contextlib.contextmanager
def defer_watchdog_signal() -> Iterator[None]:
    old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {WATCHDOG_SIGNAL})
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)


def write_all(fd: int, data: bytes | memoryview) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise Slice0Error(
                "filesystem write made no forward progress",
                reason="snapshot-write-failed",
            )
        view = view[written:]


def validate_campaign_id(value: str) -> str:
    if not CAMPAIGN_RE.fullmatch(value):
        raise Slice0Error("campaign-id does not match the closed safe-id pattern", exit_code=64)
    return value


def open_campaign(state_root: str, campaign_id: str, *, create: bool) -> CampaignFDs:
    validate_campaign_id(campaign_id)
    if state_root == "":
        raise Slice0Error("state-root must not be empty", exit_code=64)
    root_path = Path(state_root)
    try:
        lst = os.lstat(root_path)
    except OSError as exc:
        raise Slice0Error(f"state-root is unavailable: {exc}", reason="unsafe-state-root") from exc
    if stat.S_ISLNK(lst.st_mode) or not stat.S_ISDIR(lst.st_mode):
        raise Slice0Error("state-root must be a real directory", reason="unsafe-state-root")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        root_fd = os.open(root_path, flags)
    except OSError as exc:
        raise Slice0Error(f"cannot open state-root: {exc}", reason="unsafe-state-root") from exc
    campaign_fd = -1
    receipt_fd = -1
    try:
        root_st = os.fstat(root_fd)
        if not stat.S_ISDIR(root_st.st_mode) or (root_st.st_dev, root_st.st_ino) != (
            lst.st_dev,
            lst.st_ino,
        ):
            raise Slice0Error("state-root changed during open", reason="unsafe-state-root")
        held_root_path = Path(f"/proc/self/fd/{root_fd}").resolve()
        held_st = os.stat(held_root_path, follow_symlinks=False)
        if (held_st.st_dev, held_st.st_ino) != (root_st.st_dev, root_st.st_ino):
            raise Slice0Error("held state-root identity is unverifiable", reason="unsafe-state-root")
        if create:
            try:
                fcntl.flock(root_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise Slice0Error(
                    "state-root namespace lock held",
                    exit_code=3,
                    reason="campaign-lock-held",
                ) from exc
        if create:
            try:
                os.mkdir(campaign_id, mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
        try:
            campaign_fd = os.open(campaign_id, flags, dir_fd=root_fd)
        except FileNotFoundError as exc:
            raise Slice0Error(
                "campaign directory does not exist",
                reason="campaign-not-found",
            ) from exc
        except OSError as exc:
            raise Slice0Error(
                f"campaign directory is unavailable: {exc}",
                reason="unsafe-campaign-path",
            ) from exc
        fst = os.fstat(campaign_fd)
        path_st = os.stat(campaign_id, dir_fd=root_fd, follow_symlinks=False)
        if not stat.S_ISDIR(path_st.st_mode) or (fst.st_dev, fst.st_ino) != (
            path_st.st_dev,
            path_st.st_ino,
        ):
            raise Slice0Error("campaign directory identity mismatch", reason="unsafe-campaign-path")
        if create:
            try:
                fcntl.flock(campaign_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise Slice0Error(
                    "campaign directory lock held",
                    exit_code=3,
                    reason="campaign-lock-held",
                ) from exc
        if create:
            try:
                os.mkdir("stage-receipts", mode=0o700, dir_fd=campaign_fd)
            except FileExistsError:
                pass
        try:
            receipt_fd = os.open("stage-receipts", flags, dir_fd=campaign_fd)
        except FileNotFoundError as exc:
            raise Slice0Error(
                "campaign receipt directory does not exist",
                reason="campaign-incomplete",
            ) from exc
        except OSError as exc:
            raise Slice0Error(
                f"receipt directory is unavailable: {exc}",
                reason="unsafe-campaign-path",
            ) from exc
        receipt_fst = os.fstat(receipt_fd)
        receipt_path_st = os.stat(
            "stage-receipts",
            dir_fd=campaign_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISDIR(receipt_path_st.st_mode)
            or (receipt_fst.st_dev, receipt_fst.st_ino)
            != (receipt_path_st.st_dev, receipt_path_st.st_ino)
        ):
            raise Slice0Error(
                "receipt directory identity mismatch",
                reason="unsafe-campaign-path",
            )
        return CampaignFDs(
            root_fd=root_fd,
            campaign_fd=campaign_fd,
            receipt_fd=receipt_fd,
            root_path=held_root_path,
            lexical_root_path=root_path.absolute(),
            root_dev=root_st.st_dev,
            root_ino=root_st.st_ino,
            campaign_id=campaign_id,
            campaign_dev=fst.st_dev,
            campaign_ino=fst.st_ino,
            receipt_dev=receipt_fst.st_dev,
            receipt_ino=receipt_fst.st_ino,
        )
    except Exception:
        for fd in (receipt_fd, campaign_fd, root_fd):
            if fd >= 0:
                with contextlib.suppress(OSError):
                    os.close(fd)
        raise


def verify_campaign_binding(fds: CampaignFDs) -> None:
    try:
        root_current = os.lstat(fds.lexical_root_path)
    except OSError as exc:
        raise Slice0Error("state-root disappeared before receipt", reason="state-root-race") from exc
    if stat.S_ISLNK(root_current.st_mode) or (root_current.st_dev, root_current.st_ino) != (
        fds.root_dev,
        fds.root_ino,
    ):
        raise Slice0Error("state-root changed before receipt", reason="state-root-race")
    try:
        current = os.stat(fds.campaign_id, dir_fd=fds.root_fd, follow_symlinks=False)
    except OSError as exc:
        raise Slice0Error("campaign path disappeared before receipt", reason="campaign-path-race") from exc
    if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (
        fds.campaign_dev,
        fds.campaign_ino,
    ):
        raise Slice0Error("campaign path changed before receipt", reason="campaign-path-race")
    try:
        receipt_current = os.stat(
            "stage-receipts",
            dir_fd=fds.campaign_fd,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise Slice0Error(
            "receipt path disappeared before receipt",
            reason="receipt-path-race",
        ) from exc
    if not stat.S_ISDIR(receipt_current.st_mode) or (
        receipt_current.st_dev,
        receipt_current.st_ino,
    ) != (fds.receipt_dev, fds.receipt_ino):
        raise Slice0Error(
            "receipt path changed before receipt",
            reason="receipt-path-race",
        )


def atomic_write_at(
    dir_fd: int,
    name: str,
    data: bytes,
    run_id: str,
    *,
    validator: Any | None = None,
) -> str:
    tmp = f".{run_id}.{name.replace('/', '_')}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        with defer_watchdog_signal():
            fd = os.open(tmp, flags, 0o600, dir_fd=dir_fd)
            try:
                write_all(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
            persisted = read_at(dir_fd, tmp, limit=max(len(data), 1))
            if persisted != data:
                raise Slice0Error(
                    "temporary snapshot bytes changed after fsync",
                    reason="snapshot-race",
                )
            if validator is not None:
                validator(persisted)
            digest = sha256_bytes(persisted)
            os.replace(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            os.fsync(dir_fd)
            return digest
    except Exception as exc:
        with contextlib.suppress(OSError):
            os.unlink(tmp, dir_fd=dir_fd)
        if isinstance(exc, OSError):
            raise Slice0Error(
                safe_oserror_context(f"snapshot-publication:{name}", exc),
                reason="operation-failed",
            ) from exc
        raise


def read_at(dir_fd: int, name: str, *, limit: int = 256 * 1024 * 1024) -> bytes:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)
    try:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(1024 * 1024, limit - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise Slice0Error(f"{name} exceeds read limit", reason="invalid-output")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def append_progress(fds: CampaignFDs, payload: dict[str, Any]) -> None:
    line = canonical_bytes(payload)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        with defer_watchdog_signal():
            fd = os.open("progress.jsonl", flags, 0o600, dir_fd=fds.campaign_fd)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                write_all(fd, line)
                os.fsync(fd)
            finally:
                os.close(fd)
    except OSError as exc:
        raise Slice0Error(
            safe_oserror_context("progress-append", exc),
            reason="operation-failed",
        ) from exc


def read_progress_at(fds: CampaignFDs, *, limit: int) -> bytes:
    fd = os.open(
        "progress.jsonl",
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=fds.campaign_fd,
    )
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(1024 * 1024, limit - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise Slice0Error(
                    "progress.jsonl exceeds read limit",
                    reason="progress-too-large",
                )
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def progress_event(
    run_id: str,
    campaign_id: str,
    stage: str,
    completed: int,
    total: int,
    *,
    started: float,
    outcome: str,
    reason: str | None = None,
    digest: str | None = None,
) -> dict[str, Any]:
    if reason not in PROGRESS_REASON_CODES:
        raise Slice0Error("progress reason code is not registered", reason="invalid-progress")
    return {
        "run_id": run_id,
        "campaign_id": campaign_id,
        "stage": stage,
        "completed": completed,
        "total": total,
        "last_item_digest": digest,
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "outcome": outcome,
        "reason_code": reason,
        "recorded_at": utc_now(),
    }


def publish_heartbeat(
    fds: CampaignFDs,
    run_id: str,
    stage: str,
    completed: int,
    total: int,
    last_progress_at: str,
) -> None:
    payload = {
        "schema_version": "deja-review-slice0-heartbeat/v1",
        "run_id": run_id,
        "campaign_id": fds.campaign_id,
        "stage": stage,
        "completed": completed,
        "total": total,
        "last_progress_at": last_progress_at,
        "recorded_at": utc_now(),
    }
    atomic_write_at(fds.campaign_fd, "heartbeat.json", canonical_bytes(payload), run_id)


def heartbeat_child(args: argparse.Namespace) -> int:
    data = sys.stdin.buffer.read(64 * 1024 + 1)
    if len(data) > 64 * 1024:
        raise Slice0Error("heartbeat payload exceeds ceiling", reason="heartbeat-write-failed")
    payload = safe_json_loads(data)
    if (
        not isinstance(payload, dict)
        or payload.get("run_id") != args.run_id
        or payload.get("campaign_id") != args.campaign_id
    ):
        raise Slice0Error("heartbeat payload identity is invalid", reason="heartbeat-write-failed")
    atomic_write_at(args.campaign_fd, "heartbeat.json", data, args.write_id)
    return 0


class RunWatchdog:
    """Keep heartbeat live and interrupt the main thread at the hard deadline."""

    def __init__(
        self,
        fds: CampaignFDs,
        run_id: str,
        *,
        deadline: float | None = None,
    ):
        self.fds = fds
        self.run_id = run_id
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._stage = "discover"
        self._completed = 0
        self._total = 0
        self._last_progress_at = utc_now()
        self._trigger_reason: str | None = None
        self._enabled = threading.Event()
        self._old_handler: Any = None
        self._thread: threading.Thread | None = None
        self._deadline_thread: threading.Thread | None = None
        self._main_ident: int | None = None
        self.deadline = deadline if deadline is not None else time.monotonic() + DEADLINE_SECONDS
        self._started = False

    def _alarm(self, _signum: int, _frame: Any) -> None:
        if not self._enabled.is_set():
            return
        reason = self._trigger_reason or "foundation-deadline-exceeded"
        raise Slice0Error("run watchdog interrupted the active stage", reason=reason)

    def _publish_locked(self) -> None:
        payload = {
            "schema_version": "deja-review-slice0-heartbeat/v1",
            "run_id": self.run_id,
            "campaign_id": self.fds.campaign_id,
            "stage": self._stage,
            "completed": self._completed,
            "total": self._total,
            "last_progress_at": self._last_progress_at,
            "recorded_at": utc_now(),
        }
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise Slice0Error(
                "foundation deadline exceeded",
                reason="foundation-deadline-exceeded",
            )
        timeout = max(0.1, min(HEARTBEAT_IO_TIMEOUT_SECONDS, remaining))
        heartbeat_fd = os.open(
            ".",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=self.fds.campaign_fd,
        )
        write_id = f"{self.run_id}.{uuid.uuid4().hex}"
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "_heartbeat-child",
                    "--campaign-fd",
                    str(heartbeat_fd),
                    "--campaign-id",
                    self.fds.campaign_id,
                    "--run-id",
                    self.run_id,
                    "--write-id",
                    write_id,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                pass_fds=(heartbeat_fd,),
            )
        finally:
            os.close(heartbeat_fd)
        try:
            _, stderr = process.communicate(input=canonical_bytes(payload), timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            self._terminate_child_bounded(process)
            raise Slice0Error(
                "heartbeat publication exceeded its I/O deadline",
                reason="heartbeat-watchdog-failed",
            ) from exc
        except Exception:
            self._terminate_child_bounded(process)
            raise
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip().splitlines()
            suffix = f": {detail[-1]}" if detail else ""
            raise Slice0Error(
                f"heartbeat publication failed{suffix}",
                reason="heartbeat-watchdog-failed",
            )

    @staticmethod
    def _terminate_child_bounded(process: subprocess.Popen[bytes]) -> None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        try:
            process.communicate(timeout=HEARTBEAT_REAP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            # The helper owns a separate directory open-file-description and
            # therefore cannot retain the parent's authoritative flock. Keep
            # only a daemon reaper; the parent may finish cleanup and close its
            # own descriptors without waiting on uninterruptible helper I/O.
            threading.Thread(
                target=process.wait,
                name="deja-slice0-heartbeat-reaper",
                daemon=True,
            ).start()

    def _loop(self) -> None:
        interval = max(0.1, min(30.0, STALL_SECONDS / 2))
        while True:
            if self._stop.wait(interval):
                return
            try:
                with self._lock:
                    self._publish_locked()
            except (OSError, Slice0Error):
                self._trigger_main("heartbeat-watchdog-failed")
                return

    def _deadline_loop(self) -> None:
        remaining = self.deadline - time.monotonic()
        if remaining > 0 and self._stop.wait(remaining):
            return
        if not self._stop.is_set():
            self._trigger_main("foundation-deadline-exceeded")

    def _trigger_main(self, reason: str) -> None:
        self._trigger_reason = reason
        if self._enabled.is_set() and self._main_ident is not None:
            signal.pthread_kill(self._main_ident, WATCHDOG_SIGNAL)

    def start(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            raise Slice0Error(
                "watchdog requires the CLI main thread",
                reason="watchdog-unavailable",
            )
        self._main_ident = threading.get_ident()
        self._old_handler = signal.getsignal(WATCHDOG_SIGNAL)
        signal.signal(WATCHDOG_SIGNAL, self._alarm)
        self._enabled.set()
        try:
            with self._lock:
                self._publish_locked()
        except Exception:
            self._enabled.clear()
            signal.signal(WATCHDOG_SIGNAL, self._old_handler)
            raise
        self._thread = threading.Thread(
            target=self._loop,
            name=f"deja-slice0-watchdog-{self.run_id[:8]}",
            daemon=True,
        )
        self._deadline_thread = threading.Thread(
            target=self._deadline_loop,
            name=f"deja-slice0-deadline-{self.run_id[:8]}",
            daemon=True,
        )
        try:
            self._deadline_thread.start()
            self._thread.start()
        except Exception:
            self._enabled.clear()
            self._stop.set()
            if self._deadline_thread.is_alive():
                self._deadline_thread.join()
            signal.signal(WATCHDOG_SIGNAL, self._old_handler)
            raise
        self._started = True

    def update(self, stage: str, completed: int, total: int) -> None:
        self.check_deadline()
        with self._lock:
            self._stage = stage
            self._completed = completed
            self._total = total
            self._last_progress_at = utc_now()
            self._publish_locked()

    def check_deadline(self) -> None:
        if time.monotonic() >= self.deadline:
            raise Slice0Error(
                "foundation deadline exceeded",
                reason="foundation-deadline-exceeded",
            )

    def request_stop(self) -> None:
        self._enabled.clear()
        self._stop.set()

    def stop(self) -> None:
        if not self._started:
            return
        old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {WATCHDOG_SIGNAL})
        try:
            self.request_stop()
            if self._thread is not None:
                self._thread.join()
            if self._deadline_thread is not None:
                self._deadline_thread.join()
            while WATCHDOG_SIGNAL in signal.sigpending():
                signal.sigtimedwait({WATCHDOG_SIGNAL}, 0)
            signal.signal(WATCHDOG_SIGNAL, self._old_handler)
            self._started = False
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)


def source_metadata(
    paths: list[str],
    *,
    forbidden_tree: Path | None = None,
) -> list[SourceMeta]:
    if not paths:
        raise Slice0Error("at least one --source is required")
    if len(paths) > MAX_FILES:
        raise Slice0Error("source file count exceeds ceiling", reason="file-count-limit")
    seen: set[tuple[int, int]] = set()
    result: list[SourceMeta] = []
    total = 0
    for raw in paths:
        if not raw.endswith(".json"):
            raise Slice0Error("every source must have .json suffix")
        display = os.path.abspath(os.path.normpath(raw))
        if forbidden_tree is not None:
            try:
                resolved_source = Path(display).resolve(strict=True)
            except OSError as exc:
                raise Slice0Error(f"source unavailable: {display}: {exc}") from exc
            if resolved_source.is_relative_to(forbidden_tree):
                raise Slice0Error(
                    "source must not overlap campaign-managed state",
                    reason="source-state-overlap",
                )
        try:
            info = os.lstat(display)
        except OSError as exc:
            raise Slice0Error(f"source unavailable: {display}: {exc}") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise Slice0Error("source must be a non-symlink regular JSON file")
        if info.st_size > MAX_FILE_BYTES:
            raise Slice0Error("source exceeds per-file byte ceiling", reason="file-byte-limit")
        identity = (info.st_dev, info.st_ino)
        if identity in seen:
            continue
        seen.add(identity)
        total += info.st_size
        if total > MAX_TOTAL_BYTES:
            raise Slice0Error("sources exceed total byte ceiling", reason="total-byte-limit")
        result.append(
            SourceMeta(display, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        )
    if not result:
        raise Slice0Error("no unique sources remain")
    return result


def read_source(meta: SourceMeta) -> tuple[bytes, str]:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        fd = os.open(meta.path, flags)
    except OSError as exc:
        raise Slice0Error(f"source open refused: {meta.path}: {exc}", reason="source-race") from exc
    try:
        before = os.fstat(fd)
        expected = (meta.dev, meta.ino, meta.size, meta.mtime_ns)
        actual = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        if actual != expected or not stat.S_ISREG(before.st_mode):
            raise Slice0Error("source changed between admission and open", reason="source-race")
        chunks: list[bytes] = []
        total = 0
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, min(1024 * 1024, MAX_FILE_BYTES - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise Slice0Error("source grew beyond byte ceiling", reason="source-race")
            digest.update(chunk)
            chunks.append(chunk)
        after = os.fstat(fd)
        final = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if final != expected or total != meta.size:
            raise Slice0Error("source changed while being read", reason="source-race")
        return b"".join(chunks), digest.hexdigest()
    finally:
        os.close(fd)


def derive_categories(finding: dict[str, Any]) -> list[str]:
    raw = " ".join(
        str(finding.get(name, "")) for name in ("title", "location", "missed_angle")
    )
    text = unicodedata.normalize("NFKC", raw).lower()
    matches = [
        category
        for category in CATEGORY_ORDER
        if category in CATEGORY_LEXICON
        and any(token in text for token in CATEGORY_LEXICON[category])
    ][:3]
    if matches:
        return matches
    if finding["severity"] in {"REJECT", "CRITICAL", "HIGH"}:
        return ["correctness"]
    return ["other"]


def validate_artifact(payload: Any) -> dict[str, Any]:
    jsonschema.validate(payload, SOURCE_SCHEMA)
    if not isinstance(payload, dict):
        raise Slice0Error("artifact is not an object")
    reviewer = require_utf8(payload["reviewer"], "reviewer")
    if not reviewer.strip() or payload["round"] < 1:
        raise Slice0Error("reviewer must be non-empty and round positive")
    if len(reviewer.encode("utf-8")) > MAX_REVIEWER_BYTES:
        raise Slice0Error("reviewer exceeds byte ceiling")
    findings = payload["findings"]
    if len(findings) > MAX_FINDINGS:
        raise Slice0Error("artifact exceeds finding ceiling", reason="finding-count-limit")
    ids: set[str] = set()
    text_total = 0
    for finding in findings:
        finding_id = finding["finding_id"]
        require_utf8(finding_id, "finding_id")
        if not finding_id.strip() or finding_id in ids:
            raise Slice0Error("finding IDs must be non-empty and unique")
        ids.add(finding_id)
        for field in TEXT_FIELDS:
            value = finding[field]
            require_utf8(value, field)
            if "\x00" in value:
                raise Slice0Error("finding text contains NUL")
            size = len(value.encode("utf-8"))
            if size > MAX_TEXT_BYTES:
                raise Slice0Error("finding text exceeds field ceiling")
            text_total += size
        for field in OPTIONAL_TEXT_FIELDS:
            if field not in finding:
                continue
            value = finding[field]
            require_utf8(value, field)
            if "\x00" in value:
                raise Slice0Error("finding text contains NUL")
            size = len(value.encode("utf-8"))
            if size > MAX_TEXT_BYTES:
                raise Slice0Error("finding text exceeds field ceiling")
            text_total += size
        if text_total > MAX_ARTIFACT_TEXT_BYTES:
            raise Slice0Error("artifact finding text exceeds aggregate ceiling")
    return payload


def occurrence_id(source_sha: str, reviewer: str, round_: int, finding_id: str) -> str:
    data = (
        "deja-review-slice0-occurrence-v1\0"
        + source_sha
        + "\0"
        + reviewer
        + "\0"
        + str(round_)
        + "\0"
        + finding_id
    ).encode("utf-8")
    return sha256_bytes(data)


def normalize_artifact(
    payload: dict[str, Any], source_path: str, source_sha: str
) -> list[dict[str, Any]]:
    records = []
    for finding in payload["findings"]:
        record = {
            "schema_version": SCHEMA_VERSION,
            "normalizer_version": NORMALIZER_VERSION,
            "occurrence_id": occurrence_id(
                source_sha, payload["reviewer"], payload["round"], finding["finding_id"]
            ),
            "source_path": source_path,
            "source_sha256": source_sha,
            "source_artifact_id": payload["artifact_id"],
            "reviewed_artifact_sha": payload["artifact_sha"],
            "reviewer": payload["reviewer"],
            "round": payload["round"],
            "verdict": payload["verdict"],
            "schema_grounding_verdict": payload["schema_grounding_verdict"],
            "finding_id": finding["finding_id"],
            "severity": finding["severity"],
            "title": finding["title"],
            "location": finding["location"],
            "rationale": finding["rationale"],
            "required_fix": finding["required_fix"],
            "confidence": finding["confidence"],
            "dup_flag": finding["dup_flag"],
            "missed_angle": finding["missed_angle"],
            "categories": derive_categories(finding),
            "category_derivation": CATEGORY_VERSION,
            "trust": TRUST,
        }
        for field in OPTIONAL_PROTOCOL_FIELDS:
            if field in finding:
                record[field] = finding[field]
        jsonschema.validate(record, RECORD_SCHEMA)
        records.append(record)
    return sorted(
        records,
        key=lambda item: (
            item["reviewer"],
            item["round"],
            item["finding_id"],
            item["occurrence_id"],
        ),
    )


def normalizer_manifest(impl_sha: str) -> dict[str, Any]:
    return {
        "schema_version": "deja-review-slice0-normalizer-manifest/v1",
        "normalizer_version": NORMALIZER_VERSION,
        "normalizer_implementation_sha": impl_sha,
        "source_schema_sha256": sha256_bytes(SOURCE_SCHEMA_PATH.read_bytes()),
        "record_schema_sha256": sha256_bytes(RECORD_SCHEMA_PATH.read_bytes()),
        "manifest_schema_sha256": sha256_bytes(MANIFEST_SCHEMA_PATH.read_bytes()),
        "category_derivation": CATEGORY_VERSION,
    }


def intent_digest(metas: list[SourceMeta], impl_sha: str) -> str:
    payload = {
        "sources": [
            {
                "path": item.path,
                "dev": item.dev,
                "ino": item.ino,
                "size": item.size,
                "mtime_ns": item.mtime_ns,
            }
            for item in metas
        ],
        "normalizer_implementation_sha": impl_sha,
    }
    return sha256_bytes(canonical_bytes(payload))


def immutable_digest(sources: list[dict[str, Any]], impl_sha: str) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "normalizer_version": NORMALIZER_VERSION,
        "normalizer_implementation_sha": impl_sha,
        "sources": [
            {"path": item["path"], "sha256": item["sha256"]}
            for item in sorted(sources, key=lambda value: value["path"])
        ],
        "record_schema_sha256": sha256_bytes(RECORD_SCHEMA_PATH.read_bytes()),
        "source_schema_sha256": sha256_bytes(SOURCE_SCHEMA_PATH.read_bytes()),
    }
    return sha256_bytes(canonical_bytes(payload))


def current_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value * 1024)


def host_available_memory() -> int:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


def cgroup_headroom() -> int | None:
    maximum_path = Path("/sys/fs/cgroup/memory.max")
    current_path = Path("/sys/fs/cgroup/memory.current")
    missing = []
    for path in (maximum_path, current_path):
        try:
            os.stat(path)
            missing.append(False)
        except FileNotFoundError:
            missing.append(True)
        except OSError as exc:
            raise Slice0Error(
                "cgroup memory controls cannot be inspected",
                reason="memory-preflight-unavailable",
            ) from exc
    if all(missing):
        return None
    if any(missing):
        raise Slice0Error(
            "cgroup memory controls are incomplete",
            reason="memory-preflight-unavailable",
        )
    try:
        maximum = maximum_path.read_text(encoding="utf-8").strip()
        current_raw = current_path.read_text(encoding="utf-8").strip()
        if maximum == "max":
            return None
        maximum_value = int(maximum)
        current = int(current_raw)
        if maximum_value < 0 or current < 0:
            raise ValueError("negative cgroup memory counter")
        return max(0, maximum_value - current)
    except (OSError, ValueError) as exc:
        raise Slice0Error(
            "cgroup memory headroom cannot be established",
            reason="memory-preflight-unavailable",
        ) from exc


def rlimit_headroom() -> int | None:
    soft, _ = resource.getrlimit(resource.RLIMIT_AS)
    if soft == resource.RLIM_INFINITY:
        return None
    try:
        pages_raw = Path("/proc/self/statm").read_text(encoding="utf-8").split()[0]
        virtual_bytes = int(pages_raw) * int(os.sysconf("SC_PAGE_SIZE"))
        if virtual_bytes < 0:
            raise ValueError("negative virtual memory size")
    except (OSError, ValueError, IndexError) as exc:
        raise Slice0Error(
            "process virtual memory size cannot be established",
            reason="memory-preflight-unavailable",
        ) from exc
    return max(0, int(soft) - virtual_bytes)


def effective_memory_headroom() -> int:
    candidates = [
        value
        for value in (host_available_memory(), cgroup_headroom(), rlimit_headroom())
        if value is not None
    ]
    return min(candidates) if candidates else 0


def resource_projection(
    metas: list[SourceMeta], campaign_path: Path, *, retained_bytes: int
) -> dict[str, Any]:
    per_source = []
    for item in metas:
        path_bytes = len(item.path.encode("utf-8"))
        record_expansion = MAX_FINDINGS * (
            path_bytes * 6 + MAX_REVIEWER_BYTES * 6 + 4096
        )
        memory_incremental = item.size * 6 + record_expansion * 2 + 1024 * 1024
        spool = item.size * 6 + record_expansion
        per_source.append(
            {
                "source_size": item.size,
                "encoded_source_path_bytes": path_bytes,
                "normalized_record_expansion": record_expansion,
                "projected_incremental_memory": memory_incremental,
                "artifact_spool_upper_bound": spool,
            }
        )
    spool_upper = sum(item["artifact_spool_upper_bound"] for item in per_source)
    manifest_temp = 1024 * 1024 + len(metas) * 512
    progress_append = (len(metas) * 3 + 8) * 1024
    atomic_count = 8
    disk_peak = (
        retained_bytes
        + spool_upper
        + spool_upper
        + manifest_temp
        + progress_append
        + atomic_count * 4096
    )
    headroom = effective_memory_headroom()
    incremental = max(item["projected_incremental_memory"] for item in per_source)
    free_disk = shutil.disk_usage(campaign_path).free
    rss = current_rss_bytes()
    disk_pass = free_disk >= math.ceil(RESOURCE_MARGIN * disk_peak)
    memory_pass = headroom >= math.ceil(RESOURCE_MARGIN * incremental)
    return {
        "schema_version": "deja-review-slice0-resource-preflight/v1",
        "candidate_artifact_count": len(metas),
        "source_bytes": sum(item.size for item in metas),
        "max_findings_per_artifact": MAX_FINDINGS,
        "per_source_projection": per_source,
        "retained_existing_campaign_bytes": retained_bytes,
        "artifact_spool_bytes_upper_bound": spool_upper,
        "final_jsonl_temporary_bytes_upper_bound": spool_upper,
        "manifest_and_receipt_temporary_bytes": manifest_temp,
        "projected_progress_append_bytes": progress_append,
        "atomic_snapshot_output_count": atomic_count,
        "projected_simultaneous_peak_bytes": disk_peak,
        "free_bytes": free_disk,
        "current_rss_bytes": rss,
        "effective_free_headroom_bytes": headroom,
        "maximum_projected_incremental_memory_bytes": incremental,
        "projected_peak_rss_bytes": rss + incremental,
        "resource_margin": RESOURCE_MARGIN,
        "disk_pass": disk_pass,
        "memory_pass": memory_pass,
        "cpu_count": os.cpu_count() or 1,
        "cpu_worker_cap": 1,
        "embedding_concurrency": 0,
        "database_concurrency": 0,
    }


def retained_campaign_bytes(dir_fd: int) -> int:
    total = 0
    try:
        for name in os.listdir(dir_fd):
            info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
    except OSError as exc:
        raise Slice0Error(
            "retained campaign state cannot be measured",
            reason="retained-state-scan-failed",
        ) from exc
    return total


def validate_json_bytes(data: bytes, schema: dict[str, Any]) -> None:
    jsonschema.validate(safe_json_loads(data), schema)


def require_nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise Slice0Error(f"{label} must be a non-negative integer", reason="invalid-output")
    return value


def validate_resource_preflight(
    payload: Any,
    campaign: dict[str, Any],
    sources: list[dict[str, Any]],
) -> None:
    required = {
        "schema_version",
        "candidate_artifact_count",
        "source_bytes",
        "max_findings_per_artifact",
        "per_source_projection",
        "retained_existing_campaign_bytes",
        "artifact_spool_bytes_upper_bound",
        "final_jsonl_temporary_bytes_upper_bound",
        "manifest_and_receipt_temporary_bytes",
        "projected_progress_append_bytes",
        "atomic_snapshot_output_count",
        "projected_simultaneous_peak_bytes",
        "free_bytes",
        "current_rss_bytes",
        "effective_free_headroom_bytes",
        "maximum_projected_incremental_memory_bytes",
        "projected_peak_rss_bytes",
        "resource_margin",
        "disk_pass",
        "memory_pass",
        "cpu_count",
        "cpu_worker_cap",
        "embedding_concurrency",
        "database_concurrency",
        "finding_count",
        "normalized_bytes",
        "largest_actual_spool_bytes",
        "measured_peak_rss_bytes",
        "publication_memory_pass",
        "recorded_at",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise Slice0Error("resource preflight shape is invalid", reason="invalid-output")
    if payload["schema_version"] != "deja-review-slice0-resource-preflight/v1":
        raise Slice0Error("resource preflight schema is invalid", reason="invalid-output")
    integer_fields = required - {
        "schema_version",
        "per_source_projection",
        "resource_margin",
        "disk_pass",
        "memory_pass",
        "publication_memory_pass",
        "recorded_at",
    }
    for field in integer_fields:
        require_nonnegative_int(payload[field], f"resource preflight {field}")
    if (
        payload["candidate_artifact_count"] != campaign["artifact_count"]
        or payload["source_bytes"] != sum(item["bytes"] for item in sources)
        or payload["finding_count"] != campaign["finding_count"]
        or payload["normalized_bytes"] != campaign["normalized_bytes"]
        or payload["max_findings_per_artifact"] != MAX_FINDINGS
        or payload["atomic_snapshot_output_count"] != 8
        or payload["cpu_worker_cap"] != 1
        or payload["cpu_count"] < 1
        or payload["embedding_concurrency"] != 0
        or payload["database_concurrency"] != 0
    ):
        raise Slice0Error("resource preflight counts are inconsistent", reason="invalid-output")
    projections = payload["per_source_projection"]
    projection_fields = {
        "source_size",
        "encoded_source_path_bytes",
        "normalized_record_expansion",
        "projected_incremental_memory",
        "artifact_spool_upper_bound",
    }
    if not isinstance(projections, list) or len(projections) != campaign["artifact_count"]:
        raise Slice0Error("resource projection list is invalid", reason="invalid-output")
    for item in projections:
        if not isinstance(item, dict) or set(item) != projection_fields:
            raise Slice0Error("resource projection entry is invalid", reason="invalid-output")
        for field in projection_fields:
            require_nonnegative_int(item[field], f"resource projection {field}")
    expected_projections = []
    for source in sources:
        path_bytes = len(source["path"].encode("utf-8"))
        expansion = MAX_FINDINGS * (
            path_bytes * 6 + MAX_REVIEWER_BYTES * 6 + 4096
        )
        expected_projections.append(
            {
                "source_size": source["bytes"],
                "encoded_source_path_bytes": path_bytes,
                "normalized_record_expansion": expansion,
                "projected_incremental_memory": source["bytes"] * 6
                + expansion * 2
                + 1024 * 1024,
                "artifact_spool_upper_bound": source["bytes"] * 6 + expansion,
            }
        )
    projection_key = lambda item: tuple(sorted(item.items()))
    if sorted(map(projection_key, projections)) != sorted(
        map(projection_key, expected_projections)
    ):
        raise Slice0Error(
            "resource projection arithmetic is inconsistent",
            reason="invalid-output",
            )
    margin = payload["resource_margin"]
    if (
        isinstance(margin, bool)
        or not isinstance(margin, (int, float))
        or not math.isfinite(float(margin))
        or margin < 1
    ):
        raise Slice0Error("resource margin is invalid", reason="invalid-output")
    if (
        payload["disk_pass"] is not True
        or payload["memory_pass"] is not True
        or payload["publication_memory_pass"] is not True
    ):
        raise Slice0Error("resource admission did not pass", reason="invalid-output")
    spool_upper = sum(item["artifact_spool_upper_bound"] for item in projections)
    manifest_temp = 1024 * 1024 + len(sources) * 512
    progress_append = (len(sources) * 3 + 8) * 1024
    disk_peak = (
        payload["retained_existing_campaign_bytes"]
        + spool_upper * 2
        + manifest_temp
        + progress_append
        + 8 * 4096
    )
    incremental = max(
        item["projected_incremental_memory"] for item in projections
    )
    if (
        payload["artifact_spool_bytes_upper_bound"] != spool_upper
        or payload["final_jsonl_temporary_bytes_upper_bound"] != spool_upper
        or payload["manifest_and_receipt_temporary_bytes"] != manifest_temp
        or payload["projected_progress_append_bytes"] != progress_append
        or payload["projected_simultaneous_peak_bytes"] != disk_peak
        or payload["maximum_projected_incremental_memory_bytes"] != incremental
        or payload["projected_peak_rss_bytes"]
        != payload["current_rss_bytes"] + incremental
        or payload["largest_actual_spool_bytes"] > spool_upper
        or payload["free_bytes"] < math.ceil(RESOURCE_MARGIN * disk_peak)
        or payload["effective_free_headroom_bytes"]
        < math.ceil(RESOURCE_MARGIN * incremental)
        or float(margin) != RESOURCE_MARGIN
    ):
        raise Slice0Error(
            "resource preflight arithmetic is inconsistent",
            reason="invalid-output",
        )
    recorded_at = payload["recorded_at"]
    if not isinstance(recorded_at, str):
        raise Slice0Error(
            "resource preflight timestamp is invalid",
            reason="invalid-output",
        )
    try:
        parse_rfc3339(recorded_at, "resource preflight recorded_at")
    except (TypeError, ValueError, AttributeError) as exc:
        raise Slice0Error(
            "resource preflight timestamp is invalid",
            reason="invalid-output",
        ) from exc


def validate_jsonl_bytes(data: bytes) -> None:
    if data and (not data.endswith(b"\n") or b"\r" in data):
        raise Slice0Error("normalized JSONL line ending is noncanonical", reason="invalid-output")
    seen: set[str] = set()
    previous: tuple[Any, ...] | None = None
    for raw in data.splitlines():
        record = safe_json_loads(raw)
        jsonschema.validate(record, RECORD_SCHEMA)
        occurrence = record["occurrence_id"]
        if occurrence in seen:
            raise Slice0Error("duplicate occurrence ID", reason="duplicate-occurrence")
        seen.add(occurrence)
        key = (
            record["source_sha256"],
            record["reviewer"],
            record["round"],
            record["finding_id"],
            occurrence,
        )
        if previous is not None and key < previous:
            raise Slice0Error("normalized JSONL is not canonically sorted", reason="invalid-output")
        previous = key


def stream_validate_jsonl_at(
    dir_fd: int,
    name: str,
) -> tuple[str, int, int, set[tuple[str, str]]]:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)
    digest = hashlib.sha256()
    seen: set[str] = set()
    previous: tuple[Any, ...] | None = None
    count = 0
    size = 0
    source_references: set[tuple[str, str]] = set()
    try:
        with os.fdopen(fd, "rb", closefd=True) as stream:
            for raw in stream:
                size += len(raw)
                digest.update(raw)
                if not raw.endswith(b"\n") or b"\r" in raw:
                    raise Slice0Error(
                        "JSONL line ending is noncanonical",
                        reason="invalid-output",
                    )
                record = safe_json_loads(raw)
                jsonschema.validate(record, RECORD_SCHEMA)
                if record["occurrence_id"] != occurrence_id(
                    record["source_sha256"],
                    record["reviewer"],
                    record["round"],
                    record["finding_id"],
                ):
                    raise Slice0Error(
                        "normalized occurrence identity is inconsistent",
                        reason="invalid-output",
                    )
                if record["categories"] != derive_categories(record):
                    raise Slice0Error(
                        "normalized category derivation is inconsistent",
                        reason="invalid-output",
                    )
                occurrence = record["occurrence_id"]
                if occurrence in seen:
                    raise Slice0Error("duplicate occurrence ID", reason="duplicate-occurrence")
                seen.add(occurrence)
                key = (
                    record["source_sha256"],
                    record["reviewer"],
                    record["round"],
                    record["finding_id"],
                    occurrence,
                )
                if previous is not None and key < previous:
                    raise Slice0Error(
                        "normalized JSONL is not canonically sorted",
                        reason="invalid-output",
                    )
                previous = key
                source_references.add((record["source_path"], record["source_sha256"]))
                count += 1
    except Exception:
        # os.fdopen owns fd after successful construction; suppress only a
        # possible close of an already-closed descriptor.
        with contextlib.suppress(OSError):
            os.close(fd)
        raise
    return digest.hexdigest(), size, count, source_references


def stream_hash_at(dir_fd: int, name: str) -> tuple[str, int]:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)
    digest = hashlib.sha256()
    size = 0
    try:
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    finally:
        os.close(fd)
    return digest.hexdigest(), size


def publish_jsonl_from_spools(
    fds: CampaignFDs,
    spools: list[tuple[str, str, int]],
    run_id: str,
) -> tuple[str, int, int]:
    name = "normalized-findings.jsonl"
    tmp = f".{run_id}.{name}.tmp"
    try:
        with defer_watchdog_signal():
            out_fd = os.open(
                tmp,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=fds.campaign_fd,
            )
            try:
                for _, spool_name, _ in sorted(spools, key=lambda item: item[0]):
                    in_fd = os.open(
                        spool_name,
                        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                        dir_fd=fds.campaign_fd,
                    )
                    try:
                        while True:
                            chunk = os.read(in_fd, 1024 * 1024)
                            if not chunk:
                                break
                            write_all(out_fd, chunk)
                    finally:
                        os.close(in_fd)
                os.fsync(out_fd)
            finally:
                os.close(out_fd)
            digest, size, count, _ = stream_validate_jsonl_at(fds.campaign_fd, tmp)
            os.replace(tmp, name, src_dir_fd=fds.campaign_fd, dst_dir_fd=fds.campaign_fd)
            os.fsync(fds.campaign_fd)
            return digest, size, count
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp, dir_fd=fds.campaign_fd)
        raise


def validate_campaign_dir(fds: CampaignFDs) -> dict[str, Any]:
    receipt_raw = read_at(fds.receipt_fd, "prepare.json", limit=4 * 1024 * 1024)
    receipt = safe_json_loads(receipt_raw)
    if not isinstance(receipt, dict) or set(receipt) != {
        "schema_version",
        "campaign_id",
        "immutable_input_digest",
        "outputs",
        "published_at",
    }:
        raise Slice0Error("prepare receipt must be an object", reason="invalid-output")
    if receipt.get("schema_version") != "deja-review-slice0-receipt/v1":
        raise Slice0Error("prepare receipt schema is invalid", reason="invalid-output")
    outputs = receipt.get("outputs")
    if not isinstance(outputs, dict) or set(outputs) != set(IMMUTABLE_OUTPUTS):
        raise Slice0Error("prepare receipt output set is invalid", reason="invalid-output")
    if (
        receipt.get("campaign_id") != fds.campaign_id
        or not isinstance(receipt.get("immutable_input_digest"), str)
        or not HEX64_RE.fullmatch(receipt["immutable_input_digest"])
        or not all(
            isinstance(value, str) and HEX64_RE.fullmatch(value)
            for value in outputs.values()
        )
    ):
        raise Slice0Error("prepare receipt identity is invalid", reason="invalid-output")
    try:
        parse_rfc3339(receipt.get("published_at"), "receipt published_at")
    except (TypeError, ValueError, AttributeError) as exc:
        raise Slice0Error("prepare receipt timestamp is invalid", reason="invalid-output") from exc
    return validate_campaign_outputs(
        fds,
        outputs,
        receipt["immutable_input_digest"],
    )


def validate_campaign_outputs(
    fds: CampaignFDs,
    output_digests: dict[str, str],
    immutable_input_digest: str,
) -> dict[str, Any]:
    if set(output_digests) != set(IMMUTABLE_OUTPUTS) or not all(
        isinstance(value, str) and HEX64_RE.fullmatch(value)
        for value in output_digests.values()
    ):
        raise Slice0Error("immutable output digest set is invalid", reason="invalid-output")
    for name in IMMUTABLE_OUTPUTS:
        if name == "normalized-findings.jsonl":
            (
                actual_digest,
                normalized_size,
                line_count,
                source_references,
            ) = stream_validate_jsonl_at(fds.campaign_fd, name)
        else:
            actual_digest = sha256_bytes(read_at(fds.campaign_fd, name))
        if actual_digest != output_digests[name]:
            raise Slice0Error(f"receipt digest mismatch for {name}", reason="invalid-output")
    campaign = safe_json_loads(read_at(fds.campaign_fd, "campaign.json"))
    if not isinstance(campaign, dict):
        raise Slice0Error("campaign manifest must be an object", reason="invalid-output")
    jsonschema.validate(campaign, MANIFEST_SCHEMA)
    if campaign["campaign_id"] != fds.campaign_id:
        raise Slice0Error(
            "campaign manifest does not match the opened directory name",
            reason="invalid-output",
        )
    if immutable_input_digest != campaign["immutable_input_digest"]:
        raise Slice0Error("receipt input digest mismatch", reason="invalid-output")
    current_impl_sha = implementation_sha()
    if campaign["normalizer_implementation_sha"] != current_impl_sha:
        raise Slice0Error(
            "campaign normalizer implementation does not match current bytes",
            reason="invalid-output",
        )
    source_manifest = safe_json_loads(read_at(fds.campaign_fd, "source-digests.json"))
    if not isinstance(source_manifest, dict):
        raise Slice0Error("source digest manifest must be an object", reason="invalid-output")
    if source_manifest.get("schema_version") != "deja-review-slice0-source-digests/v1":
        raise Slice0Error("source digest manifest is invalid", reason="invalid-output")
    sources = source_manifest.get("sources")
    if not isinstance(sources, list) or len(sources) != campaign["artifact_count"]:
        raise Slice0Error("source count mismatch", reason="invalid-output")
    seen_sha: set[str] = set()
    manifested_references: set[tuple[str, str]] = set()
    for item in sources:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "sha256", "bytes"}
            or not isinstance(item["path"], str)
            or not HEX64_RE.fullmatch(item["sha256"])
            or isinstance(item["bytes"], bool)
            or not isinstance(item["bytes"], int)
            or item["bytes"] < 0
            or item["sha256"] in seen_sha
        ):
            raise Slice0Error("source digest entry is invalid", reason="invalid-output")
        seen_sha.add(item["sha256"])
        manifested_references.add((item["path"], item["sha256"]))
    if not source_references.issubset(manifested_references):
        raise Slice0Error(
            "normalized finding references an unknown source",
            reason="invalid-output",
        )
    if line_count != campaign["finding_count"] or normalized_size != campaign["normalized_bytes"]:
        raise Slice0Error("normalized corpus counts mismatch", reason="invalid-output")
    normalizer = safe_json_loads(read_at(fds.campaign_fd, "normalizer-manifest.json"))
    expected_normalizer = normalizer_manifest(current_impl_sha)
    if not isinstance(normalizer, dict) or normalizer != expected_normalizer:
        raise Slice0Error("normalizer manifest must be an object", reason="invalid-output")
    expected_immutable = immutable_digest(sources, current_impl_sha)
    if expected_immutable != campaign["immutable_input_digest"]:
        raise Slice0Error("immutable input digest mismatch", reason="invalid-output")
    resource_preflight = safe_json_loads(
        read_at(fds.campaign_fd, "resource-preflight.json")
    )
    validate_resource_preflight(resource_preflight, campaign, sources)
    return campaign


def lock_campaign(fds: CampaignFDs) -> int:
    # The campaign directory inode is the authoritative lock. Replacing the
    # run.lock metadata entry cannot create a second lock domain.
    try:
        fcntl.flock(fds.campaign_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise Slice0Error(
            "campaign-lock-held", exit_code=3, reason="campaign-lock-held"
        ) from exc
    fd = os.open(
        "run.lock",
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
        dir_fd=fds.campaign_fd,
    )
    return fd


def validate_owner_record(
    owner: Any,
    campaign_id: str,
) -> tuple[str, int, str]:
    required = {
        "schema_version",
        "run_id",
        "campaign_id",
        "hostname",
        "pid",
        "process_start_ticks",
        "started_at",
        "input_intent_digest",
    }
    if not isinstance(owner, dict) or set(owner) != required:
        raise ValueError("owner record shape is invalid")
    pid = owner["pid"]
    hostname = require_utf8(owner["hostname"], "owner hostname")
    run_id = require_utf8(owner["run_id"], "owner run ID")
    start_ticks = require_utf8(owner["process_start_ticks"], "owner process identity")
    if (
        owner["schema_version"] != "deja-review-slice0-owner/v1"
        or not run_id
        or owner["campaign_id"] != campaign_id
        or type(pid) is not int
        or pid <= 0
        or not start_ticks.isdigit()
        or not isinstance(owner["input_intent_digest"], str)
        or not HEX64_RE.fullmatch(owner["input_intent_digest"])
    ):
        raise ValueError("owner record identity is invalid")
    parse_rfc3339(owner["started_at"], "owner started_at")
    return hostname, pid, start_ticks


def reclaim_stale_owner(
    fds: CampaignFDs,
    run_id: str,
    started: float,
) -> bool:
    try:
        owner = safe_json_loads(
            read_at(fds.campaign_fd, "run-owner.json"),
            reason="owner-unverifiable",
        )
    except FileNotFoundError:
        return False
    except Slice0Error as exc:
        raise Slice0Error("existing owner metadata is invalid", reason="owner-unverifiable") from exc
    try:
        heartbeat = safe_json_loads(
            read_at(fds.campaign_fd, "heartbeat.json"),
            reason="owner-unverifiable",
        )
        hostname, pid, start_ticks = validate_owner_record(owner, fds.campaign_id)
        recorded, _, heartbeat_run_id = validate_heartbeat_record(
            heartbeat,
            fds.campaign_id,
        )
        if heartbeat_run_id != owner["run_id"]:
            raise ValueError("heartbeat does not belong to the recorded owner")
        age = (dt.datetime.now(dt.timezone.utc) - recorded).total_seconds()
    except (
        FileNotFoundError,
        KeyError,
        TypeError,
        ValueError,
        AttributeError,
        Slice0Error,
    ) as exc:
        raise Slice0Error("owner liveness cannot be established", reason="owner-unverifiable") from exc
    if hostname != socket.gethostname() or age <= 300:
        raise Slice0Error("owner is remote or heartbeat is fresh", reason="owner-unverifiable")
    probed_ticks = process_start_ticks(pid)
    if probed_ticks is None and process_entry_exists(pid):
        raise Slice0Error(
            "recorded owner process identity cannot be probed",
            reason="owner-unverifiable",
        )
    if probed_ticks == start_ticks:
        raise Slice0Error("recorded owner process is still live", reason="owner-still-live")
    append_progress(
        fds,
        progress_event(
            run_id,
            fds.campaign_id,
            "discover",
            0,
            0,
            started=started,
            outcome="running",
            reason="stale-lock-reclaimed",
        ),
    )
    return True


def rollback_receipt_after_binding_failure(
    fds: CampaignFDs,
    binding_error: Slice0Error,
) -> None:
    cleanup_errors: list[str] = []
    try:
        os.unlink("prepare.json", dir_fd=fds.receipt_fd)
    except OSError as exc:
        cleanup_errors.append(safe_oserror_context("receipt-unlink", exc))
    try:
        os.fsync(fds.receipt_fd)
    except OSError as exc:
        cleanup_errors.append(safe_oserror_context("receipt-directory-fsync", exc))
    if cleanup_errors:
        raise Slice0Error(
            f"{binding_error}; receipt rollback failed: {', '.join(cleanup_errors)}",
            reason=binding_error.reason,
        ) from binding_error
    raise binding_error


def cleanup_owner_after_success(fds: CampaignFDs) -> None:
    cleanup_errors: list[str] = []
    try:
        os.unlink("run-owner.json", dir_fd=fds.campaign_fd)
    except FileNotFoundError:
        pass
    except OSError as exc:
        cleanup_errors.append(safe_oserror_context("owner-removal", exc))
    try:
        os.fsync(fds.campaign_fd)
    except OSError as exc:
        cleanup_errors.append(safe_oserror_context("owner-directory-fsync", exc))
    if cleanup_errors:
        print(
            "deja-review-slice0: post-receipt-cleanup-warning: "
            + ", ".join(cleanup_errors),
            file=sys.stderr,
        )


def prepare(args: argparse.Namespace) -> int:
    campaign_id = validate_campaign_id(args.campaign_id)
    fds = open_campaign(args.state_root, campaign_id, create=True)
    lock_fd = -1
    owner_published = False
    watchdog: RunWatchdog | None = None
    run_id = uuid.uuid4().hex
    started = time.monotonic()
    try:
        lock_fd = lock_campaign(fds)
        existing_receipt = False
        try:
            os.stat("prepare.json", dir_fd=fds.receipt_fd, follow_symlinks=False)
            existing_receipt = True
        except FileNotFoundError:
            pass
        owner_was_present = False
        try:
            os.stat("run-owner.json", dir_fd=fds.campaign_fd, follow_symlinks=False)
            owner_was_present = True
        except FileNotFoundError:
            pass
        reclaimed_owner = reclaim_stale_owner(fds, run_id, started)
        metas = source_metadata(
            args.source,
            forbidden_tree=(fds.root_path / campaign_id).resolve(strict=True),
        )
        impl_sha = implementation_sha()
        intent = intent_digest(metas, impl_sha)
        owner_ticks = process_start_ticks(os.getpid())
        if owner_ticks is None or not owner_ticks.isdigit():
            raise Slice0Error(
                "current process identity cannot be established",
                reason="owner-identity-unavailable",
            )
        owner = {
            "schema_version": "deja-review-slice0-owner/v1",
            "run_id": run_id,
            "campaign_id": campaign_id,
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "process_start_ticks": owner_ticks,
            "started_at": utc_now(),
            "input_intent_digest": intent,
        }
        watchdog = RunWatchdog(
            fds,
            run_id,
            deadline=started + DEADLINE_SECONDS,
        )
        watchdog.start()
        atomic_write_at(fds.campaign_fd, "run-owner.json", canonical_bytes(owner), run_id)
        owner_published = True
        append_progress(
            fds,
            progress_event(
                run_id, campaign_id, "discover", len(metas), len(metas),
                started=started, outcome="running", digest=intent,
            ),
        )
        watchdog.update("discover", len(metas), len(metas))
        preflight: dict[str, Any] | None = None
        if not existing_receipt:
            campaign_path = fds.root_path / campaign_id
            preflight = resource_projection(
                metas,
                campaign_path,
                retained_bytes=retained_campaign_bytes(fds.campaign_fd),
            )
            if not preflight["disk_pass"]:
                raise Slice0Error(
                    "insufficient disk headroom",
                    reason="disk-preflight-failed",
                )
            if not preflight["memory_pass"]:
                raise Slice0Error(
                    "insufficient memory headroom",
                    reason="memory-preflight-failed",
                )

        source_entries: list[dict[str, Any]] = []
        source_shas: set[str] = set()
        spools: list[tuple[str, str, int]] = []
        occurrence_ids: set[str] = set()
        finding_count = 0
        for index, meta in enumerate(metas, start=1):
            watchdog.check_deadline()
            if not existing_receipt:
                if preflight is None:
                    raise Slice0Error(
                        "generation resource preflight is unavailable",
                        reason="memory-preflight-unavailable",
                    )
                incremental = preflight["per_source_projection"][index - 1][
                    "projected_incremental_memory"
                ]
                if effective_memory_headroom() < math.ceil(
                    RESOURCE_MARGIN * incremental
                ):
                    raise Slice0Error(
                        "per-artifact memory headroom failed",
                        reason="memory-preflight-failed",
                    )
            captured, digest = read_source(meta)
            if digest in source_shas:
                raise Slice0Error(
                    "distinct source files have identical bytes",
                    reason="duplicate-source-content",
                )
            source_shas.add(digest)
            source_entries.append({"path": meta.path, "sha256": digest, "bytes": len(captured)})
            if existing_receipt:
                recorded = utc_now()
                append_progress(
                    fds,
                    progress_event(
                        run_id, campaign_id, "normalize", index, len(metas),
                        started=started, outcome="running", digest=digest,
                    ),
                )
                watchdog.update("normalize", index, len(metas))
                captured = b""
                continue
            try:
                payload = validate_artifact(
                    safe_json_loads(captured, reason="invalid-input")
                )
                records = normalize_artifact(payload, meta.path, digest)
            except Slice0Error:
                raise
            except jsonschema.ValidationError as exc:
                raise Slice0Error(
                    "source artifact cannot be safely decoded or validated",
                    reason="invalid-input",
                ) from exc
            for record in records:
                if record["occurrence_id"] in occurrence_ids:
                    raise Slice0Error("duplicate occurrence ID", reason="duplicate-occurrence")
                occurrence_ids.add(record["occurrence_id"])
            spool_data = b"".join(canonical_bytes(item) for item in records)
            spool_name = f".{run_id}.artifact-{index:04d}.spool"
            spool_sha = atomic_write_at(
                fds.campaign_fd,
                spool_name,
                spool_data,
                run_id,
                validator=validate_jsonl_bytes,
            )
            spools.append((digest, spool_name, len(spool_data)))
            finding_count += len(records)
            captured = b""
            payload = {}
            records = []
            recorded = utc_now()
            append_progress(
                fds,
                progress_event(
                    run_id, campaign_id, "normalize", index, len(metas),
                    started=started, outcome="running", digest=spool_sha,
                ),
            )
            watchdog.update("normalize", index, len(metas))

        immutable = immutable_digest(source_entries, impl_sha)
        if existing_receipt:
            stored_campaign = safe_json_loads(
                read_at(fds.campaign_fd, "campaign.json"),
            )
            stored_immutable = (
                stored_campaign.get("immutable_input_digest")
                if isinstance(stored_campaign, dict)
                else None
            )
            if not isinstance(stored_immutable, str) or not HEX64_RE.fullmatch(
                stored_immutable
            ):
                raise Slice0Error(
                    "completed campaign immutable identity is invalid",
                    reason="invalid-output",
                )
            if stored_immutable != immutable:
                raise Slice0Error(
                    "completed campaign has different immutable inputs",
                    exit_code=4,
                    reason="immutable-input-mismatch",
                )
            campaign = validate_campaign_dir(fds)
            append_progress(
                fds,
                progress_event(
                    run_id, campaign_id, "publish", len(IMMUTABLE_OUTPUTS),
                    len(IMMUTABLE_OUTPUTS), started=started, outcome="success",
                    digest=immutable,
                    reason=(
                        "post-publish-recovery"
                        if owner_was_present or reclaimed_owner
                        else "exact-reuse"
                    ),
                ),
            )
            owner_published = False
            cleanup_owner_after_success(fds)
            return 0

        actual_spool = max((size for _, _, size in spools), default=0)
        if preflight is None:
            raise Slice0Error(
                "generation resource preflight is unavailable",
                reason="memory-preflight-unavailable",
            )
        headroom = effective_memory_headroom()
        if headroom < math.ceil(RESOURCE_MARGIN * (actual_spool + 1024 * 1024)):
            raise Slice0Error("publication memory headroom failed", reason="memory-preflight-failed")
        preflight.update(
            {
                "finding_count": finding_count,
                "normalized_bytes": sum(size for _, _, size in spools),
                "largest_actual_spool_bytes": actual_spool,
                "measured_peak_rss_bytes": current_rss_bytes(),
                "publication_memory_pass": True,
                "recorded_at": utc_now(),
            }
        )
        source_manifest = {
            "schema_version": "deja-review-slice0-source-digests/v1",
            "sources": sorted(source_entries, key=lambda item: item["path"]),
        }
        campaign = {
            "schema_version": CAMPAIGN_SCHEMA_VERSION,
            "campaign_id": campaign_id,
            "immutable_input_digest": immutable,
            "normalizer_implementation_sha": impl_sha,
            "artifact_count": len(source_entries),
            "finding_count": finding_count,
            "normalized_bytes": sum(size for _, _, size in spools),
            "created_at": utc_now(),
        }
        outputs_data = {
            "campaign.json": canonical_bytes(campaign),
            "source-digests.json": canonical_bytes(source_manifest),
            "normalizer-manifest.json": canonical_bytes(normalizer_manifest(impl_sha)),
            "resource-preflight.json": canonical_bytes(preflight),
        }
        validators = {
            "campaign.json": lambda data: validate_json_bytes(data, MANIFEST_SCHEMA),
            "normalized-findings.jsonl": validate_jsonl_bytes,
        }
        output_digests: dict[str, str] = {}
        for index, name in enumerate(IMMUTABLE_OUTPUTS, start=1):
            watchdog.check_deadline()
            if name == "normalized-findings.jsonl":
                digest, normalized_size, normalized_count = publish_jsonl_from_spools(
                    fds, spools, run_id
                )
                if normalized_size != campaign["normalized_bytes"] or normalized_count != finding_count:
                    raise Slice0Error(
                        "streamed normalized corpus counts mismatch",
                        reason="invalid-output",
                    )
                output_digests[name] = digest
            else:
                output_digests[name] = atomic_write_at(
                    fds.campaign_fd,
                    name,
                    outputs_data[name],
                    run_id,
                    validator=validators.get(name),
                )
            append_progress(
                fds,
                progress_event(
                    run_id, campaign_id, "publish", index, len(IMMUTABLE_OUTPUTS),
                    started=started, outcome="running", digest=output_digests[name],
                ),
            )
            watchdog.update("publish", index, len(IMMUTABLE_OUTPUTS))
        verify_campaign_binding(fds)
        validate_campaign_outputs(fds, output_digests, immutable)
        verify_campaign_binding(fds)
        receipt = {
            "schema_version": "deja-review-slice0-receipt/v1",
            "campaign_id": campaign_id,
            "immutable_input_digest": immutable,
            "outputs": output_digests,
            "published_at": utc_now(),
        }
        atomic_write_at(fds.receipt_fd, "prepare.json", canonical_bytes(receipt), run_id)
        try:
            verify_campaign_binding(fds)
        except Slice0Error as binding_error:
            rollback_receipt_after_binding_failure(fds, binding_error)
        validate_campaign_dir(fds)
        append_progress(
            fds,
            progress_event(
                run_id, campaign_id, "publish", len(IMMUTABLE_OUTPUTS),
                len(IMMUTABLE_OUTPUTS), started=started, outcome="success",
                digest=immutable,
            ),
        )
        owner_published = False
        cleanup_owner_after_success(fds)
        return 0
    except Exception as original_exc:
        exc = (
            original_exc
            if isinstance(original_exc, Slice0Error)
            else Slice0Error(
                (
                    safe_oserror_context("prepare-operation", original_exc)
                    if isinstance(original_exc, OSError)
                    else f"run operation failed: {type(original_exc).__name__}"
                ),
                reason="operation-failed",
            )
        )
        if watchdog is not None:
            watchdog.request_stop()
        if owner_published:
            cleanup_errors: list[str] = []
            try:
                append_progress(
                    fds,
                    progress_event(
                        run_id, campaign_id, "publish", 0, 0, started=started,
                        outcome="failure", reason=exc.reason,
                    ),
                )
            except OSError as cleanup_exc:
                cleanup_errors.append(
                    safe_oserror_context("terminal-progress", cleanup_exc)
                )
            except Slice0Error as cleanup_exc:
                detail = (
                    f": {cleanup_exc}"
                    if cleanup_exc.reason == "operation-failed"
                    else ""
                )
                cleanup_errors.append(
                    f"terminal-progress failed (reason={cleanup_exc.reason}){detail}"
                )
            try:
                os.unlink("run-owner.json", dir_fd=fds.campaign_fd)
            except FileNotFoundError:
                pass
            except OSError as cleanup_exc:
                cleanup_errors.append(
                    safe_oserror_context("owner-removal", cleanup_exc)
                )
            try:
                os.fsync(fds.campaign_fd)
            except OSError as cleanup_exc:
                cleanup_errors.append(
                    safe_oserror_context("owner-directory-fsync", cleanup_exc)
                )
            if cleanup_errors:
                print(
                    "deja-review-slice0: cleanup-failure: " + ", ".join(cleanup_errors),
                    file=sys.stderr,
                )
        if exc is original_exc:
            raise
        raise exc from original_exc
    finally:
        if watchdog is not None:
            watchdog.stop()
        if lock_fd >= 0:
            with contextlib.suppress(OSError):
                os.close(lock_fd)
        fds.close()


def validate_command(args: argparse.Namespace) -> int:
    path = Path(args.campaign_dir)
    campaign_id = path.name
    fds = open_campaign(str(path.parent), campaign_id, create=False)
    try:
        campaign = validate_campaign_dir(fds)
        print(canonical_bytes({"status": "valid", **campaign}).decode(), end="")
        return 0
    finally:
        fds.close()


def parse_rfc3339(value: Any, label: str) -> dt.datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} is not a string")
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{label} is timezone-naive")
    return parsed


def validate_progress_record(record: Any, campaign_id: str) -> dict[str, Any]:
    required = {
        "run_id",
        "campaign_id",
        "stage",
        "completed",
        "total",
        "last_item_digest",
        "elapsed_seconds",
        "outcome",
        "reason_code",
        "recorded_at",
    }
    if not isinstance(record, dict) or set(record) != required:
        raise ValueError("progress record shape is invalid")
    if (
        not isinstance(record["run_id"], str)
        or not record["run_id"]
        or record["campaign_id"] != campaign_id
        or record["stage"] not in {"discover", "normalize", "publish"}
        or record["outcome"] not in {"running", "success", "failure"}
    ):
        raise ValueError("progress identity or enum is invalid")
    completed = record["completed"]
    total = record["total"]
    if (
        isinstance(completed, bool)
        or isinstance(total, bool)
        or not isinstance(completed, int)
        or not isinstance(total, int)
        or completed < 0
        or total < 0
        or completed > total
    ):
        raise ValueError("progress counters are invalid")
    digest = record["last_item_digest"]
    reason = record["reason_code"]
    if digest is not None and (
        not isinstance(digest, str) or not HEX64_RE.fullmatch(digest)
    ):
        raise ValueError("progress digest is invalid")
    if reason not in PROGRESS_REASON_CODES:
        raise ValueError("progress reason is invalid")
    elapsed = record["elapsed_seconds"]
    if (
        isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or not math.isfinite(float(elapsed))
        or elapsed < 0
    ):
        raise ValueError("progress elapsed time is invalid")
    parse_rfc3339(record["recorded_at"], "progress recorded_at")
    return record


def owner_liveness_at_status(
    fds: CampaignFDs,
) -> tuple[bool, str | None, str | None]:
    try:
        owner = safe_json_loads(
            read_at(fds.campaign_fd, "run-owner.json"),
            reason="invalid-owner",
        )
    except FileNotFoundError:
        return False, None, None
    except Slice0Error:
        return True, "invalid-owner", None
    try:
        hostname, pid, ticks = validate_owner_record(owner, fds.campaign_id)
    except (KeyError, ValueError, Slice0Error):
        return True, "invalid-owner", None
    run_id = owner["run_id"]
    if hostname != socket.gethostname():
        return True, "owner-unverifiable", run_id
    probed_ticks = process_start_ticks(pid)
    if probed_ticks is None:
        return (
            (True, "owner-unverifiable", run_id)
            if process_entry_exists(pid)
            else (True, "owner-not-live", run_id)
        )
    if probed_ticks != ticks:
        return True, "owner-not-live", run_id
    return True, None, run_id


def validate_heartbeat_record(
    heartbeat: Any,
    campaign_id: str,
) -> tuple[dt.datetime, dt.datetime, str]:
    required = {
        "schema_version",
        "run_id",
        "campaign_id",
        "stage",
        "completed",
        "total",
        "last_progress_at",
        "recorded_at",
    }
    if not isinstance(heartbeat, dict) or set(heartbeat) != required:
        raise ValueError("heartbeat shape is invalid")
    completed = heartbeat["completed"]
    total = heartbeat["total"]
    run_id = heartbeat["run_id"]
    if (
        heartbeat["schema_version"] != "deja-review-slice0-heartbeat/v1"
        or not isinstance(run_id, str)
        or not run_id
        or heartbeat["campaign_id"] != campaign_id
        or heartbeat["stage"] not in {"discover", "normalize", "publish"}
        or type(completed) is not int
        or type(total) is not int
        or completed < 0
        or total < 0
        or completed > total
    ):
        raise ValueError("heartbeat identity or counters are invalid")
    recorded = parse_rfc3339(heartbeat["recorded_at"], "heartbeat recorded_at")
    progress_time = parse_rfc3339(
        heartbeat["last_progress_at"],
        "heartbeat last_progress_at",
    )
    return recorded, progress_time, run_id


def status_command(args: argparse.Namespace) -> int:
    path = Path(args.campaign_dir)
    campaign_id = path.name
    try:
        fds = open_campaign(str(path.parent), campaign_id, create=False)
    except Slice0Error as exc:
        if exc.reason == "campaign-not-found":
            print(json.dumps({"campaign_id": campaign_id, "state": "absent"}, sort_keys=True))
            return 0
        if exc.reason == "campaign-incomplete":
            print(
                json.dumps(
                    {
                        "campaign_id": campaign_id,
                        "state": "invalid",
                        "last_stage": None,
                        "completed": 0,
                        "total": 0,
                        "last_outcome": None,
                        "reason_code": "campaign-incomplete",
                        "immutable_input_digest": None,
                        "heartbeat_age_seconds": None,
                    },
                    sort_keys=True,
                )
            )
            return 2
        raise
    try:
        last: dict[str, Any] | None = None
        control_error: str | None = None
        try:
            progress = read_progress_at(fds, limit=16 * 1024 * 1024)
            if progress and (
                not progress.endswith(b"\n") or b"\r" in progress
            ):
                raise Slice0Error(
                    "progress JSONL has a noncanonical line boundary",
                    reason="invalid-progress",
                )
            for line in progress.splitlines():
                last = validate_progress_record(
                    safe_json_loads(line, reason="invalid-progress"),
                    campaign_id,
                )
        except FileNotFoundError:
            pass
        except Slice0Error as exc:
            control_error = (
                "progress-too-large"
                if exc.reason == "progress-too-large"
                else "invalid-progress"
            )
        except (json.JSONDecodeError, ValueError, TypeError):
            control_error = "invalid-progress"
        heartbeat_age = None
        heartbeat = None
        heartbeat_run_id: str | None = None
        progress_time: dt.datetime | None = None
        try:
            heartbeat = safe_json_loads(
                read_at(fds.campaign_fd, "heartbeat.json"),
                reason="invalid-heartbeat",
            )
            recorded, progress_time, heartbeat_run_id = validate_heartbeat_record(
                heartbeat,
                campaign_id,
            )
            heartbeat_age = max(0.0, (dt.datetime.now(dt.timezone.utc) - recorded).total_seconds())
        except FileNotFoundError:
            heartbeat = None
            control_error = control_error or "missing-heartbeat"
        except (KeyError, ValueError, TypeError, AttributeError, Slice0Error):
            heartbeat = None
            control_error = control_error or "invalid-heartbeat"
        receipt_exists = False
        try:
            os.stat("prepare.json", dir_fd=fds.receipt_fd, follow_symlinks=False)
            receipt_exists = True
        except FileNotFoundError:
            pass
        owner_exists, owner_error, owner_run_id = owner_liveness_at_status(fds)
        if (
            heartbeat_run_id is not None
            and owner_run_id is not None
            and heartbeat_run_id != owner_run_id
        ):
            heartbeat = None
            control_error = control_error or "invalid-heartbeat"
        return_code = 0
        if receipt_exists:
            try:
                validate_campaign_dir(fds)
                state = "complete"
                control_error = None
            except (Slice0Error, OSError, json.JSONDecodeError, jsonschema.ValidationError):
                state = "invalid"
                control_error = "invalid-receipt-or-output"
                return_code = 2
        elif owner_exists:
            if owner_error:
                state = "invalid"
                control_error = owner_error
                return_code = 2
            elif heartbeat is None or control_error:
                state = "invalid"
                return_code = 2
            elif progress_time is not None and (
                dt.datetime.now(dt.timezone.utc) - progress_time
            ).total_seconds() > STALL_SECONDS:
                state = "stalled"
            else:
                state = "running"
        elif last and last.get("outcome") == "failure":
            state = "invalid"
            return_code = 2
        else:
            state = "stale-owner" if heartbeat_age and heartbeat_age > 300 else "invalid"
            return_code = 2
        immutable = None
        try:
            campaign_display = safe_json_loads(
                read_at(fds.campaign_fd, "campaign.json"),
            )
            if isinstance(campaign_display, dict):
                candidate = campaign_display.get("immutable_input_digest")
                if isinstance(candidate, str) and HEX64_RE.fullmatch(candidate):
                    immutable = candidate
        except (FileNotFoundError, Slice0Error):
            pass
        output = {
            "campaign_id": campaign_id,
            "state": state,
            "last_stage": last.get("stage") if last else None,
            "completed": last.get("completed") if last else 0,
            "total": last.get("total") if last else 0,
            "last_outcome": last.get("outcome") if last else None,
            "reason_code": control_error or (last.get("reason_code") if last else None),
            "immutable_input_digest": immutable,
            "heartbeat_age_seconds": round(heartbeat_age, 6) if heartbeat_age is not None else None,
        }
        print(json.dumps(output, sort_keys=True))
        return return_code
    finally:
        fds.close()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--campaign-id", required=True)
    prepare_parser.add_argument("--state-root", required=True)
    prepare_parser.add_argument("--source", action="append", required=True)
    prepare_parser.set_defaults(func=prepare)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--campaign-dir", required=True)
    validate_parser.set_defaults(func=validate_command)
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--campaign-dir", required=True)
    status_parser.set_defaults(func=status_command)
    return root


def main(argv: list[str]) -> int:
    try:
        if argv and argv[0] == "_heartbeat-child":
            child_parser = argparse.ArgumentParser(add_help=False)
            child_parser.add_argument("--campaign-fd", required=True, type=int)
            child_parser.add_argument("--campaign-id", required=True)
            child_parser.add_argument("--run-id", required=True)
            child_parser.add_argument("--write-id", required=True)
            args = child_parser.parse_args(argv[1:])
            if not args.write_id.startswith(f"{args.run_id}."):
                raise Slice0Error(
                    "heartbeat write identity is invalid",
                    reason="heartbeat-write-failed",
                )
            return heartbeat_child(args)
        args = parser().parse_args(argv)
        return int(args.func(args))
    except Slice0Error as exc:
        print(f"deja-review-slice0: {exc.reason}: {exc}", file=sys.stderr)
        return exc.exit_code
    except (
        OSError,
        json.JSONDecodeError,
        UnicodeError,
        RecursionError,
        MemoryError,
        jsonschema.ValidationError,
    ) as exc:
        print(f"deja-review-slice0: invalid-input: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
