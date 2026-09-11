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

    def test_repair_contract_is_shared_by_all_review_entry_points(self) -> None:
        contracts = []
        for plugin in ("harness-magi-codex", "harness-magi", "harness-kimi"):
            skills = ROOT / "plugins" / plugin / "skills"
            dual = skills / "dual-magi-review/SKILL.md"
            text = dual.read_text(encoding="utf-8")
            heading = "## Cause-first repair contract\n"
            with self.subTest(plugin=plugin):
                self.assertEqual(text.count(heading), 1)
                contract = text.split(heading, 1)[1].split("\n## ", 1)[0].strip()
                contracts.append(contract)
                for required in (
                    "Before investigation", "Before editing", "Before re-review",
                    "same fixture and assertions", "previous causal explanation",
                    "not a mechanical edit or provider-admission gate",
                ):
                    self.assertIn(required, contract)
                for skill in ("magi", "ultramagi", "bug-hunt"):
                    entry = skills / skill / "SKILL.md"
                    if plugin == "harness-magi-codex" and skill == "bug-hunt":
                        continue
                    self.assertIn(
                        "../dual-magi-review/SKILL.md#cause-first-repair-contract",
                        entry.read_text(encoding="utf-8"),
                    )
        self.assertEqual(len(contracts), 3)
        self.assertEqual(contracts[0], contracts[1])
        self.assertEqual(contracts[0], contracts[2])

    def test_ultramagi_mirrors_split_warmup_from_irreversible_boundaries(self) -> None:
        paths = (
            PLUGIN / "skills/ultramagi/SKILL.md",
            ROOT / "plugins/harness-magi/skills/ultramagi/SKILL.md",
            ROOT / "plugins/harness-kimi/skills/ultramagi/SKILL.md",
        )
        for path in paths:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertIn("ready-to-drive", text)
                self.assertIn("warmup", text.lower())
                self.assertIn("irreversible boundary", text)
                self.assertIn("not plateau", text.lower())
                self.assertIn("exact-revision", text)
                self.assertIn("implementation", text)
                self.assertIn("CRITICAL/HIGH", text)
                self.assertIn("never authorizes shipping", text)
                self.assertIn("background wait estimated at 30 minutes or more", text)

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
            self.assertEqual(removed.returncode, 1)
            self.assertIn("invalid ownership marker", removed.stderr)
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
