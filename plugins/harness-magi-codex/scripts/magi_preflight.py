#!/usr/bin/env python3
"""Deterministically synthesize one Magi pre-flight round.

The evaluator is pure and report-only: it does not launch reviewers, mutate campaign state,
create a plateau marker, authorize shipping, or permit a second review round.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema


PERSONAS = ("MELCHIOR", "BALTHASAR", "CASPAR")
LANES = {
    "MELCHIOR": "architecture, silent failures, hidden per-unit costs",
    "BALTHASAR": "recovery, monitoring, resource peaks, concurrent operations",
    "CASPAR": "alternatives, ROI, scope cuts, pre-commit cut lines",
}
SPECIAL_IMPACTS = ("security", "data-loss", "irreversibility")
MAX_BRIEF_BYTES = 1024 * 1024
MAX_REVIEW_BYTES = 4 * 1024 * 1024


class UnsafeInput(RuntimeError):
    """Evidence is missing, stale, malformed, symlinked, or unstable."""


class UsageError(ValueError):
    """The command line is invalid."""


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True)
class StableFile:
    path: Path
    raw: bytes
    sha256: str
    identity: FileIdentity


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise UsageError(message)


def file_identity(metadata: os.stat_result) -> FileIdentity:
    return FileIdentity(
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def canonical_regular_path(raw_path: Path) -> Path:
    absolute = Path(os.path.abspath(os.path.expanduser(str(raw_path))))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            if current.is_symlink():
                raise UnsafeInput(f"symlinked input is forbidden: {raw_path}")
        except OSError as exc:
            raise UnsafeInput(f"cannot inspect input path {raw_path}: {exc}") from exc
    try:
        canonical = absolute.resolve(strict=True)
    except OSError as exc:
        raise UnsafeInput(f"cannot resolve input {raw_path}: {exc}") from exc
    if canonical != absolute:
        raise UnsafeInput(f"input path is not canonical: {raw_path}")
    return canonical


def stable_read(raw_path: Path, *, limit: int) -> StableFile:
    path = canonical_regular_path(raw_path)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise UnsafeInput(f"input is not a bounded regular file: {path}")
            chunks: list[bytes] = []
            remaining = limit + 1
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        current = os.stat(path, follow_symlinks=False)
    except UnsafeInput:
        raise
    except OSError as exc:
        raise UnsafeInput(f"cannot read input {path}: {exc}") from exc
    if len(raw) > limit:
        raise UnsafeInput(f"input exceeds {limit} bytes: {path}")
    before_identity = file_identity(before)
    if before_identity != file_identity(after) or before_identity != file_identity(current):
        raise UnsafeInput(f"input changed while read: {path}")
    return StableFile(
        path=path,
        raw=raw,
        sha256=hashlib.sha256(raw).hexdigest(),
        identity=before_identity,
    )


def review_prompt(
    brief: StableFile, persona: str, *, identity: dict[str, str] | None = None
) -> bytes:
    """Reconstruct the exact immutable prompt emitted by the structural runner."""
    if persona not in PERSONAS:
        raise UnsafeInput(f"unknown reviewer persona: {persona}")
    identity = identity or brief_identity(brief)
    identity_json = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    prefix = (
        f"REVIEWER: {persona}\n"
        f"LANE: {LANES[persona]}\n"
        "ROUND: 1\n"
        f"BRIEF_CANONICAL_PATH: {identity['canonical_path']}\n"
        f"BRIEF_ARTIFACT_ID: {identity['artifact_id']}\n"
        f"BRIEF_SHA256: {identity['sha256']}\n"
        "Return exactly one JSON object conforming to the supplied schema.\n"
        "Use only the assigned reviewer name and round 1. Do not request another round.\n"
        "Every evidence digest covers exact cited brief-line bytes including endings.\n"
        "Sibling staged files are hidden by a private mount/PID namespace. "
        "Read-only; do not read credential files.\n"
        f"BRIEF_IDENTITY_JSON: {identity_json}\n"
        "BRIEF:\n---\n"
    ).encode("utf-8")
    return prefix + brief.raw + b"\n---\n"


def assert_unchanged(stable: StableFile) -> None:
    try:
        current = os.stat(stable.path, follow_symlinks=False)
    except OSError as exc:
        raise UnsafeInput(f"input disappeared after read: {stable.path}: {exc}") from exc
    if file_identity(current) != stable.identity or not stat.S_ISREG(current.st_mode):
        raise UnsafeInput(f"input changed during evaluation: {stable.path}")


def no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise UnsafeInput(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def parse_object(stable: StableFile) -> dict[str, Any]:
    try:
        payload = json.loads(stable.raw, object_pairs_hook=no_duplicate_object)
    except UnicodeDecodeError as exc:
        raise UnsafeInput(f"JSON is not UTF-8: {stable.path}") from exc
    except json.JSONDecodeError as exc:
        raise UnsafeInput(f"invalid JSON in {stable.path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise UnsafeInput(f"JSON input is not an object: {stable.path}")
    return payload


def load_schema(name: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parent.parent / "schemas" / name
    try:
        payload = json.loads(path.read_bytes(), object_pairs_hook=no_duplicate_object)
    except (OSError, json.JSONDecodeError, UnsafeInput) as exc:
        raise RuntimeError(f"cannot load bundled schema {name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"bundled schema is not an object: {name}")
    return payload


def validate_schema(payload: dict[str, Any], schema: dict[str, Any], label: str) -> None:
    try:
        jsonschema.validate(payload, schema)
    except jsonschema.ValidationError as exc:
        raise UnsafeInput(f"{label} schema mismatch: {exc.message}") from exc


def line_slice_sha(lines: list[bytes], start_line: int, end_line: int) -> str:
    if end_line < start_line:
        raise UnsafeInput(
            f"brief evidence end_line {end_line} precedes start_line {start_line}"
        )
    if start_line < 1 or end_line > len(lines):
        raise UnsafeInput(
            f"brief evidence lines {start_line}-{end_line} exceed 1-{len(lines)}"
        )
    return hashlib.sha256(b"".join(lines[start_line - 1 : end_line])).hexdigest()


def brief_identity(brief: StableFile) -> dict[str, str]:
    return {
        "canonical_path": str(brief.path),
        "artifact_id": hashlib.sha256(str(brief.path).encode()).hexdigest()[:16],
        "sha256": brief.sha256,
    }


def validate_review(
    payload: dict[str, Any],
    *,
    source: StableFile,
    brief: StableFile,
    brief_lines: list[bytes],
    review_schema: dict[str, Any],
) -> None:
    validate_schema(payload, review_schema, str(source.path))
    expected_brief = brief_identity(brief)
    if payload["brief"] != expected_brief:
        raise UnsafeInput(f"review is stale or bound to another brief: {source.path}")

    finding_ids: set[str] = set()
    root_ids: set[str] = set()
    grounded_veto_actions: set[str] = set()
    recommended_actions: set[str] = set()
    for finding in payload["findings"]:
        finding_id = finding["finding_id"]
        root_id = finding["root_cause_id"]
        if finding_id in finding_ids:
            raise UnsafeInput(f"duplicate finding_id in {source.path}: {finding_id}")
        if root_id in root_ids:
            raise UnsafeInput(f"duplicate root_cause_id in {source.path}: {root_id}")
        finding_ids.add(finding_id)
        root_ids.add(root_id)
        recommended_actions.add(finding["recommended_decision"])
        for evidence in finding["evidence"]:
            actual = line_slice_sha(
                brief_lines, evidence["start_line"], evidence["end_line"]
            )
            if actual != evidence["sha256"]:
                raise UnsafeInput(
                    f"brief evidence digest mismatch in {source.path}#{finding_id}"
                )
        if finding["evidence"] and (
            finding["severity"] == "CRITICAL"
            or set(finding["impact"]) & set(SPECIAL_IMPACTS)
        ):
            grounded_veto_actions.add(finding["recommended_decision"])

    verdict = payload["verdict"]
    if verdict in {"PIVOT", "ABORT"} and verdict not in recommended_actions:
        raise UnsafeInput(
            f"{source.path}: {verdict} verdict has no matching recommended finding"
        )
    if verdict == "PROCEED" and grounded_veto_actions:
        raise UnsafeInput(f"{source.path}: PROCEED contradicts a grounded veto finding")


def veto_reason(finding: dict[str, Any]) -> str | None:
    if not finding["evidence"]:
        return None
    if finding["severity"] == "CRITICAL":
        return "critical"
    impacts = set(finding["impact"])
    return next((impact for impact in SPECIAL_IMPACTS if impact in impacts), None)


def fail_closed(detail: str) -> dict[str, Any]:
    return {
        "schema": "magi-preflight-decision/v1",
        "mode": "report-only",
        "decision": "ABORT",
        "reason_code": "UNSAFE_OR_INCOMPLETE_INPUT",
        "brief": None,
        "rounds_consumed": 1,
        "allows_second_round": False,
        "authorizes_shipping": False,
        "source_artifacts": [],
        "vetoes": [],
        "corroborated_roots": [],
        "questions": [],
        "detail": detail[:2000],
    }


def evaluate(brief_path: Path, run_manifest_path: Path) -> dict[str, Any]:
    brief = stable_read(brief_path, limit=MAX_BRIEF_BYTES)
    if not brief.raw:
        raise UnsafeInput("brief must not be empty")
    try:
        brief.raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsafeInput(f"brief is not UTF-8: {brief.path}") from exc
    brief_lines = brief.raw.splitlines(keepends=True)
    if not brief_lines:
        raise UnsafeInput("brief must contain at least one line")
    if len(brief_lines) > 200:
        raise UnsafeInput("brief exceeds the one-shot limit of 200 lines")

    run_manifest = stable_read(run_manifest_path, limit=MAX_REVIEW_BYTES)
    run_payload = parse_object(run_manifest)
    run_schema = load_schema("preflight-run.schema.json")
    validate_schema(run_payload, run_schema, str(run_manifest.path))
    if run_payload["brief"] != brief_identity(brief):
        raise UnsafeInput("run manifest is stale or bound to another brief")
    manifest_entries = {
        entry["reviewer"]: entry for entry in run_payload["reviewers"]
    }
    if set(manifest_entries) != set(PERSONAS):
        raise UnsafeInput("run manifest must bind exactly the three Magi personas")

    reviews = [
        stable_read(Path(manifest_entries[persona]["path"]), limit=MAX_REVIEW_BYTES)
        for persona in PERSONAS
    ]
    canonical_review_paths = {review.path for review in reviews}
    if len(canonical_review_paths) != 3:
        raise UnsafeInput("reviewer artifacts must be three distinct canonical files")
    for persona, review in zip(PERSONAS, reviews, strict=True):
        entry = manifest_entries[persona]
        if str(review.path) != entry["path"] or review.sha256 != entry["sha256"]:
            raise UnsafeInput(f"run manifest output binding mismatch for {persona}")
        expected_prompt_sha = hashlib.sha256(review_prompt(brief, persona)).hexdigest()
        if entry["prompt_sha256"] != expected_prompt_sha:
            raise UnsafeInput(f"run manifest prompt binding mismatch for {persona}")

    review_schema = load_schema("preflight-review.schema.json")
    decision_schema = load_schema("preflight-decision.schema.json")
    payloads: dict[str, tuple[dict[str, Any], StableFile]] = {}
    for review in reviews:
        payload = parse_object(review)
        validate_review(
            payload,
            source=review,
            brief=brief,
            brief_lines=brief_lines,
            review_schema=review_schema,
        )
        reviewer = payload["reviewer"]
        expected_reviewer = next(
            persona
            for persona, candidate in zip(PERSONAS, reviews, strict=True)
            if candidate.path == review.path
        )
        if reviewer != expected_reviewer:
            raise UnsafeInput(
                f"run manifest persona mismatch: expected {expected_reviewer}, got {reviewer}"
            )
        if reviewer in payloads:
            raise UnsafeInput(f"duplicate reviewer persona: {reviewer}")
        payloads[reviewer] = (payload, review)
    if set(payloads) != set(PERSONAS):
        raise UnsafeInput("reviewer set must be exactly MELCHIOR, BALTHASAR, and CASPAR")

    source_artifacts = [
        {
            "reviewer": reviewer,
            "path": str(payloads[reviewer][1].path),
            "sha256": payloads[reviewer][1].sha256,
        }
        for reviewer in PERSONAS
    ]
    findings_by_root: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    vetoes: list[dict[str, str]] = []
    for reviewer in PERSONAS:
        payload, _ = payloads[reviewer]
        for finding in payload["findings"]:
            source_ref = f"{reviewer}#{finding['finding_id']}"
            findings_by_root[finding["root_cause_id"]].append((reviewer, finding))
            reason = veto_reason(finding)
            if reason is not None:
                vetoes.append(
                    {
                        "source_ref": source_ref,
                        "root_cause_id": finding["root_cause_id"],
                        "decision": finding["recommended_decision"],
                        "reason": reason,
                    }
                )
    vetoes.sort(key=lambda item: (item["root_cause_id"], item["source_ref"]))

    corroborated: list[dict[str, Any]] = []
    questions: list[dict[str, str]] = []
    veto_refs = {item["source_ref"] for item in vetoes}
    for root_id in sorted(findings_by_root):
        root_findings = findings_by_root[root_id]
        reviewers = sorted({item[0] for item in root_findings}, key=PERSONAS.index)
        source_refs = sorted(
            f"{reviewer}#{finding['finding_id']}"
            for reviewer, finding in root_findings
        )
        if len(reviewers) >= 2:
            corroborated.append(
                {
                    "root_cause_id": root_id,
                    "reviewers": reviewers,
                    "source_refs": source_refs,
                    "decision": (
                        "ABORT"
                        if any(
                            finding["recommended_decision"] == "ABORT"
                            for _, finding in root_findings
                        )
                        else "PIVOT"
                    ),
                }
            )
            continue
        reviewer, finding = root_findings[0]
        source_ref = f"{reviewer}#{finding['finding_id']}"
        if source_ref not in veto_refs:
            questions.append(
                {
                    "root_cause_id": root_id,
                    "source_ref": source_ref,
                    "question": finding["question_if_uncorroborated"],
                }
            )
    questions.sort(key=lambda item: (item["root_cause_id"], item["source_ref"]))

    if any(item["decision"] == "ABORT" for item in vetoes):
        decision, reason_code = "ABORT", "GROUNDED_ABORT_VETO"
    elif any(item["decision"] == "ABORT" for item in corroborated):
        decision, reason_code = "ABORT", "CORROBORATED_ABORT"
    elif any(item["decision"] == "PIVOT" for item in vetoes):
        decision, reason_code = "PIVOT", "GROUNDED_PIVOT_VETO"
    elif any(item["decision"] == "PIVOT" for item in corroborated):
        decision, reason_code = "PIVOT", "CORROBORATED_PIVOT"
    else:
        decision, reason_code = "PROCEED", "NO_BLOCKING_CONCERN"

    result = {
        "schema": "magi-preflight-decision/v1",
        "mode": "report-only",
        "decision": decision,
        "reason_code": reason_code,
        "brief": brief_identity(brief),
        "rounds_consumed": 1,
        "allows_second_round": False,
        "authorizes_shipping": False,
        "source_artifacts": source_artifacts,
        "vetoes": vetoes,
        "corroborated_roots": corroborated,
        "questions": questions,
        "detail": "",
    }
    validate_schema(result, decision_schema, "preflight decision")
    assert_unchanged(brief)
    assert_unchanged(run_manifest)
    for review in reviews:
        assert_unchanged(review)
    return result


def parser() -> Parser:
    root = Parser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    evaluate_parser = commands.add_parser("evaluate")
    evaluate_parser.add_argument("brief")
    evaluate_parser.add_argument("run_manifest")
    return root


def main() -> int:
    try:
        args = parser().parse_args()
    except UsageError as exc:
        print(f"MAGI_PREFLIGHT_USAGE: {exc}", file=sys.stderr)
        return 64
    try:
        result = evaluate(Path(args.brief), Path(args.run_manifest))
    except (UnsafeInput, OSError, RuntimeError) as exc:
        result = fail_closed(str(exc))
        try:
            validate_schema(
                result,
                load_schema("preflight-decision.schema.json"),
                "fail-closed decision",
            )
        except (UnsafeInput, RuntimeError):
            pass
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
