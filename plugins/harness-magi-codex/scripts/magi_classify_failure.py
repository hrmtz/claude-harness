#!/usr/bin/env python3
"""Build bounded Magi failure metadata without retaining provider content."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import jsonschema

from magi_validate_findings import validate


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def load_scrub_meta(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def classify(
    *,
    output: Path,
    log: Path,
    scrub_meta: Path,
    provider_exit: int,
    scrub_exit: int,
    status_valid: bool,
    schema_path: Path,
    doc: Path,
    reviewer: str,
    round_number: int,
    expected_artifact_id: str,
    expected_artifact_sha: str,
) -> dict[str, object]:
    meta = load_scrub_meta(scrub_meta)
    result: dict[str, object] = {
        "reviewer": reviewer,
        "round": round_number,
        "classification": "ok",
        "provider_exit_code": provider_exit,
        "scrubber_exit_code": scrub_exit,
        "output_bytes": file_size(output),
        "log_bytes": file_size(log),
        "input_bytes": meta.get("input_bytes", 0),
        "input_parsed_json": meta.get("parsed_json", False),
        "redactions": meta.get("redactions", 0),
    }
    if not status_valid:
        result["classification"] = "status-missing-or-invalid"
        return result
    if scrub_exit != 0:
        result["classification"] = "scrubber-failure"
        return result
    if provider_exit in {124, 137}:
        result["classification"] = "provider-timeout"
        return result
    if provider_exit != 0:
        result["classification"] = "provider-exit"
        return result
    try:
        current_artifact_sha = hashlib.sha256(doc.read_bytes()).hexdigest()
    except OSError:
        result["classification"] = "live-doc-unavailable"
        return result
    if current_artifact_sha != expected_artifact_sha:
        result["classification"] = "live-doc-drift"
        return result
    if result["output_bytes"] == 0:
        result["classification"] = "empty-output"
        return result
    try:
        payload = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        result["classification"] = (
            "post-scrub-corruption"
            if meta.get("parsed_json") is True
            else "json-parse-rejection"
        )
        return result
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.validate(instance=payload, schema=schema)
    except (OSError, json.JSONDecodeError, jsonschema.ValidationError):
        result["classification"] = "json-schema-rejection"
        return result
    expected_identity = {
        "reviewer": reviewer,
        "round": round_number,
        "artifact_id": expected_artifact_id,
        "artifact_sha": expected_artifact_sha,
    }
    for field, expected in expected_identity.items():
        if payload.get(field) != expected:
            result["classification"] = "artifact-identity-rejection"
            result["identity_field"] = field
            return result
    try:
        validate(payload, schema)
    except ValueError:
        result["classification"] = "convergence-rule-rejection"
        return result
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--scrub-meta", required=True)
    parser.add_argument("--provider-exit", type=int, required=True)
    parser.add_argument("--scrub-exit", type=int, required=True)
    parser.add_argument("--status-valid", type=int, choices=(0, 1), required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--doc", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--round", type=int, required=True)
    parser.add_argument("--claim-id", required=True)
    parser.add_argument("--artifact-id", required=True)
    parser.add_argument("--artifact-sha", required=True)
    args = parser.parse_args()

    result = classify(
        output=Path(args.output),
        log=Path(args.log),
        scrub_meta=Path(args.scrub_meta),
        provider_exit=args.provider_exit,
        scrub_exit=args.scrub_exit,
        status_valid=bool(args.status_valid),
        schema_path=Path(args.schema),
        doc=Path(args.doc),
        reviewer=args.reviewer,
        round_number=args.round,
        expected_artifact_id=args.artifact_id,
        expected_artifact_sha=args.artifact_sha,
    )
    result["claim_id"] = args.claim_id
    result["artifact_id"] = args.artifact_id
    result["artifact_sha"] = args.artifact_sha
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
