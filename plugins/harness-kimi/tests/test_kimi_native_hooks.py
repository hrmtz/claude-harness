#!/usr/bin/env python3
"""Verify the harness-kimi native-hook port (gh #54).

Covers:
  - harness-core PreToolUse guards accept Kimi's native payload shape and emit
    the hookSpecificOutput deny JSON Kimi honors (verified against 0.28.1).
  - lib.sh absorbs the two Kimi deltas: PostToolUse `.tool_output` (plain
    string) and wire.jsonl resolution from `.session_id`.
  - install-kimi-hooks.sh merges a valid [[hooks]] block into config.toml
    idempotently, preserving the user's own settings; uninstaller removes it.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]  # plugins/harness-kimi/tests -> repo root
CORE_HOOKS = REPO / "plugins" / "harness-core" / "hooks"
INSTALLER = REPO / "install-kimi-hooks.sh"
UNINSTALLER = REPO / "uninstall-kimi-hooks.sh"


def run_hook(script: Path, payload: dict, env_extra: dict | None = None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def kimi_payload(command: str, event: str = "PreToolUse") -> dict:
    p = {
        "hook_event_name": event,
        "session_id": "session_00000000-0000-0000-0000-000000000000",
        "cwd": "/tmp",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    if event == "PostToolUse":
        p["tool_call_id"] = "tc_1"
    return p


class TestPreToolUseGuard(unittest.TestCase):
    GUARD = CORE_HOOKS / "bash_command_guard.sh"

    def test_forbidden_command_denied(self):
        r = run_hook(self.GUARD, kimi_payload("sops -d secrets.enc.yaml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(
            doc["hookSpecificOutput"]["permissionDecision"], "deny",
            f"expected deny JSON, got: {r.stdout!r}")

    def test_benign_command_allowed(self):
        r = run_hook(self.GUARD, kimi_payload("git status"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn('"permissionDecision": "deny"', r.stdout)
        self.assertNotIn('"permissionDecision":"deny"', r.stdout)


class TestLibKimiCompat(unittest.TestCase):
    def _source_and_run(self, body: str, payload: dict, env_extra: dict | None = None):
        script = (
            f'source "{CORE_HOOKS}/lib.sh"\n'
            "HOOK_INPUT=$(cat); export HOOK_INPUT\n" + body
        )
        env = dict(os.environ)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            ["bash", "-c", script],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

    def test_parse_tool_output_reads_tool_output_string(self):
        # Kimi PostToolUse carries the result as a plain string under .tool_output.
        p = kimi_payload("echo x", event="PostToolUse")
        p["tool_output"] = "postgres://user:secretpw@db:5432/x\n"
        r = self._source_and_run("parse_tool_output", p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("postgres://user:secretpw@", r.stdout)

    def test_active_jsonl_resolves_wire_jsonl_by_session_id(self):
        tmp = Path(tempfile.mkdtemp(prefix="kimi_home_test_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        sid = "session_11111111-2222-3333-4444-555555555555"
        wire = tmp / "sessions" / "wd_proj_deadbeef" / sid / "agents" / "main" / "wire.jsonl"
        wire.parent.mkdir(parents=True)
        wire.write_text('{"type":"metadata"}\n')
        r = self._source_and_run(
            "active_jsonl", {"session_id": sid},
            env_extra={"KIMI_CODE_HOME": str(tmp)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), str(wire))


class TestInstaller(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kimi_install_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.kimi_home = self.tmp / "kimi-home"
        self.kimi_home.mkdir()
        self.config = self.kimi_home / "config.toml"
        self.config.write_text(
            'default_model = "kimi-code/k3"\n\n[thinking]\nenabled = true\n')
        self.env = dict(os.environ, KIMI_CODE_HOME=str(self.kimi_home))

    def _run(self, script: Path):
        return subprocess.run(
            ["bash", str(script)], capture_output=True, text=True,
            env=self.env, timeout=60)

    def test_install_is_idempotent_and_preserves_settings(self):
        for _ in range(2):
            r = self._run(INSTALLER)
            self.assertEqual(r.returncode, 0, r.stderr)
        text = self.config.read_text()
        # user's own settings survive
        self.assertIn('default_model = "kimi-code/k3"', text)
        self.assertIn("[thinking]", text)
        # exactly one marker block after two installs
        self.assertEqual(text.count("# >>> harness-kimi hooks"), 1)
        self.assertEqual(text.count("# <<< harness-kimi hooks <<<"), 1)
        # valid TOML, [[hooks]] entries carry only the four allowed fields
        import tomllib
        doc = tomllib.loads(text)
        hooks = doc.get("hooks", [])
        self.assertGreater(len(hooks), 0)
        for h in hooks:
            self.assertLessEqual(set(h), {"event", "matcher", "command", "timeout"})
            self.assertIn("event", h)
            self.assertIn("command", h)
        # lifecycle events carry no matcher
        for h in hooks:
            if h["event"] == "UserPromptSubmit":
                self.assertNotIn("matcher", h)

    def test_uninstall_removes_block_and_keeps_settings(self):
        self.assertEqual(self._run(INSTALLER).returncode, 0)
        r = self._run(UNINSTALLER)
        self.assertEqual(r.returncode, 0, r.stderr)
        text = self.config.read_text()
        self.assertNotIn("harness-kimi hooks", text)
        self.assertIn('default_model = "kimi-code/k3"', text)
        import tomllib
        tomllib.loads(text)  # still valid TOML


if __name__ == "__main__":
    unittest.main()
