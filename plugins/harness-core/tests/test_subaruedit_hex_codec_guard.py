#!/usr/bin/env python3
"""Claude/Codex compatibility tests for subaruEdit PC1 path enforcement."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


GUARD = Path(__file__).resolve().parents[1] / "hooks/subaruedit_hex_codec_guard.py"


def run(command: str, *, camel_case: bool = False) -> subprocess.CompletedProcess[str]:
    holder = "toolInput" if camel_case else "tool_input"
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        holder: {"command": command},
    }
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=10,
    )


def decision(result: subprocess.CompletedProcess[str]) -> str | None:
    assert result.returncode == 0, result.stderr
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]


@pytest.mark.parametrize("camel_case", [False, True], ids=["claude-codex", "camel-alias"])
@pytest.mark.parametrize(
    "command",
    [
        "python3 scripts/epifan_roundtrip.py encrypt --in combined.bin --out bad.hex",
        "cd repo && python3 scripts/epifan_roundtrip.py encrypt --in x --out y.hex",
        "env MODE=test /usr/bin/python3.12 ./scripts/epifan_roundtrip.py encrypt --in x",
        "bash -c 'python3 scripts/epifan_roundtrip.py encrypt --in x --out y.hex'",
        "./scripts/epifan_roundtrip.py encrypt --in x --out y.hex",
        "python3 -m scripts.epifan_roundtrip encrypt --in x --out y.hex",
    ],
)
def test_obsolete_xor_encrypt_is_denied(command: str, camel_case: bool) -> None:
    assert decision(run(command, camel_case=camel_case)) == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "python3 scripts/subaruedit_cal.py pack patched.rom base.hex out.hex",
        "python3 scripts/subaruedit_cal.py verify out.hex",
        "python3 scripts/epifan_roundtrip.py decrypt --in archive.hex --out plain.bin",
        "pytest -q tests/test_epifan_roundtrip.py",
        "rg -n 'epifan_roundtrip.py encrypt' scripts docs",
        "git commit -m 'block epifan_roundtrip.py encrypt'",
        "printf '%s\\n' 'epifan_roundtrip.py encrypt is forbidden'",
    ],
)
def test_legitimate_or_documentary_commands_pass(command: str) -> None:
    assert decision(run(command)) is None


def test_deny_reason_names_only_valid_replacement() -> None:
    result = run("python3 scripts/epifan_roundtrip.py encrypt --in x --out bad.hex")
    reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "subaruedit_cal.py pack" in reason
    assert "subaruedit_cal.py verify" in reason
    assert "全 gate PASS" in reason
    assert "XOR round-trip成功はPC1 acceptanceを証明しない" in reason


@pytest.mark.parametrize("payload", ["not-json", "[]", '{"tool_input":{"command":7}}'])
def test_malformed_payload_fails_closed(payload: str) -> None:
    result = subprocess.run(
        [sys.executable, str(GUARD)],
        input=payload,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert not result.stdout.strip()
