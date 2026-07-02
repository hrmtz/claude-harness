#!/usr/bin/env python3
"""Verify kimi_session_scrub redacts known credential values from Kimi wire.jsonl."""

import hashlib
import hmac
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Ensure harness-core hooks and the local scrubber are importable.
CORE_HOOKS = Path.home() / "projects" / "claude-harness" / "plugins" / "harness-core" / "hooks"
sys.path.insert(0, str(CORE_HOOKS))
sys.path.insert(0, str(HERE.parent))

import credential_scrub as cs  # noqa: E402
import kimi_session_scrub as ks  # noqa: E402


class TestKimiScrub(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="kimi_scrub_test_"))
        cls.manifest_dir = cls.tmp / "manifest"
        cls.manifest_dir.mkdir()
        cls.salt_file = cls.tmp / "salt.bin"
        cls.hook_log = cls.tmp / "hooks.log"
        cls.salt = os.urandom(32)
        cls.salt_file.write_bytes(cls.salt)

        # Patch credential_scrub to use temp paths.
        cs.MANIFEST_DIR = cls.manifest_dir
        cs.SALT_FILE = cls.salt_file
        cs.HOOK_LOG = cls.hook_log
        cs.STATE_DIR = cls.tmp / "credential_scrub"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _make_manifest(self, secret: bytes, key_name: str = "TEST_API_KEY") -> Path:
        h = hmac.new(self.salt, secret, hashlib.sha256).hexdigest()
        doc = {
            "format_version": 1,
            "algorithm": "sha256-hmac",
            "entries": [
                {"key": key_name, "byte_length": len(secret), "hmac": h}
            ],
        }
        path = self.manifest_dir / "test.scrub.json"
        path.write_text(json.dumps(doc))
        return path

    def _make_wire(self, secret: str) -> Path:
        # Uses the REAL Kimi wire.jsonl schema (context.append_loop_event with
        # nested .event of type tool.call / tool.result, plus turn.prompt).
        # The old test used tool_call/tool_response/user records that never
        # existed in real wire.jsonl, so it passed against a broken scrubber
        # (code-review #52).
        path = self.tmp / "wire.jsonl"
        records = [
            {"type": "turn.prompt", "input": "run this"},
            {
                "type": "context.append_loop_event",
                "event": {
                    "type": "tool.call",
                    "name": "Bash",
                    "args": {"command": f"echo '{secret}'"},
                },
            },
            {
                "type": "context.append_loop_event",
                "event": {
                    "type": "tool.result",
                    "result": {"output": secret, "isError": False},
                },
            },
        ]
        with path.open("w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        # Backdate mtime so the QUIESCENCE race-guard treats the file as idle
        # (a freshly-written file looks like an active session and is skipped).
        old = time.time() - (ks.QUIESCENCE_SECONDS + 60)
        os.utime(path, (old, old))
        return path

    def test_redacts_secret_in_wire_jsonl(self):
        secret = "sk-test-REDACTME_0123456789abcdef"
        self._make_manifest(secret.encode("utf-8"))
        wire = self._make_wire(secret)

        replaced, _ = ks.scrub_file(wire)

        self.assertGreater(replaced, 0)
        new_text = wire.read_text(encoding="utf-8")
        self.assertNotIn(secret, new_text)
        self.assertIn(cs.GENERIC_REDACT_MARKER, new_text)

    def test_no_false_positive_when_manifest_empty(self):
        wire = self._make_wire("sk-test-NOT_IN_MANIFEST_0123456789abcdef")
        replaced, _ = ks.scrub_file(wire)
        self.assertEqual(replaced, 0)

    def test_extract_corpus_matches_real_schema(self):
        # Guards against the schema-drift regression: the credential must appear
        # in the extracted corpus for real record types.
        secret = "sk-test-SCHEMA_0123456789abcdef"
        wire = self._make_wire(secret)
        corpus = ks.extract_corpus(wire).decode("utf-8", errors="surrogateescape")
        self.assertIn(secret, corpus)

    def test_active_file_is_not_redacted(self):
        # Race guard: a file written just now (active session) must be skipped,
        # not rewritten under the live writer.
        secret = "sk-test-ACTIVE_0123456789abcdef"
        self._make_manifest(secret.encode("utf-8"))
        wire = self._make_wire(secret)
        os.utime(wire, None)  # bump mtime to now → looks active
        replaced, _ = ks.scrub_file(wire)
        self.assertEqual(replaced, 0)
        self.assertIn(secret, wire.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
