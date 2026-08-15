#!/usr/bin/env python3
"""Fixture regression for the Codex managed-block drift checker (gh #174)."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "plugins/cross_cli_hooks.json"
RENDER = ROOT / "scripts/lib/render_codex_hooks.py"
MERGE = ROOT / "scripts/lib/merge_codex_hooks.py"
CHECK = ROOT / "scripts/check_cross_cli_hooks.sh"
BEGIN = "# BEGIN claude-harness managed hooks"
END = "# END claude-harness managed hooks"


class CodexManagedBlockFixtureTest(unittest.TestCase):
    def test_fresh_installer_block_has_zero_findings(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["HIPPOCAMPUS_HOME"] = str(home / "no-companion-repo")
            fake_bin = home / "bin"
            fake_bin.mkdir()
            fake_codex = fake_bin / "codex"
            fake_codex.write_text(
                '#!/bin/sh\n'
                'if [ "$1 $2 $3" = "plugin list --json" ]; then\n'
                '  printf \'%s\\n\' \'{"installed":[]}\'\n'
                '  exit 0\n'
                'fi\n'
                'exit 1\n'
            )
            fake_codex.chmod(0o755)
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            block = subprocess.run(
                ["python3", str(RENDER), "block", str(OVERLAY), str(ROOT)],
                env=env,
                check=True,
                text=True,
                capture_output=True,
            ).stdout
            config = home / ".codex/config.toml"
            config.parent.mkdir(parents=True)
            config.write_text(f"{BEGIN}\n{block}{END}\n")

            self.assertIn("harness-hook", block)
            self.assertIn("codex_tmux_self_name.sh", block)

            result = subprocess.run(
                ["bash", str(CHECK), "--live"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(
                result.returncode,
                0,
                msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
            )
            self.assertNotIn("DRIFT:", result.stderr)
            self.assertNotIn("DUPLICATE/UNEXPECTED", result.stderr)
            self.assertNotIn("MISSING managed Codex hooks", result.stderr)

    def test_enabled_plugins_leave_only_global_inline_hook(self):
        enabled = [
            ("harness-core", ROOT / "plugins/harness-core"),
            ("harness-rails", ROOT / "plugins/harness-rails"),
            ("harness-formation", ROOT / "plugins/harness-formation"),
            ("harness-magi-codex", ROOT / "plugins/harness-magi-codex"),
        ]
        command = [
            "python3",
            str(RENDER),
            "block",
            str(OVERLAY),
            str(ROOT),
        ]
        for plugin, root in enabled:
            command.extend(["--enabled-plugin-root", f"{plugin}={root}"])
        block = subprocess.run(
            command,
            check=True,
            text=True,
            capture_output=True,
        ).stdout

        self.assertIn("versioning_autorun.py", block)
        self.assertNotIn("admission_reminder.sh", block)
        self.assertNotIn("temporal_anchor.sh", block)
        self.assertNotIn("codex_session_start.sh", block)
        self.assertNotIn("codex_tmux_self_name.sh", block)

    def test_plugin_enabled_migration_removes_legacy_inline_duplicates(self):
        full = subprocess.run(
            [
                "python3",
                str(RENDER),
                "block",
                str(OVERLAY),
                str(ROOT),
            ],
            check=True,
            text=True,
            capture_output=True,
        ).stdout
        filtered = subprocess.run(
            [
                "python3",
                str(RENDER),
                "block",
                str(OVERLAY),
                str(ROOT),
                "--enabled-plugin-root",
                f"harness-core={ROOT / 'plugins/harness-core'}",
                "--enabled-plugin-root",
                f"harness-rails={ROOT / 'plugins/harness-rails'}",
                "--enabled-plugin-root",
                f"harness-formation={ROOT / 'plugins/harness-formation'}",
                "--enabled-plugin-root",
                f"harness-magi-codex={ROOT / 'plugins/harness-magi-codex'}",
            ],
            check=True,
            text=True,
            capture_output=True,
        ).stdout

        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            config = temp / "config.toml"
            block = temp / "block.toml"
            output = temp / "output.toml"
            config.write_text(
                f'model = "gpt-5"\n[hooks.state]\ntrusted = "keep"\n'
                f"{BEGIN}\n{full}{END}\n"
            )
            block.write_text(filtered)
            subprocess.run(
                ["python3", str(MERGE), str(config), str(block), str(output)],
                check=True,
            )
            result = config.read_text()

        self.assertEqual(result.count(BEGIN), 1)
        self.assertIn('trusted = "keep"', result)
        self.assertIn("versioning_autorun.py", result)
        self.assertNotIn("admission_reminder.sh", result)
        self.assertNotIn("temporal_anchor.sh", result)

    def test_older_plugin_generation_keeps_missing_hook_inline(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin_root = Path(temp) / "harness-core"
            hooks_dir = plugin_root / "hooks"
            hooks_dir.mkdir(parents=True)
            hooks_dir.joinpath("hooks.json").write_text(json.dumps({
                "hooks": {
                    "UserPromptSubmit": [{
                        "hooks": [{
                            "command": (
                                '"${HOME}/.local/bin/harness-hook" '
                                "harness-core hooks/admission_reminder.sh"
                            )
                        }]
                    }]
                }
            }))
            block = subprocess.run(
                [
                    "python3",
                    str(RENDER),
                    "block",
                    str(OVERLAY),
                    str(ROOT),
                    "--enabled-plugin-root",
                    f"harness-core={plugin_root}",
                ],
                check=True,
                text=True,
                capture_output=True,
            ).stdout

        self.assertNotIn("admission_reminder.sh", block)
        self.assertIn("secret_delivery_prompt.py", block)
        self.assertIn("codex_session_start.sh", block)
        self.assertIn("codex_tmux_self_name.sh", block)


if __name__ == "__main__":
    unittest.main()
