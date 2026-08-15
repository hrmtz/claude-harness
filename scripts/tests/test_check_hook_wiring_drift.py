#!/usr/bin/env python3
"""Regression tests for the host-local hook wiring drift checker."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts" / "check_hook_wiring_drift.py"


def block(command: str) -> dict[str, object]:
    return {"hooks": [{"type": "command", "command": command}]}


class HookWiringDriftTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.live = self.root / "settings.json"
        self.plugins = self.root / "plugins"
        self.hooks_json = self.plugins / "harness-core" / "hooks" / "hooks.json"
        self.hooks_json.parent.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(
        self,
        *,
        live: dict[str, list[dict[str, object]]],
        plugin: dict[str, list[dict[str, object]]],
    ) -> None:
        self.live.write_text(json.dumps({"hooks": live}))
        self.hooks_json.write_text(json.dumps({"hooks": plugin}))

    def invoke(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(CHECKER),
                "--live",
                str(self.live),
                "--plugins",
                str(self.plugins),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_in_sync_is_zero(self) -> None:
        wiring = {"PreToolUse": [block("/repo/hooks/guard.sh")]}
        self.write(live=wiring, plugin=wiring)
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("IN SYNC", result.stdout)

    def test_orphan_and_dormant_are_both_drift(self) -> None:
        self.write(
            live={"PreToolUse": [block("/live/hooks/orphan.sh")]},
            plugin={"Stop": [block("/repo/hooks/dormant.py")]},
        )
        result = self.invoke()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("ORPHAN", result.stdout)
        self.assertIn("orphan.sh", result.stdout)
        self.assertIn("DORMANT", result.stdout)
        self.assertIn("dormant.py", result.stdout)

    def test_allowlisted_live_only_hook_is_ignored(self) -> None:
        self.write(
            live={"SessionEnd": [block("/integration/session_end_ingest.sh")]},
            plugin={},
        )
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("IN SYNC", result.stdout)

    def test_extensionless_bin_entrypoint_survives_live_path_rewrite(self) -> None:
        self.write(
            live={
                "SessionStart": [
                    block(
                        '"/repo/plugins/harness-core/bin/'
                        'install-cache-safe-entrypoints"'
                    )
                ]
            },
            plugin={
                "SessionStart": [
                    block(
                        '"${CLAUDE_PLUGIN_ROOT}/bin/'
                        'install-cache-safe-entrypoints"'
                    )
                ]
            },
        )
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("live-wired: 1  plugin-wired: 1", result.stdout)

    def test_unresolved_plugin_root_is_drift_even_when_entrypoint_matches(self) -> None:
        unresolved = (
            '"${CLAUDE_PLUGIN_ROOT}/bin/'
            'install-cache-safe-entrypoints"'
        )
        self.write(
            live={"SessionStart": [block(unresolved)]},
            plugin={"SessionStart": [block(unresolved)]},
        )
        result = self.invoke()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("UNRESOLVED_PLUGIN_ROOT", result.stdout)
        self.assertIn("install-cache-safe-entrypoints", result.stdout)
        self.assertNotIn("IN SYNC", result.stdout)

    def test_missing_extensionless_bin_entrypoint_is_dormant(self) -> None:
        self.write(
            live={},
            plugin={
                "SessionStart": [
                    block(
                        '"${CLAUDE_PLUGIN_ROOT}/bin/'
                        'install-cache-safe-entrypoints"'
                    )
                ]
            },
        )
        result = self.invoke()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("DORMANT", result.stdout)
        self.assertIn("install-cache-safe-entrypoints", result.stdout)

    def test_dispatcher_bin_path_does_not_hide_actual_script(self) -> None:
        dispatcher = (
            'test -x "${HOME}/.local/bin/harness-hook" || '
            'exec bash "${CLAUDE_PLUGIN_ROOT}/hooks/'
        )
        self.write(
            live={
                "PreToolUse": [
                    block(dispatcher + 'live_guard.sh"; exit 0')
                ]
            },
            plugin={
                "PreToolUse": [
                    block(dispatcher + 'plugin_guard.py"; exit 0')
                ]
            },
        )
        result = self.invoke()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("live_guard.sh", result.stdout)
        self.assertIn("plugin_guard.py", result.stdout)
        self.assertNotIn("[PreToolUse] harness-hook", result.stdout)

    def test_malformed_input_is_checker_error(self) -> None:
        self.live.write_text("{broken")
        self.hooks_json.write_text(json.dumps({"hooks": {}}))
        result = self.invoke()
        self.assertEqual(result.returncode, 2)
        self.assertIn("HOOK WIRING CHECK ERROR", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
