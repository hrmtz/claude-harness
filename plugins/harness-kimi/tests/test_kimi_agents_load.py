#!/usr/bin/env python3
"""Verify the Kimi AGENTS.md template loads and contains key rules."""

import re
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "AGENTS.md.template"


class TestKimiAgentsLoad(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = TEMPLATE.read_text(encoding="utf-8")

    def test_file_exists(self):
        self.assertTrue(TEMPLATE.is_file())

    def test_contains_sops_2_command(self):
        self.assertIn("sops edit", self.text)
        self.assertIn("sops exec-env", self.text)

    def test_contains_personas(self):
        self.assertRegex(self.text, r"真田志郎")
        self.assertRegex(self.text, r"松岡修造")
        self.assertRegex(self.text, r"東方仗助")

    def test_contains_backup_rule(self):
        self.assertRegex(self.text, r"sanada_backup")

    def test_no_blocked_verb(self):
        """Polarity rule: hook-style 'blocked/denied/violation' words should not appear."""
        lowered = self.text.lower()
        for bad in ("blocked", "denied", "violation", "forbidden", "refused"):
            self.assertNotIn(bad, lowered, f"found polarity-negative verb: {bad}")

    def test_no_emoji_warnings(self):
        """No emoji warning markers."""
        self.assertFalse(re.search(r"[🚨⚠️🛡]", self.text))

    def test_contains_genshijin_compression(self):
        """claude-harness#218: worker prose runs compressed (genshijin 通常)."""
        self.assertRegex(self.text, r"応答圧縮")
        self.assertIn("genshijin", self.text)
        self.assertIn("体言止め", self.text)

    def test_contains_double_submit_rail(self):
        """gh #105: the always-loaded template must pin the pane-messaging contract."""
        for token in (
            "formation msg",
            "tmux_send_submit",
            "sleep ~0.4s",
            "sleep ~0.5s",
            "send-keys -X cancel",
            "#{pane_in_mode}",
            "load-buffer",
            "paste-buffer -p",
        ):
            self.assertIn(token, self.text)
        # Double Enter: two submissions around the second delay, and the
        # shell-command single-Enter distinction.
        self.assertGreaterEqual(self.text.count("Enter"), 2)
        self.assertIn("1発", self.text)


if __name__ == "__main__":
    unittest.main()
