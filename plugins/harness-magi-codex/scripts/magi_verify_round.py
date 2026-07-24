#!/usr/bin/env python3
"""Write-free G1-G6/G9 verification shared by convergence evaluation.

G7/G8 and plateau marker ownership intentionally remain in magi_plateau_gate.sh.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
from pathlib import Path
from typing import Any


VALID_VERDICTS = {"GO", "GO-WITH-REVISE", "REVISE", "REJECT"}
MAGI_GATE_OWNERSHIP = ("G1", "G2", "G3", "G4", "G5", "G6", "G9")
FAMILY_MARKERS = {
    ("codex", "claude"): ("claude",),
    ("codex", "grok"): ("grok",),
    ("claude", "codex"): ("gpt", "o1", "o3", "codex"),
    ("claude", "grok"): ("grok",),
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _models_and_tools(path: Path, reviewer_family: str) -> tuple[set[str], int]:
    models: set[str] = set()
    tool_uses = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if reviewer_family == "claude":
                message = record.get("message") or {}
                model = message.get("model")
                content = message.get("content")
                if isinstance(content, list):
                    tool_uses += sum(
                        1
                        for block in content
                        if isinstance(block, dict) and block.get("type") == "tool_use"
                    )
            else:
                model = record.get("model_id") if record.get("type") == "assistant" else None
                calls = record.get("tool_calls")
                if isinstance(calls, list):
                    tool_uses += len(calls)
            if model:
                models.add(str(model))
    return models, tool_uses


def verify_round(
    doc: Path,
    out_prefix: Path,
    orchestrator_family: str,
    reviewer_family: str | None,
    *,
    expected_artifact_sha: str | None = None,
) -> dict[str, Any]:
    """Return parsed artifacts and failures without changing any file."""

    doc = doc.resolve()
    findings_path = Path(f"{out_prefix}.json")
    meta_path = Path(f"{out_prefix}.meta.json")
    failures: list[str] = []

    def fail(gate: str, message: str) -> None:
        failures.append(f"{gate}: {message}")

    findings: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None
    findings_sha: str | None = None
    if not findings_path.exists():
        fail("G1", f"no cross-family findings at {findings_path}")
    elif not meta_path.exists():
        fail("G1", f"no provenance meta at {meta_path}")
    else:
        try:
            findings_bytes = findings_path.read_bytes()
            meta_bytes = meta_path.read_bytes()
            findings_raw = json.loads(findings_bytes)
            meta_raw = json.loads(meta_bytes)
            findings_sha = hashlib.sha256(findings_bytes).hexdigest()
        except (OSError, json.JSONDecodeError) as exc:
            fail("G1", f"unreadable round artifacts: {exc}")
        else:
            if not isinstance(findings_raw, dict) or not isinstance(meta_raw, dict):
                fail("G1", "round artifacts are not JSON objects")
            else:
                findings, meta = findings_raw, meta_raw
                if findings.get("verdict") not in VALID_VERDICTS:
                    fail(
                        "G1",
                        f"verdict {findings.get('verdict')!r} not in {sorted(VALID_VERDICTS)}",
                    )
                if not isinstance(findings.get("findings"), list):
                    fail("G1", "findings is not an array")

    if failures or findings is None or meta is None:
        return {
            "findings": findings,
            "meta": meta,
            "failures": failures,
            "transcript_path": None,
        }

    if reviewer_family is None:
        candidate_family = meta.get("reviewer_family")
        reviewer_family = (
            candidate_family if candidate_family in {"claude", "grok"} else "unsupported"
        )
    markers = FAMILY_MARKERS.get((orchestrator_family, reviewer_family))
    model_id = str(meta.get("model_id") or "")
    keys = meta.get("model_usage_keys") or []
    recorded_family = meta.get("reviewer_family") or "claude"

    def cross_family(name: str) -> bool:
        return bool(markers) and any(marker in name.lower() for marker in markers)

    if not markers:
        fail(
            "G2",
            f"unsupported family route {orchestrator_family!r}->{reviewer_family!r}",
        )
    elif recorded_family != reviewer_family:
        fail(
            "G2",
            f"meta reviewer_family {recorded_family!r} != requested {reviewer_family!r}",
        )
    elif not model_id:
        fail("G2", "meta records no model_id")
    elif not cross_family(model_id):
        fail(
            "G2",
            f"model_id {model_id!r} is not cross-family for orchestrator "
            f"{orchestrator_family!r}",
        )
    elif not isinstance(keys, list) or not keys:
        fail("G2", "meta records no model_usage_keys")
    elif not all(isinstance(key, str) and cross_family(key) for key in keys):
        fail("G2", f"modelUsage keys {keys} are not all cross-family")

    actual_sha = expected_artifact_sha or _sha(doc)
    if meta.get("artifact_sha") != actual_sha:
        fail(
            "G3",
            f"artifact_sha mismatch: round reviewed {str(meta.get('artifact_sha'))[:16]}…, "
            f"doc is now {actual_sha[:16]}… (stale round, or doc edited after review)",
        )

    if meta.get("output_sha") != findings_sha:
        fail("G4", "output_sha mismatch: findings file changed since the adapter wrote it")

    turns = meta.get("num_turns")
    commands = findings.get("verify_commands_executed")
    if type(turns) is not int:
        fail("G5", f"num_turns={turns!r} is not an integer")
    elif turns < 1:
        fail("G5", f"num_turns={turns}")
    elif turns <= 1 and isinstance(commands, list) and commands:
        fail(
            "G5",
            f"self-contradiction: num_turns={turns} but {len(commands)} commands reported",
        )
    if not isinstance(commands, list):
        fail("G9", "verify_commands_executed is not an array")
        commands = []

    sid = meta.get("session_id")
    transcripts: list[str] = []
    transcript_path: Path | None = None
    transcript_models: set[str] = set()
    tool_uses = 0
    if not sid or not isinstance(sid, str):
        fail("G6", f"session_id {sid!r} missing")
    else:
        pattern = (
            f"~/.claude/projects/*/{glob.escape(sid)}.jsonl"
            if reviewer_family == "claude"
            else f"~/.grok/sessions/*/{glob.escape(sid)}/chat_history.jsonl"
        )
        transcripts = glob.glob(os.path.expanduser(pattern))
        if not transcripts:
            fail(
                "G6",
                f"session_id {sid!r} does not resolve to a {reviewer_family} transcript",
            )
        elif len(transcripts) != 1:
            fail("G6", f"session_id {sid!r} resolves to {len(transcripts)} transcripts")
        else:
            transcript_path = Path(transcripts[0])
            recorded_path = meta.get("transcript_path")
            if recorded_path and os.path.realpath(recorded_path) != os.path.realpath(
                transcript_path
            ):
                fail(
                    "G6",
                    "meta transcript_path does not match provider transcript resolution",
                )
            recorded_sha = meta.get("transcript_sha")
            actual_transcript_sha = _sha(transcript_path)
            if reviewer_family == "grok" and not recorded_sha:
                fail("G6", "Grok meta records no transcript_sha")
            elif recorded_sha and recorded_sha != actual_transcript_sha:
                fail("G6", "transcript_sha mismatch: transcript changed after adapter completion")
            try:
                transcript_models, tool_uses = _models_and_tools(
                    transcript_path, reviewer_family
                )
            except OSError as exc:
                fail(
                    "G6",
                    f"cannot read {reviewer_family} transcript model provenance: {exc}",
                )
            else:
                if not transcript_models:
                    fail(
                        "G6",
                        f"{reviewer_family} transcript records no assistant model",
                    )
                elif not any(
                    model_id in served or served in model_id
                    for served in transcript_models
                ):
                    fail(
                        "G6",
                        f"meta model_id {model_id!r} inconsistent with "
                        f"{reviewer_family} transcript models {sorted(transcript_models)}",
                    )
                requested = meta.get("requested_model")
                if not requested:
                    fail("G6", "meta records no requested_model")
                elif not any(
                    served == requested or str(requested) in served
                    for served in transcript_models
                ):
                    fail(
                        "G6",
                        f"requested model {requested!r} did not run: transcript ran "
                        f"{sorted(transcript_models)} (silent same-family downgrade)",
                    )

    grounding = findings.get("schema_grounding_verdict")
    if grounding == "FAIL":
        fail("G9", "reviewer self-reported schema_grounding_verdict=FAIL")
    elif not commands:
        fail(
            "G9",
            f"grounding={grounding} but verify_commands_executed is empty "
            "(a grounded round must have run commands)",
        )
    elif transcript_path is not None and tool_uses == 0:
        fail(
            "G9",
            f"{len(commands)} commands reported but the transcript shows no tool use "
            "(fabricated verify_commands_executed)",
        )

    return {
        "findings": findings,
        "meta": meta,
        "failures": failures,
        "transcript_path": str(transcript_path) if transcript_path else None,
        "findings_sha": findings_sha,
    }
