#!/usr/bin/env python3
"""Pin the v0.11.0 free-range reviewer prose contract."""

import json
from pathlib import Path
import sys
import unittest


SKILL = Path(__file__).resolve().parents[1] / "skills/dual-magi-review/SKILL.md"
ROOT = SKILL.parents[4]
CODEX_SCRIPTS = ROOT / "plugins/harness-magi-codex/scripts"
CODEX_SCHEMA = ROOT / "plugins/harness-magi-codex/schemas/finding.schema.json"
sys.path.insert(0, str(CODEX_SCRIPTS))

from magi_validate_findings import validate  # noqa: E402


class FreerangeContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = SKILL.read_text(encoding="utf-8")

    def test_flag_is_optional_fourth_reviewer_after_round_one(self) -> None:
        self.assertIn("version: 0.11.0", self.text)
        self.assertIn("[--freerange]", self.text)
        self.assertIn("round 2 以降だけ有効", self.text)
        self.assertIn("追加の 4 体目", self.text)
        self.assertIn("既存 reviewer の置換・必要 reviewer 集合への算入は禁止", self.text)
        self.assertIn("`xfamily` / `freerange` は", " ".join(self.text.split()))

    def test_output_stays_in_per_doc_namespace_and_return_is_bounded(self) -> None:
        self.assertIn("${magi_dir}/round_<N>_freerange.json", self.text)
        self.assertIn("${doc_dir}/.dual-magi/` flat 域への書込みは禁止", self.text)
        self.assertIn("返り値は通常 reviewer と同じく ≤200 words", self.text)

    def test_severity_gate_is_prose_owned(self) -> None:
        normalized = " ".join(self.text.split())
        sentence = " ".join(
            (
                "freerange の CRITICAL / HIGH は、他 reviewer と同様に plateau を止める。",
                "「数えない」のは必要 reviewer 集合であって severity gate ではない。",
            )
        )
        self.assertIn(sentence, normalized)
        self.assertIn("この Step 6 の散文 gate が担う", self.text)

    def test_verification_is_sidecar_only_and_asynchronous(self) -> None:
        self.assertIn("${magi_dir}/verification.json", self.text)
        self.assertIn('"artifact_digest":', self.text)
        self.assertIn(
            "canonical_repo + source_relpath + artifact_digest + reviewer + finding_id",
            self.text,
        )
        self.assertIn("verdict の DB 反映は非同期", self.text)
        self.assertIn("次回 harvest", self.text)
        self.assertNotIn("magi_findings_mark_verdict.py", self.text)

    def test_required_reviewer_failure_is_not_masked_by_freerange(self) -> None:
        normalized = " ".join(self.text.split())
        self.assertIn("元の 3 perspective label", self.text)
        self.assertIn(
            "required reviewer の欠落を埋めたものとして数えない",
            normalized,
        )
        self.assertIn("optional_freerange_missing", self.text)

    def test_retirement_uses_filesystem_floor_global_query_and_reviewed_coverage(self) -> None:
        self.assertIn("reviewer_files", self.text)
        self.assertIn("zero-finding output", self.text)
        self.assertIn("first_seen_at` を launch count / order に使わない", self.text)
        self.assertIn("全 repository を横断", self.text)
        self.assertIn("--git-common-dir", self.text)
        self.assertIn("observed_findings = expected_findings", self.text)
        self.assertIn("count(DISTINCT (canonical_repo, campaign_id)) <> 3", self.text)
        self.assertIn(
            "count(DISTINCT (f.title_norm, f.location_norm, f.severity_norm))",
            self.text,
        )
        self.assertIn("parent_verdict = 'verified'", self.text)
        self.assertIn("parent_verdict IN ('verified', 'disputed')", self.text)
        self.assertIn("`unreviewed` は coverage に数えない", self.text)
        self.assertIn("WHERE NOT f.dropped", self.text)
        self.assertIn("'RETIRE'", self.text)

    def test_shared_validator_accepts_freerange_reviewer_label(self) -> None:
        # P13 is a narrow compatibility probe for the label. Claude-native
        # reviewer output is not claimed to use this companion full envelope.
        schema = json.loads(CODEX_SCHEMA.read_text(encoding="utf-8"))
        payload = {
            "reviewer": "freerange",
            "round": 2,
            "artifact_id": "0" * 16,
            "artifact_sha": "0" * 64,
            "verdict": "GO",
            "schema_grounding_verdict": "PARTIAL",
            "verify_commands_executed": ["Read full document"],
            "source_artifacts": [],
            "dispositions": [],
            "findings": [
                {
                    "finding_id": "r2-freerange-1",
                    "severity": "LOW",
                    "title": "Operator step lacks a dry-run example",
                    "location": "Operational procedure",
                    "rationale": "The procedure is auditable but easy to misinvoke.",
                    "required_fix": "Add a no-write example using representative paths.",
                    "confidence": "high",
                    "dup_flag": "new",
                    "missed_angle": "Checklist-free review caught an operator usability gap.",
                }
            ],
        }
        validate(
            payload,
            schema,
            expected_reviewer="freerange",
            expected_round=2,
        )


if __name__ == "__main__":
    unittest.main()
