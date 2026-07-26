#!/usr/bin/env python3

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/sanada_backup_retention.py"


class SanadaRetentionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.backups = self.home / "sanada_backup_persistent"
        self.backups.mkdir()
        self.now = 2_000_000_000.0

    def tearDown(self) -> None:
        self.temp.cleanup()

    def make_dir(self, name: str, age_days: float, keep_nested: bool = False) -> Path:
        path = self.backups / name
        path.mkdir()
        (path / "data").write_bytes(b"x" * 4096)
        if keep_nested:
            nested = path / "nested"
            nested.mkdir()
            (nested / ".keep").touch()
        timestamp = self.now - age_days * 86400
        os.utime(path, (timestamp, timestamp))
        return path

    def run_retention(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(RUNNER),
                "--root",
                str(self.backups),
                "--log",
                str(self.home / "retention.log"),
                "--now",
                str(self.now),
                *extra,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_default_is_dry_run_and_policy_is_classified_by_name(self) -> None:
        old_auto = self.make_dir("auto_old", 4)
        young_auto = self.make_dir("auto_young", 2)
        old_named = self.make_dir("manual_old", 8)
        young_named = self.make_dir("manual_young", 6)
        protected = self.make_dir("auto_protected", 30, keep_nested=True)

        result = self.run_retention()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("mode=DRY-RUN delete_dirs=2 auto=1 named=1", result.stdout)
        self.assertIn("keep_excluded=1", result.stdout)
        for path in (old_auto, young_auto, old_named, young_named, protected):
            self.assertTrue(path.exists())

    def test_apply_deletes_whole_selected_directories_only(self) -> None:
        old_auto = self.make_dir("auto_old", 4)
        old_named = self.make_dir("manual_old", 8)
        young = self.make_dir("auto_young", 1)
        protected = self.make_dir("manual_protected", 20, keep_nested=True)

        result = self.run_retention("--apply")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(old_auto.exists())
        self.assertFalse(old_named.exists())
        self.assertTrue(young.exists())
        self.assertTrue(protected.exists())
        log = (self.home / "retention.log").read_text()
        self.assertIn("complete mode=APPLY deleted_dirs=2", log)

    def test_symlink_top_level_is_never_selected(self) -> None:
        outside = self.home / "outside"
        outside.mkdir()
        link = self.backups / "auto_link"
        link.symlink_to(outside, target_is_directory=True)

        result = self.run_retention("--apply")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(link.is_symlink())
        self.assertTrue(outside.exists())

    def test_exact_cutoff_is_retained(self) -> None:
        exact = self.make_dir("auto_exact", 3)
        result = self.run_retention("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(exact.exists())

    def test_symlink_root_fails_closed(self) -> None:
        link = self.home / "linked-root"
        link.symlink_to(self.backups, target_is_directory=True)
        result = subprocess.run(
            [
                "python3",
                str(RUNNER),
                "--root",
                str(link),
                "--log",
                str(self.home / "retention.log"),
                "--now",
                str(self.now),
                "--apply",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertTrue(self.backups.exists())


if __name__ == "__main__":
    unittest.main()
