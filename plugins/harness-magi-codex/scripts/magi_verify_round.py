#!/usr/bin/env python3
"""Write-free G1-G6/G9 verification shared by convergence evaluation.

G7/G8 and plateau marker ownership intentionally remain in magi_plateau_gate.sh.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

import jsonschema

from magi_validate_findings import validate as validate_findings
from magi_protocol import protocol_sha, strict_json_loads
from magi_campaign_guard import load_ledger


MAGI_GATE_OWNERSHIP = ("G1", "G2", "G3", "G4", "G5", "G6", "G9")
FAMILY_MARKERS = {
    ("codex", "claude"): ("claude",),
    ("codex", "grok"): ("grok",),
    ("claude", "codex"): ("gpt", "o1", "o3", "codex"),
    ("claude", "grok"): ("grok",),
}


MAX_ROUND_JSON_BYTES = 10 * 1024 * 1024
MAX_TRANSCRIPT_BYTES = 256 * 1024 * 1024
MAX_TRANSCRIPT_LINE_BYTES = 1024 * 1024


def _regular_size(path: Path, label: str, maximum: int | None = None) -> int:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"{label} must be a regular file: {path}")
    if maximum is not None and info.st_size > maximum:
        raise ValueError(f"{label} exceeds {maximum}-byte limit: {path}")
    return info.st_size


def _sha(path: Path, maximum: int | None = None) -> str:
    _regular_size(path, "hashed artifact", maximum)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bounded_jsonl(path: Path):
    with path.open("rb") as handle:
        line_number = 0
        while True:
            raw = handle.readline(MAX_TRANSCRIPT_LINE_BYTES + 1)
            if not raw:
                return
            line_number += 1
            if len(raw) > MAX_TRANSCRIPT_LINE_BYTES:
                raise ValueError(
                    f"transcript line exceeds {MAX_TRANSCRIPT_LINE_BYTES}-byte limit "
                    f"at {path}:{line_number}"
                )
            try:
                yield line_number, raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(
                    f"transcript is not UTF-8 at {path}:{line_number}"
                ) from exc


def _models_and_tools(path: Path, reviewer_family: str) -> tuple[set[str], int]:
    _regular_size(path, "transcript", MAX_TRANSCRIPT_BYTES)
    models: set[str] = set()
    tool_uses = 0
    for line_number, line in _bounded_jsonl(path):
        if not line.strip():
            continue
        try:
            record = strict_json_loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"malformed transcript JSON at {path}:{line_number}: {exc.msg}"
            ) from exc
        if not isinstance(record, dict):
            raise ValueError(
                f"transcript record must be an object at {path}:{line_number}"
            )
        if reviewer_family == "claude":
            message = record.get("message") or {}
            if not isinstance(message, dict):
                raise ValueError(
                    f"Claude transcript message must be an object at {path}:{line_number}"
                )
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
    require_successful_claim: bool = False,
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
    expected_protocol_sha: str | None = None
    if not findings_path.exists():
        fail("G1", f"no cross-family findings at {findings_path}")
    elif not meta_path.exists():
        fail("G1", f"no provenance meta at {meta_path}")
    else:
        try:
            _regular_size(findings_path, "findings", MAX_ROUND_JSON_BYTES)
            _regular_size(meta_path, "metadata", MAX_ROUND_JSON_BYTES)
            findings_bytes = findings_path.read_bytes()
            meta_bytes = meta_path.read_bytes()
            findings_raw = strict_json_loads(findings_bytes)
            meta_raw = strict_json_loads(meta_bytes)
            findings_sha = hashlib.sha256(findings_bytes).hexdigest()
        except (OSError, ValueError, RecursionError, json.JSONDecodeError) as exc:
            fail("G1", f"unreadable round artifacts: {exc}")
        else:
            if not isinstance(findings_raw, dict) or not isinstance(meta_raw, dict):
                fail("G1", "round artifacts are not JSON objects")
            else:
                findings, meta = findings_raw, meta_raw
                schema_path = (
                    Path(__file__).resolve().parent.parent
                    / "schemas"
                    / "finding.schema.json"
                )
                try:
                    schema = strict_json_loads(schema_path.read_bytes())
                    validate_findings(
                        findings,
                        schema,
                        doc=doc,
                        same_doc_only=expected_artifact_sha is not None,
                    )
                except (
                    OSError,
                    json.JSONDecodeError,
                    jsonschema.ValidationError,
                    ValueError,
                ) as exc:
                    fail("G1", f"findings schema/semantic validation failed: {exc}")

    if failures or findings is None or meta is None:
        return {
            "findings": findings,
            "meta": meta,
            "failures": failures,
            "transcript_path": None,
            "protocol_sha": expected_protocol_sha,
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
    g3_failures = []
    if meta.get("artifact_sha") != actual_sha:
        g3_failures.append(
            f"artifact_sha mismatch: round reviewed {str(meta.get('artifact_sha'))[:16]}…, "
            f"doc is now {actual_sha[:16]}… (stale round, or doc edited after review)",
        )
    try:
        expected_protocol_sha = protocol_sha()
    except (OSError, RuntimeError, ValueError) as exc:
        g3_failures.append(f"current protocol manifest is invalid: {exc}")
    else:
        if meta.get("protocol_sha") != expected_protocol_sha:
            g3_failures.append(
                "protocol_sha mismatch: cross-family round used a different runtime generation",
            )
    if g3_failures:
        fail("G3", "; ".join(g3_failures))

    if meta.get("output_sha") != findings_sha:
        fail("G4", "output_sha mismatch: findings file changed since the adapter wrote it")

    if require_successful_claim:
        try:
            ledger = load_ledger(doc, create=False)
            review_round = findings.get("round")
            state_dir = out_prefix.parent.resolve()
            authorized = [
                launch
                for campaign in ledger["campaigns"]
                if isinstance(campaign, dict)
                for launch in campaign.get("launches", [])
                if isinstance(launch, dict)
                and launch.get("status") == "success"
                and launch.get("phase") == "xfamily"
                and launch.get("round") == review_round
                and launch.get("artifact_sha") == actual_sha
                and launch.get("protocol_sha") == expected_protocol_sha
                and Path(str(launch.get("state_dir", ""))).resolve() == state_dir
            ]
            if len(authorized) != 1:
                fail(
                    "G6",
                    "canonical cross-family pair lacks exactly one matching successful ledger claim",
                )
        except (OSError, RuntimeError, ValueError) as exc:
            fail("G6", f"cannot authorize cross-family pair from campaign ledger: {exc}")

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
            actual_transcript_sha = _sha(transcript_path, MAX_TRANSCRIPT_BYTES)
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
        "protocol_sha": expected_protocol_sha,
    }
