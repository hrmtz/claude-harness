#!/usr/bin/env python3
import pathlib
import unittest

SKILL = pathlib.Path(__file__).resolve().parents[1] / "skills" / "babysit-pr" / "SKILL.md"


class BabysitSkillContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SKILL.read_text(encoding="utf-8")

    def test_receipt_is_an_unconditional_entry_gate(self):
        self.assertIn("If it is absent, malformed", self.text)
        self.assertIn("return a no-op", self.text)
        self.assertIn("Never reconstruct or mint a receipt manually", self.text)

    def test_authority_boundaries_are_explicit(self):
        for phrase in (
            "Never auto-resolve a thread",
            "merge",
            "gh pr ready",
            "PUBLIC",
            "do not post automatically",
            "Never paste raw",
            "five fix iterations",
            "90 minutes",
            "every 60 seconds",
            ".github/workflows/**",
            "tests/**",
            "durable receipt",
        ):
            self.assertIn(phrase, self.text)

    def test_completion_evidence_is_required(self):
        for phrase in (
            "cumulative diffstat",
            "before/after check-name set difference",
            "before/after changed-file set difference",
            "reply correspondence table",
        ):
            self.assertIn(phrase, self.text)


if __name__ == "__main__":
    unittest.main()
