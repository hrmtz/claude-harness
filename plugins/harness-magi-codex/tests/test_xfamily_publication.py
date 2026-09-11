#!/usr/bin/env python3
"""Offline adapter regression; run identical fixtures against --plugin BASE/CANDIDATE."""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PLUGIN = Path(os.environ.get("MAGI_TEST_PLUGIN", Path(__file__).resolve().parents[1]))

# These envelopes/transcripts use the same provider contracts as test_*_provider.sh.
# Both executables are local stubs: no credentials, provider process, or network needed.
PROVIDER_STUB = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ["PUBLICATION_FIXTURE"])
family = pathlib.Path(sys.argv[0]).name
with (root / "calls").open("a") as handle:
    handle.write(family + "\n")
if family == "claude":
    sys.stdin.read()
if os.environ.get("PUBLICATION_TRANSPORT_FAILURE") == "1":
    print("synthetic provider unavailable", file=sys.stderr)
    sys.exit(7)
payload = json.loads((root / "response.json").read_text())
sid = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
home = pathlib.Path.home()
if family == "claude":
    transcript = home / ".claude/projects/fixture" / (sid + ".jsonl")
    records = [{"message": {"model": "claude-fable-5", "content": [
        {"type": "tool_use", "name": "Read", "input": {"file_path": "design.md"}}]}},
        {"message": {"model": "claude-fable-5", "content": [
        {"type": "tool_result", "content": "grounded", "is_error": False}]}}]
    envelope = {"structured_output": payload, "result": json.dumps(payload),
        "session_id": sid, "modelUsage": {"claude-fable-5": {"inputTokens": 10}},
        "num_turns": 2, "permission_denials": []}
else:
    transcript = home / ".grok/sessions/workspace" / sid / "chat_history.jsonl"
    records = [{"type": "assistant", "content": "reviewing", "model_id": "grok-4.6",
        "tool_calls": [{"id": "x", "name": "read_file", "arguments": "{}"}]},
        {"type": "tool_result", "content": "grounded"},
        {"type": "assistant", "content": "done", "model_id": "grok-4.6", "tool_calls": []}]
    envelope = {"structuredOutput": payload, "text": json.dumps(payload),
        "stopReason": "EndTurn", "sessionId": sid}
transcript.parent.mkdir(parents=True, exist_ok=True)
transcript.write_text("".join(json.dumps(record) + "\n" for record in records))
print(json.dumps(envelope))
'''


def write_json(path, payload):
    path.write_text(json.dumps(payload) + "\n")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Fixture:
    def __init__(self, root, family, verdict="GO-WITH-REVISE"):
        self.root, self.family = root, family
        self.scripts = PLUGIN / "scripts"
        for name in ("state", "bin", "home", "deja"):
            (root / name).mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("MAGI_", "DEJA_", "PUBLICATION_"))}
        self.env.update(HOME=str(root / "home"),
                        PATH=str(root / "bin") + os.pathsep + os.environ["PATH"],
                        DEJA_REVIEW_STATE_ROOT=str(root / "deja"),
                        PUBLICATION_FIXTURE=str(root),
                        MAGI_XFAMILY_CLAUDE_MODEL="claude-fable-5",
                        MAGI_XFAMILY_GROK_MODEL="grok-4.6")
        for name in ("claude", "grok"):
            stub = root / "bin" / name
            stub.write_text(PROVIDER_STUB)
            stub.chmod(0o755)
        self.doc = root / "design.md"
        self.doc.write_text("A grounded synthetic design.\n")
        self.doc_id = hashlib.sha256(os.fsencode(self.doc.resolve())).hexdigest()[:16]
        self.state = root / "state"
        self.prefix = self.state / "round_2_xfamily"
        self.prior = self.state / "round_1_codex_synthesis.json"
        base = {"reviewer": "BALTHASAR", "round": 1, "artifact_id": self.doc_id,
                "artifact_sha": sha(self.doc), "verdict": verdict,
                "schema_grounding_verdict": "PASS", "verify_commands_executed": ["Read design.md"],
                "source_artifacts": [], "dispositions": [], "findings": []}
        for index in (1, 2):
            base["findings"].append({"finding_id": f"BALTHASAR-R1-0{index}",
                "severity": "HIGH" if verdict == "REVISE" else "MED",
                "title": f"Synthetic issue {index}", "location": "design.md:1",
                "rationale": "Synthetic invariant needs clarification.",
                "required_fix": "Clarify the invariant.", "confidence": "high",
                "dup_flag": "new", "missed_angle": "", "subsystem": "fixture",
                "root_cause_id": f"fixture-{index}"})
        source = self.state / "round_1_balthasar.json"
        write_json(source, base)
        prior = copy.deepcopy(base)
        prior["reviewer"] = "SYNTHESIS"
        prior["source_artifacts"] = [{"path": source.name, "sha256": sha(source)}]
        for index, finding in enumerate(prior["findings"], 1):
            source_id = finding["finding_id"]
            finding["finding_id"] = f"SYN-{index}"
            prior["dispositions"].append({"source_ref": f"{source.name}#{source_id}",
                "disposition": "carried", "synthesis_finding_id": finding["finding_id"]})
        write_json(self.prior, prior)
        self.payload = copy.deepcopy(prior)
        self.payload.update(reviewer=family.upper() + "-XFAMILY", round=2, source_artifacts=[])
        self.respond()
        claim = self.run("magi_campaign_guard.py", "claim", self.doc, 1, "fanout", self.state)
        self.require_success(claim)
        claim_id = claim.stdout.strip().split("CLAIM_ID=")[-1]
        self.require_success(self.run("magi_campaign_guard.py", "finish", self.doc, claim_id, "success"))
        protocol = self.run("magi_protocol.py", "sha")
        self.require_success(protocol)
        self.require_success(self.run("magi_deja_context.py", "select", "--target", self.doc,
            "--magi-state", self.state, "--target-path-id", self.doc_id,
            "--target-sha", sha(self.doc), "--protocol-sha", protocol.stdout.strip()))

    @staticmethod
    def require_success(result):
        assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)

    def run(self, script, *args):
        interpreter = "/bin/bash" if script.endswith(".sh") else sys.executable
        return subprocess.run([interpreter, str(self.scripts / script), *map(str, args)],
            env=self.env, cwd=self.root, capture_output=True, text=True, timeout=60)

    def respond(self):
        write_json(self.root / "response.json", self.payload)

    def invoke(self):
        return self.run("magi_xfamily.sh", "--reviewer", self.family, self.doc, 2,
                        self.prior, self.prefix)

    def launches(self):
        ledger = self.root / ".dual-magi" / f"CAMPAIGN.{self.doc_id}.json"
        return json.loads(ledger.read_text())["campaigns"][-1]["launches"]

    def calls(self):
        return (self.root / "calls").read_text().splitlines()


class PublicationTests(unittest.TestCase):
    def assert_failed(self, fixture, result, attempts):
        launches = fixture.launches()
        observed = {"exit": result.returncode,
            "statuses": [row["status"] for row in launches],
            "charges": [row["model_launches"] for row in launches],
            "canonical": [Path(str(fixture.prefix) + suffix).exists()
                          for suffix in (".json", ".meta.json")],
            "failed_artifact": Path(str(fixture.prefix) + ".FAILED.json").exists()}
        self.assertEqual(observed, {"exit": 2, "statuses": ["success"] + ["failed"] * attempts,
            "charges": [3] + [1] * attempts, "canonical": [False, False],
            "failed_artifact": True}, result.stderr)
        self.assertEqual(len(fixture.calls()), attempts)

    def test_invalid_publication_and_retry_exhaustion(self):
        for family in ("claude", "grok"):
            for defect in ("missing_source_ref", "mismatched_id_ref", "unsupported_reviewer",
                           "duplicate_finding_id"):
                with self.subTest(family=family, defect=defect), tempfile.TemporaryDirectory() as raw:
                    fixture = Fixture(Path(raw), family)
                    if defect == "missing_source_ref":
                        fixture.payload["dispositions"][0]["source_ref"] = "round_1_synthesis.json#SYN-1"
                    elif defect == "mismatched_id_ref":
                        fixture.payload["dispositions"][0]["synthesis_finding_id"] = "SYN-2"
                    elif defect == "unsupported_reviewer":
                        fixture.payload["reviewer"] = "UNSUPPORTED-REVIEWER"
                    else:
                        fixture.payload["dispositions"] = []
                        fixture.payload["findings"][1]["finding_id"] = fixture.payload["findings"][0]["finding_id"]
                    fixture.respond()
                    self.assert_failed(fixture, fixture.invoke(), 1)
                    self.assert_failed(fixture, fixture.invoke(), 2)
                    before = fixture.launches()
                    denied = fixture.invoke()
                    self.assertEqual(denied.returncode, 64, denied.stderr)
                    self.assertIn("retry budget exhausted", denied.stderr)
                    self.assertEqual(fixture.launches(), before)
                    self.assertEqual(len(fixture.calls()), 2)

    def test_valid_pairs_and_product_revise_are_success(self):
        for family in ("claude", "grok"):
            for verdict in ("GO-WITH-REVISE", "REVISE"):
                with self.subTest(family=family, verdict=verdict), tempfile.TemporaryDirectory() as raw:
                    fixture = Fixture(Path(raw), family, verdict)
                    result = fixture.invoke()
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual([row["status"] for row in fixture.launches()], ["success", "success"])
                    self.assertEqual([row["model_launches"] for row in fixture.launches()], [3, 1])
                    output = json.loads(Path(str(fixture.prefix) + ".json").read_text())
                    self.assertEqual(output, fixture.payload)
                    self.assertTrue(Path(str(fixture.prefix) + ".meta.json").exists())
                    self.assertFalse(Path(str(fixture.prefix) + ".FAILED.json").exists())
                    gate = fixture.run("magi_plateau_gate.sh", fixture.doc, fixture.prefix,
                        "--orchestrator-family", "codex", "--reviewer-family", family)
                    if verdict == "REVISE":
                        self.assertNotEqual(gate.returncode, 0, gate.stdout)
                        self.assertIn("G8", gate.stderr)
                    else:
                        self.assertEqual(gate.returncode, 0, gate.stderr)

    def test_failed_attempt_can_retry_valid_result(self):
        for family in ("claude", "grok"):
            for failure in ("transport", "invalid"):
                with self.subTest(family=family, failure=failure), tempfile.TemporaryDirectory() as raw:
                    fixture = Fixture(Path(raw), family)
                    valid = copy.deepcopy(fixture.payload)
                    if failure == "transport":
                        fixture.env["PUBLICATION_TRANSPORT_FAILURE"] = "1"
                    else:
                        fixture.payload["dispositions"][0]["source_ref"] = "missing.json#SYN-1"
                        fixture.respond()
                    self.assert_failed(fixture, fixture.invoke(), 1)
                    fixture.env.pop("PUBLICATION_TRANSPORT_FAILURE", None)
                    fixture.payload = valid
                    fixture.respond()
                    result = fixture.invoke()
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual([row["status"] for row in fixture.launches()],
                                     ["success", "failed", "success"])
                    self.assertEqual([row["model_launches"] for row in fixture.launches()], [3, 1, 1])
                    self.assertEqual(len(fixture.calls()), 2)
                    self.assertEqual(json.loads(Path(str(fixture.prefix) + ".json").read_text()), valid)
                    self.assertTrue(Path(str(fixture.prefix) + ".meta.json").exists())
                    self.assertFalse(Path(str(fixture.prefix) + ".FAILED.json").exists())

    def test_transport_retry_ceiling_unchanged(self):
        for family in ("claude", "grok"):
            with self.subTest(family=family), tempfile.TemporaryDirectory() as raw:
                fixture = Fixture(Path(raw), family)
                fixture.env["PUBLICATION_TRANSPORT_FAILURE"] = "1"
                self.assert_failed(fixture, fixture.invoke(), 1)
                self.assert_failed(fixture, fixture.invoke(), 2)
                before = fixture.launches()
                denied = fixture.invoke()
                self.assertEqual(denied.returncode, 64, denied.stderr)
                self.assertIn("retry budget exhausted", denied.stderr)
                self.assertEqual(fixture.launches(), before)
                self.assertEqual(len(fixture.calls()), 2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin", type=Path, default=PLUGIN)
    args, remaining = parser.parse_known_args()
    PLUGIN = args.plugin.resolve()
    unittest.main(argv=[sys.argv[0], *remaining], verbosity=2)
