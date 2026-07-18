#!/usr/bin/env python3
"""Validate the shared findings schema plus convergence rules the API schema cannot express."""

from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path

import jsonschema


NONBLOCKING = {"readiness-gap", "scope-expansion"}
BLOCKING_SEVERITIES = {"REJECT", "CRITICAL", "HIGH"}


def artifact_id(doc: Path) -> str:
    return hashlib.sha256(str(doc.resolve()).encode()).hexdigest()[:16]


def validate(
    payload: object,
    schema: object,
    *,
    doc: Path | None = None,
    same_doc_only: bool = False,
) -> None:
    jsonschema.validate(instance=payload, schema=schema)
    if not isinstance(payload, dict):
        raise ValueError("findings payload must be an object")
    if doc is not None:
        if payload.get("artifact_id") != artifact_id(doc):
            raise ValueError("artifact_id does not match the canonical document path")
        if not same_doc_only:
            expected_sha = hashlib.sha256(doc.read_bytes()).hexdigest()
            if payload.get("artifact_sha") != expected_sha:
                raise ValueError("artifact_sha does not match the current document revision")
    findings = payload.get("findings") or []
    for finding in findings:
        if (
            finding.get("dup_flag") in NONBLOCKING
            and finding.get("severity") in BLOCKING_SEVERITIES
        ):
            raise ValueError(
                f"{finding.get('finding_id')}: {finding.get('dup_flag')} cannot have "
                f"blocking severity {finding.get('severity')}"
            )
    if findings and all(finding.get("dup_flag") in NONBLOCKING for finding in findings):
        if payload.get("verdict") != "GO-WITH-REVISE":
            raise ValueError(
                "readiness-gap/scope-expansion-only findings require GO-WITH-REVISE"
            )


def validate_prior_envelope(
    payload: dict[str, object],
    payload_path: Path,
    schema: object,
    doc: Path,
    current_round: int,
    state_dir: Path,
) -> None:
    if payload.get("reviewer") != "SYNTHESIS":
        raise ValueError("prior artifact reviewer must be SYNTHESIS")
    source_round = current_round - 1
    if payload.get("round") != source_round:
        raise ValueError(
            f"prior round {payload.get('round')!r} does not precede round {current_round}"
        )
    if payload_path.resolve().parent != state_dir.resolve():
        raise ValueError("prior artifact is outside the active state directory")

    candidates = {
        path.name: path
        for path in state_dir.glob(f"round_{source_round}_*.json")
        if path.resolve() != payload_path.resolve()
        and not path.name.endswith(".meta.json")
        and not path.name.endswith(".FAILED.json")
    }
    listed = payload.get("source_artifacts")
    dispositions = payload.get("dispositions")
    if not isinstance(listed, list) or not listed:
        raise ValueError("SYNTHESIS prior requires non-empty source_artifacts")
    if not isinstance(dispositions, list):
        raise ValueError("SYNTHESIS prior requires dispositions")
    listed_paths = {
        item.get("path")
        for item in listed
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    if listed_paths != set(candidates) or len(listed) != len(candidates):
        raise ValueError(
            f"source_artifacts {sorted(str(p) for p in listed_paths)} do not cover "
            f"state sources {sorted(candidates)}"
        )

    source_refs: set[str] = set()
    for item in listed:
        assert isinstance(item, dict)
        name = item["path"]
        source_path = candidates[name]
        actual_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
        if item.get("sha256") != actual_sha:
            raise ValueError(f"source artifact digest mismatch: {name}")
        source_payload = json.loads(source_path.read_text(encoding="utf-8"))
        validate(source_payload, schema, doc=doc, same_doc_only=True)
        if source_payload.get("round") != source_round:
            raise ValueError(f"source artifact has wrong round: {name}")
        for source_finding in source_payload.get("findings") or []:
            source_refs.add(f"{name}#{source_finding['finding_id']}")

    disposition_refs = {
        item.get("source_ref")
        for item in dispositions
        if isinstance(item, dict) and isinstance(item.get("source_ref"), str)
    }
    if disposition_refs != source_refs or len(dispositions) != len(source_refs):
        raise ValueError("dispositions must cover every source finding exactly once")
    synthesis_ids = {
        finding.get("finding_id")
        for finding in payload.get("findings") or []
        if isinstance(finding, dict)
    }
    for disposition in dispositions:
        assert isinstance(disposition, dict)
        if disposition.get("disposition") in {"carried", "duplicate"}:
            target = disposition.get("synthesis_finding_id")
            if target not in synthesis_ids:
                raise ValueError(
                    f"{disposition.get('source_ref')}: carried/duplicate disposition has no "
                    "valid synthesis_finding_id"
                )
        elif disposition.get("synthesis_finding_id") != "":
            raise ValueError(
                f"{disposition.get('source_ref')}: resolved/deferred disposition must use an "
                "empty synthesis_finding_id"
            )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("findings")
    parser.add_argument("schema", nargs="?")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--doc")
    mode.add_argument("--same-doc")
    parser.add_argument("--prior-for-round", type=int)
    parser.add_argument("--state-dir")
    args = parser.parse_args()
    payload_path = Path(args.findings)
    schema_path = Path(args.schema) if args.schema else (
        Path(__file__).resolve().parent.parent / "schemas" / "finding.schema.json"
    )
    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        expected_doc = Path(args.doc or args.same_doc).resolve() if (args.doc or args.same_doc) else None
        validate(payload, schema, doc=expected_doc, same_doc_only=bool(args.same_doc))
        if args.prior_for_round is not None:
            if args.prior_for_round <= 1:
                raise ValueError("--prior-for-round must be greater than 1")
            if expected_doc is None or not args.state_dir:
                raise ValueError("prior validation requires --same-doc and --state-dir")
            validate_prior_envelope(
                payload,
                payload_path,
                schema,
                expected_doc,
                args.prior_for_round,
                Path(args.state_dir),
            )
    except (OSError, json.JSONDecodeError, jsonschema.ValidationError, ValueError) as exc:
        print(f"magi-findings-invalid: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
