#!/usr/bin/env python3
"""Build an exact-Git-SHA implementation review packet at a stable manifest path."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import jsonschema

from magi_git import run_git


def git(repo: Path, *args: str, text: bool = True) -> str | bytes:
    result = run_git(repo, *args, text=text)
    if result.returncode != 0:
        stderr = result.stderr if text else result.stderr.decode(errors="replace")
        raise ValueError(f"git {' '.join(args)} failed: {stderr.strip()}")
    return result.stdout.strip() if text else result.stdout


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def read_previous(output: Path) -> tuple[bytes | None, dict[str, Any] | None]:
    if not output.exists():
        return None, None
    if output.is_symlink() or not output.is_file():
        raise ValueError("existing output is not a safe regular file")
    raw = output.read_bytes()
    try:
        previous = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("existing output is not a valid review packet") from exc
    if not isinstance(previous, dict):
        raise ValueError("existing output is not a review packet object")
    return raw, previous


def archive_previous(
    output: Path,
    raw: bytes,
    previous: dict[str, Any],
) -> list[dict[str, str]]:
    digest = hashlib.sha256(raw).hexdigest()
    archive_dir = output.parent / ".dual-magi" / "manifests"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive = archive_dir / f"{digest}.json"
    if archive.exists():
        if archive.read_bytes() != raw:
            raise ValueError("historical manifest digest collision")
    else:
        fd = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
    history = previous.get("historical_manifests") or []
    if not isinstance(history, list):
        raise ValueError("existing historical_manifests is malformed")
    return [
        *history,
        {"path": str(archive.resolve()), "artifact_sha": digest},
    ]


def exact_diff(repo: Path, base: str, target: str) -> tuple[list[str], bytes, int]:
    changed_paths = sorted(
        line
        for line in str(
            git(
                repo,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--name-only",
                base,
                target,
                "--",
            )
        ).splitlines()
        if line
    )
    if not changed_paths:
        raise ValueError("review base..target has no changed paths")
    diff = git(
        repo,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--binary",
        "--full-index",
        base,
        target,
        "--",
        *changed_paths,
        text=False,
    )
    assert isinstance(diff, bytes)
    if len(diff) > 900000:
        raise ValueError("exact-SHA review packet diff exceeds 900000 bytes")
    numstat = str(
        git(
            repo,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--numstat",
            base,
            target,
            "--",
            *changed_paths,
        )
    )
    changed_loc = 0
    for line in numstat.splitlines():
        added, deleted, *_ = line.split("\t", 2)
        if added == "-" or deleted == "-":
            changed_loc = 1_000_000
            break
        changed_loc += int(added) + int(deleted)
    return changed_paths, diff, changed_loc


def build(args: argparse.Namespace) -> dict[str, Any]:
    repo_input = Path(args.repo).expanduser()
    if not repo_input.is_absolute() or repo_input.is_symlink():
        raise ValueError("--repo must be an absolute non-symlink path")
    repo = repo_input.resolve(strict=True)
    if repo != repo_input or git(repo, "rev-parse", "--show-toplevel") != str(repo):
        raise ValueError("--repo must be the canonical git top-level")
    target = args.target or git(repo, "rev-parse", "HEAD")
    if git(repo, "rev-parse", target) != target:
        raise ValueError("--target must be a full commit SHA")
    git(repo, "merge-base", "--is-ancestor", args.base, target)
    deadline = datetime.fromisoformat(args.deadline.replace("Z", "+00:00"))
    if deadline.tzinfo is None:
        raise ValueError("--deadline must include a timezone")
    output = Path(args.output).expanduser()
    if not output.is_absolute() or output.is_symlink():
        raise ValueError("--output must be an absolute non-symlink path")
    canonical_output = output.parent.resolve(strict=True) / output.name
    if canonical_output != output:
        raise ValueError("--output must be canonical")
    previous_raw, previous = read_previous(output)
    surface_changes = {
        name: name in set(args.surface_change)
        for name in (
            "public_interface",
            "trust_boundary",
            "persistence_schema_rollback",
            "design_invariant",
        )
    }
    incremental_candidate = (
        args.allow_incremental
        and previous is not None
        and isinstance(previous.get("target_git_sha"), str)
        and args.risk_class == "standard"
        and not any(surface_changes.values())
    )
    review_base = (
        str(previous["target_git_sha"]) if incremental_candidate else args.base
    )
    git(repo, "merge-base", "--is-ancestor", review_base, target)
    changed_paths, diff, changed_loc = exact_diff(repo, review_base, target)
    incremental_eligible = (
        incremental_candidate
        and len(changed_paths) <= 8
        and changed_loc <= 200
    )
    if review_base != args.base and not incremental_eligible:
        review_base = args.base
        changed_paths, diff, changed_loc = exact_diff(repo, review_base, target)
    try:
        diff_text = diff.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("exact-SHA review packet diff is not UTF-8") from exc
    invariants = sorted(set(args.invariant))
    if previous is None:
        campaign_id = str(uuid.uuid4())
    else:
        for field, expected in (
            ("repository_root", str(repo)),
            ("base_git_sha", args.base),
            ("scope_id", args.scope),
            ("risk_class", args.risk_class),
            ("affected_invariants", invariants),
        ):
            if previous.get(field) != expected:
                raise ValueError(f"existing packet {field} is immutable")
        previous_path = previous.get("canonical_control_path")
        if previous_path is not None and previous_path != str(canonical_output):
            raise ValueError("existing packet canonical_control_path mismatch")
        previous_campaign = previous.get("implementation_campaign_id")
        campaign_id = (
            previous_campaign if isinstance(previous_campaign, str) else str(uuid.uuid4())
        )
    history = (
        archive_previous(output, previous_raw, previous)
        if previous_raw is not None and previous is not None
        else []
    )
    payload: dict[str, Any] = {
        "schema": "magi-implementation-convergence/v1",
        "scope_id": args.scope,
        "implementation_campaign_id": campaign_id,
        "canonical_control_path": str(canonical_output),
        "risk_class": args.risk_class,
        "repository_root": str(repo),
        "target_git_sha": target,
        "base_git_sha": args.base,
        "review_base_git_sha": review_base,
        "changed_paths": changed_paths,
        "affected_invariants": invariants,
        "incremental_review": {
            "eligible": incremental_eligible,
            "changed_loc": changed_loc,
            "surface_changes": surface_changes,
        },
        "review_packet": {
            "target_tree_sha": git(repo, "rev-parse", f"{target}^{{tree}}"),
            "diff_sha256": hashlib.sha256(diff).hexdigest(),
            "diff": diff_text,
        },
        "wall_clock_deadline": args.deadline,
    }
    if history:
        payload["historical_manifests"] = history
    if args.max_model_launches is not None:
        payload["max_model_launches"] = args.max_model_launches
    schema_path = Path(__file__).resolve().parent.parent / "schemas" / (
        "implementation-convergence.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(payload, schema, format_checker=jsonschema.FormatChecker())
    return payload


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--repo", required=True)
    root.add_argument("--base", required=True)
    root.add_argument("--target")
    root.add_argument("--scope", required=True)
    root.add_argument(
        "--risk-class",
        default="standard",
        choices=("standard", "canonical-migration", "data-loss", "security", "irreversible"),
    )
    root.add_argument("--invariant", action="append", required=True)
    root.add_argument("--deadline", required=True)
    root.add_argument("--max-model-launches", type=int)
    root.add_argument(
        "--allow-incremental",
        action="store_true",
        help="allow a bounded weight-1 fix review when all mechanical safety rails pass",
    )
    root.add_argument(
        "--surface-change",
        action="append",
        default=[],
        choices=(
            "public_interface",
            "trust_boundary",
            "persistence_schema_rollback",
            "design_invariant",
        ),
        help="declare a fix surface that mechanically forces full review or redesign",
    )
    root.add_argument("--output", required=True)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        payload = build(args)
        output = Path(args.output).expanduser()
        atomic_write(output, (json.dumps(payload, indent=2) + "\n").encode())
    except (OSError, ValueError, jsonschema.ValidationError) as exc:
        print(f"MAGI_REVIEW_PACKET_ERROR: {exc}", file=sys.stderr)
        return 2
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
