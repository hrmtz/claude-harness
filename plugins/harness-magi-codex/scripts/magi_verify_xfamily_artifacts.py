#!/usr/bin/env python3
"""Validate a durable cross-family findings/meta pair for crash recovery."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

from magi_protocol import protocol_sha, strict_json_loads
from magi_validate_findings import validate

MAX_JSON_BYTES = 10 * 1024 * 1024
MAX_TRANSCRIPT_BYTES = 256 * 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bounded_json(path: Path, label: str) -> object:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"{label} must be a regular file")
    if info.st_size > MAX_JSON_BYTES:
        raise ValueError(f"{label} exceeds {MAX_JSON_BYTES}-byte limit")
    return strict_json_loads(path.read_bytes())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("doc", type=Path)
    parser.add_argument("round", type=int)
    parser.add_argument("findings", type=Path)
    parser.add_argument("meta", type=Path)
    parser.add_argument("--reviewer", required=True, choices=("claude", "grok"))
    return parser.parse_args()


def verify(args: argparse.Namespace) -> None:
    lexical_paths = (args.doc.expanduser(), args.findings.expanduser(), args.meta.expanduser())
    for path, label in zip(lexical_paths, ("document", "findings", "metadata")):
        try:
            info = path.lstat()
        except OSError as exc:
            raise ValueError(f"{label} is unreadable: {exc}") from exc
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"{label} must be a regular file")
    doc, findings_path, meta_path = (path.resolve() for path in lexical_paths)

    script_dir = Path(__file__).resolve().parent
    schema = strict_json_loads(
        (script_dir.parent / "schemas" / "finding.schema.json").read_text(
            encoding="utf-8"
        )
    )
    findings = bounded_json(findings_path, "findings")
    meta = bounded_json(meta_path, "metadata")
    if not isinstance(findings, dict) or not isinstance(meta, dict):
        raise ValueError("findings and meta must be JSON objects")
    validate(findings, schema, doc=doc)
    if findings.get("round") != args.round:
        raise ValueError("findings round does not match recovery target")

    artifact_sha = sha256_file(doc)
    expected_protocol = protocol_sha()
    checks = {
        "reviewer_family": args.reviewer,
        "round": args.round,
        "artifact_sha": artifact_sha,
        "protocol_sha": expected_protocol,
        "output_sha": sha256_file(findings_path),
    }
    mismatches = {
        key: (meta.get(key), expected)
        for key, expected in checks.items()
        if meta.get(key) != expected
    }
    if mismatches:
        raise ValueError(f"xfamily metadata mismatch: {mismatches}")

    session_id = meta.get("session_id")
    transcript_path = meta.get("transcript_path")
    transcript_sha = meta.get("transcript_sha")
    if not all(isinstance(value, str) and value for value in
               (session_id, transcript_path, transcript_sha,
                meta.get("model_id"), meta.get("requested_model"))):
        raise ValueError("xfamily metadata provenance is incomplete")
    lexical_transcript = Path(str(transcript_path)).expanduser()
    if not stat.S_ISREG(lexical_transcript.lstat().st_mode):
        raise ValueError("xfamily transcript must be a regular file")
    if lexical_transcript.lstat().st_size > MAX_TRANSCRIPT_BYTES:
        raise ValueError(f"xfamily transcript exceeds {MAX_TRANSCRIPT_BYTES}-byte limit")
    transcript = lexical_transcript.resolve()
    if sha256_file(transcript) != transcript_sha:
        raise ValueError("xfamily transcript digest changed")
    if args.reviewer == "claude":
        if transcript.name != f"{session_id}.jsonl":
            raise ValueError("Claude transcript path does not match session_id")
    else:
        if transcript.name != "chat_history.jsonl" or transcript.parent.name != session_id:
            raise ValueError("Grok transcript path does not match session_id")


def main() -> int:
    args = parse_args()
    if args.round < 1:
        raise ValueError("round must be at least 1")
    verify(args)
    print("magi-verify-xfamily: durable artifact pair verified")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RecursionError, json.JSONDecodeError) as exc:
        print(f"magi-verify-xfamily: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
