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
FANOUT_PERSONA_SETS = (
    PERSONAS,
    ("hornet", "gnat", "wasp"),
)
SCOPED_FINAL_CYCLE_DOC = Path(
    "/home/hrmtz/projects/ZN6/ecu-tuning/docs/designs/"
    "TELEMETRY-FI-CALIBRATION-HARDENING/01a-canonical-inventory-publication.md"
)
SCOPED_FINAL_CYCLE_AUTHORITY = Path(
    "/home/hrmtz/projects/ZN6/ecu-tuning/docs/designs/"
    "TELEMETRY-FI-CALIBRATION-HARDENING/.dual-magi-01a/"
    "CONVERGENCE-AUTHORITY.ZN6-01A-FINAL-CYCLE-2026-08-22.json"
)
SCOPED_FINAL_CYCLE_AUTHORITY_SHA256 = (
    "73c8dd18ba99d4f88f1c09b1a92dd8f9828e4ada9fceae1348dcf3577c900ef6"
)
E2A_CHECKPOINT_DOC = Path(
    "/home/hrmtz/projects/ZN6/ecu-re/docs/designs/TORQUE-CONTROL-REHOME/"
    "E2a3a1-publisher-hardening-worktree.md"
)
E2A_CHECKPOINT_AUTHORITY = Path(
    "/home/hrmtz/projects/ZN6/ecu-re/docs/designs/TORQUE-CONTROL-REHOME/.dual-magi/"
    "CONVERGENCE-AUTHORITY.E2A3A1-CHECKPOINT-14-18.json"
)
E2A_CHECKPOINT_AUTHORITY_SHA256 = (
    "932dc80be50ec0e66cbd498e569b32e502f0ac17081c30ca39be45df0219b3ba"
)


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
    expected_reviewer: str,
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
    if payload.get("reviewer") != expected_reviewer:
        raise UnsafeInput(f"reviewer identity does not match output basename: {path}")
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
    absent_paths: set[Path],
) -> list[dict[str, Any]]:
    state = canonical_state_dir(launch["state_dir"])
    round_no = int(launch["round"])
    artifact_sha = str(launch["artifact_sha"])
    phase = launch["phase"]
    if phase == "fanout":
        authorized_sets = {
            frozenset(persona.upper() for persona in personas)
            for personas in FANOUT_PERSONA_SETS
        }
        if authorized_sets != set(guard.STARTUP_REVIEWER_SETS.get("fanout", ())):
            raise UnsafeInput("fanout persona sets differ from campaign protocol")
        candidates = [
            (
                personas,
                tuple(state / f"round_{round_no}_{persona}.json" for persona in personas),
            )
            for personas in FANOUT_PERSONA_SETS
        ]
        presence: dict[Path, bool] = {}
        for _personas, paths in candidates:
            for path in paths:
                try:
                    metadata = path.lstat()
                except FileNotFoundError:
                    absent_paths.add(path)
                    presence[path] = False
                    continue
                except OSError as exc:
                    raise UnsafeInput(
                        f"cannot inspect fanout output path {path}: {exc}"
                    ) from exc
                if not stat.S_ISREG(metadata.st_mode):
                    raise UnsafeInput(f"fanout output path is not a regular file: {path}")
                presence[path] = True
        if any(
            any(presence[path] for path in paths)
            and not all(presence[path] for path in paths)
            for _personas, paths in candidates
        ):
            raise UnsafeInput(f"fanout output set is incomplete for round {round_no}")
        complete = [
            (personas, paths)
            for personas, paths in candidates
            if all(presence[path] for path in paths)
        ]
        if not complete:
            raise UnsafeInput(f"fanout output set is incomplete for round {round_no}")
        if len(complete) != 1:
            raise UnsafeInput(f"fanout output set is ambiguous for round {round_no}")
        personas, paths = complete[0]
        return [
            validate_review(
                path,
                doc=doc,
                artifact_sha=artifact_sha,
                round_no=round_no,
                expected_reviewer=persona.upper(),
                schema=schema,
                observed=observed,
            )
            for persona, path in zip(personas, paths, strict=True)
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
    ledger_reviewer_family = launch.get("reviewer_family")
    meta_reviewer_family = meta.get("reviewer_family")
    if (
        ledger_reviewer_family is not None
        and ledger_reviewer_family != meta_reviewer_family
    ):
        raise UnsafeInput("xfamily ledger reviewer family does not match adapter metadata")
    verified_reviewer_family = ledger_reviewer_family or meta_reviewer_family
    if verified_reviewer_family not in {"claude", "grok"}:
        raise UnsafeInput("xfamily reviewer family is absent or invalid")
    try:
        verified = verify_round(
            doc,
            prefix,
            "codex",
            str(verified_reviewer_family),
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


def scoped_max_logical_cycles(doc: Path, observed: dict[Path, str]) -> int:
    """Return the global default or one exact, hash-bound user-authorized exception."""
    resolved = doc.resolve()
    if resolved == SCOPED_FINAL_CYCLE_DOC:
        authority_path = SCOPED_FINAL_CYCLE_AUTHORITY
        expected_digest = SCOPED_FINAL_CYCLE_AUTHORITY_SHA256
        expected = {
            "authority_kind": "exact-document-final-logical-cycle",
            "canonical_document_id": "af61a5fa1b729d66",
            "canonical_document_path": str(SCOPED_FINAL_CYCLE_DOC),
            "effective_max_logical_cycles": 3,
            "global_weighted_launch_ceiling": 36,
            "prior_usage": 28,
        }
    elif resolved == E2A_CHECKPOINT_DOC:
        authority_path = E2A_CHECKPOINT_AUTHORITY
        expected_digest = E2A_CHECKPOINT_AUTHORITY_SHA256
        expected = {
            "active_weighted_launch_ceiling": 18,
            "authority_kind": "exact-document-four-launch-checkpoint",
            "authority_reference_mailbox_seq": 4401,
            "authorized_max_weighted_launch_ceiling": 32,
            "canonical_document_id": "a3be751c394d935f",
            "canonical_document_path": str(E2A_CHECKPOINT_DOC),
            "checkpoint_interval": 4,
            "checkpoint_reviewer": "torque-integrator",
            "effective_max_logical_cycles": 3,
            "prior_document_sha256": "ac42206bbe26dcd0b73a677e31100cb7a23be0b58da39d5dd560f96213cee80e",
            "prior_ledger_sha256": "a448b67a69d52d9a43fc343fc6adfeddb213d60ba98d6a341dcdcd9a8881d6d6",
            "prior_protocol_sha256": "f14f74b38e65ba1c9498faeb8ee939ccecd73bfcaeafd5a67bf4c2218b1d9746",
            "prior_usage": 14,
            "review_required_at_usage": 18,
        }
    else:
        return kernel.MAX_LOGICAL_CYCLES
    authority, digest = stable_json(authority_path)
    observed[authority_path] = digest
    if digest != expected_digest:
        raise UnsafeInput("scoped final-cycle authority digest mismatch")
    for key, value in expected.items():
        if authority.get(key) != value:
            raise UnsafeInput(f"scoped final-cycle authority field mismatch: {key}")
    return int(expected["effective_max_logical_cycles"])


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
    observed: dict[Path, str], absent_paths: set[Path] | tuple[Path, ...] = ()
) -> None:
    for path, expected_digest in observed.items():
        current_digest = hashlib.sha256(stable_bytes(path)).hexdigest()
        if current_digest != expected_digest:
            raise UnsafeInput(f"input changed during evaluation: {path}")
    for path in absent_paths:
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise UnsafeInput(f"cannot inspect absent input {path}: {exc}") from exc
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
    max_logical_cycles = scoped_max_logical_cycles(doc, observed)

    schema_path = Path(__file__).resolve().parent.parent / "schemas" / "finding.schema.json"
    schema, schema_digest = stable_json(schema_path, limit=1024 * 1024)
    observed[schema_path] = schema_digest
    ledger, ledger_path, ledger_digest = load_ledger(doc)
    absent_paths = {ledger_path} if ledger_digest == "no-ledger" else set()
    if ledger_digest != "no-ledger":
        observed[ledger_path] = ledger_digest
    campaigns = ledger["campaigns"]
    used = guard.model_launches(campaigns)
    ceiling, fuse_authority = guard.global_ceiling_policy(doc)
    campaign_ceiling = guard.base_ceiling()
    current_protocol_sha = guard.protocol_sha()
    current_artifact_sha = guard.file_sha(doc)
    guard.enforce_scoped_artifact_sha(fuse_authority, current_artifact_sha)

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
        if (
            launch.get("status") != "success"
            or launch.get("protocol_sha") != current_protocol_sha
        ):
            continue
        launch_sha = str(launch["artifact_sha"])
        if launch_sha not in reviews_by_revision:
            revision_order.append(launch_sha)
        reviews_by_revision[launch_sha].extend(
            launch_reviews(
                launch,
                doc=doc,
                schema=schema,
                observed=observed,
                absent_paths=absent_paths,
            )
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
    active_used = guard.model_launches([active])
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
            if (
                not isinstance(launch, dict)
                or launch.get("status") != "success"
                or launch.get("protocol_sha") != current_protocol_sha
            ):
                continue
            if launch.get("phase") == "fanout":
                pending = str(launch["artifact_sha"])
            elif launch.get("phase") == "xfamily" and pending == launch.get("artifact_sha"):
                completed_cycles.append(pending)
                pending = None

    transition = guard.next_transition(active_launches)
    transition_blocked = (
        transition["kind"] == "transition-blocked"
        and not guard.may_rollover(
            ledger,
            active,
            1,
            "fanout",
            artifact_sha=current_artifact_sha,
            review_protocol_sha=current_protocol_sha,
        )
    )
    if transition_blocked:
        result = blocked_output(
            "DESIGN_RETRY_BUDGET_EXHAUSTED",
            used=used,
            ceiling=ceiling,
            artifact_sha=artifact_sha,
        )
    else:
        def admission_for(phase: str) -> dict[str, object]:
            artifact_changed = bool(
                active_launches
                and isinstance(active_launches[-1], dict)
                and active_launches[-1].get("artifact_sha")
                != current_artifact_sha
            )
            replacement = (
                None
                if artifact_changed
                else guard.replacement_source(active_launches, phase)
            )
            rollover = replacement is None and guard.may_rollover(
                ledger,
                active,
                1,
                phase,
                artifact_sha=current_artifact_sha,
                review_protocol_sha=current_protocol_sha,
            )
            return guard.bounded_admission_decision(
                0 if rollover else active_used,
                campaign_ceiling,
                used,
                ceiling,
                phase,
                launch_weight=0 if replacement is not None else None,
            )

        state = {
            "delta": delta,
            "used": used,
            "ceiling": ceiling,
            "target_sha": artifact_sha,
            "cycles": len(completed_cycles),
            "max_logical_cycles": max_logical_cycles,
            "current_phases": current_phases,
            "admissions": {
                phase: admission_for(phase)
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
