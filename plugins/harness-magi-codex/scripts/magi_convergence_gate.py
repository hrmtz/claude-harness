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
import magi_convergence_kernel as kernel
from magi_git import run_git
from magi_validate_findings import validate as validate_findings
from magi_verify_round import verify_round


PERSONA_SETS = (
    ("melchior", "balthasar", "caspar"),
    ("hornet", "gnat", "wasp"),
)
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_INCREMENTAL_PATHS = 8
MAX_INCREMENTAL_LOC = 200


class UnsafeInput(RuntimeError):
    """Input cannot be evaluated safely (exit 2)."""


class UsageError(ValueError):
    """Invalid invocation (exit 64)."""


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise UsageError(message)


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
    review_base = payload.get("review_base_git_sha", payload["base_git_sha"])
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
        review_base,
        target,
        "--",
        *payload["changed_paths"],
    )
    try:
        diff_text = diff_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsafeInput("review packet diff is not UTF-8") from exc
    if packet["diff"] != diff_text:
        raise UnsafeInput("review_packet diff does not match review_base_git_sha..target_git_sha")
    if packet["diff_sha256"] != hashlib.sha256(diff_bytes).hexdigest():
        raise UnsafeInput("review_packet diff_sha256 mismatch")


def validate_declared_scope(payload: dict[str, Any], repo: Path) -> None:
    review_base = payload.get("review_base_git_sha", payload["base_git_sha"])
    actual_paths = sorted(
        line
        for line in git(
            repo,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-only",
            review_base,
            payload["target_git_sha"],
            "--",
        ).splitlines()
        if line
    )
    if actual_paths != sorted(payload["changed_paths"]):
        raise UnsafeInput("changed_paths does not exactly cover review_base_git_sha..target_git_sha")
    if payload["changed_paths"] != sorted(payload["changed_paths"]):
        raise UnsafeInput("changed_paths must be sorted")
    if payload["affected_invariants"] != sorted(payload["affected_invariants"]):
        raise UnsafeInput("affected_invariants must be sorted")
    validate_packet(payload, repo)


def diff_changed_loc(payload: dict[str, Any], repo: Path) -> int:
    review_base = str(payload.get("review_base_git_sha", payload["base_git_sha"]))
    result = git(
        repo,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--numstat",
        review_base,
        str(payload["target_git_sha"]),
        "--",
        *payload["changed_paths"],
    )
    total = 0
    for line in result.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            raise UnsafeInput("malformed git numstat for incremental review")
        if parts[0] == "-" or parts[1] == "-":
            return 1_000_000
        try:
            total += int(parts[0]) + int(parts[1])
        except ValueError as exc:
            raise UnsafeInput("non-numeric git numstat for incremental review") from exc
    return total


def incremental_eligible(payload: dict[str, Any], repo: Path) -> bool:
    policy = payload.get("incremental_review")
    if not isinstance(policy, dict) or policy.get("eligible") is not True:
        return False
    surfaces = policy.get("surface_changes")
    if not isinstance(surfaces, dict):
        raise UnsafeInput("incremental_review surface_changes is malformed")
    changed_loc = diff_changed_loc(payload, repo)
    if policy.get("changed_loc") != changed_loc:
        raise UnsafeInput("incremental_review changed_loc does not match exact diff")
    eligible = (
        payload.get("risk_class") == "standard"
        and bool(payload.get("historical_manifests"))
        and payload.get("review_base_git_sha") != payload.get("base_git_sha")
        and len(payload["changed_paths"]) <= MAX_INCREMENTAL_PATHS
        and changed_loc <= MAX_INCREMENTAL_LOC
        and not any(surfaces.values())
    )
    if not eligible:
        raise UnsafeInput("incremental_review eligibility contradicts safety rails")
    return True


def targeted_persona(payload: dict[str, Any]) -> str:
    text = " ".join(str(item).lower() for item in payload["affected_invariants"])
    if any(token in text for token in ("race", "lock", "concurr", "signal", "process")):
        return "hornet"
    if any(token in text for token in ("error", "fail", "retry", "rollback")):
        return "wasp"
    return "gnat"


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
    if launch["phase"] == "targeted":
        review = validate_review(
            state / f"round_{round_no}_targeted.json",
            manifest_path=manifest_path,
            artifact_sha=artifact_sha,
            round_no=round_no,
            schema=schema,
        )
        manifest, _ = stable_json(manifest_path, limit=1024 * 1024)
        expected = targeted_persona(manifest)
        if str(review.get("reviewer", "")).lower() != expected:
            raise UnsafeInput(
                f"targeted reviewer mismatch: expected {expected}, "
                f"got {review.get('reviewer')!r}"
            )
        return [review]

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


def cycle_summary(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        return kernel.summarize_revision(reviews)
    except kernel.KernelInputError as exc:
        raise UnsafeInput(str(exc)) from exc


def evaluate(manifest_path: Path) -> dict[str, Any]:
    manifest_input = manifest_path.expanduser()
    if manifest_input.is_symlink():
        raise UsageError(f"manifest not found or unsafe: {manifest_input}")
    manifest_path = manifest_input.resolve()
    if not manifest_path.is_file():
        raise UsageError(f"manifest not found or unsafe: {manifest_path}")
    manifest, current_artifact_sha = validate_manifest(manifest_path)
    repo = Path(manifest["repository_root"])
    incremental_allowed = incremental_eligible(manifest, repo)
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
    if launches and launches[-1].get("status") == "superseded-by-requirement-revision":
        # A changed requirement invalidates the prior review scope, not merely its fix diff.
        incremental_allowed = False
    for launch in launches:
        phase = launch.get("phase")
        if phase not in guard.PHASE_WEIGHT:
            raise UnsafeInput("ledger contains an invalid phase")
        if launch.get("model_launches") != guard.PHASE_WEIGHT[phase]:
            raise UnsafeInput("ledger contains an invalid phase weight")
        if launch.get("status") in guard.NONTERMINAL_STATUSES:
            reason_code = (
                "REQUIREMENT_REVISION_CANCELLATION_IN_PROGRESS"
                if launch.get("status") == "cancellation_in_progress"
                else "LAUNCH_STILL_RUNNING"
            )
            return kernel.output(
                "BLOCKED",
                reason_code,
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

    if incremental_allowed:
        current_target = str(manifest["target_git_sha"])
        if current_target in revision_order:
            current_index = revision_order.index(current_target)
            expected_review_base = (
                revision_order[current_index - 1] if current_index > 0 else None
            )
        else:
            expected_review_base = revision_order[-1] if revision_order else None
        if expected_review_base is None:
            incremental_allowed = False
        elif manifest.get("review_base_git_sha") != expected_review_base:
            raise UnsafeInput(
                "incremental review_base_git_sha is not the immediately preceding "
                "successfully reviewed target"
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
            if launch.get("phase") in {"fanout", "targeted"}:
                pending_artifact = artifact_sha
            elif pending_artifact == artifact_sha:
                completed_cycle_targets.append(artifact_targets[artifact_sha])
                pending_artifact = None

    summaries = {
        target_sha: cycle_summary(reviews_by_revision[target_sha])
        for target_sha in revision_order
    }
    current_target_sha = manifest["target_git_sha"]
    cycles = len(completed_cycle_targets)
    delta = kernel.revision_delta(revision_order, summaries, current_target_sha)
    design_invariant_changed = (
        isinstance(manifest.get("incremental_review"), dict)
        and manifest["incremental_review"].get("surface_changes", {}).get(
            "design_invariant"
        )
    ) or any(
        finding.get("changes_design_invariant") is True
        for finding in delta["current_summary"]["roots"].values()
    )
    state = {
        "used": used,
        "ceiling": ceiling,
        "target_sha": current_target_sha,
        "cycles": cycles,
        "current_phases": current_phases,
        "delta": delta,
        "deadline_expired": deadline_expired,
        "design_invariant_changed": design_invariant_changed,
        "transition_blocked": transition_blocked,
        "incremental_allowed": incremental_allowed,
        "targeted_persona": targeted_persona(manifest),
        "admissions": {
            phase: guard.admission_decision(used, ceiling, phase)
            for phase in ("fanout", "targeted", "xfamily")
        },
    }
    decision = kernel.evaluate_profile("ultramagi-implementation", state)

    if ledger_path.is_file() and sha256(ledger_path) != ledger_sha:
        raise UnsafeInput("ledger changed during evaluation")
    if sha256(manifest_path) != current_artifact_sha:
        raise UnsafeInput("manifest changed during evaluation")
    return decision


def parser() -> Parser:
    root = Parser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    evaluate_parser = commands.add_parser("evaluate")
    evaluate_parser.add_argument("manifest")
    return root


def main() -> int:
    try:
        args = parser().parse_args()
        result = evaluate(Path(args.manifest))
    except (UsageError, guard.UsageError) as exc:
        print(f"MAGI_CONVERGENCE_USAGE: {exc}", file=sys.stderr)
        return 64
    except (
        UnsafeInput,
        guard.StateError,
        guard.TransitionError,
        OSError,
        RuntimeError,
    ) as exc:
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
