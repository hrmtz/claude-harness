#!/usr/bin/env python3
"""Pure evidence predicates used by babysit-pr and the integration audit."""

from __future__ import annotations

import pathlib
import re
from collections.abc import Iterable, Mapping

PASS_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
PENDING_STATUSES = {"PENDING", "QUEUED", "IN_PROGRESS", "WAITING", "EXPECTED"}
FAIL_STATUSES = {"FAILURE", "ERROR"}
MARKER_RE = re.compile(
    r"^Independent review verdict: \*\*(BLOCK|PASS)\*\* @ ([0-9a-f]{40})$",
    re.MULTILINE,
)


def classify_checks(pr: Mapping[str, object]) -> str:
    """Return PASS, PENDING, FAILED_OR_UNKNOWN, or UNKNOWN for a PR check-set."""
    checks = pr.get("statusCheckRollup") or []
    if not isinstance(checks, list) or not checks:
        return "UNKNOWN"
    bad = pending = unknown = 0
    for check in checks:
        if not isinstance(check, Mapping):
            unknown += 1
            continue
        conclusion = str(check.get("conclusion") or "").upper()
        status = str(check.get("status") or check.get("state") or "").upper()
        if (conclusion and conclusion not in PASS_CONCLUSIONS) or (
            not conclusion and status in FAIL_STATUSES
        ):
            bad += 1
        elif not conclusion and status == "SUCCESS":
            continue
        elif not conclusion and status in PENDING_STATUSES:
            pending += 1
        elif not conclusion:
            unknown += 1
    if bad or unknown:
        return "FAILED_OR_UNKNOWN"
    if pending:
        return "PENDING"
    return "PASS"


def _comment_body(comment: Mapping[str, object]) -> str:
    return str(comment.get("body") or "")


def _comment_time(comment: Mapping[str, object]) -> str:
    # ISO-8601 GitHub timestamps sort lexicographically. id is a deterministic
    # tie-breaker for synthetic fixtures with identical timestamps.
    return f"{comment.get('createdAt') or ''}\0{comment.get('id') or ''}"


def classify_review(pr: Mapping[str, object]) -> str:
    """Return PASS, BLOCK, or REVIEW_UNRECORDED for the current head OID."""
    head = str(pr.get("headRefOid") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        return "REVIEW_UNRECORDED"
    markers: list[tuple[str, str]] = []
    comments = pr.get("comments") or []
    if not isinstance(comments, list):
        return "REVIEW_UNRECORDED"
    for comment in comments:
        if not isinstance(comment, Mapping):
            continue
        match = MARKER_RE.search(_comment_body(comment))
        if match and match.group(2) == head:
            markers.append((_comment_time(comment), match.group(1)))
    if not markers:
        return "REVIEW_UNRECORDED"
    return max(markers)[1]


def is_green(pr: Mapping[str, object]) -> bool:
    """Strict composite predicate; state is interpreted before mergeability."""
    state = str(pr.get("state") or "").upper()
    if state != "OPEN":
        return False
    if str(pr.get("mergeable") or "").upper() != "MERGEABLE":
        return False
    merge_state = str(pr.get("mergeStateStatus") or "").upper()
    if merge_state not in {"CLEAN", "HAS_HOOKS", "UNSTABLE"}:
        return False
    return classify_checks(pr) == "PASS" and classify_review(pr) == "PASS"


def lineage_is_owned(
    anchor: str, head: str, anchor_to_head: Iterable[str], in_run_pushes: Iterable[str]
) -> bool:
    """Accept only the creation anchor plus commits pushed by this invocation."""
    if not re.fullmatch(r"[0-9a-f]{40}", anchor):
        return False
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        return False
    commits = list(anchor_to_head)
    if any(not re.fullmatch(r"[0-9a-f]{40}", oid) for oid in commits):
        return False
    if head == anchor:
        return not commits
    allowed = set(in_run_pushes)
    return bool(commits) and head in commits and all(oid in allowed for oid in commits)


def _workflow_run_paths(text: str) -> set[str]:
    """Extract repo-local executables named by workflow run blocks."""
    paths: set[str] = set()
    for token in re.findall(
        r"(?:^|[\s\"'])(\.?/?(?:plugins|scripts)/[A-Za-z0-9_./-]+)",
        text,
        re.MULTILINE,
    ):
        paths.add(token.removeprefix("./").rstrip("\\"))
    return paths


def ci_deny_paths(repo_root: pathlib.Path) -> tuple[set[str], set[str]]:
    """Return exact files and directory prefixes that a repair commit may not edit."""
    exact: set[str] = set()
    prefixes = {"tests/", "migrations/", "creds-migration/"}
    workflows = repo_root / ".github" / "workflows"
    if not workflows.is_dir():
        return exact, prefixes
    for workflow in sorted(workflows.glob("*.y*ml")):
        rel = workflow.relative_to(repo_root).as_posix()
        exact.add(rel)
        text = workflow.read_text(encoding="utf-8")
        exact.update(_workflow_run_paths(text))
        for local_action in re.findall(r"uses:\s*[\"']?\./([^\"'\s]+)", text):
            action = local_action.rstrip("/")
            candidate = repo_root / action
            if candidate.is_dir():
                prefixes.add(action + "/")
            else:
                exact.add(action)
    return exact, prefixes


def repairable_path(
    path: str, *, exact_deny: Iterable[str], prefix_deny: Iterable[str]
) -> bool:
    """Allow plugin source only; workflow paths allowlists are intentionally irrelevant."""
    clean = pathlib.PurePosixPath(path).as_posix().lstrip("/")
    name = pathlib.PurePosixPath(clean).name
    if clean in set(exact_deny):
        return False
    if any(clean.startswith(prefix) for prefix in prefix_deny):
        return False
    if name.startswith("test_") or ".enc." in name:
        return False
    return clean.startswith("plugins/")
