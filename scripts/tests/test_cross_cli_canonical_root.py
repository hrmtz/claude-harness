#!/usr/bin/env python3
"""Cross-CLI live wiring must never retain a disposable caller worktree."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
INSTALLERS = (
    "install-codex-hooks.sh",
    "install-grok-hooks.sh",
    "install-kimi-hooks.sh",
)


def run(*args: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    if result.returncode:
        raise AssertionError(
            f"{args!r} failed ({result.returncode}):\n{result.stdout}{result.stderr}"
        )
    return result


class CrossCliCanonicalRootTest(unittest.TestCase):
    def make_fixture(self, root: Path) -> tuple[Path, Path]:
        primary = root / "primary"
        linked = root / "linked"
        primary.mkdir()
        for name in INSTALLERS:
            shutil.copy2(ROOT / name, primary / name)
        shutil.copytree(ROOT / "plugins", primary / "plugins", symlinks=True)
        (primary / "scripts").mkdir()
        shutil.copytree(ROOT / "scripts/lib", primary / "scripts/lib", symlinks=True)
        shutil.copy2(
            ROOT / "scripts/check_cross_cli_hooks.sh",
            primary / "scripts/check_cross_cli_hooks.sh",
        )
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
        return primary.resolve(), linked.resolve()

    def fake_cli_env(self, root: Path, home: Path) -> dict[str, str]:
        fake_bin = root / "fake-bin"
        fake_bin.mkdir()
        codex = fake_bin / "codex"
        codex.write_text(
            "#!/bin/bash\n"
            "case \"$*\" in\n"
            "  'plugin list --json') printf '%s\\n' '{\"installed\":[]}' ;;\n"
            "  'features list') printf '%s\\n' 'hooks experimental true' ;;\n"
            "  'features enable hooks') exit 0 ;;\n"
            "  *) exit 0 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        codex.chmod(0o755)
        for name in ("grok", "kimi"):
            command = fake_bin / name
            command.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            command.chmod(0o755)
        env = {
            **os.environ,
            "HOME": str(home),
            "KIMI_CODE_HOME": str(home / ".kimi-code"),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        }
        return env

    def test_linked_installers_and_live_checker_use_primary_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            primary, linked = self.make_fixture(root)
            home = root / "home"
            (home / ".codex").mkdir(parents=True)
            (home / ".grok/hooks").mkdir(parents=True)
            (home / ".kimi-code").mkdir(parents=True)
            (home / ".codex/config.toml").write_text(
                'model = "keep-codex-user-setting"\n'
                "[[hooks.UserPromptSubmit]]\n"
                "[[hooks.UserPromptSubmit.hooks]]\n"
                'type = "command"\n'
                'command = "printf codex-user-owned-hook"\n'
                "timeout = 5\n",
                encoding="utf-8",
            )
            (home / ".kimi-code/config.toml").write_text(
                'default_model = "keep-kimi-user-setting"\n'
                "[[hooks]]\n"
                "event = 'Notification'\n"
                "command = 'printf kimi-user-owned-hook'\n"
                "timeout = 5\n",
                encoding="utf-8",
            )
            env = self.fake_cli_env(root, home)

            configs = (
                home / ".codex/config.toml",
                home / ".grok/hooks/harness.json",
                home / ".kimi-code/config.toml",
            )
            first: tuple[bytes, ...] | None = None
            for iteration in range(2):
                for name in INSTALLERS:
                    run("bash", str(linked / name), cwd=linked, env=env)
                current = tuple(path.read_bytes() for path in configs)
                if iteration == 0:
                    first = current
                else:
                    self.assertEqual(current, first)

            combined = b"\n".join(path.read_bytes() for path in configs).decode()
            self.assertIn(str(primary), combined)
            self.assertNotIn(str(linked), combined)
            self.assertIn("keep-codex-user-setting", configs[0].read_text())
            self.assertIn("keep-kimi-user-setting", configs[2].read_text())
            self.assertIn("codex-user-owned-hook", configs[0].read_text())
            self.assertIn("kimi-user-owned-hook", configs[2].read_text())

            checked = run(
                "bash",
                str(linked / "scripts/check_cross_cli_hooks.sh"),
                "--live",
                cwd=linked,
                env=env,
            )
            self.assertIn("in sync", checked.stdout)

            linked_overlay = linked / "plugins/cross_cli_hooks.json"
            payload = json.loads(linked_overlay.read_text(encoding="utf-8"))
            payload["codex"]["hooks"].append(
                "harness-core/hooks/linked-only-missing.sh"
            )
            linked_overlay.write_text(json.dumps(payload), encoding="utf-8")
            non_live = subprocess.run(
                ["bash", str(linked / "scripts/check_cross_cli_hooks.sh")],
                cwd=linked,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(non_live.returncode, 0)
            self.assertIn("linked-only-missing.sh", non_live.stderr)
            canonical_live = run(
                "bash",
                str(linked / "scripts/check_cross_cli_hooks.sh"),
                "--live",
                cwd=linked,
                env=env,
            )
            self.assertIn("in sync", canonical_live.stdout)

    def test_non_git_installers_fail_before_live_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            repo = root / "not-a-repo"
            scripts_lib = repo / "scripts/lib"
            scripts_lib.mkdir(parents=True)
            for name in INSTALLERS:
                shutil.copy2(ROOT / name, repo / name)
            shutil.copy2(
                ROOT / "scripts/lib/resolve_harness_root.sh",
                scripts_lib / "resolve_harness_root.sh",
            )
            targets = {
                "install-codex-hooks.sh": Path(".codex/config.toml"),
                "install-grok-hooks.sh": Path(".grok/hooks/harness.json"),
                "install-kimi-hooks.sh": Path(".kimi-code/config.toml"),
            }
            before = b'{"user_owned":"untouched"}\n'
            for name, relative in targets.items():
                with self.subTest(installer=name):
                    home = root / f"home-{name}"
                    config = home / relative
                    config.parent.mkdir(parents=True)
                    config.write_bytes(before)
                    env = {
                        **os.environ,
                        "HOME": str(home),
                        "GIT_CEILING_DIRECTORIES": str(root),
                    }
                    env["KIMI_CODE_HOME"] = str(home / ".kimi-code")
                    result = subprocess.run(
                        ["bash", str(repo / name)],
                        cwd=repo,
                        env=env,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(
                        "cannot resolve canonical checkout", result.stderr
                    )
                    self.assertEqual(config.read_bytes(), before)
                    self.assertFalse(
                        (home / "sanada_backup_persistent").exists()
                    )


if __name__ == "__main__":
    unittest.main()
