#!/usr/bin/env python3
"""Read-only implementation-review convergence evaluator.

This script never launches reviewers, changes campaign state, or grants PASS. It validates the
current implementation manifest and the existing guarded review history, then reports the next
safe orchestration state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema

sys.dont_write_bytecode = True

import magi_campaign_guard as guard
from magi_git import run_git
from magi_validate_findings import validate as validate_findings
from magi_verify_round import verify_round


BLOCKING = {"REJECT", "CRITICAL", "HIGH"}
SEVERITY_MASS = {"REJECT": 16, "CRITICAL": 8, "HIGH": 4, "MED": 2, "LOW": 1, "nit": 0}
PERSONA_SETS = (
    ("melchior", "balthasar", "caspar"),
    ("hornet", "gnat", "wasp"),
)
MAX_LOGICAL_CYCLES = 2
MAX_JSON_BYTES = 4 * 1024 * 1024


class UnsafeInput(RuntimeError):
    """Input cannot be evaluated safely (exit 2)."""


class UsageError(ValueError):
    """Invalid invocation (exit 64)."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def stable_json(path: Path, *, limit: int = MAX_JSON_BYTES) -> tuple[dict[str, Any], str]:
    try:
        before = path.stat()
        if not path.is_file() or path.is_symlink() or before.st_size > limit:
            raise UnsafeInput(f"unsafe JSON input: {path}")
        raw = path.read_bytes()
        after = path.stat()
    except OSError as exc:
        raise UnsafeInput(f"cannot read {path}: {exc}") from exc
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after:
        raise UnsafeInput(f"input changed while read: {path}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UnsafeInput(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise UnsafeInput(f"JSON input is not an object: {path}")
    return payload, hashlib.sha256(raw).hexdigest()


def git(repo: Path, *args: str) -> str:
    result = run_git(repo, *args)
    if result.returncode != 0:
        raise UnsafeInput(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def git_bytes(repo: Path, *args: str) -> bytes:
    result = run_git(repo, *args, text=False)
    if result.returncode != 0:
        raise UnsafeInput(
            f"git {' '.join(args)} failed: "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    return result.stdout


def validate_packet(payload: dict[str, Any], repo: Path) -> None:
    packet = payload["review_packet"]
    target = payload["target_git_sha"]
    target_tree = git(repo, "rev-parse", f"{target}^{{tree}}")
    if packet["target_tree_sha"] != target_tree:
        raise UnsafeInput("review_packet target_tree_sha does not match target_git_sha")
    diff_bytes = git_bytes(
        repo,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--binary",
        "--full-index",
        payload["base_git_sha"],
        target,
        "--",
        *payload["changed_paths"],
    )
    try:
        diff_text = diff_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsafeInput("review packet diff is not UTF-8") from exc
    if packet["diff"] != diff_text:
        raise UnsafeInput("review_packet diff does not match base_git_sha..target_git_sha")
    if packet["diff_sha256"] != hashlib.sha256(diff_bytes).hexdigest():
        raise UnsafeInput("review_packet diff_sha256 mismatch")


def validate_declared_scope(payload: dict[str, Any], repo: Path) -> None:
    actual_paths = sorted(
        line
        for line in git(
            repo,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-only",
            payload["base_git_sha"],
            payload["target_git_sha"],
            "--",
        ).splitlines()
        if line
    )
    if actual_paths != sorted(payload["changed_paths"]):
        raise UnsafeInput("changed_paths does not exactly cover base_git_sha..target_git_sha")
    if payload["changed_paths"] != sorted(payload["changed_paths"]):
        raise UnsafeInput("changed_paths must be sorted")
    if payload["affected_invariants"] != sorted(payload["affected_invariants"]):
        raise UnsafeInput("affected_invariants must be sorted")
    validate_packet(payload, repo)


def validate_manifest(path: Path) -> tuple[dict[str, Any], str]:
    payload, digest = stable_json(path, limit=1024 * 1024)
    schema_path = Path(__file__).resolve().parent.parent / "schemas" / (
        "implementation-convergence.schema.json"
    )
    schema, _ = stable_json(schema_path, limit=1024 * 1024)
    try:
        jsonschema.validate(payload, schema, format_checker=jsonschema.FormatChecker())
    except jsonschema.ValidationError as exc:
        raise UnsafeInput(f"manifest schema mismatch: {exc.message}") from exc
    if not payload.get("implementation_campaign_id"):
        raise UnsafeInput("current manifest lacks implementation_campaign_id")
    if payload.get("canonical_control_path") != str(path.resolve()):
        raise UnsafeInput("canonical_control_path does not match the manifest realpath")

    repo_input = Path(payload["repository_root"])
    if not repo_input.is_absolute():
        raise UnsafeInput("repository_root must be absolute")
    if repo_input.is_symlink():
        raise UnsafeInput("repository_root must not be a symlink")
    try:
        repo = repo_input.resolve(strict=True)
    except OSError as exc:
        raise UnsafeInput(f"repository_root cannot be resolved: {exc}") from exc
    if repo != repo_input:
        raise UnsafeInput("repository_root must be canonical")
    if git(repo, "rev-parse", "--show-toplevel") != str(repo):
        raise UnsafeInput("repository_root is not the canonical git top-level")
    if git(repo, "rev-parse", "HEAD") != payload["target_git_sha"]:
        raise UnsafeInput("target_git_sha is not the repository HEAD")
    if git(repo, "merge-base", "--is-ancestor", payload["base_git_sha"], payload["target_git_sha"]):
        pass
    validate_declared_scope(payload, repo)
    try:
        deadline = datetime.fromisoformat(payload["wall_clock_deadline"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise UnsafeInput("wall_clock_deadline is not RFC3339") from exc
    if deadline.tzinfo is None:
        raise UnsafeInput("wall_clock_deadline must include a timezone")
    return payload, digest


def historical_target_map(
    manifest: dict[str, Any],
    current_artifact_sha: str,
    successful_artifacts: set[str],
    schema: dict[str, Any],
) -> dict[str, str]:
    repo = Path(manifest["repository_root"])
    mapping = {current_artifact_sha: manifest["target_git_sha"]}
    entries = manifest.get("historical_manifests") or []
    seen: set[str] = set()
    for entry in entries:
        artifact_sha = entry["artifact_sha"]
        if artifact_sha in seen or artifact_sha == current_artifact_sha:
            raise UnsafeInput("historical_manifests contains a duplicate/current artifact")
        seen.add(artifact_sha)
        archive_input = Path(entry["path"])
        if (
            not archive_input.is_absolute()
            or archive_input.is_symlink()
            or archive_input.resolve() != archive_input
        ):
            raise UnsafeInput("historical manifest path must be absolute, canonical, and non-symlink")
        archived, digest = stable_json(archive_input, limit=1024 * 1024)
        if digest != artifact_sha:
            raise UnsafeInput(f"historical manifest digest mismatch: {archive_input}")
        try:
            jsonschema.validate(
                archived, schema, format_checker=jsonschema.FormatChecker()
            )
        except jsonschema.ValidationError as exc:
            raise UnsafeInput(
                f"historical manifest schema mismatch: {archive_input}: {exc.message}"
            ) from exc
        if archived["repository_root"] != manifest["repository_root"]:
            raise UnsafeInput("historical manifest belongs to another repository")
        if archived["base_git_sha"] != manifest["base_git_sha"]:
            raise UnsafeInput("historical manifest uses another base_git_sha")
        if archived.get("scope_id") != manifest["scope_id"]:
            raise UnsafeInput("historical manifest uses another scope_id")
        historical_campaign = archived.get("implementation_campaign_id")
        if (
            historical_campaign is not None
            and historical_campaign != manifest["implementation_campaign_id"]
        ):
            raise UnsafeInput("historical manifest uses another implementation_campaign_id")
        historical_control_path = archived.get("canonical_control_path")
        if (
            historical_control_path is not None
            and historical_control_path != manifest["canonical_control_path"]
        ):
            raise UnsafeInput("historical manifest uses another canonical_control_path")
        validate_declared_scope(archived, repo)
        try:
            git(repo, "merge-base", "--is-ancestor", archived["target_git_sha"], manifest["target_git_sha"])
        except UnsafeInput as exc:
            raise UnsafeInput("historical target is not an ancestor of current target") from exc
        mapping[artifact_sha] = archived["target_git_sha"]
    missing = successful_artifacts - set(mapping)
    if missing:
        raise UnsafeInput(
            f"successful historical manifest archive missing for {sorted(missing)}"
        )
    return mapping


def load_ledger(manifest_path: Path) -> tuple[dict[str, Any], Path, str]:
    manifest_path = manifest_path.resolve()
    ledger = manifest_path.parent / ".dual-magi" / (
        f"CAMPAIGN.{guard.doc_id(manifest_path)}.json"
    )
    if not ledger.is_file():
        return guard.new_ledger(manifest_path), ledger, "no-ledger"
    payload, digest = stable_json(ledger)
    # Reuse the guard's structural validation without acquiring its write-capable lock.
    validated = guard.load_ledger(manifest_path, create=False)
    if validated != payload:
        raise UnsafeInput("ledger normalization would change persisted accounting")
    return payload, ledger, digest


def validate_review(
    path: Path,
    *,
    manifest_path: Path,
    artifact_sha: str,
    round_no: int,
    schema: dict[str, Any],
) -> dict[str, Any]:
    payload, _ = stable_json(path)
    try:
        validate_findings(payload, schema, doc=manifest_path, same_doc_only=True)
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


def validate_review_payload(
    payload: dict[str, Any],
    *,
    manifest_path: Path,
    artifact_sha: str,
    round_no: int,
    schema: dict[str, Any],
) -> dict[str, Any]:
    try:
        validate_findings(payload, schema, doc=manifest_path, same_doc_only=True)
    except (jsonschema.ValidationError, ValueError) as exc:
        raise UnsafeInput(f"invalid verified review artifact: {exc}") from exc
    if payload.get("artifact_sha") != artifact_sha:
        raise UnsafeInput("verified review artifact SHA does not match launch")
    if payload.get("round") != round_no:
        raise UnsafeInput("verified review round does not match launch")
    return payload


def launch_reviews(
    launch: dict[str, Any],
    manifest_path: Path,
    schema: dict[str, Any],
) -> list[dict[str, Any]]:
    state = Path(str(launch["state_dir"]))
    if (
        not state.is_absolute()
        or not state.is_dir()
        or state.is_symlink()
        or state.resolve() != state
    ):
        raise UnsafeInput(f"unsafe launch state_dir: {state}")
    round_no = launch["round"]
    artifact_sha = launch["artifact_sha"]
    if launch["phase"] == "fanout":
        matching_sets = [
            personas
            for personas in PERSONA_SETS
            if all((state / f"round_{round_no}_{persona}.json").is_file() for persona in personas)
        ]
        if len(matching_sets) != 1:
            raise UnsafeInput(f"fanout output set is missing or ambiguous for round {round_no}")
        return [
            validate_review(
                state / f"round_{round_no}_{persona}.json",
                manifest_path=manifest_path,
                artifact_sha=artifact_sha,
                round_no=round_no,
                schema=schema,
            )
            for persona in matching_sets[0]
        ]

    try:
        verified = verify_round(
            manifest_path,
            state / f"round_{round_no}_xfamily",
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
    return [
        validate_review_payload(
            review,
            manifest_path=manifest_path,
            artifact_sha=artifact_sha,
            round_no=round_no,
            schema=schema,
        )
    ]


def normalize_root(finding: dict[str, Any]) -> str:
    explicit = finding.get("root_cause_id")
    if not explicit:
        raise UnsafeInput(
            f"blocking finding lacks stable root_cause_id: {finding.get('finding_id')}"
        )
    return str(explicit)


def cycle_summary(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    roots: dict[str, dict[str, Any]] = {}
    for review in reviews:
        for finding in review.get("findings") or []:
            if not isinstance(finding, dict) or finding.get("severity") not in BLOCKING:
                continue
            root = normalize_root(finding)
            current = roots.get(root)
            if (
                current is not None
                and current.get("subsystem")
                and finding.get("subsystem")
                and current["subsystem"] != finding["subsystem"]
            ):
                raise UnsafeInput(f"root_cause_id {root!r} has contradictory subsystems")
            if current is None or SEVERITY_MASS[finding["severity"]] > SEVERITY_MASS[
                current["severity"]
            ]:
                roots[root] = finding
    return {
        "roots": roots,
        "mass": sum(SEVERITY_MASS[item["severity"]] for item in roots.values()),
        "has_regression": any(
            finding.get("dup_flag") == "regression"
            or finding.get("relation_to_prior") == "fix-induced-regression"
            for finding in roots.values()
        ),
    }


def output(
    decision: str,
    reason_code: str,
    *,
    next_mode: str | None,
    used: int,
    ceiling: int,
    target_sha: str,
    blocker_mass: int,
    cycles: int,
    new_roots: list[str] | None = None,
    resolved_roots: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "mode": "report-only",
        "decision": decision,
        "next_mode": next_mode,
        "reason_code": reason_code,
        "usage": used,
        "ceiling": ceiling,
        "target_git_sha": target_sha,
        "blocker_mass": blocker_mass,
        "logical_cycles": cycles,
        "new_blocking_roots": new_roots or [],
        "resolved_blocking_roots": resolved_roots or [],
        "authorizes_shipping": False,
    }


def evaluate(manifest_path: Path) -> dict[str, Any]:
    manifest_input = manifest_path.expanduser()
    if manifest_input.is_symlink():
        raise UsageError(f"manifest not found or unsafe: {manifest_input}")
    manifest_path = manifest_input.resolve()
    if not manifest_path.is_file():
        raise UsageError(f"manifest not found or unsafe: {manifest_path}")
    manifest, current_artifact_sha = validate_manifest(manifest_path)
    ledger, ledger_path, ledger_sha = load_ledger(manifest_path)
    campaigns = ledger["campaigns"]
    used = guard.model_launches(campaigns)
    ceiling = min(
        guard.GLOBAL_MAX_MODEL_LAUNCHES,
        guard.base_ceiling(),
        int(manifest.get("max_model_launches", guard.DEFAULT_MAX_MODEL_LAUNCHES)),
    )
    deadline = datetime.fromisoformat(
        manifest["wall_clock_deadline"].replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    deadline_expired = deadline <= datetime.now(timezone.utc)
    schema, _ = stable_json(
        Path(__file__).resolve().parent.parent / "schemas" / "finding.schema.json"
    )
    manifest_schema, _ = stable_json(
        Path(__file__).resolve().parent.parent
        / "schemas"
        / "implementation-convergence.schema.json"
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
        if launch.get("status") == "running":
            return output(
                "BLOCKED",
                "LAUNCH_STILL_RUNNING",
                next_mode=None,
                used=used,
                ceiling=ceiling,
                target_sha=manifest["target_git_sha"],
                blocker_mass=0,
                cycles=0,
            )

    active = guard.active_campaign(ledger)
    active_launches = active["launches"]
    assert isinstance(active_launches, list)
    current_protocol_sha = guard.protocol_sha()
    transition = guard.next_transition(active_launches)
    transition_blocked = transition["kind"] == "transition-blocked" and not guard.may_rollover(
        ledger, active, manifest_path, 1, "fanout"
    )

    successful_artifacts = {
        str(launch["artifact_sha"])
        for launch in launches
        if launch.get("status") == "success"
    }
    artifact_targets = historical_target_map(
        manifest,
        current_artifact_sha,
        successful_artifacts,
        manifest_schema,
    )
    reviews_by_revision: dict[str, list[dict[str, Any]]] = defaultdict(list)
    revision_order: list[str] = []
    for launch in launches:
        if launch.get("status") != "success":
            continue
        artifact_sha = str(launch.get("artifact_sha"))
        target_sha = artifact_targets[artifact_sha]
        if target_sha not in reviews_by_revision:
            if revision_order:
                try:
                    git(
                        Path(manifest["repository_root"]),
                        "merge-base",
                        "--is-ancestor",
                        revision_order[-1],
                        target_sha,
                    )
                except UnsafeInput as exc:
                    raise UnsafeInput(
                        "review target history is not an ordered ancestry chain"
                    ) from exc
            revision_order.append(target_sha)
        reviews_by_revision[target_sha].extend(
            launch_reviews(launch, manifest_path, schema)
        )

    current_phases = {
        str(launch["phase"])
        for launch in active_launches
        if isinstance(launch, dict)
        and launch.get("status") == "success"
        and launch.get("artifact_sha") == current_artifact_sha
        and launch.get("protocol_sha") == current_protocol_sha
    }
    completed_cycle_targets: list[str] = []
    for campaign in campaigns:
        if not isinstance(campaign, dict):
            continue
        pending_artifact: str | None = None
        for launch in campaign.get("launches", []):
            if not isinstance(launch, dict) or launch.get("status") != "success":
                continue
            artifact_sha = str(launch["artifact_sha"])
            if launch.get("phase") == "fanout":
                pending_artifact = artifact_sha
            elif pending_artifact == artifact_sha:
                completed_cycle_targets.append(artifact_targets[artifact_sha])
                pending_artifact = None

    summaries = {
        target_sha: cycle_summary(reviews_by_revision[target_sha])
        for target_sha in revision_order
    }
    current_target_sha = manifest["target_git_sha"]
    current_summary = summaries.get(
        current_target_sha, {"roots": {}, "mass": 0, "has_regression": False}
    )
    current_roots = set(current_summary["roots"])
    previous_roots: set[str] = set()
    previous_summary: dict[str, Any] | None = None
    if current_target_sha in revision_order:
        current_index = revision_order.index(current_target_sha)
        if current_index > 0:
            previous_summary = summaries[revision_order[current_index - 1]]
            previous_roots = set(previous_summary["roots"])
    new_roots = sorted(current_roots - previous_roots)
    resolved_roots = sorted(previous_roots - current_roots)
    cycles = len(completed_cycle_targets)
    previous_new_subsystems: set[str] = set()
    current_new_subsystems = {
        str(current_summary["roots"][root].get("subsystem"))
        for root in new_roots
        if current_summary["roots"][root].get("subsystem")
    }
    if previous_summary is not None:
        previous_index = revision_order.index(current_target_sha) - 1
        roots_before_previous: set[str] = set()
        if previous_index > 0:
            roots_before_previous = set(
                summaries[revision_order[previous_index - 1]]["roots"]
            )
        previous_new_roots = set(previous_summary["roots"]) - roots_before_previous
        previous_new_subsystems = {
            str(previous_summary["roots"][root].get("subsystem"))
            for root in previous_new_roots
            if previous_summary["roots"][root].get("subsystem")
        }

    if deadline_expired:
        decision = output(
            "BLOCKED",
            "WALL_CLOCK_DEADLINE_EXPIRED",
            next_mode=None,
            used=used,
            ceiling=ceiling,
            target_sha=manifest["target_git_sha"],
            blocker_mass=current_summary["mass"],
            cycles=cycles,
            new_roots=new_roots,
            resolved_roots=resolved_roots,
        )
    elif any(
        finding.get("changes_design_invariant") is True
        for finding in current_summary["roots"].values()
    ):
        decision = output(
            "REDESIGN",
            "DESIGN_INVARIANT_CHANGED",
            next_mode=None,
            used=used,
            ceiling=ceiling,
            target_sha=manifest["target_git_sha"],
            blocker_mass=current_summary["mass"],
            cycles=cycles,
            new_roots=new_roots,
            resolved_roots=resolved_roots,
        )
    elif previous_summary and previous_summary["has_regression"] and current_summary["has_regression"]:
        decision = output(
            "REDESIGN",
            "CONSECUTIVE_FIX_INDUCED_REGRESSIONS",
            next_mode=None,
            used=used,
            ceiling=ceiling,
            target_sha=manifest["target_git_sha"],
            blocker_mass=current_summary["mass"],
            cycles=cycles,
            new_roots=new_roots,
            resolved_roots=resolved_roots,
        )
    elif current_roots & previous_roots:
        decision = output(
            "REDESIGN",
            "BLOCKING_ROOT_REPEATED",
            next_mode=None,
            used=used,
            ceiling=ceiling,
            target_sha=manifest["target_git_sha"],
            blocker_mass=current_summary["mass"],
            cycles=cycles,
            new_roots=new_roots,
            resolved_roots=resolved_roots,
        )
    elif current_new_subsystems & previous_new_subsystems:
        decision = output(
            "REDESIGN",
            "SAME_SUBSYSTEM_NEW_ROOTS_RECURRED",
            next_mode=None,
            used=used,
            ceiling=ceiling,
            target_sha=manifest["target_git_sha"],
            blocker_mass=current_summary["mass"],
            cycles=cycles,
            new_roots=new_roots,
            resolved_roots=resolved_roots,
        )
    elif transition_blocked:
        decision = output(
            "BLOCKED",
            "RETRY_BUDGET_EXHAUSTED",
            next_mode=None,
            used=used,
            ceiling=ceiling,
            target_sha=manifest["target_git_sha"],
            blocker_mass=current_summary["mass"],
            cycles=cycles,
            new_roots=new_roots,
            resolved_roots=resolved_roots,
        )
    elif cycles >= MAX_LOGICAL_CYCLES and "xfamily" not in current_phases:
        decision = output(
            "BLOCKED",
            "MAX_LOGICAL_CYCLES_REACHED",
            next_mode=None,
            used=used,
            ceiling=ceiling,
            target_sha=manifest["target_git_sha"],
            blocker_mass=current_summary["mass"],
            cycles=cycles,
            new_roots=new_roots,
            resolved_roots=resolved_roots,
        )
    elif not current_phases or "fanout" not in current_phases:
        admission = guard.admission_decision(used, ceiling, "fanout")
        decision = output(
            "CONTINUE" if admission["affordable"] else "BLOCKED",
            "INITIAL_FULL_REQUIRED" if admission["affordable"] else "NEXT_FANOUT_UNAFFORDABLE",
            next_mode="initial-full" if admission["affordable"] else None,
            used=used,
            ceiling=ceiling,
            target_sha=manifest["target_git_sha"],
            blocker_mass=0,
            cycles=cycles,
        )
    elif "xfamily" not in current_phases:
        admission = guard.admission_decision(used, ceiling, "xfamily")
        decision = output(
            "FINAL_REVIEW_REQUIRED" if admission["affordable"] else "BLOCKED",
            "FINAL_DIVERSE_RECHECK_REQUIRED"
            if admission["affordable"]
            else "FINAL_DIVERSE_RECHECK_UNAFFORDABLE",
            next_mode="final-full" if admission["affordable"] else None,
            used=used,
            ceiling=ceiling,
            target_sha=manifest["target_git_sha"],
            blocker_mass=current_summary["mass"],
            cycles=cycles,
            new_roots=new_roots,
            resolved_roots=resolved_roots,
        )
    elif not current_roots:
        decision = output(
            "BLOCKED",
            "REPORT_ONLY_READY_FOR_EXISTING_PLATEAU_GATE",
            next_mode=None,
            used=used,
            ceiling=ceiling,
            target_sha=manifest["target_git_sha"],
            blocker_mass=0,
            cycles=cycles,
            resolved_roots=resolved_roots,
        )
    elif (
        len(revision_order) >= 3
        and summaries[revision_order[-2]]["mass"] >= summaries[revision_order[-3]]["mass"]
        and summaries[revision_order[-1]]["mass"] >= summaries[revision_order[-2]]["mass"]
    ):
        decision = output(
            "BLOCKED",
            "BLOCKER_MASS_STALLED",
            next_mode=None,
            used=used,
            ceiling=ceiling,
            target_sha=manifest["target_git_sha"],
            blocker_mass=current_summary["mass"],
            cycles=cycles,
            new_roots=new_roots,
            resolved_roots=resolved_roots,
        )
    elif cycles >= MAX_LOGICAL_CYCLES:
        decision = output(
            "BLOCKED",
            "MAX_LOGICAL_CYCLES_WITH_BLOCKERS",
            next_mode=None,
            used=used,
            ceiling=ceiling,
            target_sha=manifest["target_git_sha"],
            blocker_mass=current_summary["mass"],
            cycles=cycles,
            new_roots=new_roots,
            resolved_roots=resolved_roots,
        )
    else:
        admission = guard.admission_decision(used, ceiling, "fanout")
        decision = output(
            "CONTINUE" if admission["affordable"] else "BLOCKED",
            "FULL_TARGET_FIX_REQUIRED"
            if admission["affordable"]
            else "NEXT_FANOUT_UNAFFORDABLE",
            next_mode="full-target-fix" if admission["affordable"] else None,
            used=used,
            ceiling=ceiling,
            target_sha=manifest["target_git_sha"],
            blocker_mass=current_summary["mass"],
            cycles=cycles,
            new_roots=new_roots,
            resolved_roots=resolved_roots,
        )

    if ledger_path.is_file() and sha256(ledger_path) != ledger_sha:
        raise UnsafeInput("ledger changed during evaluation")
    if sha256(manifest_path) != current_artifact_sha:
        raise UnsafeInput("manifest changed during evaluation")
    return decision


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    evaluate_parser = commands.add_parser("evaluate")
    evaluate_parser.add_argument("manifest")
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        result = evaluate(Path(args.manifest))
    except (UsageError, guard.UsageError) as exc:
        print(f"MAGI_CONVERGENCE_USAGE: {exc}", file=sys.stderr)
        return 64
    except (UnsafeInput, guard.StateError, guard.TransitionError) as exc:
        print(
            json.dumps(
                {
                    "mode": "report-only",
                    "decision": "BLOCKED",
                    "reason_code": "UNSAFE_OR_INCOMPLETE_INPUT",
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
