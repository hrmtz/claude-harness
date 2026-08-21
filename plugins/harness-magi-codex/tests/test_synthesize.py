#!/usr/bin/env python3
"""Regression tests for lossless Magi synthesis."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "magi_synthesize.py"
GUARD = ROOT / "scripts" / "magi_campaign_guard.py"
PROTOCOL = ROOT / "scripts" / "magi_protocol.py"


def finding(reviewer: str, artifact_id: str, artifact_sha: str, severity: str) -> dict:
    return {
        "reviewer": reviewer,
        "round": 1,
        "artifact_id": artifact_id,
        "artifact_sha": artifact_sha,
        "verdict": "REVISE" if severity == "HIGH" else "GO-WITH-REVISE",
        "schema_grounding_verdict": "PASS",
        "verify_commands_executed": [f"read {reviewer}"],
        "source_artifacts": [],
        "dispositions": [],
        "findings": [
            {
                "finding_id": "SHARED-ID",
                "severity": severity,
                "title": f"{reviewer} finding",
                "location": "test",
                "rationale": "synthetic fixture",
                "required_fix": "fix fixture",
                "confidence": "high",
                "dup_flag": "new",
                "missed_angle": reviewer,
                "subsystem": "synthesis-fixture",
                "root_cause_id": f"fixture-{reviewer.lower()}",
                "affected_invariant": "lossless synthesis",
                "changes_design_invariant": False,
                "relation_to_prior": "none",
            }
        ],
    }


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        state = Path(temporary)
        doc = state / "artifact.patch"
        doc.write_text("diff fixture\n", encoding="utf-8")
        artifact_id = hashlib.sha256(str(doc.resolve()).encode()).hexdigest()[:16]
        artifact_sha = hashlib.sha256(doc.read_bytes()).hexdigest()

        for reviewer, severity in (
            ("MELCHIOR", "HIGH"),
            ("BALTHASAR", "MED"),
            ("CASPAR", "LOW"),
        ):
            path = state / f"round_1_{reviewer.lower()}.json"
            path.write_text(
                json.dumps(finding(reviewer, artifact_id, artifact_sha, severity)),
                encoding="utf-8",
            )

        output = state / "round_1_magi_synthesis.json"
        subprocess.run(
            [
                str(SCRIPT),
                str(doc),
                "1",
                str(state),
                str(output),
                "--persona-set",
                "magi",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert payload["reviewer"] == "SYNTHESIS"
        assert payload["verdict"] == "REVISE"
        assert len(payload["source_artifacts"]) == 3
        assert len(payload["findings"]) == 3
        assert len({item["finding_id"] for item in payload["findings"]}) == 3
        assert len(payload["dispositions"]) == 3
        assert all(item["disposition"] == "carried" for item in payload["dispositions"])

        first = output.read_bytes()
        subprocess.run(
            [
                str(SCRIPT),
                str(doc),
                "1",
                str(state),
                str(output),
                "--persona-set",
                "magi",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        assert output.read_bytes() == first, "synthesis output is not deterministic"

        original_sources = {}
        for reviewer in ("melchior", "balthasar", "caspar"):
            source = state / f"round_1_{reviewer}.json"
            original_sources[reviewer] = source.read_bytes()
            source_payload = json.loads(original_sources[reviewer])
            source_payload["verify_commands_executed"] = [
                f"{reviewer}-command-{index}" for index in range(256)
            ]
            source.write_text(json.dumps(source_payload), encoding="utf-8")
        output.unlink()
        subprocess.run(
            [
                str(SCRIPT), str(doc), "1", str(state), str(output),
                "--persona-set", "magi",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        bounded_synthesis = json.loads(output.read_text(encoding="utf-8"))
        assert len(bounded_synthesis["verify_commands_executed"]) == 768
        assert bounded_synthesis["schema_grounding_verdict"] == "PASS"
        assert bounded_synthesis["verify_commands_executed"][:2] == [
            "balthasar-command-0",
            "balthasar-command-1",
        ]
        for reviewer, raw in original_sources.items():
            (state / f"round_1_{reviewer}.json").write_bytes(raw)
        output.unlink()
        subprocess.run(
            [
                str(SCRIPT), str(doc), "1", str(state), str(output),
                "--persona-set", "magi",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        balthasar = state / "round_1_balthasar.json"
        valid_balthasar = balthasar.read_bytes()
        bounded_payload = json.loads(valid_balthasar)
        bounded_payload["verify_commands_executed"] = [
            f"rg -n bounded-command-{index}" for index in range(257)
        ]
        balthasar.write_text(json.dumps(bounded_payload), encoding="utf-8")
        too_many_commands = subprocess.run(
            [
                str(SCRIPT), str(doc), "1", str(state), str(output),
                "--persona-set", "magi",
            ],
            capture_output=True,
            text=True,
        )
        assert too_many_commands.returncode == 1
        assert "source verification command count exceeds 256" in too_many_commands.stderr
        balthasar.write_bytes(valid_balthasar)

        payload_text = valid_balthasar.decode()
        balthasar.write_text(
            payload_text.replace('"findings": [', '"findings": [], "findings": [', 1),
            encoding="utf-8",
        )
        duplicate_top = subprocess.run(
            [
                str(SCRIPT), str(doc), "1", str(state), str(output),
                "--persona-set", "magi",
            ],
            capture_output=True,
            text=True,
        )
        assert duplicate_top.returncode == 1
        assert "duplicate JSON member: findings" in duplicate_top.stderr

        balthasar.write_text(
            payload_text.replace(
                '"title": "BALTHASAR finding"',
                '"title": "discarded", "title": "BALTHASAR finding"',
                1,
            ),
            encoding="utf-8",
        )
        duplicate_nested = subprocess.run(
            [
                str(SCRIPT), str(doc), "1", str(state), str(output),
                "--persona-set", "magi",
            ],
            capture_output=True,
            text=True,
        )
        assert duplicate_nested.returncode == 1
        assert "duplicate JSON member: title" in duplicate_nested.stderr

        balthasar.unlink()
        os.mkfifo(balthasar)
        fifo = subprocess.run(
            [
                str(SCRIPT), str(doc), "1", str(state), str(output),
                "--persona-set", "magi",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        assert fifo.returncode == 1
        assert "source artifact must be a regular file" in fifo.stderr
        balthasar.unlink()
        balthasar.write_bytes(valid_balthasar)

        lock_dir = state / ".dual-magi"
        lock_dir.mkdir(exist_ok=True)
        lock_path = lock_dir / f".review.{artifact_id}.lock"
        with lock_path.open("w") as lock_handle:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = subprocess.run(
                [
                    str(SCRIPT), str(doc), "1", str(state), str(output),
                    "--persona-set", "magi",
                ],
                capture_output=True,
                text=True,
            )
        assert locked.returncode == 1
        assert "document review lock is held" in locked.stderr

        (state / "round_1_balthasar.json").unlink()
        missing = subprocess.run(
            [
                str(SCRIPT),
                str(doc),
                "1",
                str(state),
                str(output),
                "--persona-set",
                "magi",
            ],
            capture_output=True,
            text=True,
        )
        assert missing.returncode == 1
        assert "source set mismatch" in missing.stderr

        oversized_path = state / "round_1_balthasar.json"
        oversized_path.write_bytes(b" " * (10 * 1024 * 1024 + 1))
        oversized = subprocess.run(
            [
                str(SCRIPT),
                str(doc),
                "1",
                str(state),
                str(output),
                "--persona-set",
                "magi",
            ],
            capture_output=True,
            text=True,
        )
        assert oversized.returncode == 1
        assert "source exceeds" in oversized.stderr

        oversized_path.write_text(
            json.dumps(finding("BALTHASAR", artifact_id, artifact_sha, "MED")),
            encoding="utf-8",
        )
        doc.write_text("changed after review\n", encoding="utf-8")
        stale = subprocess.run(
            [
                str(SCRIPT),
                str(doc),
                "1",
                str(state),
                str(output),
                "--persona-set",
                "magi",
            ],
            capture_output=True,
            text=True,
        )
        assert stale.returncode == 1
        assert "artifact_sha does not match" in stale.stderr

        # The output basename is fixed review provenance: contradictory names are refused.
        contradictory = subprocess.run(
            [
                str(SCRIPT),
                str(doc),
                "1",
                str(state),
                str(state / "round_1_codex.json"),
                "--persona-set",
                "magi",
            ],
            capture_output=True,
            text=True,
        )
        assert contradictory.returncode == 1
        assert "output basename must be round_1_magi_synthesis.json" in contradictory.stderr
        assert not (state / "round_1_codex.json").exists()

        # Cross-family synthesis: reviewers currently emit "<provider>-xfamily";
        # retain the legacy "<provider>-cross-family" spelling too.
        artifact_sha = hashlib.sha256(doc.read_bytes()).hexdigest()
        xfamily_source = finding("CLAUDE-XFAMILY", artifact_id, artifact_sha, "MED")
        xfamily_source["round"] = 2
        (state / "round_2_xfamily.json").write_text(
            json.dumps(xfamily_source), encoding="utf-8"
        )
        protocol_sha = subprocess.run(
            ["python3", str(PROTOCOL), "sha"], check=True, capture_output=True, text=True
        ).stdout.strip()
        session_id = "fixture-session"
        transcript = state / f"{session_id}.jsonl"
        transcript.write_text(
            '{"message":{"model":"claude-fable-5","content":[]}}\n',
            encoding="utf-8",
        )
        (state / "round_2_xfamily.meta.json").write_text(
            json.dumps(
                {
                    "reviewer_family": "claude",
                    "round": 2,
                    "artifact_sha": artifact_sha,
                    "protocol_sha": protocol_sha,
                    "output_sha": hashlib.sha256(
                        (state / "round_2_xfamily.json").read_bytes()
                    ).hexdigest(),
                    "session_id": session_id,
                    "model_id": "claude-fable-5",
                    "requested_model": "claude-fable-5",
                    "transcript_path": str(transcript),
                    "transcript_sha": hashlib.sha256(transcript.read_bytes()).hexdigest(),
                }
            ),
            encoding="utf-8",
        )
        for review_round, phase in ((1, "fanout"), (2, "xfamily")):
            claimed = subprocess.run(
                [
                    "python3", str(GUARD), "claim", str(doc), str(review_round), phase,
                    str(state), "--owner-pid", str(os.getpid()), "--adapter-kind", phase,
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            claim_id = claimed.rsplit("CLAIM_ID=", 1)[1].strip()
            subprocess.run(
                ["python3", str(GUARD), "finish", str(doc), claim_id, "success"],
                check=True,
                capture_output=True,
                text=True,
            )
        xfamily_output = state / "round_2_xfamily_synthesis.json"
        subprocess.run(
            [
                str(SCRIPT),
                str(doc),
                "2",
                str(state),
                str(xfamily_output),
                "--persona-set",
                "xfamily",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(xfamily_output.read_text(encoding="utf-8"))
        assert payload["reviewer"] == "SYNTHESIS"
        assert [item["path"] for item in payload["source_artifacts"]] == [
            "round_2_xfamily.json"
        ]

        xfamily_source["reviewer"] = "CLAUDE-CROSS-FAMILY"
        (state / "round_2_xfamily.json").write_text(
            json.dumps(xfamily_source), encoding="utf-8"
        )
        meta_path = state / "round_2_xfamily.meta.json"
        meta_payload = json.loads(meta_path.read_text(encoding="utf-8"))
        meta_payload["output_sha"] = hashlib.sha256(
            (state / "round_2_xfamily.json").read_bytes()
        ).hexdigest()
        meta_path.write_text(json.dumps(meta_payload), encoding="utf-8")
        xfamily_output.unlink()
        subprocess.run(
            [
                str(SCRIPT),
                str(doc),
                "2",
                str(state),
                str(xfamily_output),
                "--persona-set",
                "xfamily",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        # The durable metadata pins the provider family. A valid findings/meta pair must not
        # relabel a Claude review as Grok (or vice versa), including the provider's normal
        # "-cross-family" or "-xfamily" reviewer label.
        for wrong_reviewer in ("GROK-XFAMILY", "GROK-CROSS-FAMILY"):
            xfamily_source["reviewer"] = wrong_reviewer
            (state / "round_2_xfamily.json").write_text(
                json.dumps(xfamily_source), encoding="utf-8"
            )
            meta_payload["output_sha"] = hashlib.sha256(
                (state / "round_2_xfamily.json").read_bytes()
            ).hexdigest()
            meta_path.write_text(json.dumps(meta_payload), encoding="utf-8")
            if xfamily_output.exists():
                xfamily_output.unlink()
            wrong_family = subprocess.run(
                [
                    str(SCRIPT),
                    str(doc),
                    "2",
                    str(state),
                    str(xfamily_output),
                    "--persona-set",
                    "xfamily",
                ],
                capture_output=True,
                text=True,
            )
            assert wrong_family.returncode == 1
            assert "source has wrong reviewer identity" in wrong_family.stderr

        mislabeled = subprocess.run(
            [
                str(SCRIPT),
                str(doc),
                "2",
                str(state),
                str(state / "round_2_codex.json"),
                "--persona-set",
                "xfamily",
            ],
            capture_output=True,
            text=True,
        )
        assert mislabeled.returncode == 1
        assert "output basename must be round_2_xfamily_synthesis.json" in mislabeled.stderr
        assert not (state / "round_2_codex.json").exists()

    print("test_synthesize: PASS")


if __name__ == "__main__":
    main()
