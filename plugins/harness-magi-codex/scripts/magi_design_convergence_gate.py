#!/usr/bin/env python3
"""Read-only bounded convergence evaluator for Dual-Magi design reviews."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import stat
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import jsonschema

sys.dont_write_bytecode = True

import magi_campaign_guard as guard
import magi_convergence_kernel as kernel
from magi_validate_findings import validate as validate_findings
from magi_verify_round import verify_round


MAX_JSON_BYTES = 4 * 1024 * 1024
PERSONAS = ("melchior", "balthasar", "caspar")


class UnsafeInput(RuntimeError):
    """Evidence cannot be evaluated safely (exit 2)."""


class UsageError(ValueError):
    """The operator invocation is invalid (exit 64)."""


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise UsageError(message)


def stable_bytes(path: Path, *, limit: int = MAX_JSON_BYTES) -> bytes:
    """Read one regular file through O_NOFOLLOW and reject in-read mutation."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise UnsafeInput(f"cannot safely open {path}: {exc}") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise UnsafeInput(f"unsafe file input: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, min(1024 * 1024, limit + 1))
            if not chunk:
                break
            chunks.append(chunk)
            if sum(map(len, chunks)) > limit:
                raise UnsafeInput(f"file exceeds size limit: {path}")
        after = os.fstat(fd)
    finally:
        os.close(fd)
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if identity_before != identity_after:
        raise UnsafeInput(f"input changed while read: {path}")
    return b"".join(chunks)


def stable_json(path: Path, *, limit: int = MAX_JSON_BYTES) -> tuple[dict[str, Any], str]:
    raw = stable_bytes(path, limit=limit)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UnsafeInput(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise UnsafeInput(f"JSON input is not an object: {path}")
    return payload, hashlib.sha256(raw).hexdigest()


def canonical_state_dir(raw: object) -> Path:
    state = Path(str(raw))
    if not state.is_absolute() or state.is_symlink():
        raise UnsafeInput(f"unsafe launch state_dir: {state}")
    try:
        resolved = state.resolve(strict=True)
    except OSError as exc:
        raise UnsafeInput(f"cannot resolve launch state_dir {state}: {exc}") from exc
    if resolved != state or not state.is_dir():
        raise UnsafeInput(f"unsafe launch state_dir: {state}")
    return state


def validate_review(
    path: Path,
    *,
    doc: Path,
    artifact_sha: str,
    round_no: int,
    schema: dict[str, Any],
    observed: dict[Path, str],
) -> dict[str, Any]:
    payload, digest = stable_json(path)
    observed[path] = digest
    try:
        validate_findings(payload, schema, doc=doc, same_doc_only=True)
    except (jsonschema.ValidationError, ValueError) as exc:
        raise UnsafeInput(f"invalid review artifact {path}: {exc}") from exc
    if payload.get("artifact_sha") != artifact_sha:
        raise UnsafeInput(f"review artifact SHA does not match launch: {path}")
    if payload.get("round") != round_no:
        raise UnsafeInput(f"review round does not match launch: {path}")
    if payload.get("schema_grounding_verdict") == "FAIL":
        raise UnsafeInput(f"ungrounded review artifact: {path}")
    if not payload.get("verify_commands_executed"):
        raise UnsafeInput(f"review artifact records no grounding commands: {path}")
    return payload


def transcript_for(meta: dict[str, Any]) -> Path | None:
    family = meta.get("reviewer_family")
    session_id = meta.get("session_id")
    if family not in {"claude", "grok"} or not isinstance(session_id, str) or not session_id:
        return None
    pattern = (
        f"~/.claude/projects/*/{glob.escape(session_id)}.jsonl"
        if family == "claude"
        else f"~/.grok/sessions/*/{glob.escape(session_id)}/chat_history.jsonl"
    )
    matches = glob.glob(os.path.expanduser(pattern))
    if len(matches) != 1:
        return None
    return Path(matches[0])


def launch_reviews(
    launch: dict[str, Any],
    *,
    doc: Path,
    schema: dict[str, Any],
    observed: dict[Path, str],
) -> list[dict[str, Any]]:
    state = canonical_state_dir(launch["state_dir"])
    round_no = int(launch["round"])
    artifact_sha = str(launch["artifact_sha"])
    phase = launch["phase"]
    if phase == "fanout":
        paths = [state / f"round_{round_no}_{persona}.json" for persona in PERSONAS]
        if not all(path.is_file() for path in paths):
            raise UnsafeInput(f"fanout output set is incomplete for round {round_no}")
        return [
            validate_review(
                path,
                doc=doc,
                artifact_sha=artifact_sha,
                round_no=round_no,
                schema=schema,
                observed=observed,
            )
            for path in paths
        ]
    if phase == "targeted":
        raise UnsafeInput("targeted review is not valid evidence for dual-magi-design")

    prefix = state / f"round_{round_no}_xfamily"
    findings_path = Path(f"{prefix}.json")
    meta_path = Path(f"{prefix}.meta.json")
    _, findings_digest = stable_json(findings_path)
    meta, meta_digest = stable_json(meta_path)
    observed[findings_path] = findings_digest
    observed[meta_path] = meta_digest
    transcript = transcript_for(meta)
    if transcript is not None:
        transcript_raw = stable_bytes(transcript)
        observed[transcript] = hashlib.sha256(transcript_raw).hexdigest()
    try:
        verified = verify_round(
            doc,
            prefix,
            "codex",
            None,
            expected_artifact_sha=artifact_sha,
        )
    except Exception as exc:
        raise UnsafeInput(
            f"xfamily verifier failed closed: {type(exc).__name__}: {exc}"
        ) from exc
    if verified["failures"]:
        raise UnsafeInput(
            "xfamily G1-G6/G9 verification failed: "
            + "; ".join(str(item) for item in verified["failures"])
        )
    review = verified["findings"]
    if not isinstance(review, dict):
        raise UnsafeInput("xfamily verifier returned no findings object")
    try:
        validate_findings(review, schema, doc=doc, same_doc_only=True)
    except (jsonschema.ValidationError, ValueError) as exc:
        raise UnsafeInput(f"invalid verified xfamily artifact: {exc}") from exc
    if review.get("artifact_sha") != artifact_sha or review.get("round") != round_no:
        raise UnsafeInput("verified xfamily artifact does not match its launch")
    returned_transcript = verified.get("transcript_path")
    if returned_transcript:
        returned_path = Path(str(returned_transcript))
        if transcript is None or returned_path.resolve() != transcript.resolve():
            raise UnsafeInput("xfamily transcript changed during verification")
    return [review]


def load_ledger(doc: Path) -> tuple[dict[str, Any], Path, str]:
    ledger_path = doc.parent / ".dual-magi" / f"CAMPAIGN.{guard.doc_id(doc)}.json"
    if not ledger_path.exists():
        return guard.new_ledger(doc), ledger_path, "no-ledger"
    payload, digest = stable_json(ledger_path)
    try:
        validated = guard.load_ledger(doc, create=False)
    except (guard.StateError, guard.UsageError, OSError) as exc:
        raise UnsafeInput(f"invalid campaign ledger: {exc}") from exc
    if validated != payload:
        raise UnsafeInput("ledger normalization would change persisted accounting")
    return payload, ledger_path, digest


def blocked_output(
    reason_code: str,
    *,
    used: int,
    ceiling: int,
    artifact_sha: str,
) -> dict[str, Any]:
    return kernel.output(
        "BLOCKED",
        reason_code,
        next_mode=None,
        used=used,
        ceiling=ceiling,
        target_sha=artifact_sha,
        blocker_mass=0,
        cycles=0,
    )


def verify_observed(
    observed: dict[Path, str], absent_paths: tuple[Path, ...] = ()
) -> None:
    for path, expected_digest in observed.items():
        current_digest = hashlib.sha256(stable_bytes(path)).hexdigest()
        if current_digest != expected_digest:
            raise UnsafeInput(f"input changed during evaluation: {path}")
    for path in absent_paths:
        if path.exists():
            raise UnsafeInput(f"input appeared during evaluation: {path}")


def evaluate(doc_raw: Path) -> dict[str, Any]:
    expanded = doc_raw.expanduser()
    if expanded.is_symlink():
        raise UsageError(f"design document not found or unsafe: {expanded}")
    try:
        doc = expanded.resolve(strict=True)
    except OSError as exc:
        raise UsageError(f"design document not found or unsafe: {expanded}") from exc
    if not doc.is_file():
        raise UsageError(f"design document not found or unsafe: {doc}")
    doc_raw_bytes = stable_bytes(doc)
    artifact_sha = hashlib.sha256(doc_raw_bytes).hexdigest()
    observed: dict[Path, str] = {doc: artifact_sha}

    schema_path = Path(__file__).resolve().parent.parent / "schemas" / "finding.schema.json"
    schema, schema_digest = stable_json(schema_path, limit=1024 * 1024)
    observed[schema_path] = schema_digest
    ledger, ledger_path, ledger_digest = load_ledger(doc)
    absent_paths = (ledger_path,) if ledger_digest == "no-ledger" else ()
    if ledger_digest != "no-ledger":
        observed[ledger_path] = ledger_digest
    campaigns = ledger["campaigns"]
    used = guard.model_launches(campaigns)
    ceiling = min(
        guard.GLOBAL_MAX_MODEL_LAUNCHES,
        guard.base_ceiling(),
    )

    launches = [
        launch
        for campaign in campaigns
        if isinstance(campaign, dict)
        for launch in campaign.get("launches", [])
        if isinstance(launch, dict)
    ]
    for launch in launches:
        phase = launch.get("phase")
        if phase not in guard.PHASE_WEIGHT:
            raise UnsafeInput("ledger contains an invalid phase")
        if launch.get("model_launches") != guard.PHASE_WEIGHT[phase]:
            raise UnsafeInput("ledger contains an invalid phase weight")
        if launch.get("status") in guard.NONTERMINAL_STATUSES:
            reason = (
                "DESIGN_REQUIREMENT_REVISION_CANCELLATION_IN_PROGRESS"
                if launch.get("status") == "cancellation_in_progress"
                else "DESIGN_LAUNCH_STILL_RUNNING"
            )
            result = blocked_output(
                reason, used=used, ceiling=ceiling, artifact_sha=artifact_sha
            )
            verify_observed(observed, absent_paths)
            return result

    reviews_by_revision: dict[str, list[dict[str, Any]]] = defaultdict(list)
    revision_order: list[str] = []
    for launch in launches:
        if launch.get("status") != "success":
            continue
        launch_sha = str(launch["artifact_sha"])
        if launch_sha not in reviews_by_revision:
            revision_order.append(launch_sha)
        reviews_by_revision[launch_sha].extend(
            launch_reviews(launch, doc=doc, schema=schema, observed=observed)
        )

    try:
        summaries = {
            revision: kernel.summarize_revision(reviews_by_revision[revision])
            for revision in revision_order
        }
        delta = kernel.revision_delta(revision_order, summaries, artifact_sha)
    except kernel.KernelInputError as exc:
        raise UnsafeInput(str(exc)) from exc

    active = guard.active_campaign(ledger)
    active_launches = active["launches"]
    assert isinstance(active_launches, list)
    current_protocol_sha = guard.protocol_sha()
    current_phases = {
        str(launch["phase"])
        for launch in active_launches
        if isinstance(launch, dict)
        and launch.get("status") == "success"
        and launch.get("artifact_sha") == artifact_sha
        and launch.get("protocol_sha") == current_protocol_sha
    }
    completed_cycles: list[str] = []
    for campaign in campaigns:
        if not isinstance(campaign, dict):
            continue
        pending: str | None = None
        for launch in campaign.get("launches", []):
            if not isinstance(launch, dict) or launch.get("status") != "success":
                continue
            if launch.get("phase") == "fanout":
                pending = str(launch["artifact_sha"])
            elif launch.get("phase") == "xfamily" and pending == launch.get("artifact_sha"):
                completed_cycles.append(pending)
                pending = None

    transition = guard.next_transition(active_launches)
    transition_blocked = (
        transition["kind"] == "transition-blocked"
        and not guard.may_rollover(ledger, active, doc, 1, "fanout")
    )
    if transition_blocked:
        result = blocked_output(
            "DESIGN_RETRY_BUDGET_EXHAUSTED",
            used=used,
            ceiling=ceiling,
            artifact_sha=artifact_sha,
        )
    else:
        state = {
            "delta": delta,
            "used": used,
            "ceiling": ceiling,
            "target_sha": artifact_sha,
            "cycles": len(completed_cycles),
            "current_phases": current_phases,
            "admissions": {
                phase: guard.admission_decision(used, ceiling, phase)
                for phase in ("fanout", "xfamily")
            },
        }
        result = kernel.evaluate_profile("dual-magi-design", state)

    verify_observed(observed, absent_paths)
    return result


def parser() -> Parser:
    root = Parser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    evaluate_parser = commands.add_parser("evaluate")
    evaluate_parser.add_argument("design_doc")
    return root


def main() -> int:
    try:
        args = parser().parse_args()
        result = evaluate(Path(args.design_doc))
    except UsageError as exc:
        print(f"MAGI_DESIGN_CONVERGENCE_USAGE: {exc}", file=sys.stderr)
        return 64
    except (
        UnsafeInput,
        guard.StateError,
        guard.TransitionError,
        kernel.KernelInputError,
        OSError,
        RuntimeError,
    ) as exc:
        print(
            json.dumps(
                {
                    "mode": "report-only",
                    "decision": "BLOCKED",
                    "reason_code": "UNSAFE_OR_INCOMPLETE_DESIGN_INPUT",
                    "detail": str(exc),
                    "authorizes_shipping": False,
                },
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
