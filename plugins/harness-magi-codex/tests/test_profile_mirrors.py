#!/usr/bin/env python3
"""Drift checks for bounded convergence profile documentation and installers."""

from __future__ import annotations

import unittest
import os
import subprocess
import tempfile
from pathlib import Path


PLUGIN = Path(__file__).resolve().parent.parent
ROOT = PLUGIN.parents[1]


class ProfileMirrorTest(unittest.TestCase):
    def test_all_magi_mirrors_are_one_shot_and_name_the_gate(self) -> None:
        paths = (
            PLUGIN / "skills/magi/SKILL.md",
            ROOT / "plugins/harness-magi/skills/magi/SKILL.md",
            ROOT / "plugins/harness-kimi/skills/magi/SKILL.md",
        )
        for index, path in enumerate(paths):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path):
                if index == 0:
                    self.assertIn("magi_preflight_codex.sh", text)
                    self.assertIn("PROCEED", text)
                else:
                    self.assertIn("Mechanical availability boundary", text)
                    self.assertIn("fail-closed", text)
                self.assertIn("PIVOT", text)
                self.assertIn("ABORT", text)
                self.assertNotIn("Round 2+", text)
                self.assertNotIn("re-run Round 1", text)

    def test_dual_magi_mirrors_preserve_plateau_separation(self) -> None:
        paths = (
            PLUGIN / "skills/dual-magi-review/SKILL.md",
            ROOT / "plugins/harness-magi/skills/dual-magi-review/SKILL.md",
        )
        for path in paths:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertIn("magi_design_convergence_gate.py", text)
                self.assertIn("PLATEAU_CANDIDATE", text)
                self.assertIn("magi_plateau_gate.sh", text)

    def test_legacy_installer_and_uninstaller_cover_all_codex_skills(self) -> None:
        expected = "for skill in magi dual-magi-review ultramagi; do"
        self.assertIn(expected, (PLUGIN / "install-codex-skills.sh").read_text())
        self.assertIn(expected, (PLUGIN / "uninstall-codex-skills.sh").read_text())

    def test_installer_refuses_foreign_magi_directory_and_symlink(self) -> None:
        installer = PLUGIN / "install-codex-skills.sh"
        for kind in ("directory", "symlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                home = root / "codex"
                skills = home / "skills"
                skills.mkdir(parents=True)
                foreign = root / "foreign"
                foreign.mkdir()
                (foreign / "SKILL.md").write_text("user owned\n")
                target = skills / "magi"
                if kind == "directory":
                    target.mkdir()
                    (target / "SKILL.md").write_text("user owned\n")
                else:
                    target.symlink_to(foreign)
                before = (foreign if kind == "symlink" else target) / "SKILL.md"
                env = os.environ.copy()
                env["CODEX_HOME"] = str(home)
                result = subprocess.run(
                    ["bash", str(installer)],
                    text=True,
                    capture_output=True,
                    check=False,
                    env=env,
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(before.read_text(), "user owned\n")

    def test_copy_install_resolves_runtime_and_uninstall_preserves_foreign_marker(self) -> None:
        installer = PLUGIN / "install-codex-skills.sh"
        uninstaller = PLUGIN / "uninstall-codex-skills.sh"
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw) / "codex"
            env = os.environ.copy()
            env["CODEX_HOME"] = str(home)
            installed = subprocess.run(
                ["bash", str(installer), "--copy"],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )
            self.assertEqual(installed.returncode, 0, installed.stderr)
            resolver = home / "skills/magi/scripts/resolve-root.sh"
            resolved = subprocess.run(
                ["bash", str(resolver)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(resolved.returncode, 0, resolved.stderr)
            self.assertEqual(Path(resolved.stdout.strip()), PLUGIN)

            marker = home / "skills/magi/.harness-magi-codex"
            marker.write_text("foreign marker\n")
            removed = subprocess.run(
                ["bash", str(uninstaller)],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )
            self.assertEqual(removed.returncode, 0)
            self.assertTrue((home / "skills/magi/SKILL.md").is_file())

    def test_default_symlink_install_resolves_runtime_outside_plugin_tree(self) -> None:
        installer = PLUGIN / "install-codex-skills.sh"
        with tempfile.TemporaryDirectory() as raw:
            temp_root = Path(raw)
            home = temp_root / "codex"
            unrelated = temp_root / "unrelated"
            unrelated.mkdir()
            env = os.environ.copy()
            env["CODEX_HOME"] = str(home)
            installed = subprocess.run(
                ["bash", str(installer)],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )
            self.assertEqual(installed.returncode, 0, installed.stderr)
            resolver = home / "skills/magi/scripts/resolve-root.sh"
            resolved = subprocess.run(
                ["bash", str(resolver)],
                cwd=unrelated,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(resolved.returncode, 0, resolved.stderr)
            self.assertEqual(Path(resolved.stdout.strip()), PLUGIN)


if __name__ == "__main__":
    unittest.main()
