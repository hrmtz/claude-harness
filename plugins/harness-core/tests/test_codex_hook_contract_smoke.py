#!/usr/bin/env python3
"""gh #4: Codex hook I/O contract smoke fixture.

Feeds the harness-core guards the exact Codex-shaped stdin envelopes documented
in docs/codex_hooks.md and asserts:

1. PreToolUse: bash_command_guard denies a high-risk credential command with
   the documented hookSpecificOutput/permissionDecision output shape.
2. PostToolUse: credential_value_scrub detects a fake credential in Bash
   output and redacts it from the active Codex rollout JSONL located via the
   payload's transcript_path (not Claude project scanning).
"""
import json
import os
import subprocess
import tempfile

HOOKS = os.path.join(os.path.dirname(__file__), "..", "hooks")
GUARD = os.path.join(HOOKS, "bash_command_guard.sh")
SCRUB = os.path.join(HOOKS, "credential_value_scrub.sh")
TOKEN = "sk-ant-" + "A1b2c3" * 4


def base_payload(event, transcript):
    return {
        "session_id": "smoke-session",
        "turn_id": "smoke-turn",
        "transcript_path": transcript,
        "cwd": "/home/user/project",
        "hook_event_name": event,
        "model": "gpt-5.5",
        "permission_mode": "default",
        "tool_name": "Bash",
        "tool_use_id": "call_smoke",
    }


def main():
    with tempfile.TemporaryDirectory() as home:
        # Codex session layout: ~/.codex/sessions/<date>/rollout-*.jsonl
        rollout_dir = os.path.join(home, ".codex", "sessions", "2026", "05", "10")
        os.makedirs(rollout_dir)
        rollout = os.path.join(rollout_dir, "rollout-smoke.jsonl")
        with open(rollout, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"output": f"leaked: {TOKEN}"}) + "\n")

        env = dict(os.environ, HOME=home)

        # 1. PreToolUse deny contract.
        payload = base_payload("PreToolUse", rollout)
        payload["tool_input"] = {"command": "cat .env"}
        proc = subprocess.run(
            ["bash", GUARD], input=json.dumps(payload),
            capture_output=True, text=True, env=env,
        )
        decision = (
            json.loads(proc.stdout)
            .get("hookSpecificOutput", {})
            .get("permissionDecision")
        )
        assert decision == "deny", f"PreToolUse: expected deny, got {proc.stdout!r}"

        # 2. PostToolUse scrub of the active Codex rollout JSONL.
        payload = base_payload("PostToolUse", rollout)
        payload["tool_input"] = {"command": "env"}
        payload["tool_response"] = {"stdout": f"leaked: {TOKEN}"}
        subprocess.run(
            ["bash", SCRUB], input=json.dumps(payload),
            capture_output=True, text=True, env=env, check=True,
        )
        with open(rollout, encoding="utf-8") as handle:
            scrubbed = handle.read()
        assert TOKEN not in scrubbed, "fake credential survived in Codex rollout JSONL"
        assert "sk-ant-<REDACTED>" in scrubbed, "redaction marker missing from rollout"

    print("codex hook contract smoke (#4): ALL PASS ✓")


if __name__ == "__main__":
    main()
