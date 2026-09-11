#!/usr/bin/env python3
"""Validate the shared findings schema plus convergence rules the API schema cannot express."""

from __future__ import annotations

import json
import hashlib
import os
import sys
from pathlib import Path

import jsonschema
from magi_protocol import sha256_file, strict_json_loads


NONBLOCKING = {"readiness-gap", "scope-expansion"}
BLOCKING_SEVERITIES = {"REJECT", "CRITICAL", "HIGH"}


def artifact_id(doc: Path) -> str:
    return hashlib.sha256(os.fsencode(doc.resolve())).hexdigest()[:16]


def validate(
    payload: object,
    schema: object,
    *,
    doc: Path | None = None,
    same_doc_only: bool = False,
    expected_reviewer: str | None = None,
    expected_round: int | None = None,
) -> None:
    jsonschema.validate(instance=payload, schema=schema)
    if not isinstance(payload, dict):
        raise ValueError("findings payload must be an object")
    if expected_reviewer is not None and payload.get("reviewer") != expected_reviewer:
        raise ValueError("reviewer does not match the launched persona")
    if expected_round is not None and payload.get("round") != expected_round:
        raise ValueError("round does not match the launched review round")
    grounding = payload.get("schema_grounding_verdict")
    commands = payload.get("verify_commands_executed")
    if grounding == "FAIL":
        raise ValueError("schema_grounding_verdict=FAIL is unusable reviewer output")
    if grounding in {"PASS", "PARTIAL"} and not commands:
        raise ValueError(
            f"schema_grounding_verdict={grounding} requires verification commands"
        )
    if doc is not None:
        if payload.get("artifact_id") != artifact_id(doc):
            raise ValueError("artifact_id does not match the canonical document path")
        if not same_doc_only:
            expected_sha = sha256_file(doc)
            if payload.get("artifact_sha") != expected_sha:
                raise ValueError("artifact_sha does not match the current document revision")
    findings = payload.get("findings") or []
    finding_ids = [finding.get("finding_id") for finding in findings]
    if len(finding_ids) != len(set(finding_ids)):
        raise ValueError("source contains duplicate finding_id values")
    for finding in findings:
        if finding.get("severity") in BLOCKING_SEVERITIES:
            if not finding.get("subsystem") or not finding.get("root_cause_id"):
                raise ValueError(
                    f"{finding.get('finding_id')}: blocking finding requires "
                    "subsystem and root_cause_id"
                )
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
        actual_sha = sha256_file(source_path)
        if item.get("sha256") != actual_sha:
            raise ValueError(f"source artifact digest mismatch: {name}")
        source_payload = strict_json_loads(source_path.read_bytes())
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


def validate_xfamily_reviewer(payload: dict, reviewer_family: str) -> None:
    """Use the same provider identity contract at publication and synthesis."""
    reviewer = str(payload.get("reviewer", "")).lower()
    if reviewer_family not in {"claude", "grok"} or reviewer not in {
        reviewer_family,
        f"{reviewer_family}-cross-family",
        f"{reviewer_family}-xfamily",
        f"xfamily-{reviewer_family}",
    }:
        raise ValueError("source has wrong reviewer identity")


def carried_prior_blockers(current_findings, out_prefix, doc):
    """Return unresolved prior HIGH+ findings carried by the current round.

    The current reviewer may recalibrate a duplicated finding downward, but a
    carried/duplicate disposition is not a resolution. Bind those dispositions
    back to the validated immediately preceding SYNTHESIS envelope and retain
    its blocking severity for G8.
    """
    carried = [
        item
        for item in (current_findings.get("dispositions") or [])
        if isinstance(item, dict)
        and item.get("disposition") in {"carried", "duplicate"}
    ]
    if not carried:
        return []
    current_round = current_findings.get("round")
    if type(current_round) is not int or current_round <= 1:
        raise ValueError("carried dispositions require a preceding review round")
    state_dir = Path(out_prefix).resolve().parent
    candidates = sorted(state_dir.glob(f"round_{current_round - 1}_*_synthesis.json"))
    schema_path = (
        Path(__file__).resolve().parent.parent
        / "schemas"
        / "finding.schema.json"
    )
    schema = strict_json_loads(schema_path.read_bytes())
    valid_priors = []
    for candidate in candidates:
        try:
            prior = strict_json_loads(candidate.read_bytes())
            validate(
                prior,
                schema,
                doc=Path(doc).resolve(),
                same_doc_only=True,
                expected_reviewer="SYNTHESIS",
                expected_round=current_round - 1,
            )
            if prior.get("artifact_sha") != current_findings.get("artifact_sha"):
                raise ValueError("prior synthesis artifact revision mismatch")
            validate_prior_envelope(
                prior,
                candidate,
                schema,
                Path(doc).resolve(),
                current_round,
                state_dir,
            )
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        valid_priors.append(prior)
    if len(valid_priors) != 1:
        raise ValueError(
            f"expected exactly one validated prior synthesis, found {len(valid_priors)}"
        )
    prior = valid_priors[0]
    prior_findings = {
        item.get("finding_id"): item
        for item in (prior.get("findings") or [])
        if isinstance(item, dict) and isinstance(item.get("finding_id"), str)
    }
    prior_dispositions = {
        item.get("source_ref"): item
        for item in (prior.get("dispositions") or [])
        if isinstance(item, dict) and isinstance(item.get("source_ref"), str)
    }
    blocking = []
    for item in carried:
        source_ref = item.get("source_ref")
        finding_id = item.get("synthesis_finding_id")
        prior_disposition = prior_dispositions.get(source_ref)
        if (
            not isinstance(finding_id, str)
            or not finding_id
            or not isinstance(prior_disposition, dict)
            or prior_disposition.get("synthesis_finding_id") != finding_id
        ):
            raise ValueError(f"carried disposition is not bound to prior synthesis: {source_ref}")
        prior_finding = prior_findings.get(finding_id)
        if not isinstance(prior_finding, dict):
            raise ValueError(f"carried prior finding is missing: {finding_id}")
        if prior_finding.get("severity") in {"REJECT", "CRITICAL", "HIGH"}:
            blocking.append(prior_finding)
    return blocking


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
    parser.add_argument("--reviewer")
    parser.add_argument("--round", type=int)
    args = parser.parse_args()
    payload_path = Path(args.findings)
    schema_path = Path(args.schema) if args.schema else (
        Path(__file__).resolve().parent.parent / "schemas" / "finding.schema.json"
    )
    try:
        payload = strict_json_loads(payload_path.read_bytes())
        schema = strict_json_loads(schema_path.read_bytes())
        expected_doc = Path(args.doc or args.same_doc).resolve() if (args.doc or args.same_doc) else None
        validate(
            payload,
            schema,
            doc=expected_doc,
            same_doc_only=bool(args.same_doc),
            expected_reviewer=args.reviewer,
            expected_round=args.round,
        )
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
