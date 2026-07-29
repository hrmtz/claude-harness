#!/usr/bin/env python3
"""Acknowledgement gate for sessions latched by fabricated-user-turn detection.

The hook is deliberately fail-open.  State and input failures are logged
best-effort and produce no hook decision.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import tempfile
import time
from typing import Iterator


VERSION = 1
SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
ASK_REASON = (
    "acknowledgement-gate quarantine: operator acknowledgement required "
    "for this outward action"
)
CONTROL_TOKENS = {";", ";;", ";&", ";;&", "&&", "||", "|", "|&", "&", "(", ")"}
WRAPPERS = {
    "command", "env", "exec", "flock", "nice", "nohup", "setsid", "stdbuf",
    "sudo", "timeout", "xargs",
}
SHELLS = {"bash", "dash", "ksh", "sh", "zsh"}
GH_MUTATIONS = {
    "issue": {
        "close", "comment", "create", "delete", "develop", "edit", "lock",
        "pin", "reopen", "transfer", "unlock", "unpin",
    },
    "pr": {
        "close", "comment", "create", "edit", "lock", "merge", "ready",
        "reopen", "review", "unlock",
    },
    "release": {"create", "delete", "delete-asset", "edit", "upload"},
    "repo": {"archive", "create", "delete", "edit", "fork", "rename", "sync"},
    "workflow": {"disable", "enable", "run"},
    "run": {"cancel", "delete", "rerun"},
    "label": {"clone", "create", "delete", "edit"},
    "secret": {"delete", "set"},
    "variable": {"delete", "set"},
    "gist": {"create", "delete", "edit", "rename"},
}


class GuardError(Exception):
    """Expected fail-open state or input error."""


def _state_dir() -> Path:
    override = os.environ.get("FABRICATED_USER_TURN_GUARD_STATE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".claude" / "state" / "fabricated_user_turn_guard"


def _validate_session_id(value: object) -> str:
    if not isinstance(value, str) or not SESSION_ID.fullmatch(value):
        raise GuardError("invalid session_id")
    return value


def _ensure_state_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
    ):
        raise GuardError("state directory is not a directory")
    os.chmod(path, 0o700)


def _log(message: str) -> None:
    """Write metadata-only diagnostics without ever affecting hook behavior."""
    try:
        directory = _state_dir()
        _ensure_state_dir(directory)
        path = directory / "guard.log"
        fd = os.open(
            path,
            os.O_WRONLY
            | os.O_APPEND
            | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            os.fchmod(fd, 0o600)
            stamp = dt.datetime.now(dt.timezone.utc).isoformat()
            os.write(fd, f"{stamp} {message}\n".encode())
        finally:
            os.close(fd)
    except Exception:
        pass


@contextlib.contextmanager
def _session_lock(session_id: str, timeout: float = 1.0) -> Iterator[Path]:
    directory = _state_dir()
    _ensure_state_dir(directory)
    lock_path = directory / f"{session_id}.lock"
    fd = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        os.close(fd)
        raise GuardError("unsafe lock file")
    os.fchmod(fd, 0o600)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise GuardError("lock failed") from exc
                if time.monotonic() >= deadline:
                    raise GuardError("lock timeout")
                time.sleep(0.01)
        yield directory / f"{session_id}.json"
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _read_state(path: Path) -> dict | None:
    try:
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise GuardError("unsafe state file")
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise GuardError("state read failed") from exc
    try:
        state = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise GuardError("state JSON malformed") from exc
    if (
        not isinstance(state, dict)
        or state.get("version") != VERSION
        or state.get("status") not in {"active", "acknowledged"}
        or not isinstance(state.get("armed_by"), str)
        or not isinstance(state.get("incident_fingerprint"), str)
        or not isinstance(state.get("pending_tool_use_ids"), list)
        or not all(isinstance(item, str) for item in state["pending_tool_use_ids"])
    ):
        raise GuardError("state schema invalid")
    return state


def _write_state(path: Path, state: dict) -> None:
    directory = path.parent
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=directory)
    temp_path = Path(name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
        dir_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def arm_session(session_id: object, fingerprint: str, armed_by: str) -> bool:
    """Arm a session for one detector/reason.  Return False on fail-open error."""
    try:
        sid = _validate_session_id(session_id)
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise GuardError("invalid fingerprint")
        if not isinstance(armed_by, str) or not re.fullmatch(
            r"[a-z][a-z0-9_]{0,63}", armed_by
        ):
            raise GuardError("invalid armed_by")
        with _session_lock(sid) as path:
            state = _read_state(path)
            if (
                state is not None
                and state["incident_fingerprint"] == fingerprint
                and state["armed_by"] == armed_by
            ):
                return True
            state = {
                "version": VERSION,
                "status": "active",
                "armed_by": armed_by,
                "incident_fingerprint": fingerprint,
                "detected_at": _now(),
                "pending_tool_use_ids": [],
            }
            _write_state(path, state)
        _log(f"armed session={sid} armed_by={armed_by}")
        return True
    except Exception as exc:
        _log(f"arm fail-open: {type(exc).__name__}")
        return False


def _tokenize(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="();<>|&")
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except (TypeError, ValueError) as exc:
        raise GuardError("command parse failed") from exc


def _segments(tokens: list[str]) -> Iterator[list[str]]:
    current: list[str] = []
    skip_redirect_target = False
    for token in tokens:
        if skip_redirect_target:
            skip_redirect_target = False
            continue
        if token in CONTROL_TOKENS:
            if current:
                yield current
                current = []
            continue
        if token in {"<", ">", "<<", ">>", "<>", ">&", "<&"}:
            if current and current[-1].isdigit():
                current.pop()
            skip_redirect_target = True
            continue
        current.append(token)
    if current:
        yield current


def _unwrap(tokens: list[str]) -> list[str]:
    index = 0
    assignment = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", re.DOTALL)
    while index < len(tokens):
        while index < len(tokens) and assignment.fullmatch(tokens[index]):
            index += 1
        if index >= len(tokens):
            return []
        base = os.path.basename(tokens[index])
        if base not in WRAPPERS:
            return tokens[index:]
        index += 1
        if base == "env":
            while index < len(tokens):
                token = tokens[index]
                if assignment.fullmatch(token):
                    index += 1
                elif token == "--":
                    index += 1
                    break
                elif token in {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}:
                    index += 2
                elif token.startswith("-"):
                    index += 1
                else:
                    break
        elif base == "timeout":
            while index < len(tokens) and tokens[index].startswith("-"):
                if tokens[index] in {"-k", "--kill-after", "-s", "--signal"}:
                    index += 2
                else:
                    index += 1
            index += 1  # duration
        elif base == "flock":
            while index < len(tokens) and tokens[index].startswith("-"):
                if tokens[index] in {"-w", "--wait", "-E", "--conflict-exit-code"}:
                    index += 2
                else:
                    index += 1
            index += 1  # lock file or fd
        elif base in {"nice", "sudo"}:
            value_options = (
                {"-n", "--adjustment"}
                if base == "nice"
                else {
                    "-C", "-D", "-g", "-h", "-p", "-R", "-r", "-t", "-u",
                    "--chdir", "--close-from", "--group", "--host", "--prompt",
                    "--role", "--type", "--user",
                }
            )
            while index < len(tokens) and tokens[index].startswith("-"):
                if tokens[index] in value_options:
                    index += 2
                else:
                    index += 1
        elif base == "xargs":
            value_options = {
                "-a", "--arg-file", "-d", "--delimiter", "-E", "-I",
                "--replace", "-L", "--max-lines", "-n", "--max-args", "-P",
                "--max-procs", "-s", "--max-chars",
            }
            while index < len(tokens) and tokens[index].startswith("-"):
                if tokens[index] in value_options:
                    index += 2
                else:
                    index += 1
        else:
            while index < len(tokens) and (tokens[index].startswith("-") or tokens[index] == "--"):
                index += 1
    return []


def _git_subcommand(args: list[str]) -> str | None:
    index = 1
    options_with_value = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
    while index < len(args):
        token = args[index]
        if token == "--":
            index += 1
            break
        if token in options_with_value:
            index += 2
            continue
        if any(token.startswith(f"{option}=") for option in options_with_value if option.startswith("--")):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        return token
    return args[index] if index < len(args) else None


def _gh_words(args: list[str]) -> tuple[str | None, str | None]:
    words: list[str] = []
    index = 1
    global_value_options = {"-R", "--repo", "--hostname"}
    while index < len(args):
        token = args[index]
        if token == "--":
            index += 1
            break
        if token in global_value_options:
            index += 2
            continue
        if token.startswith("--repo=") or token.startswith("--hostname="):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        words.append(token)
        index += 1
        if len(words) == 2:
            break
    while index < len(args) and len(words) < 2:
        if not args[index].startswith("-"):
            words.append(args[index])
        index += 1
    return (
        words[0] if words else None,
        words[1] if len(words) > 1 else None,
    )


def _segment_is_target(segment: list[str], depth: int = 0) -> bool:
    args = _unwrap(segment)
    if not args:
        return False
    executable = os.path.basename(args[0])
    if executable in SHELLS and depth < 4:
        for index, token in enumerate(args[1:], 1):
            if token in {"-c", "-lc", "-ic"} and index + 1 < len(args):
                return command_is_target(args[index + 1], depth + 1)
    if executable == "eval" and depth < 4:
        return command_is_target(" ".join(args[1:]), depth + 1)
    if executable == "discord-bot":
        return len(args) > 1 and args[1] == "post"
    if executable == "formation":
        return len(args) > 1 and args[1] in {"spawn", "msg", "resolve", "ack"}
    if executable == "mailbox-send":
        return True
    if executable == "git":
        return _git_subcommand(args) == "push"
    if executable == "gh":
        group, action = _gh_words(args)
        if group == "api":
            return True
        return bool(group in GH_MUTATIONS and action in GH_MUTATIONS[group])
    return False


def command_is_target(command: str, depth: int = 0) -> bool:
    if not isinstance(command, str) or not command or "\x00" in command:
        return False
    try:
        if depth < 4:
            for nested in _nested_commands(command):
                if command_is_target(nested, depth + 1):
                    return True
            for line in _unquoted_lines(command):
                if line != command and command_is_target(line, depth + 1):
                    return True
        return any(
            _segment_is_target(segment, depth)
            for segment in _segments(_tokenize(command))
        )
    except GuardError:
        return False


def _unquoted_lines(command: str) -> Iterator[str]:
    """Split shell source at unquoted newlines."""
    single = False
    double = False
    escaped = False
    start = 0
    for index, char in enumerate(command):
        if escaped:
            escaped = False
            continue
        if char == "\\" and not single:
            escaped = True
            continue
        if char == "'" and not double:
            single = not single
            continue
        if char == '"' and not single:
            double = not double
            continue
        if char == "\n" and not single and not double:
            yield command[start:index]
            start = index + 1
    yield command[start:]


def _nested_commands(command: str) -> Iterator[str]:
    """Yield executable backtick and ``$(...)`` command substitutions."""
    single = False
    double = False
    escaped = False
    index = 0
    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\" and not single:
            escaped = True
            index += 1
            continue
        if char == "'" and not double:
            single = not single
            index += 1
            continue
        if char == '"' and not single:
            double = not double
            index += 1
            continue
        if char == "`" and not single:
            end = index + 1
            inner_escaped = False
            while end < len(command):
                if inner_escaped:
                    inner_escaped = False
                elif command[end] == "\\":
                    inner_escaped = True
                elif command[end] == "`":
                    yield command[index + 1 : end]
                    index = end
                    break
                end += 1
        elif not single and command.startswith("$(", index):
            end = _matching_paren(command, index + 2)
            if end is not None:
                yield command[index + 2 : end]
                index = end
        index += 1


def _matching_paren(command: str, start: int) -> int | None:
    depth = 1
    single = False
    double = False
    escaped = False
    index = start
    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
        elif char == "\\" and not single:
            escaped = True
        elif char == "'" and not double:
            single = not single
        elif char == '"' and not single:
            double = not double
        elif not single and not double:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return index
        index += 1
    return None


def _payload_targeted(payload: dict) -> bool:
    tool_name = payload.get("tool_name")
    if tool_name in {"Agent", "Task"}:
        return True
    if tool_name != "Bash":
        return False
    tool_input = payload.get("tool_input")
    return (
        isinstance(tool_input, dict)
        and isinstance(tool_input.get("command"), str)
        and command_is_target(tool_input["command"])
    )


def pre_tool_use(payload: dict) -> dict | None:
    try:
        if not _payload_targeted(payload):
            return None
        sid = _validate_session_id(payload.get("session_id"))
        tool_use_id = payload.get("tool_use_id")
        if not isinstance(tool_use_id, str) or not tool_use_id:
            raise GuardError("invalid tool_use_id")
        with _session_lock(sid) as path:
            state = _read_state(path)
            if state is None or state["status"] != "active":
                return None
            if tool_use_id not in state["pending_tool_use_ids"]:
                state["pending_tool_use_ids"].append(tool_use_id)
                _write_state(path, state)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": ASK_REASON,
            }
        }
    except Exception as exc:
        _log(f"pre fail-open: {type(exc).__name__}")
        return None


def post_tool_use(payload: dict) -> None:
    try:
        sid = _validate_session_id(payload.get("session_id"))
        tool_use_id = payload.get("tool_use_id")
        if not isinstance(tool_use_id, str) or not tool_use_id:
            raise GuardError("invalid tool_use_id")
        with _session_lock(sid) as path:
            state = _read_state(path)
            if (
                state is None
                or state["status"] != "active"
                or tool_use_id not in state["pending_tool_use_ids"]
            ):
                return
            state["status"] = "acknowledged"
            state["acknowledged_tool_use_id"] = tool_use_id
            state["acknowledged_at"] = _now()
            state["pending_tool_use_ids"] = []
            _write_state(path, state)
    except Exception as exc:
        _log(f"post fail-open: {type(exc).__name__}")


def _hook() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise GuardError("payload is not an object")
        event = payload.get("hook_event_name")
        if event == "PreToolUse":
            output = pre_tool_use(payload)
            if output is not None:
                print(json.dumps(output, ensure_ascii=False))
        elif event in {"PostToolUse", "PostToolUseFailure"}:
            post_tool_use(payload)
    except Exception as exc:
        _log(f"hook fail-open: {type(exc).__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_hook())
