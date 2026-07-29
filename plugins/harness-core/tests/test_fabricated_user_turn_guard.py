#!/usr/bin/env python3
"""Acceptance tests for the fabricated-user-turn acknowledgement gate."""
from __future__ import annotations

import concurrent.futures
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "fabricated_user_turn_guard.py"
ADVISOR = ROOT / "hooks" / "fabricated_user_turn_advisor.py"
HOOKS_JSON = ROOT / "hooks" / "hooks.json"
SPEC = importlib.util.spec_from_file_location("fabricated_user_turn_guard", HOOK)
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)

SESSION = "123e4567-e89b-12d3-a456-426614174000"
FP1 = hashlib.sha256(b"incident one").hexdigest()
FP2 = hashlib.sha256(b"incident two").hexdigest()


def state_path(directory: Path) -> Path:
    return directory / f"{SESSION}.json"


def read_state(directory: Path) -> dict:
    return json.loads(state_path(directory).read_text())


def payload(event: str, tool_id: str, tool_name: str = "Agent", command: str = "") -> dict:
    result = {
        "hook_event_name": event,
        "session_id": SESSION,
        "tool_use_id": tool_id,
        "tool_name": tool_name,
        "tool_input": {},
    }
    if tool_name == "Bash":
        result["tool_input"]["command"] = command
    return result


def run_hook(data: object, directory: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["FABRICATED_USER_TURN_GUARD_STATE_DIR"] = str(directory)
    return subprocess.run(
        ["python3", str(HOOK)],
        input=json.dumps(data),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_arm_idempotence_rearm_and_permissions(monkeypatch, tmp_path):
    monkeypatch.setenv("FABRICATED_USER_TURN_GUARD_STATE_DIR", str(tmp_path))
    assert guard.arm_session(SESSION, FP1, "fabricated_user_turn")
    first = read_state(tmp_path)
    assert first["status"] == "active"
    assert first["armed_by"] == "fabricated_user_turn"
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE(state_path(tmp_path).stat().st_mode) == 0o600

    assert guard.pre_tool_use(payload("PreToolUse", "tool-approved"))
    guard.post_tool_use(payload("PostToolUse", "tool-approved"))
    acknowledged = read_state(tmp_path)
    assert acknowledged["status"] == "acknowledged"

    assert guard.arm_session(SESSION, FP1, "fabricated_user_turn")
    assert read_state(tmp_path) == acknowledged
    assert guard.arm_session(SESSION, FP2, "fabricated_user_turn")
    rearmed = read_state(tmp_path)
    assert rearmed["status"] == "active"
    assert rearmed["incident_fingerprint"] == FP2
    assert rearmed["pending_tool_use_ids"] == []


def test_pre_post_ack_semantics(monkeypatch, tmp_path):
    monkeypatch.setenv("FABRICATED_USER_TURN_GUARD_STATE_DIR", str(tmp_path))
    assert guard.arm_session(SESSION, FP1, "fabricated_user_turn")

    ask = guard.pre_tool_use(payload("PreToolUse", "denied"))
    assert ask["hookSpecificOutput"]["permissionDecision"] == "ask"
    # A denied tool has no PostToolUse edge.  Its pending ID cannot clear state.
    assert read_state(tmp_path)["status"] == "active"

    guard.post_tool_use(payload("PostToolUse", "wrong-id"))
    assert read_state(tmp_path)["status"] == "active"

    ask = guard.pre_tool_use(payload("PreToolUse", "attempted-but-failed"))
    assert ask["hookSpecificOutput"]["permissionDecision"] == "ask"
    guard.post_tool_use(payload("PostToolUseFailure", "attempted-but-failed"))
    state = read_state(tmp_path)
    assert state["status"] == "acknowledged"
    assert state["acknowledged_tool_use_id"] == "attempted-but-failed"

    assert guard.arm_session(SESSION, FP2, "fabricated_user_turn")
    guard.post_tool_use(payload("PostToolUse", "denied"))
    assert read_state(tmp_path)["status"] == "active"


def test_parallel_pending_and_stop_post_race(monkeypatch, tmp_path):
    monkeypatch.setenv("FABRICATED_USER_TURN_GUARD_STATE_DIR", str(tmp_path))
    assert guard.arm_session(SESSION, FP1, "fabricated_user_turn")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        asks = list(
            pool.map(
                guard.pre_tool_use,
                [payload("PreToolUse", f"parallel-{index}") for index in range(8)],
            )
        )
    assert all(item["hookSpecificOutput"]["permissionDecision"] == "ask" for item in asks)
    assert set(read_state(tmp_path)["pending_tool_use_ids"]) == {
        f"parallel-{index}" for index in range(8)
    }

    # Whichever lock wins, the old Post ID cannot acknowledge the new fingerprint.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            pool.submit(
                guard.arm_session, SESSION, FP2, "fabricated_user_turn"
            ),
            pool.submit(guard.post_tool_use, payload("PostToolUse", "parallel-0")),
        ]
        for result in results:
            result.result()
    state = read_state(tmp_path)
    assert state["status"] == "active"
    assert state["incident_fingerprint"] == FP2


def test_command_matching_selected_and_excluded():
    selected = [
        "discord-bot post general hello",
        "formation spawn brief.md worker",
        "formation msg worker hello",
        "formation resolve request result",
        "formation ack request",
        "mailbox-send %1 hello",
        "git push origin HEAD",
        "git -C /repo push origin dev",
        "command git push",
        "env LC_ALL=C git -C /repo push",
        "true && git push",
        "printf x | gh api repos/o/r/issues -f title=x",
        "echo `git push origin HEAD`",
        "echo \"$(git push origin HEAD)\"",
        "echo \"it's done\" `git push origin HEAD`",
        "2>/dev/null git push origin HEAD",
        "eval 'formation msg worker hello'",
        "git status\ngit push origin HEAD",
        "cd /repo\ngit push origin HEAD",
        "timeout 60 git push origin HEAD",
        "sudo -u builder git push origin HEAD",
        "nice -n 5 git push origin HEAD",
        "setsid git push origin HEAD",
        "stdbuf -oL git push origin HEAD",
        "flock /tmp/repo.lock git push origin HEAD",
        "xargs -I{} git push origin HEAD",
        "gh api repos/o/r",
        "gh issue create --title x",
        "gh pr merge 12",
        "gh release upload v1 file",
        "gh repo edit --visibility private",
        "gh workflow run ci.yml",
        "gh run rerun 123",
        "gh label create bug",
        "gh secret set NAME",
        "gh variable delete NAME",
        "gh gist edit abc",
        "bash -lc 'git -C /repo push origin dev'",
    ]
    excluded = [
        "git status",
        "git commit -m x",
        "formation report done",
        "formation done complete",
        "gh issue list",
        "gh issue view 1",
        "gh pr diff 1",
        "gh pr checkout 1",
        "gh release view v1",
        "gh repo view",
        "gh workflow view ci.yml",
        "gh run watch 123",
        "echo 'git push'",
        "echo '`git push`'",
        "printf '%s' 'formation msg worker hello'",
        "echo discord-bot post",
        "python3 -m pytest",
    ]
    for command in selected:
        assert guard.command_is_target(command), command
    for command in excluded:
        assert not guard.command_is_target(command), command
    assert not guard.command_is_target("git push 'unterminated")
    assert not guard.command_is_target("")


def test_tool_classes_and_fail_open(monkeypatch, tmp_path):
    monkeypatch.setenv("FABRICATED_USER_TURN_GUARD_STATE_DIR", str(tmp_path))
    assert guard.arm_session(SESSION, FP1, "fabricated_user_turn")
    for index, tool_name in enumerate(("Agent", "Task")):
        result = guard.pre_tool_use(payload("PreToolUse", f"spawn-{index}", tool_name))
        assert result["hookSpecificOutput"]["permissionDecision"] == "ask"
    result = guard.pre_tool_use(
        payload("PreToolUse", "bash-target", "Bash", "git -C /repo push")
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert guard.pre_tool_use(
        payload("PreToolUse", "bash-read", "Bash", "gh issue view 154")
    ) is None
    assert guard.pre_tool_use(payload("PreToolUse", "read", "Read")) is None

    malformed = run_hook({"hook_event_name": "PreToolUse"}, tmp_path)
    assert malformed.returncode == 0 and malformed.stdout == ""
    invalid = run_hook(
        {
            **payload("PreToolUse", "bad-session"),
            "session_id": "../../escape",
        },
        tmp_path,
    )
    assert invalid.returncode == 0 and invalid.stdout == ""

    state_path(tmp_path).write_text("{not json")
    corrupt = run_hook(payload("PreToolUse", "corrupt"), tmp_path)
    assert corrupt.returncode == 0 and corrupt.stdout == ""
    assert (tmp_path / "guard.log").is_file()
    assert stat.S_IMODE((tmp_path / "guard.log").stat().st_mode) == 0o600


def test_advisor_arms_and_reports_arm_failure(tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "報告。\n\nuser 頼んだ"}],
                },
            }
        )
        + "\n"
    )
    state_dir = tmp_path / "state"
    env = os.environ.copy()
    env["FABRICATED_USER_TURN_GUARD_STATE_DIR"] = str(state_dir)
    result = subprocess.run(
        ["python3", str(ADVISOR)],
        input=json.dumps({"session_id": SESSION, "transcript_path": str(transcript)}),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0
    assert "gate not armed" not in json.loads(result.stdout)["systemMessage"]
    assert read_state(state_dir)["status"] == "active"

    env["FABRICATED_USER_TURN_GUARD_STATE_DIR"] = "/dev/null/state"
    failed = subprocess.run(
        ["python3", str(ADVISOR)],
        input=json.dumps({"session_id": SESSION, "transcript_path": str(transcript)}),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert failed.returncode == 0
    assert "gate not armed" in json.loads(failed.stdout)["systemMessage"]

    isolated_advisor = tmp_path / "fabricated_user_turn_advisor.py"
    shutil.copyfile(ADVISOR, isolated_advisor)
    missing_dependency = subprocess.run(
        ["python3", str(isolated_advisor)],
        input=json.dumps({"session_id": SESSION, "transcript_path": str(transcript)}),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert missing_dependency.returncode == 0
    assert "gate not armed" in json.loads(missing_dependency.stdout)["systemMessage"]


def test_hooks_json_registration():
    config = json.loads(HOOKS_JSON.read_text())
    hooks = config["hooks"]
    assert any(
        entry.get("matcher") == "Agent|Task|Bash"
        and "fabricated_user_turn_guard.py" in entry["hooks"][0]["command"]
        for entry in hooks["PreToolUse"]
    )
    for event in ("PostToolUse", "PostToolUseFailure"):
        assert any(
            entry.get("matcher") == "Agent|Task|Bash"
            and "fabricated_user_turn_guard.py" in entry["hooks"][0]["command"]
            for entry in hooks[event]
        )
