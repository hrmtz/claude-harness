#!/usr/bin/env python3
import json
import os
import pathlib
import subprocess
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
HOOK = HERE.parent / "hooks" / "pr_receipt.sh"
OID = "a" * 40
NONCE = "b" * 32


class ReceiptHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.home = self.root / "home"
        self.repo = self.root / "repo"
        self.bin = self.root / "bin"
        self.home.mkdir()
        self.repo.mkdir()
        self.bin.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "dev"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "fixture"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "fixture@example.invalid"],
                       cwd=self.repo, check=True)
        (self.repo / "tracked").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=self.repo, check=True)
        self.oid = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, text=True
        ).strip()
        tmux = self.bin / "tmux"
        tmux.write_text(
            "#!/usr/bin/env bash\n"
            "printf '0\\t%s\\n'\n" % self.repo,
            encoding="utf-8",
        )
        tmux.chmod(0o755)
        self.registry = self.home / ".formation" / "formation" / "registry.jsonl"
        self.registry.parent.mkdir(parents=True)
        self.write_active_registry()

    def tearDown(self):
        self.tmp.cleanup()

    def write_active_registry(self):
        self.registry.write_text(json.dumps({
            "id": "fixture-worker",
            "pane_id": "%42",
            "session_id": "fixture-session",
        }) + "\n", encoding="utf-8")

    def payload(self, event="PostToolUse"):
        body = f"Fixture PR\n\n<!-- babysit-pr-nonce: {NONCE} -->"
        return {
            "hook_event_name": event,
            "session_id": "fixture-session",
            "cwd": str(self.repo),
            "timestamp": "2026-07-28T00:00:00Z",
            "tool_input": {
                "command": "gh pr create --draft --title fixture --body "
                           + json.dumps(body),
            },
            "tool_response": {
                "stdout": "https://github.com/owner/repo/pull/42\n",
            },
        }

    def run_hook(self, payload):
        return subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps(payload),
            text=True,
            env={
                **os.environ,
                "HOME": str(self.home),
                "PATH": f"{self.bin}:{os.environ['PATH']}",
            },
            capture_output=True,
            check=False,
        )

    def receipts(self):
        directory = self.home / ".claude" / "pr_receipts"
        return list(directory.glob("*.json")) if directory.exists() else []

    def test_success_payload_mints_receipt_bound_to_remote_body_marker(self):
        result = self.run_hook(self.payload())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.receipts()), 1)
        receipt = json.loads(self.receipts()[0].read_text(encoding="utf-8"))
        self.assertEqual(receipt, {
            "created_at": "2026-07-28T00:00:00Z",
            "head_oid": self.oid,
            "nonce": NONCE,
            "pr": 42,
            "repo": "owner/repo",
        })
        self.assertIn(receipt["nonce"], self.payload()["tool_input"]["command"])

    def test_failure_event_never_mints_even_if_it_contains_success_shaped_stdout(self):
        result = self.run_hook(self.payload("PostToolUseFailure"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.receipts(), [])

    def test_nonexact_command_or_stdout_never_mints(self):
        for mutate in ("compound", "extra_stdout", "no_marker"):
            payload = self.payload()
            if mutate == "compound":
                payload["tool_input"]["command"] = "true && " + payload["tool_input"]["command"]
            elif mutate == "extra_stdout":
                payload["tool_response"]["stdout"] += "another line\n"
            else:
                payload["tool_input"]["command"] = "gh pr create --body fixture"
            self.assertEqual(self.run_hook(payload).returncode, 0)
            self.assertEqual(self.receipts(), [])

    def test_registry_guard_five_fail_closed_cases(self):
        cases = ("missing", "empty", "malformed", "directory", "inactive")
        for case in cases:
            if self.registry.is_dir():
                self.registry.rmdir()
            elif self.registry.exists():
                self.registry.unlink()
            if case == "empty":
                self.registry.touch()
            elif case == "malformed":
                self.registry.write_text("{broken\n", encoding="utf-8")
            elif case == "directory":
                self.registry.mkdir()
            elif case == "inactive":
                self.registry.write_text(json.dumps({
                    "id": "other", "pane_id": "%43", "session_id": "other-session",
                }) + "\n", encoding="utf-8")
            result = self.run_hook(self.payload())
            self.assertEqual(result.returncode, 0, case)
            self.assertEqual(self.receipts(), [], case)


if __name__ == "__main__":
    unittest.main()
