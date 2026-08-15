#!/usr/bin/env python3
"""Live hook paths must be anchored to the durable primary checkout."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "sync_hooks_to_live.py"


def run(*args: str, cwd: Path) -> None:
    result = subprocess.run(args, cwd=cwd, check=False, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(
            f"{args!r} failed ({result.returncode}): {result.stdout}{result.stderr}"
        )


class SyncHookPathsTest(unittest.TestCase):
    def test_linked_worktree_stamps_primary_extensionless_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            primary = root / "primary"
            linked = root / "linked"
            home = root / "home"
            script = primary / "scripts" / SCRIPT.name
            hooks_json = primary / "plugins/harness-core/hooks/hooks.json"
            entrypoint = (
                primary
                / "plugins/harness-core/bin/install-cache-safe-entrypoints"
            )

            script.parent.mkdir(parents=True)
            hooks_json.parent.mkdir(parents=True)
            entrypoint.parent.mkdir(parents=True)
            shutil.copy2(SCRIPT, script)
            hooks_json.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "SessionStart": [
                                {
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": (
                                                '"${CLAUDE_PLUGIN_ROOT}/bin/'
                                                'install-cache-safe-entrypoints"'
                                            ),
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            entrypoint.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            entrypoint.chmod(0o755)

            run("git", "init", "-q", cwd=primary)
            run("git", "switch", "-q", "-c", "fixture", cwd=primary)
            run("git", "add", ".", cwd=primary)
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
            run(
                "git",
                "worktree",
                "add",
                "-q",
                "-b",
                "linked",
                str(linked),
                cwd=primary,
            )

            live = home / ".claude"
            (live / "hooks").mkdir(parents=True)
            (live / "settings.json").write_text(
                json.dumps({"hooks": {}, "sentinel": "preserved"}),
                encoding="utf-8",
            )
            env = {**os.environ, "HOME": str(home)}
            result = subprocess.run(
                [
                    "python3",
                    str(linked / "scripts" / SCRIPT.name),
                    "--ts",
                    "19700101_000000",
                ],
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            payload = json.loads((live / "settings.json").read_text(encoding="utf-8"))
            command = payload["hooks"]["SessionStart"][0]["hooks"][0]["command"]
            resolved_entrypoint = entrypoint.resolve()
            self.assertEqual(command, f'"{resolved_entrypoint}"')
            self.assertNotIn(str(linked), command)
            self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", command)
            self.assertEqual(payload["sentinel"], "preserved")
            self.assertTrue(os.access(resolved_entrypoint, os.X_OK))
            subprocess.run(
                shlex.split(command),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=2,
                check=True,
            )

    def test_missing_canonical_plugins_fails_before_live_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            home = root / "home"
            script = repo / "scripts" / SCRIPT.name
            script.parent.mkdir(parents=True)
            shutil.copy2(SCRIPT, script)
            run("git", "init", "-q", cwd=repo)

            live = home / ".claude"
            (live / "hooks").mkdir(parents=True)
            settings = live / "settings.json"
            before = b'{"hooks": {}, "sentinel": "untouched"}'
            settings.write_bytes(before)
            result = subprocess.run(
                ["python3", str(script), "--ts", "19700101_000000"],
                env={**os.environ, "HOME": str(home)},
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("no plugin hook manifests", result.stderr)
            self.assertEqual(settings.read_bytes(), before)
            self.assertFalse((home / "sanada_backup_persistent").exists())

    def test_bare_primary_fails_before_live_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            bare = root / "bare.git"
            linked = root / "linked"
            home = root / "home"
            script = source / "scripts" / SCRIPT.name
            hooks_json = source / "plugins/harness-core/hooks/hooks.json"
            script.parent.mkdir(parents=True)
            hooks_json.parent.mkdir(parents=True)
            shutil.copy2(SCRIPT, script)
            hooks_json.write_text('{"hooks": {}}', encoding="utf-8")
            run("git", "init", "-q", cwd=source)
            run("git", "switch", "-q", "-c", "fixture", cwd=source)
            run("git", "add", ".", cwd=source)
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
                cwd=source,
            )
            run("git", "clone", "-q", "--bare", str(source), str(bare), cwd=root)
            run(
                "git",
                f"--git-dir={bare}",
                "worktree",
                "add",
                "-q",
                str(linked),
                "fixture",
                cwd=root,
            )

            live = home / ".claude"
            (live / "hooks").mkdir(parents=True)
            settings = live / "settings.json"
            before = b'{"hooks": {}, "sentinel": "untouched"}'
            settings.write_bytes(before)
            result = subprocess.run(
                ["python3", str(linked / "scripts" / SCRIPT.name)],
                env={**os.environ, "HOME": str(home)},
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("canonical checkout is bare", result.stderr)
            self.assertEqual(settings.read_bytes(), before)
            self.assertFalse((home / "sanada_backup_persistent").exists())

    def test_missing_git_is_clean_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            home = root / "home"
            script = repo / "scripts" / SCRIPT.name
            script.parent.mkdir(parents=True)
            home.mkdir()
            shutil.copy2(SCRIPT, script)
            result = subprocess.run(
                [os.path.realpath(sys.executable), str(script), "--dry-run"],
                env={**os.environ, "HOME": str(home), "PATH": str(root / "empty-bin")},
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("cannot run git", result.stderr)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
