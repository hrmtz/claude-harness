#!/usr/bin/env python3
"""PreToolUse guard: refuse the obsolete XOR path for subaruEdit ``.hex`` files.

Claude Code and Codex use the same deny response but may spell the tool-input
container as ``tool_input`` or ``toolInput``.  Keep parsing and classification
here so both hook adapters enforce one rule:

``epifan_roundtrip.py encrypt`` is never a flash/container build path.
Use ``subaruedit_cal.py pack`` and require every PC1 open gate to pass.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from typing import Iterable


MAX_INPUT_BYTES = 4 * 1024 * 1024
CONTROL = {";", "&&", "||", "|", "|&", "&", "\n"}
SHELLS = {"bash", "dash", "ksh", "sh", "zsh"}
SIMPLE_WRAPPERS = {"command", "exec", "nohup"}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

DENY_REASON = """subaruEdit/epifanSoftware 用 .hex に epifan_roundtrip.py encrypt を使えない。

epifan_roundtrip は旧 XOR 自己試験モデル。PC1 container、dword-offset XOR、
header/payload/inner CRC open gate を生成しないため、出力 .hex はepifanで読めない。

正規経路:
  python3 scripts/subaruedit_cal.py pack <patched.rom> <matching-base.hex> <out.hex>
  python3 scripts/subaruedit_cal.py verify <out.hex>  # 全 gate PASS 必須

telemetry適用込みなら scripts/apply_telemetry_piggyback_to_rom.py を使う。
XOR round-trip成功はPC1 acceptanceを証明しない。"""


def _tokens(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|\n")
    lexer.whitespace_split = True
    lexer.whitespace = " \t\r"
    lexer.commenters = ""
    raw = list(lexer)
    tokens: list[str] = []
    operators = re.compile(r"&&|\|\||\|&|[;|&\n]")
    for token in raw:
        if token and all(character in ";&|\n" for character in token):
            tokens.extend(operators.findall(token))
        else:
            tokens.append(token)
    return tokens


def _segments(tokens: Iterable[str]) -> Iterable[list[str]]:
    segment: list[str] = []
    for token in tokens:
        if token in CONTROL:
            if segment:
                yield segment
                segment = []
        else:
            segment.append(token)
    if segment:
        yield segment


def _strip_prefix(tokens: list[str]) -> list[str]:
    work = list(tokens)
    while work and ASSIGNMENT.match(work[0]):
        work.pop(0)
    while work:
        executable = os.path.basename(work[0])
        if executable in SIMPLE_WRAPPERS:
            work.pop(0)
            while work and work[0].startswith("-"):
                work.pop(0)
            continue
        if executable == "env":
            work.pop(0)
            while work and (work[0].startswith("-") or ASSIGNMENT.match(work[0])):
                work.pop(0)
            continue
        if executable in {"sudo", "doas"}:
            work.pop(0)
            while work and work[0].startswith("-"):
                work.pop(0)
            continue
        if executable == "timeout":
            work.pop(0)
            while work and work[0].startswith("-"):
                work.pop(0)
            if work:
                work.pop(0)  # duration
            continue
        break
    return work


def _python_invokes_epifan_encrypt(argv: list[str]) -> bool:
    args = argv[1:]
    index = 0
    while index < len(args):
        token = args[index]
        if token in {"-c", "--command"}:
            return False
        if token == "-m" and index + 1 < len(args):
            module = args[index + 1]
            return module.endswith("epifan_roundtrip") and "encrypt" in args[index + 2 :]
        if token == "--":
            index += 1
            break
        if token.startswith("-"):
            index += 1
            continue
        break
    if index >= len(args):
        return False
    script = os.path.basename(args[index])
    return script == "epifan_roundtrip.py" and "encrypt" in args[index + 1 :]


def _segment_is_denied(segment: list[str], depth: int) -> bool:
    work = _strip_prefix(segment)
    if not work:
        return False
    executable = os.path.basename(work[0])
    if executable in SHELLS and depth < 4:
        for index, token in enumerate(work[1:], 1):
            if token == "-c" and index + 1 < len(work):
                return command_is_denied(work[index + 1], depth + 1)
        return False
    if executable == "epifan_roundtrip.py":
        return "encrypt" in work[1:]
    if re.fullmatch(r"python(?:[23](?:\.\d+)?)?", executable):
        return _python_invokes_epifan_encrypt(work)
    return False


def command_is_denied(command: str, depth: int = 0) -> bool:
    try:
        return any(_segment_is_denied(segment, depth) for segment in _segments(_tokens(command)))
    except ValueError:
        # Invalid shell quoting plus both load-bearing terms: conservative deny.
        return "epifan_roundtrip" in command and re.search(r"\bencrypt\b", command) is not None


def _command(payload: object) -> str:
    if not isinstance(payload, dict):
        raise ValueError("hook payload must be an object")
    values: list[str] = []
    for holder in ("tool_input", "toolInput"):
        container = payload.get(holder)
        if container is None:
            continue
        if not isinstance(container, dict):
            raise ValueError(f"{holder} must be an object")
        value = container.get("command")
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError(f"{holder}.command must be a string")
        if value.strip():
            values.append(value)
    if len(set(values)) > 1:
        raise ValueError("conflicting command aliases")
    return values[0] if values else ""


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("hook input exceeds size limit")
    payload = json.loads(raw)
    command = _command(payload)
    if command and command_is_denied(command):
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": DENY_REASON,
            }
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, json.JSONDecodeError) as error:
        print(f"subaruEdit hex codec guard input invalid: {error}; refusing tool execution", file=sys.stderr)
        raise SystemExit(2)
