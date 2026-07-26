#!/usr/bin/env python3
"""Issue #155: backup suppression and value-safe eradication verification."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[3]
VERIFY = REPO / "scripts" / "verify_credential_eradication.sh"


class CredentialEradicationTest(unittest.TestCase):
    def run_verify(self, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(VERIFY), str(root)],
            capture_output=True,
            text=True,
            env={**os.environ, "HARNESS_ERADICATION_SCAN_TIMEOUT": "10"},
            check=False,
        )

    def test_reports_path_but_never_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            value = "ghp_" + ("Z" * 30)
            leaked = root / f"session-{value}.jsonl"
            leaked.write_text(value, encoding="utf-8")

            result = self.run_verify(root)

            self.assertEqual(result.returncode, 1)
            self.assertIn("session-ghp_<REDACTED>.jsonl", result.stdout)
            self.assertNotIn(value, result.stdout + result.stderr)

    def test_clean_for_known_identifier_and_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "driver.c").write_text(
                "cmcp_check_config_fw_match\n", encoding="utf-8"
            )
            (root / "example.env").write_text(
                "API_TOKEN=example0123456789012345\n", encoding="utf-8"
            )

            result = self.run_verify(root)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("credential-eradication: CLEAN", result.stdout)


if __name__ == "__main__":
    unittest.main()
