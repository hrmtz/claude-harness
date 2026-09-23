#!/usr/bin/env python3
"""Regression for a known secret quoted inside an interpreter NameError."""

import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SCRUB = HERE.parent / "hooks" / "credential_scrub.py"
CORPUS = HERE / "fp_corpus" / "corpus.jsonl"


def load_scrub():
    spec = importlib.util.spec_from_file_location("credential_scrub_nameerror", SCRUB)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CredentialNameErrorScrubTest(unittest.TestCase):
    def test_known_secret_in_nameerror_is_redacted(self):
        case = next(
            json.loads(line)
            for line in CORPUS.read_text(encoding="utf-8").splitlines()
            if '"id": "mal80"' in line
        )
        self.assertIn("synthetic", case["note"])
        secret = re.search(r"NameError: name '([^']+)'", case["payload"]).group(1)

        scrub = load_scrub()
        salt = bytes(32)
        digest = scrub.compute_hmac(secret.encode(), salt, "sha256-hmac")
        manifest = {len(secret): {digest: ["SYNTHETIC_KEY"]}}
        hits, complete = scrub.scan_output(
            case["payload"].encode(), manifest, salt, "sha256-hmac"
        )
        self.assertTrue(complete)
        self.assertEqual([value.decode() for value, _ in hits], [secret])

        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "session.jsonl"
            transcript.write_text(
                json.dumps({"type": "tool", "content": case["payload"]}) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(scrub.redact_jsonl(transcript, [hits[0][0]]), 1)
            redacted = transcript.read_text(encoding="utf-8")
            self.assertNotIn(secret, redacted)
            self.assertIn("NameError: name '<REDACTED>' is not defined", redacted)


if __name__ == "__main__":
    unittest.main()
