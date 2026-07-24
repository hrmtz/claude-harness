#!/usr/bin/env python3
"""Tests for deterministic one-shot Magi pre-flight synthesis."""

from __future__ import annotations

import hashlib
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import jsonschema


HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
SCRIPT = PLUGIN / "scripts" / "magi_preflight.py"
RUNNER = PLUGIN / "scripts" / "magi_preflight_codex.sh"
REVIEW_SCHEMA = json.loads(
    (PLUGIN / "schemas" / "preflight-review.schema.json").read_text()
)
DECISION_SCHEMA = json.loads(
    (PLUGIN / "schemas" / "preflight-decision.schema.json").read_text()
)
RUN_SCHEMA = json.loads(
    (PLUGIN / "schemas" / "preflight-run.schema.json").read_text()
)
sys.path.insert(0, str(PLUGIN / "scripts"))
import magi_preflight as preflight  # noqa: E402


PERSONAS = ("MELCHIOR", "BALTHASAR", "CASPAR")


class PreflightTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.brief = self.root / "brief.md"
        self.brief.write_text("change\nrisk boundary\nrollback path\n", encoding="utf-8")
        self.paths = {
            persona: self.root / f"{persona.lower()}.json" for persona in PERSONAS
        }
        self.manifest = self.root / "preflight-run.json"
        self.write_reviews({})

    def tearDown(self) -> None:
        self.temp.cleanup()

    def brief_identity(self) -> dict[str, str]:
        canonical = self.brief.resolve()
        return {
            "canonical_path": str(canonical),
            "artifact_id": hashlib.sha256(str(canonical).encode()).hexdigest()[:16],
            "sha256": hashlib.sha256(self.brief.read_bytes()).hexdigest(),
        }

    def evidence(self, start: int = 2, end: int = 2) -> list[dict[str, object]]:
        lines = self.brief.read_bytes().splitlines(keepends=True)
        digest = hashlib.sha256(b"".join(lines[start - 1 : end])).hexdigest()
        return [
            {
                "kind": "brief-lines",
                "start_line": start,
                "end_line": end,
                "sha256": digest,
            }
        ]

    def finding(
        self,
        finding_id: str,
        root: str,
        *,
        severity: str = "HIGH",
        impact: list[str] | None = None,
        decision: str = "PIVOT",
        evidence: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        return {
            "finding_id": finding_id,
            "root_cause_id": root,
            "severity": severity,
            "impact": impact or ["technical"],
            "summary": f"summary for {root}",
            "rationale": f"rationale for {root}",
            "required_change": f"change {root}",
            "recommended_decision": decision,
            "evidence": [] if evidence is None else evidence,
            "question_if_uncorroborated": f"Is {root} a real blocker?",
        }

    def write_reviews(
        self,
        findings: dict[str, list[dict[str, object]]],
        verdicts: dict[str, str] | None = None,
    ) -> None:
        verdicts = verdicts or {}
        for persona in PERSONAS:
            payload = {
                "schema": "magi-preflight-review/v1",
                "reviewer": persona,
                "round": 1,
                "brief": self.brief_identity(),
                "verdict": verdicts.get(persona, "PROCEED"),
                "findings": findings.get(persona, []),
            }
            self.paths[persona].write_text(
                json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8"
            )
        self.write_manifest()

    def write_manifest(self, order: tuple[str, ...] = PERSONAS) -> None:
        payload = {
            "schema": "magi-preflight-run/v1",
            "runner": "magi-preflight-codex/v1",
            "run_id": "1" * 32,
            "round": 1,
            "brief": self.brief_identity(),
            "started_before_output_collection": True,
            "allows_second_round": False,
            "reviewers": [
                {
                    "reviewer": persona,
                    "path": str(self.paths[persona].resolve()),
                    "sha256": hashlib.sha256(
                        self.paths[persona].read_bytes()
                    ).hexdigest(),
                    "prompt_sha256": hashlib.sha256(
                        preflight.review_prompt(
                            preflight.stable_read(
                                self.brief, limit=preflight.MAX_BRIEF_BYTES
                            ),
                            persona,
                        )
                    ).hexdigest(),
                }
                for persona in order
            ],
        }
        self.manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    def evaluate(self) -> dict[str, object]:
        return preflight.evaluate(self.brief, self.manifest)

    def run_cli(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "evaluate",
                str(self.brief),
                str(self.manifest),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_empty_complete_round_proceeds_report_only(self) -> None:
        result = self.evaluate()
        self.assertEqual(result["decision"], "PROCEED")
        self.assertEqual(result["reason_code"], "NO_BLOCKING_CONCERN")
        self.assertEqual(result["rounds_consumed"], 1)
        self.assertFalse(result["allows_second_round"])
        self.assertFalse(result["authorizes_shipping"])
        self.assertEqual(
            [source["reviewer"] for source in result["source_artifacts"]],
            list(PERSONAS),
        )
        jsonschema.validate(result, DECISION_SCHEMA)

    def test_unsupported_minority_becomes_question(self) -> None:
        finding = self.finding("M1", "runtime.timeout")
        self.write_reviews({"MELCHIOR": [finding]})
        result = self.evaluate()
        self.assertEqual(result["decision"], "PROCEED")
        self.assertEqual(result["vetoes"], [])
        self.assertEqual(
            result["questions"],
            [
                {
                    "root_cause_id": "runtime.timeout",
                    "source_ref": "MELCHIOR#M1",
                    "question": "Is runtime.timeout a real blocker?",
                }
            ],
        )

    def test_grounded_minority_critical_is_pivot_veto(self) -> None:
        finding = self.finding(
            "M1",
            "unsafe.migration",
            severity="CRITICAL",
            evidence=self.evidence(),
        )
        self.write_reviews({"MELCHIOR": [finding]}, {"MELCHIOR": "PIVOT"})
        result = self.evaluate()
        self.assertEqual(result["decision"], "PIVOT")
        self.assertEqual(result["reason_code"], "GROUNDED_PIVOT_VETO")
        self.assertEqual(result["vetoes"][0]["reason"], "critical")
        self.assertEqual(result["questions"], [])

    def test_grounded_minority_special_impact_is_abort_veto(self) -> None:
        finding = self.finding(
            "B1",
            "backup.missing",
            severity="LOW",
            impact=["operational", "data-loss"],
            decision="ABORT",
            evidence=self.evidence(3, 3),
        )
        self.write_reviews({"BALTHASAR": [finding]}, {"BALTHASAR": "ABORT"})
        result = self.evaluate()
        self.assertEqual(result["decision"], "ABORT")
        self.assertEqual(result["reason_code"], "GROUNDED_ABORT_VETO")
        self.assertEqual(result["vetoes"][0]["reason"], "data-loss")

    def test_two_person_exact_root_corroborates(self) -> None:
        self.write_reviews(
            {
                "MELCHIOR": [self.finding("M1", "scope.too-wide")],
                "CASPAR": [
                    self.finding(
                        "C1",
                        "scope.too-wide",
                        impact=["commercial"],
                    )
                ],
            },
            {"MELCHIOR": "PIVOT", "CASPAR": "PIVOT"},
        )
        result = self.evaluate()
        self.assertEqual(result["decision"], "PIVOT")
        self.assertEqual(result["reason_code"], "CORROBORATED_PIVOT")
        self.assertEqual(
            result["corroborated_roots"][0]["reviewers"],
            ["MELCHIOR", "CASPAR"],
        )
        self.assertEqual(result["questions"], [])

    def test_corroborated_abort_precedes_grounded_pivot(self) -> None:
        self.write_reviews(
            {
                "MELCHIOR": [
                    self.finding(
                        "M1",
                        "grounded.veto",
                        severity="CRITICAL",
                        evidence=self.evidence(),
                    )
                ],
                "BALTHASAR": [
                    self.finding(
                        "B1",
                        "no.value",
                        impact=["operational"],
                        decision="ABORT",
                    )
                ],
                "CASPAR": [
                    self.finding(
                        "C1",
                        "no.value",
                        impact=["commercial"],
                        decision="ABORT",
                    )
                ],
            },
            {"MELCHIOR": "PIVOT", "BALTHASAR": "ABORT", "CASPAR": "ABORT"},
        )
        result = self.evaluate()
        self.assertEqual(result["decision"], "ABORT")
        self.assertEqual(result["reason_code"], "CORROBORATED_ABORT")

    def test_input_order_does_not_change_output(self) -> None:
        first = self.evaluate()
        self.write_manifest(("CASPAR", "MELCHIOR", "BALTHASAR"))
        second = self.evaluate()
        self.assertEqual(first, second)
        self.assertEqual(
            json.dumps(first, sort_keys=True, separators=(",", ":")),
            json.dumps(second, sort_keys=True, separators=(",", ":")),
        )

    def test_stale_brief_binding_fails_closed(self) -> None:
        payload = json.loads(self.paths["MELCHIOR"].read_text())
        payload["brief"]["sha256"] = "0" * 64
        self.paths["MELCHIOR"].write_text(json.dumps(payload))
        self.write_manifest()
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["decision"], "ABORT")
        self.assertEqual(decision["reason_code"], "UNSAFE_OR_INCOMPLETE_INPUT")
        self.assertFalse(decision["authorizes_shipping"])
        jsonschema.validate(decision, DECISION_SCHEMA)

    def test_wrong_line_slice_digest_fails_closed(self) -> None:
        finding = self.finding(
            "M1",
            "bad.digest",
            severity="CRITICAL",
            evidence=self.evidence(),
        )
        finding["evidence"][0]["sha256"] = "0" * 64
        self.write_reviews({"MELCHIOR": [finding]}, {"MELCHIOR": "PIVOT"})
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("evidence digest mismatch", json.loads(result.stdout)["detail"])

    def test_symlinked_artifact_fails_closed(self) -> None:
        target = self.paths["CASPAR"]
        link = self.root / "caspar-link.json"
        link.symlink_to(target)
        manifest = json.loads(self.manifest.read_text())
        manifest["reviewers"][2]["path"] = str(link)
        self.manifest.write_text(json.dumps(manifest))
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("symlinked input", json.loads(result.stdout)["detail"])

    def test_duplicate_persona_is_incomplete(self) -> None:
        payload = json.loads(self.paths["CASPAR"].read_text())
        payload["reviewer"] = "MELCHIOR"
        self.paths["CASPAR"].write_text(json.dumps(payload))
        self.write_manifest()
        with self.assertRaisesRegex(preflight.UnsafeInput, "persona mismatch"):
            self.evaluate()

    def test_duplicate_root_within_one_reviewer_is_rejected(self) -> None:
        self.write_reviews(
            {
                "MELCHIOR": [
                    self.finding("M1", "same.root"),
                    self.finding("M2", "same.root"),
                ]
            }
        )
        with self.assertRaisesRegex(preflight.UnsafeInput, "duplicate root_cause_id"):
            self.evaluate()

    def test_mutation_detected_during_final_resample(self) -> None:
        with mock.patch.object(
            preflight,
            "assert_unchanged",
            side_effect=preflight.UnsafeInput("input changed during evaluation"),
        ):
            with self.assertRaisesRegex(preflight.UnsafeInput, "changed during"):
                self.evaluate()

    def test_exactly_three_artifacts_required(self) -> None:
        payload = json.loads(self.manifest.read_text())
        payload["reviewers"].pop()
        self.manifest.write_text(json.dumps(payload))
        with self.assertRaisesRegex(preflight.UnsafeInput, "schema mismatch"):
            self.evaluate()

    def test_brief_line_limit_accepts_200_and_rejects_201(self) -> None:
        self.brief.write_text("line\n" * 200, encoding="utf-8")
        self.write_reviews({})
        self.assertEqual(self.evaluate()["decision"], "PROCEED")
        self.brief.write_text("line\n" * 201, encoding="utf-8")
        self.write_reviews({})
        with self.assertRaisesRegex(preflight.UnsafeInput, "200 lines"):
            self.evaluate()

    def test_review_schema_rejects_second_round(self) -> None:
        payload = json.loads(self.paths["MELCHIOR"].read_text())
        payload["round"] = 2
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(payload, REVIEW_SCHEMA)

    def test_proceed_cannot_contradict_grounded_veto(self) -> None:
        finding = self.finding(
            "M1",
            "contradiction",
            severity="CRITICAL",
            evidence=self.evidence(),
        )
        self.write_reviews({"MELCHIOR": [finding]})
        with self.assertRaisesRegex(preflight.UnsafeInput, "PROCEED contradicts"):
            self.evaluate()

    def test_manifest_digest_tamper_fails_closed(self) -> None:
        payload = json.loads(self.manifest.read_text())
        payload["reviewers"][0]["sha256"] = "0" * 64
        self.manifest.write_text(json.dumps(payload))
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("output binding mismatch", json.loads(result.stdout)["detail"])

    def test_manifest_prompt_digest_tamper_fails_closed(self) -> None:
        payload = json.loads(self.manifest.read_text())
        payload["reviewers"][0]["prompt_sha256"] = "0" * 64
        self.manifest.write_text(json.dumps(payload))
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("prompt binding mismatch", json.loads(result.stdout)["detail"])

    def test_decision_schema_couples_reason_and_exact_personas(self) -> None:
        valid = self.evaluate()
        contradictory = dict(valid)
        contradictory["decision"] = "ABORT"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(contradictory, DECISION_SCHEMA)
        duplicate = json.loads(json.dumps(valid))
        duplicate["source_artifacts"][2]["reviewer"] = "MELCHIOR"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(duplicate, DECISION_SCHEMA)

    def test_structural_runner_isolates_three_reviewers_before_collection(self) -> None:
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        codex = fake_bin / "codex"
        codex.write_text(
            """#!/usr/bin/env python3
import json, os, pathlib, re, sys
args = sys.argv[1:]
output = pathlib.Path(args[args.index("-o") + 1])
prompt = sys.stdin.read()
def field(name):
    match = re.search(rf"^{name}: (.+)$", prompt, re.M)
    if not match:
        raise SystemExit(9)
    return match.group(1)
persona = field("REVIEWER")
siblings = {"MELCHIOR", "BALTHASAR", "CASPAR"} - {persona}
stage = output.parent
if any((stage / f"{sibling.lower()}.prompt").exists() for sibling in siblings):
    raise SystemExit(8)
runtime_probe = pathlib.Path(os.environ["TMPDIR"]) / "codex-runtime-probe"
runtime_probe.write_text("writable")
payload = {
    "schema": "magi-preflight-review/v1",
    "reviewer": persona,
    "round": 1,
    "brief": {
        "canonical_path": field("BRIEF_CANONICAL_PATH"),
        "artifact_id": field("BRIEF_ARTIFACT_ID"),
        "sha256": field("BRIEF_SHA256"),
    },
    "verdict": "PROCEED",
    "findings": [],
}
output.write_text(json.dumps(payload) + "\\n")
""",
            encoding="utf-8",
        )
        codex.chmod(0o755)
        output = self.root / "runner-output"
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env['PATH']}"
        result = subprocess.run(
            ["bash", str(RUNNER), str(self.brief.resolve()), str(output)],
            text=True,
            capture_output=True,
            check=False,
            env=env,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["decision"], "PROCEED")
        run = json.loads((output / "preflight-run.json").read_text())
        jsonschema.validate(run, RUN_SCHEMA)
        self.assertTrue(run["started_before_output_collection"])
        self.assertEqual(
            {entry["reviewer"] for entry in run["reviewers"]}, set(PERSONAS)
        )
        for entry in run["reviewers"]:
            artifact = Path(entry["path"])
            self.assertEqual(
                hashlib.sha256(artifact.read_bytes()).hexdigest(), entry["sha256"]
            )

    def test_structural_runner_refuses_concurrent_output_owner(self) -> None:
        output = self.root / "locked-output"
        output.mkdir()
        lock_path = output / ".preflight.lock"
        with lock_path.open("w") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run(
                ["bash", str(RUNNER), str(self.brief.resolve()), str(output)],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 3)
        self.assertIn("another run owns", result.stderr)
        self.assertFalse((output / "preflight-run.json").exists())


if __name__ == "__main__":
    unittest.main()
