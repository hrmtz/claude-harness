#!/usr/bin/env python3
"""Fixture regression for the Codex managed-block drift checker (gh #174)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "plugins/cross_cli_hooks.json"
RENDER = ROOT / "scripts/lib/render_codex_hooks.py"
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


if __name__ == "__main__":
    unittest.main()
