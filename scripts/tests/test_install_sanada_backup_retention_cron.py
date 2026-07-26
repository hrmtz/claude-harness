#!/usr/bin/env python3

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts/install_sanada_backup_retention_cron.sh"
RUNNER = ROOT / "scripts/sanada_backup_retention.py"
MARKER = "# sanada_backup_retention_daily"


def run(*args: str, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


class SanadaRetentionCronInstallerTest(unittest.TestCase):
    def test_linked_worktree_installs_primary_apply_path_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            primary = root / "primary"
            linked = root / "linked"
            home = root / "home"
            scripts = primary / "scripts"
            scripts.mkdir(parents=True)
            home.mkdir()
            shutil.copy2(INSTALLER, scripts / INSTALLER.name)
            shutil.copy2(RUNNER, scripts / RUNNER.name)
            (scripts / RUNNER.name).chmod(0o755)

            run("git", "init", "-q", cwd=primary)
            run("git", "switch", "-q", "-c", "fixture", cwd=primary)
            run("git", "add", "scripts", cwd=primary)
            run(
                "git",
                "-c",
                "user.name=Harness Test",
                "-c",
                "user.email=harness@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "fixture",
                cwd=primary,
            )
            run("git", "worktree", "add", "-q", "-b", "linked", str(linked), cwd=primary)

            state = root / "crontab"
            fake = root / "fake-crontab"
            fake.write_text(
                "#!/bin/bash\n"
                "if [ \"${1:-}\" = -l ]; then\n"
                "  [ -f \"$FAKE_CRONTAB_STATE\" ] && command cat \"$FAKE_CRONTAB_STATE\"\n"
                "elif [ \"${1:-}\" = - ]; then\n"
                "  command cat > \"$FAKE_CRONTAB_STATE\"\n"
                "else\n"
                "  exit 2\n"
                "fi\n"
            )
            fake.chmod(0o755)
            env = {
                **os.environ,
                "HOME": str(home),
                "HARNESS_CRONTAB_BIN": str(fake),
                "FAKE_CRONTAB_STATE": str(state),
            }
            linked_installer = linked / "scripts" / INSTALLER.name
            for _ in range(2):
                subprocess.run(
                    ["bash", str(linked_installer)],
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                )

            marked = [row for row in state.read_text().splitlines() if MARKER in row]
            self.assertEqual(len(marked), 1)
            self.assertIn(str(primary / "scripts" / RUNNER.name), marked[0])
            self.assertNotIn(str(linked), marked[0])
            self.assertIn("--apply", marked[0])


if __name__ == "__main__":
    unittest.main()
