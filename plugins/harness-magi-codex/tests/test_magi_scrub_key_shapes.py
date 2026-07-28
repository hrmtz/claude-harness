#!/usr/bin/env python3
"""Key-shape coverage for magi_scrub (gh #212).

Every fixture below is synthetic. The point of the file is the pair: each shape
we claim to catch must actually disappear, and each shape we deliberately keep
must actually survive. A scrubber tested only on positives degrades into one
that redacts everything, which destroys the artifacts review depends on.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from magi_scrub import REDACTED, scrub_text_with_count  # noqa: E402

SCRUB = ROOT / "scripts" / "magi_scrub.py"

# Synthetic values with the right shape and no real entropy.
AGE_SECRET = "AGE-SECRET-KEY-1" + "QQQQQQQQ" * 5 + "ZZZZZZZZ"
AGE_PUBLIC = "age1" + "qqqqqqqq" * 5 + "zzzzzzzz"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0LXN1YmplY3QifQ.c2lnbmF0dXJlLXBsYWNlaG9sZGVy"
PEM = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAAB\n"
    "AAAAMwAAAAtzc2gtZWQyNTUxOQAAACBQTEFDRUhPTERFUlBMQUNF\n"
    "-----END OPENSSH PRIVATE KEY-----"
)

CAUGHT = {
    "age_secret_key": AGE_SECRET,
    "jwt": JWT,
    "github_oauth": "gho_" + "A" * 24,
    "github_server": "ghs_" + "B" * 24,
    "github_pat": "github_pat_" + "C" * 30,
    "google_api": "AIza" + "D" * 30,
    "xai": "xai-" + "E" * 30,
    "aws_sts": "ASIA" + "F" * 16,
    # Pre-existing shapes: these must not regress while new ones are added.
    "openai": "sk-" + "G" * 32,
    "github_classic": "ghp_" + "H" * 24,
    "aws_access": "AKIA" + "I" * 16,
    "bearer": "Bearer " + "J" * 32,
    "dsn": "postgresql://svc:hunter2placeholder@db.example.internal:5432/app",
}

KEPT = {
    # Published by design — redacting it hides which recipient a file targets.
    "age_public_key": AGE_PUBLIC,
    # A path names the file to rotate; scrubbing it destroys the remediation hint.
    "secret_file_path": "creds-migration/secrets-template/hippocampus.enc.yaml",
    # Digests are already one-way.
    "sha256_digest": "sha256:" + "0123456789abcdef" * 4,
    # Ordinary prose and flags that merely contain the word key.
    "schema_flag": "--json-schema key=value",
    "prose": "the age recipient key rotates quarterly",
}


class CaughtShapes(unittest.TestCase):
    def test_each_caught_shape_is_redacted(self) -> None:
        for name, value in CAUGHT.items():
            with self.subTest(shape=name):
                scrubbed, count = scrub_text_with_count(f"context {value} trailing")
                self.assertNotIn(value, scrubbed)
                self.assertGreaterEqual(count, 1)
                self.assertIn(REDACTED, scrubbed)

    def test_pem_body_goes_but_the_marker_stays(self) -> None:
        scrubbed, count = scrub_text_with_count(PEM)
        self.assertGreaterEqual(count, 1)
        self.assertNotIn("b3BlbnNzaC1rZXktdjEA", scrubbed)
        self.assertIn("-----BEGIN OPENSSH PRIVATE KEY-----", scrubbed)
        self.assertIn("-----END OPENSSH PRIVATE KEY-----", scrubbed)

    def test_age_secret_survives_neither_json_values_nor_keys(self) -> None:
        doc = json.dumps({AGE_SECRET: {"note": f"pasted {AGE_SECRET}"}})
        out = subprocess.run(
            [sys.executable, str(SCRUB)], input=doc, capture_output=True, text=True, check=True
        ).stdout
        self.assertNotIn(AGE_SECRET, out)

    def test_non_json_input_is_still_scrubbed(self) -> None:
        out = subprocess.run(
            [sys.executable, str(SCRUB)],
            input=f"not json at all {AGE_SECRET}",
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertNotIn(AGE_SECRET, out)


class KeptShapes(unittest.TestCase):
    def test_each_kept_shape_survives_untouched(self) -> None:
        for name, value in KEPT.items():
            with self.subTest(shape=name):
                scrubbed, count = scrub_text_with_count(f"context {value} trailing")
                self.assertIn(value, scrubbed)
                self.assertEqual(count, 0)

    def test_age_public_key_survives_next_to_a_redacted_secret(self) -> None:
        text = f"recipient {AGE_PUBLIC} identity {AGE_SECRET}"
        scrubbed, _ = scrub_text_with_count(text)
        self.assertIn(AGE_PUBLIC, scrubbed)
        self.assertNotIn(AGE_SECRET, scrubbed)


if __name__ == "__main__":
    unittest.main()
