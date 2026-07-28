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

    def test_contains_native_hook_preamble(self):
        """gh #54 / #231: preamble states Kimi >= 0.28 native hooks + wiring path."""
        self.assertIn("native hook API", self.text)
        self.assertIn("install-kimi-hooks.sh", self.text)
        self.assertIn("cross_cli_hooks.json", self.text)
        self.assertIn("fail-open", self.text)

    def test_no_stale_no_hooks_claim(self):
        """gh #231: the pre-0.28 'Kimi has no hooks' claim must not come back."""
        self.assertNotIn("hook がないため", self.text)

    def test_contains_sops_flat_mapping_constraint(self):
        """gh #231: CH-only nested mapping / list constraint is merged into §2."""
        self.assertIn("flat mapping", self.text)
        self.assertIn("nested mapping", self.text)

    def test_contains_identity_locked(self):
        """gh #231: §8 keeps the @formation_identity_locked source of truth."""
        self.assertIn("@formation_identity_locked", self.text)
        self.assertIn("routing identity と表示名", self.text)

    def test_contains_pane_messaging_section(self):
        """gh #231: §9 Formation pane messaging is present (31 installs lack it)."""
        self.assertRegex(self.text, r"(?m)^## 9\. Formation pane messaging")
        self.assertIn("pane-messaging-rail.md", self.text)

    def test_section_numbering_has_no_gap(self):
        """gh #231: sections 1-10 all present, in order, no gap."""
        heads = re.findall(r"^## (\d+)\.", self.text, flags=re.MULTILINE)
        self.assertEqual(heads, [str(n) for n in range(1, 11)])

    def test_no_volatile_tmp_backup(self):
        """gh #231: the anagram-port /tmp 24h backup rule is discarded."""
        self.assertNotIn("/tmp/sanada_backup", self.text)

    def test_contains_template_stamp(self):
        """gh #231: machine-readable provenance stamp for the drift checker."""
        self.assertRegex(self.text, r"<!-- harness-agents-template: rev=\S+ -->")

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
