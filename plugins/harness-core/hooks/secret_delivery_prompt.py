#!/usr/bin/env python3
"""Mint a short-lived, one-use receipt for one explicit SCP delivery."""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit


MAX_INPUT_BYTES = 1024 * 1024
MAX_FILE_BYTES = 1024 * 1024
RECEIPT_TTL_SECONDS = 120
SESSION_RE = re.compile(r"[A-Za-z0-9._:][A-Za-z0-9._:-]{0,127}")
COMPONENT_RE = re.compile(r"[A-Za-z0-9._-]{1,255}")
USER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9._-]{0,63}")
HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,252}")
AUTH_RE = re.compile(
    r"AUTHORIZE_SECRET_DELIVERY source=(\S+) "
    r"destination=(scp://\S+) representation=file"
)


class DeliveryError(Exception):
    pass


def read_event() -> dict[str, object]:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if not raw or len(raw) > MAX_INPUT_BYTES:
        raise DeliveryError("INVALID_HOOK_INPUT")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeliveryError("INVALID_HOOK_INPUT") from exc
    if not isinstance(value, dict):
        raise DeliveryError("INVALID_HOOK_INPUT")
    return value


def unique_string(event: dict[str, object], names: tuple[str, ...], code: str) -> str:
    values: list[str] = []
    for name in names:
        value = event.get(name)
        if value is None:
            continue
        if not isinstance(value, str):
            raise DeliveryError(code)
        if value.strip():
            values.append(value.strip())
    unique = list(dict.fromkeys(values))
    if len(unique) != 1:
        raise DeliveryError(code)
    return unique[0]


def validate_absolute_path(raw: str) -> Path:
    if len(raw) > 4096 or not raw.startswith("/"):
        raise DeliveryError("INVALID_SOURCE")
    path = Path(raw)
    if any(not COMPONENT_RE.fullmatch(part) for part in path.parts[1:]):
        raise DeliveryError("INVALID_SOURCE")
    try:
        before = path.lstat()
    except OSError as exc:
        raise DeliveryError("INVALID_SOURCE") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise DeliveryError("INVALID_SOURCE")
    try:
        canonical = path.resolve(strict=True)
    except OSError as exc:
        raise DeliveryError("INVALID_SOURCE") from exc
    if len(str(canonical)) > 4096:
        raise DeliveryError("INVALID_SOURCE")
    return canonical


def parse_destination(raw: str) -> dict[str, object]:
    if len(raw) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise DeliveryError("INVALID_DESTINATION")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise DeliveryError("INVALID_DESTINATION") from exc
    if (
        parsed.scheme != "scp"
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.hostname
        or (port is not None and not (1 <= port <= 65535))
    ):
        raise DeliveryError("INVALID_DESTINATION")
    if parsed.username is not None and not USER_RE.fullmatch(parsed.username):
        raise DeliveryError("INVALID_DESTINATION")
    if not HOST_RE.fullmatch(parsed.hostname):
        raise DeliveryError("INVALID_DESTINATION")
    if not parsed.path.startswith("/") or len(parsed.path) > 4096:
        raise DeliveryError("INVALID_DESTINATION")
    if any(not COMPONENT_RE.fullmatch(part) for part in Path(parsed.path).parts[1:]):
        raise DeliveryError("INVALID_DESTINATION")
    return {
        "user": parsed.username,
        "host": parsed.hostname,
        "port": port,
        "path": parsed.path,
    }


def source_identity(path: Path) -> dict[str, object]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise DeliveryError("INVALID_SOURCE") from exc
    try:
        info = os.fstat(fd)
    finally:
        os.close(fd)
    if not stat.S_ISREG(info.st_mode) or not (1 <= info.st_size <= MAX_FILE_BYTES):
        raise DeliveryError("INVALID_SOURCE")
    return {
        "path": str(path),
        "device": info.st_dev,
        "inode": info.st_ino,
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
    }


def require_classified(path: Path) -> None:
    guard = Path(__file__).with_name("credential_file_read_guard.sh")
    try:
        result = subprocess.run(
            ["bash", str(guard), "--classify-path", str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeliveryError("SOURCE_NOT_CLASSIFIED") from exc
    if result.returncode != 0:
        raise DeliveryError("SOURCE_NOT_CLASSIFIED")


def state_receipts_dir() -> Path:
    root = Path.home() / ".claude" / "state" / "secret_delivery"
    receipts = root / "receipts"
    for directory in (root, receipts):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        if directory.is_symlink() or not directory.is_dir():
            raise DeliveryError("UNSAFE_STATE")
    return receipts


def mint_receipt(
    source: dict[str, object],
    destination: dict[str, object],
    session_id: str,
) -> tuple[str, int]:
    receipts = state_receipts_dir()
    now = int(time.time())
    expires = now + RECEIPT_TTL_SECONDS
    receipt_id = secrets.token_hex(32)
    payload = {
        "version": 1,
        "receipt_id": receipt_id,
        "source": source,
        "destination": destination,
        "representation": "file",
        "session_id": session_id,
        "created_at": now,
        "expires_at": expires,
    }
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    target = receipts / f"{receipt_id}.json"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)
    directory_fd = os.open(receipts, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return receipt_id, expires


def emit_context(content: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": content,
                }
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def main() -> int:
    try:
        event = read_event()
        prompt_names = ("prompt", "userPrompt", "user_prompt")
        if not any(name in event for name in prompt_names):
            # Kimi's UserPromptSubmit payload has no supported prompt field.
            # Without a prompt we cannot identify an authorization request, so
            # emitting REFUSED would be a false delivery decision.
            return 0
        prompt = unique_string(event, prompt_names, "INVALID_PROMPT")
        match = AUTH_RE.fullmatch(prompt)
        if match is None:
            return 0
        session_id = unique_string(
            event, ("session_id", "sessionId"), "INVALID_SESSION"
        )
        if SESSION_RE.fullmatch(session_id) is None:
            raise DeliveryError("INVALID_SESSION")
        source_path = validate_absolute_path(match.group(1))
        destination = parse_destination(match.group(2))
        source = source_identity(source_path)
        require_classified(source_path)
        receipt_id, expires = mint_receipt(source, destination, session_id)
        emit_context(
            "SECRET_DELIVERY_AUTHORIZED "
            f"receipt={receipt_id} expires_at={expires}. "
            "Chat plaintext remains forbidden. Run exactly: "
            "harness-hook harness-core bin/harness-secret-deliver "
            f"--receipt {receipt_id} --session {session_id}"
        )
        return 0
    except DeliveryError as exc:
        emit_context(f"SECRET_DELIVERY_REFUSED reason={exc}")
        return 0
    except Exception:
        emit_context("SECRET_DELIVERY_REFUSED reason=INTERNAL_ERROR")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
