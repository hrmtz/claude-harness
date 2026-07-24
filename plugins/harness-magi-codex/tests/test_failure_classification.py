#!/usr/bin/env python3
"""Unit coverage for content-free fan-out failure classifications."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from magi_classify_failure import classify  # noqa: E402
from magi_scrub import REDACTED, scrub_text_with_count  # noqa: E402
from magi_validate_findings import artifact_id  # noqa: E402


class FailureClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.doc = self.base / "design.md"
        self.doc.write_text("design\n")
        self.output = self.base / "safe.json"
        self.log = self.base / "safe.log"
        self.log.write_text("scrubbed log\n")
        self.meta = self.base / "scrub-meta.json"
        self.schema = ROOT / "schemas" / "finding.schema.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_meta(self, *, parsed: bool, input_bytes: int = 10) -> None:
        self.meta.write_text(
            json.dumps(
                {
                    "input_bytes": input_bytes,
                    "output_bytes": self.output.stat().st_size if self.output.exists() else 0,
                    "parsed_json": parsed,
                    "redactions": 0,
                }
            )
        )

    def valid_payload(self) -> dict[str, object]:
        return {
            "reviewer": "MELCHIOR",
            "round": 1,
            "artifact_id": artifact_id(self.doc),
            "artifact_sha": hashlib.sha256(self.doc.read_bytes()).hexdigest(),
            "verdict": "GO",
            "schema_grounding_verdict": "PASS",
            "verify_commands_executed": ["rg invariant design.md"],
            "source_artifacts": [],
            "dispositions": [],
            "findings": [],
        }

    def result(self, *, provider_exit: int = 0, scrub_exit: int = 0) -> dict[str, object]:
        return classify(
            output=self.output,
            log=self.log,
            scrub_meta=self.meta,
            provider_exit=provider_exit,
            scrub_exit=scrub_exit,
            status_valid=True,
            schema_path=self.schema,
            doc=self.doc,
            reviewer="MELCHIOR",
            round_number=1,
            expected_artifact_id=artifact_id(self.doc),
            expected_artifact_sha=hashlib.sha256(self.doc.read_bytes()).hexdigest(),
        )

    def test_provider_exit_precedes_payload_checks(self) -> None:
        self.write_meta(parsed=False)
        self.assertEqual(self.result(provider_exit=70)["classification"], "provider-exit")

    def test_empty_output(self) -> None:
        self.output.write_text("")
        self.write_meta(parsed=False, input_bytes=0)
        self.assertEqual(self.result()["classification"], "empty-output")

    def test_raw_json_parse_rejection(self) -> None:
        self.output.write_text("not-json")
        self.write_meta(parsed=False)
        self.assertEqual(self.result()["classification"], "json-parse-rejection")

    def test_post_scrub_corruption(self) -> None:
        self.output.write_text("not-json")
        self.write_meta(parsed=True)
        self.assertEqual(self.result()["classification"], "post-scrub-corruption")

    def test_schema_and_identity_rejections_are_distinct(self) -> None:
        self.output.write_text("{}")
        self.write_meta(parsed=True)
        self.assertEqual(self.result()["classification"], "json-schema-rejection")

        payload = self.valid_payload()
        payload["artifact_sha"] = "0" * 64
        self.output.write_text(json.dumps(payload))
        self.write_meta(parsed=True)
        result = self.result()
        self.assertEqual(result["classification"], "artifact-identity-rejection")
        self.assertEqual(result["identity_field"], "artifact_sha")

    def test_valid_payload_classifies_ok(self) -> None:
        self.output.write_text(json.dumps(self.valid_payload()))
        self.write_meta(parsed=True)
        self.assertEqual(self.result()["classification"], "ok")

    def test_doc_mutation_is_distinct_from_claim_bound_identity(self) -> None:
        payload = self.valid_payload()
        self.output.write_text(json.dumps(payload))
        self.write_meta(parsed=True)
        self.doc.write_text("mutated after claim\n")
        result = classify(
            output=self.output,
            log=self.log,
            scrub_meta=self.meta,
            provider_exit=0,
            scrub_exit=0,
            status_valid=True,
            schema_path=self.schema,
            doc=self.doc,
            reviewer="MELCHIOR",
            round_number=1,
            expected_artifact_id=payload["artifact_id"],
            expected_artifact_sha=payload["artifact_sha"],
        )
        self.assertEqual(result["classification"], "live-doc-drift")

    def test_missing_status_is_distinct_from_scrubber_failure(self) -> None:
        self.write_meta(parsed=False)
        result = classify(
            output=self.output,
            log=self.log,
            scrub_meta=self.meta,
            provider_exit=1,
            scrub_exit=1,
            status_valid=False,
            schema_path=self.schema,
            doc=self.doc,
            reviewer="MELCHIOR",
            round_number=1,
            expected_artifact_id=artifact_id(self.doc),
            expected_artifact_sha=hashlib.sha256(self.doc.read_bytes()).hexdigest(),
        )
        self.assertEqual(result["classification"], "status-missing-or-invalid")

    def test_password_only_dsn_and_quoted_assignment_are_scrubbed(self) -> None:
        dsn = "redis://" + ":" + "fixture-value" + "@host"
        assignment = "pass" + "word=" + '"' + "fixture-value" + '"'
        truncated = "to" + "ken=" + '"' + "truncated-fixture"
        escaped = "se" + "cret=" + "\\" + '"' + "escaped-fixture" + "\\" + '"'
        internal = (
            "api_" + "key=" + '"' + "prefix" + "\\" + '"' + "internal-fixture" + '"'
        )
        with_suffix = (
            "pass" + "word=" + '"' + "suffix-fixture" + '"' + " then restart"
        )
        scrubbed, count = scrub_text_with_count(
            f"{dsn} {assignment}\n{truncated}\n{escaped}\n{internal}\n{with_suffix}"
        )
        self.assertEqual(count, 6)
        self.assertNotIn("fixture-value", scrubbed)
        self.assertNotIn("truncated-fixture", scrubbed)
        self.assertNotIn("escaped-fixture", scrubbed)
        self.assertNotIn("internal-fixture", scrubbed)
        self.assertNotIn("suffix-fixture", scrubbed)
        self.assertIn(" then restart", scrubbed)
        self.assertEqual(scrubbed.count(REDACTED), 6)


if __name__ == "__main__":
    unittest.main()
