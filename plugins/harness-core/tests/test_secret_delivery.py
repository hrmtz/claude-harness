#!/usr/bin/env python3
"""Synthetic tests for destination-bound credential-file delivery."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


PLUGIN = Path(__file__).resolve().parent.parent
PROMPT_HOOK = PLUGIN / "hooks" / "secret_delivery_prompt.py"
CLASSIFIER = PLUGIN / "hooks" / "credential_file_read_guard.sh"
DELIVER = PLUGIN / "bin" / "harness-secret-deliver"
RECEIPT_RE = re.compile(r"receipt=([0-9a-f]{64})")
FILE_PAYLOAD = "synthetic-delivery-payload"


class SecretDeliveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.source = self.home / "credentials.txt"
        self.source.write_text(FILE_PAYLOAD, encoding="utf-8")
        self.env = dict(os.environ, HOME=str(self.home))

        self.scp_log = self.home / "scp-argv.json"
        self.fake_scp = self.home / "fake-scp"
        self.fake_scp.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "with open(os.environ['FAKE_SCP_LOG'], 'a', encoding='utf-8') as out:\n"
            "    out.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            f"print({FILE_PAYLOAD!r})\n"
            f"print({FILE_PAYLOAD!r}, file=sys.stderr)\n",
            encoding="utf-8",
        )
        self.fake_scp.chmod(0o700)
        self.delivery_env = dict(
            self.env,
            HARNESS_SECRET_DELIVERY_TESTING="1",
            HARNESS_SECRET_DELIVERY_SCP=str(self.fake_scp),
            FAKE_SCP_LOG=str(self.scp_log),
        )

    def run_hook(
        self,
        prompt: str,
        *,
        session_key: str = "session_id",
        session: str = "session-108",
    ) -> subprocess.CompletedProcess[str]:
        event = {"prompt": prompt, session_key: session}
        return self.run_hook_event(event)

    def run_hook_event(
        self, event: dict[str, object]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(PROMPT_HOOK)],
            input=json.dumps(event),
            text=True,
            capture_output=True,
            env=self.env,
            check=False,
        )

    def authorization_prompt(
        self,
        destination: str = "scp://deploy@taketsuru/srv/secrets/credentials.txt",
    ) -> str:
        return (
            "AUTHORIZE_SECRET_DELIVERY "
            f"source={self.source} destination={destination} representation=file"
        )

    def authorize(
        self,
        destination: str = "scp://deploy@taketsuru/srv/secrets/credentials.txt",
    ) -> tuple[str, subprocess.CompletedProcess[str]]:
        result = self.run_hook(self.authorization_prompt(destination))
        match = RECEIPT_RE.search(result.stdout)
        self.assertIsNotNone(match, result.stdout)
        return match.group(1), result

    def deliver(
        self, receipt: str, session: str = "session-108"
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(DELIVER),
                "--receipt",
                receipt,
                "--session",
                session,
            ],
            text=True,
            capture_output=True,
            env=self.delivery_env,
            check=False,
        )

    def test_classifier_cli_statuses(self) -> None:
        classified = subprocess.run(
            ["bash", str(CLASSIFIER), "--classify-path", str(self.source)],
            check=False,
        )
        ordinary = subprocess.run(
            ["bash", str(CLASSIFIER), "--classify-path", str(self.home / "notes.txt")],
            check=False,
        )
        template = subprocess.run(
            ["bash", str(CLASSIFIER), "--classify-path", str(self.home / ".env.example")],
            check=False,
        )
        self.assertEqual(classified.returncode, 0)
        self.assertEqual(ordinary.returncode, 10)
        self.assertEqual(template.returncode, 11)

    def test_authorize_and_deliver_once_without_chat_disclosure(self) -> None:
        receipt, hook = self.authorize()
        self.assertEqual(hook.returncode, 0)
        self.assertNotIn(FILE_PAYLOAD, hook.stdout + hook.stderr)

        receipt_path = (
            self.home
            / ".claude"
            / "state"
            / "secret_delivery"
            / "receipts"
            / f"{receipt}.json"
        )
        self.assertEqual(stat.S_IMODE(receipt_path.stat().st_mode), 0o600)
        self.assertNotIn(FILE_PAYLOAD, receipt_path.read_text(encoding="utf-8"))

        delivered = self.deliver(receipt)
        self.assertEqual(delivered.returncode, 0, delivered.stderr)
        self.assertEqual(delivered.stdout.strip(), "SECRET_DELIVERY_OK")
        self.assertNotIn(FILE_PAYLOAD, delivered.stdout + delivered.stderr)

        calls = self.scp_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(calls), 1)
        argv = json.loads(calls[0])
        self.assertEqual(
            argv,
            [
                "-B",
                "-q",
                "-oBatchMode=yes",
                "-oStrictHostKeyChecking=yes",
                "-oClearAllForwardings=yes",
                "-oForwardAgent=no",
                "-oPermitLocalCommand=no",
                "-oPasswordAuthentication=no",
                "-oKbdInteractiveAuthentication=no",
                "--",
                str(self.source),
                "deploy@taketsuru:/srv/secrets/credentials.txt",
            ],
        )
        second = self.deliver(receipt)
        self.assertNotEqual(second.returncode, 0)
        self.assertEqual(len(self.scp_log.read_text(encoding="utf-8").splitlines()), 1)

    def test_explicit_port_is_bound_in_argv(self) -> None:
        receipt, _ = self.authorize(
            "scp://deploy@host.example:2222/srv/secrets/credentials.txt"
        )
        delivered = self.deliver(receipt)
        self.assertEqual(delivered.returncode, 0, delivered.stderr)
        argv = json.loads(self.scp_log.read_text(encoding="utf-8").splitlines()[0])
        self.assertIn("-P", argv)
        self.assertEqual(argv[argv.index("-P") + 1], "2222")

    def test_unrelated_and_unclassified_prompts_do_not_mint(self) -> None:
        unrelated = self.run_hook("please explain this file")
        self.assertEqual(unrelated.returncode, 0)
        self.assertEqual(unrelated.stdout, "")

        ordinary = self.home / "notes.txt"
        ordinary.write_text("ordinary", encoding="utf-8")
        refused = self.run_hook(
            "AUTHORIZE_SECRET_DELIVERY "
            f"source={ordinary} "
            "destination=scp://host.example/srv/notes.txt representation=file"
        )
        self.assertIn("SOURCE_NOT_CLASSIFIED", refused.stdout)
        receipts = self.home / ".claude" / "state" / "secret_delivery" / "receipts"
        self.assertFalse(receipts.exists())

    def test_kimi_payload_without_supported_prompt_is_silent(self) -> None:
        result = self.run_hook_event(
            {
                "hook_event_name": "UserPromptSubmit",
                "messages": [{"role": "user"}],
            }
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_matching_authorization_with_invalid_session_is_refused(self) -> None:
        result = self.run_hook_event({"prompt": self.authorization_prompt()})
        self.assertEqual(result.returncode, 0)
        self.assertIn("SECRET_DELIVERY_REFUSED reason=INVALID_SESSION", result.stdout)
        self.assertNotIn("SECRET_DELIVERY_AUTHORIZED", result.stdout)

    def test_claude_authorization_remains_authorized(self) -> None:
        result = self.run_hook_event(
            {
                "prompt": self.authorization_prompt(),
                "session_id": "claude-session",
            }
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("SECRET_DELIVERY_AUTHORIZED", result.stdout)
        self.assertNotIn("SECRET_DELIVERY_REFUSED", result.stdout)

    def test_session_mismatch_consumes_receipt_without_running_scp(self) -> None:
        receipt, _ = self.authorize()
        failed = self.deliver(receipt, session="different-session")
        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse(self.scp_log.exists())
        again = self.deliver(receipt)
        self.assertNotEqual(again.returncode, 0)
        self.assertFalse(self.scp_log.exists())

    def test_source_change_is_detected(self) -> None:
        receipt, _ = self.authorize()
        self.source.write_text(FILE_PAYLOAD + "-changed", encoding="utf-8")
        failed = self.deliver(receipt)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("SOURCE_CHANGED", failed.stderr)
        self.assertFalse(self.scp_log.exists())

    def test_camel_case_event_aliases(self) -> None:
        prompt = (
            "AUTHORIZE_SECRET_DELIVERY "
            f"source={self.source} "
            "destination=scp://host.example/srv/credentials.txt representation=file"
        )
        event = {"userPrompt": prompt, "sessionId": "camel-session"}
        result = subprocess.run(
            ["python3", str(PROMPT_HOOK)],
            input=json.dumps(event),
            text=True,
            capture_output=True,
            env=self.env,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertRegex(result.stdout, RECEIPT_RE)


if __name__ == "__main__":
    unittest.main()
