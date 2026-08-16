#!/usr/bin/env python3
"""Build a lossless synthesis envelope from one completed Magi review round."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from magi_validate_findings import validate, validate_prior_envelope
from magi_campaign_guard import load_ledger
from magi_protocol import sha256_file
from magi_verify_xfamily_artifacts import verify as verify_xfamily_artifacts


VERDICT_ORDER = {"GO": 0, "GO-WITH-REVISE": 1, "REVISE": 2, "REJECT": 3}
GROUNDING_ORDER = {"PASS": 0, "PARTIAL": 1, "FAIL": 2}
MAX_SOURCE_BYTES = 10 * 1024 * 1024
MAX_SOURCE_VERIFY_COMMANDS = 256
MAX_XFAMILY_VERIFY_COMMANDS = 768
MAX_SYNTHESIS_VERIFY_COMMANDS = 768
PERSONA_SETS = {
    "magi": ("melchior", "balthasar", "caspar"),
    "bug-hunt": ("hornet", "gnat", "wasp"),
    "xfamily": ("xfamily",),
}


def reject_duplicate_members(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON member: {key}")
        result[key] = value
    return result


def strict_json_loads(raw: bytes | str) -> object:
    return json.loads(raw, object_pairs_hook=reject_duplicate_members)


def require_regular(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ValueError(f"{label} is unreadable: {path}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"{label} must be a regular file: {path}")
    return info


def require_committed_xfamily(doc: Path, state_dir: Path, round_number: int) -> str:
    findings_path = state_dir / f"round_{round_number}_xfamily.json"
    meta_path = state_dir / f"round_{round_number}_xfamily.meta.json"
    require_regular(findings_path, "cross-family findings")
    require_regular(meta_path, "cross-family metadata")
    findings_info = findings_path.lstat()
    meta_info = meta_path.lstat()
    if findings_info.st_size > MAX_SOURCE_BYTES or meta_info.st_size > MAX_SOURCE_BYTES:
        raise ValueError("cross-family findings/meta exceed the bounded source limit")
    meta = strict_json_loads(meta_path.read_bytes())
    if not isinstance(meta, dict):
        raise ValueError("cross-family metadata must be a JSON object")
    reviewer = meta.get("reviewer_family")
    if reviewer not in {"claude", "grok"}:
        raise ValueError("cross-family metadata reviewer is unsupported")
    verify_xfamily_artifacts(
        argparse.Namespace(
            doc=doc,
            round=round_number,
            findings=findings_path,
            meta=meta_path,
            reviewer=reviewer,
        )
    )
    findings_sha = hashlib.sha256(findings_path.read_bytes()).hexdigest()
    artifact_sha = sha256_file(doc)
    if meta.get("output_sha") != findings_sha or meta.get("artifact_sha") != artifact_sha:
        raise ValueError("cross-family canonical pair identity does not match")
    ledger = load_ledger(doc, create=False)
    matches = [
        launch
        for campaign in ledger["campaigns"]
        if isinstance(campaign, dict)
        for launch in campaign.get("launches", [])
        if isinstance(launch, dict)
        and launch.get("status") == "success"
        and launch.get("phase") == "xfamily"
        and launch.get("round") == round_number
        and launch.get("artifact_sha") == artifact_sha
        and launch.get("protocol_sha") == meta.get("protocol_sha")
        and Path(str(launch.get("state_dir", ""))).resolve() == state_dir
    ]
    if len(matches) != 1:
        raise ValueError(
            "cross-family canonical pair lacks one matching successful ledger claim"
        )
    return reviewer


@contextmanager
def document_lock(doc: Path):
    control_dir = doc.parent / ".dual-magi"
    control_dir.mkdir(mode=0o700, exist_ok=True)
    artifact_id = hashlib.sha256(os.fsencode(doc.resolve())).hexdigest()[:16]
    lock_path = control_dir / f".review.{artifact_id}.lock"
    fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(f"document review lock is held: {lock_path}") from exc
        yield
    finally:
        os.close(fd)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Carry every finding from one round into a validated SYNTHESIS envelope."
    )
    parser.add_argument("doc", type=Path)
    parser.add_argument("round", type=int)
    parser.add_argument("state_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--persona-set", required=True, choices=sorted(PERSONA_SETS))
    return parser.parse_args()


def source_paths(
    state_dir: Path, round_number: int, output: Path, persona_set: str
) -> list[Path]:
    candidates = {}
    for path in state_dir.glob(f"round_{round_number}_*.json"):
        if path.resolve() == output.resolve():
            continue
        if path.name.endswith((".meta.json", ".FAILED.json")):
            continue
        candidates[path.name] = path
    expected = {
        f"round_{round_number}_{persona}.json" for persona in PERSONA_SETS[persona_set]
    }
    if set(candidates) != expected:
        missing = sorted(expected - set(candidates))
        extra = sorted(set(candidates) - expected)
        raise ValueError(f"source set mismatch: missing={missing}, extra={extra}")
    return [candidates[name] for name in sorted(expected)]


def synthesis_id(source_ref: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", source_ref).strip("-")
    digest = hashlib.sha256(source_ref.encode()).hexdigest()[:10]
    return f"SYN-{slug[:48]}-{digest}"


def load_sources(
    paths: list[Path],
    round_number: int,
    persona_set: str,
    schema: dict,
    doc: Path,
    xfamily_reviewer: str | None = None,
) -> list[tuple[Path, dict, bytes]]:
    loaded = []
    artifact_identity = None
    for path in paths:
        size = require_regular(path, "source artifact").st_size
        if size > MAX_SOURCE_BYTES:
            raise ValueError(
                f"source exceeds {MAX_SOURCE_BYTES}-byte limit: {path.name} ({size} bytes)"
            )
        raw = path.read_bytes()
        payload = strict_json_loads(raw)
        validate(payload, schema, doc=doc)
        command_limit = (
            MAX_XFAMILY_VERIFY_COMMANDS if persona_set == "xfamily"
            else MAX_SOURCE_VERIFY_COMMANDS
        )
        if len(payload.get("verify_commands_executed", [])) > command_limit:
            raise ValueError(
                f"source verification command count exceeds {command_limit}: {path.name}"
            )
        if payload.get("reviewer") == "SYNTHESIS":
            raise ValueError(f"source is already a synthesis: {path.name}")
        expected_persona = path.stem.rsplit("_", 1)[-1]
        reviewer = str(payload.get("reviewer", "")).lower()
        if persona_set == "xfamily":
            expected_reviewers = {
                xfamily_reviewer,
                f"{xfamily_reviewer}-cross-family",
            }
            if (
                xfamily_reviewer not in {"claude", "grok"}
                or reviewer not in expected_reviewers
            ):
                raise ValueError(f"source has wrong reviewer identity: {path.name}")
        elif reviewer != expected_persona:
            raise ValueError(f"source has wrong reviewer identity: {path.name}")
        if payload.get("round") != round_number:
            raise ValueError(f"source has wrong round: {path.name}")
        identity = (payload.get("artifact_id"), payload.get("artifact_sha"))
        if artifact_identity is None:
            artifact_identity = identity
        elif identity != artifact_identity:
            raise ValueError(f"source artifact identity differs: {path.name}")
        finding_ids = [finding.get("finding_id") for finding in payload.get("findings", [])]
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError(f"source contains duplicate finding_id values: {path.name}")
        loaded.append((path, payload, raw))
    return loaded


def build_envelope(loaded: list[tuple[Path, dict, bytes]], round_number: int) -> dict:
    first = loaded[0][1]
    findings = []
    dispositions = []
    commands = []
    seen_commands = set()
    source_artifacts = []
    verdict = "GO"
    grounding = "PASS"

    for path, payload, raw in loaded:
        source_artifacts.append(
            {"path": path.name, "sha256": hashlib.sha256(raw).hexdigest()}
        )
        verdict = max((verdict, payload["verdict"]), key=VERDICT_ORDER.__getitem__)
        grounding = max(
            (grounding, payload["schema_grounding_verdict"]),
            key=GROUNDING_ORDER.__getitem__,
        )
        for command in payload.get("verify_commands_executed", []):
            if (
                command not in seen_commands
                and len(commands) < MAX_SYNTHESIS_VERIFY_COMMANDS
            ):
                seen_commands.add(command)
                commands.append(command)
        for finding in payload.get("findings", []):
            source_ref = f"{path.name}#{finding['finding_id']}"
            carried = dict(finding)
            carried["finding_id"] = synthesis_id(source_ref)
            findings.append(carried)
            dispositions.append(
                {
                    "source_ref": source_ref,
                    "disposition": "carried",
                    "synthesis_finding_id": carried["finding_id"],
                }
            )

    return {
        "reviewer": "SYNTHESIS",
        "round": round_number,
        "artifact_id": first["artifact_id"],
        "artifact_sha": first["artifact_sha"],
        "verdict": verdict,
        "schema_grounding_verdict": grounding,
        "verify_commands_executed": commands,
        "source_artifacts": source_artifacts,
        "dispositions": dispositions,
        "findings": findings,
    }


def main() -> int:
    args = parse_args()
    if args.round < 1:
        raise ValueError("round must be at least 1")
    lexical_doc = args.doc.expanduser()
    lexical_state_dir = args.state_dir.expanduser()
    lexical_output = args.output.expanduser()
    require_regular(lexical_doc, "document")
    try:
        state_info = lexical_state_dir.lstat()
    except OSError as exc:
        raise ValueError(f"state directory is unreadable: {lexical_state_dir}: {exc}") from exc
    if not stat.S_ISDIR(state_info.st_mode):
        raise ValueError(f"state directory must be a real directory: {lexical_state_dir}")
    if os.path.lexists(lexical_output):
        require_regular(lexical_output, "existing output")
    doc = lexical_doc.resolve()
    state_dir = lexical_state_dir.resolve()
    output = lexical_output.resolve()
    if output.parent != state_dir:
        raise ValueError("output must be directly inside the state directory")
    # The basename is provenance: on session resume a human (or agent) infers which reviewer
    # family produced a round from the filename alone. A synthesis of a cross-family round
    # named round_<N>_codex.json mislabels Claude/Grok findings as same-family output, so the
    # name is fixed by the persona set instead of being caller-chosen.
    expected_name = f"round_{args.round}_{args.persona_set}_synthesis.json"
    if output.name != expected_name:
        raise ValueError(
            f"output basename must be {expected_name} for --persona-set {args.persona_set} "
            f"(got {output.name}); the basename records review provenance"
        )

    script_dir = Path(__file__).resolve().parent
    schema = script_dir.parent / "schemas" / "finding.schema.json"
    schema_payload = strict_json_loads(schema.read_bytes())
    if not isinstance(schema_payload, dict):
        raise ValueError("findings schema must be a JSON object")

    with document_lock(doc):
        xfamily_reviewer = None
        if args.persona_set == "xfamily":
            xfamily_reviewer = require_committed_xfamily(doc, state_dir, args.round)
        paths = source_paths(state_dir, args.round, output, args.persona_set)
        envelope = build_envelope(
            load_sources(
                paths,
                args.round,
                args.persona_set,
                schema_payload,
                doc,
                xfamily_reviewer,
            ),
            args.round,
        )

        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{output.name}.", suffix=".tmp", dir=state_dir
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(envelope, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            validate(envelope, schema_payload, doc=doc, same_doc_only=True)
            validate_prior_envelope(
                envelope,
                output,
                schema_payload,
                doc,
                args.round + 1,
                state_dir,
            )
            if output.exists():
                require_regular(output, "existing output")
                if output.read_bytes() != temporary.read_bytes():
                    raise ValueError(
                        "output already exists with different content; refusing to overwrite"
                    )
            else:
                os.replace(temporary, output)
                directory_fd = os.open(state_dir, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)
    print(f"magi-synthesize: {len(paths)} sources -> {output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RecursionError, json.JSONDecodeError) as exc:
        print(f"magi-synthesize: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
