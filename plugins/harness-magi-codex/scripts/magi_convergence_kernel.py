#!/usr/bin/env python3
"""Pure, report-only convergence primitives and profile policies.

This module performs no filesystem, clock, process, provider, or ledger access. Callers must
validate and bind evidence before projecting it into these functions.
"""

from __future__ import annotations

from typing import Any


BLOCKING = frozenset({"REJECT", "CRITICAL", "HIGH"})
SEVERITY_MASS = {
    "REJECT": 16,
    "CRITICAL": 8,
    "HIGH": 4,
    "MED": 2,
    "LOW": 1,
    "nit": 0,
}
MAX_LOGICAL_CYCLES = 2


class KernelInputError(ValueError):
    """Validated evidence cannot be normalized consistently."""


def normalize_root(finding: dict[str, Any]) -> str:
    explicit = finding.get("root_cause_id")
    if not explicit:
        raise KernelInputError(
            f"blocking finding lacks stable root_cause_id: {finding.get('finding_id')}"
        )
    return str(explicit)


def summarize_revision(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    """Collapse validated reviews into unique blocking roots at maximum severity."""
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
                raise KernelInputError(
                    f"root_cause_id {root!r} has contradictory subsystems"
                )
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


def revision_delta(
    revision_order: list[str],
    summaries: dict[str, dict[str, Any]],
    current_target_sha: str,
) -> dict[str, Any]:
    """Project the current, previous, and pre-previous revision relationship."""
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
    elif revision_order:
        previous_summary = summaries[revision_order[-1]]
        previous_roots = set(previous_summary["roots"])

    new_roots = sorted(current_roots - previous_roots)
    resolved_roots = sorted(previous_roots - current_roots)
    previous_new_subsystems: set[str] = set()
    current_new_subsystems = {
        str(current_summary["roots"][root].get("subsystem"))
        for root in new_roots
        if current_summary["roots"][root].get("subsystem")
    }
    if previous_summary is not None:
        previous_index = (
            revision_order.index(current_target_sha) - 1
            if current_target_sha in revision_order
            else len(revision_order) - 1
        )
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

    mass_stalled = (
        len(revision_order) >= 3
        and summaries[revision_order[-2]]["mass"]
        >= summaries[revision_order[-3]]["mass"]
        and summaries[revision_order[-1]]["mass"]
        >= summaries[revision_order[-2]]["mass"]
    )
    return {
        "current_summary": current_summary,
        "current_roots": current_roots,
        "previous_summary": previous_summary,
        "previous_roots": previous_roots,
        "new_roots": new_roots,
        "resolved_roots": resolved_roots,
        "current_new_subsystems": current_new_subsystems,
        "previous_new_subsystems": previous_new_subsystems,
        "mass_stalled": mass_stalled,
    }


def launch_affordability(
    used: int, ceiling: int, *, launch_weight: int, reserved_weight: int
) -> dict[str, int | bool]:
    """Return pure launch arithmetic; atomic admission remains the guard's job."""
    required = launch_weight + reserved_weight
    return {
        "required": required,
        "affordable": used + required <= ceiling,
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
    next_persona: str | None = None,
    prior_roots: list[str] | None = None,
) -> dict[str, Any]:
    """Build the compatibility-stable report-only decision envelope."""
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
        "next_persona": next_persona,
        "prior_blocking_roots": prior_roots or [],
        "authorizes_shipping": False,
    }


def _common(state: dict[str, Any]) -> dict[str, Any]:
    delta = state["delta"]
    return {
        "used": state["used"],
        "ceiling": state["ceiling"],
        "target_sha": state["target_sha"],
        "blocker_mass": delta["current_summary"]["mass"],
        "cycles": state["cycles"],
        "new_roots": delta["new_roots"],
        "resolved_roots": delta["resolved_roots"],
    }


def evaluate_ultramagi_implementation(state: dict[str, Any]) -> dict[str, Any]:
    """Preserve the issue-107 implementation convergence transition table."""
    delta = state["delta"]
    current_summary = delta["current_summary"]
    current_roots = delta["current_roots"]
    previous_summary = delta["previous_summary"]
    previous_roots = delta["previous_roots"]
    current_phases = state["current_phases"]
    cycles = state["cycles"]
    common = _common(state)

    if state["deadline_expired"]:
        return output(
            "BLOCKED", "WALL_CLOCK_DEADLINE_EXPIRED", next_mode=None, **common
        )
    if state["design_invariant_changed"]:
        return output("REDESIGN", "DESIGN_INVARIANT_CHANGED", next_mode=None, **common)
    if (
        previous_summary
        and previous_summary["has_regression"]
        and current_summary["has_regression"]
    ):
        return output(
            "REDESIGN",
            "CONSECUTIVE_FIX_INDUCED_REGRESSIONS",
            next_mode=None,
            **common,
        )
    if current_roots & previous_roots:
        return output("REDESIGN", "BLOCKING_ROOT_REPEATED", next_mode=None, **common)
    if delta["current_new_subsystems"] & delta["previous_new_subsystems"]:
        return output(
            "REDESIGN",
            "SAME_SUBSYSTEM_NEW_ROOTS_RECURRED",
            next_mode=None,
            **common,
        )
    if state["transition_blocked"]:
        return output("BLOCKED", "RETRY_BUDGET_EXHAUSTED", next_mode=None, **common)
    if cycles >= MAX_LOGICAL_CYCLES and "xfamily" not in current_phases:
        return output(
            "BLOCKED", "MAX_LOGICAL_CYCLES_REACHED", next_mode=None, **common
        )
    if not current_phases:
        is_targeted = state["incremental_allowed"] and previous_summary is not None
        phase = "targeted" if is_targeted else "fanout"
        affordable = state["admissions"][phase]["affordable"]
        return output(
            "CONTINUE" if affordable else "BLOCKED",
            (
                "INCREMENTAL_FIX_REVIEW_REQUIRED"
                if is_targeted and affordable
                else "INITIAL_FULL_REQUIRED"
                if affordable
                else "NEXT_TARGETED_UNAFFORDABLE"
                if is_targeted
                else "NEXT_FANOUT_UNAFFORDABLE"
            ),
            next_mode=(
                "incremental-fix"
                if is_targeted and affordable
                else "initial-full"
                if affordable
                else None
            ),
            used=state["used"],
            ceiling=state["ceiling"],
            target_sha=state["target_sha"],
            blocker_mass=0,
            cycles=cycles,
            next_persona=state["targeted_persona"] if is_targeted else None,
            prior_roots=sorted(previous_roots) if is_targeted else None,
        )
    if current_phases & {"fanout", "targeted"} and "xfamily" not in current_phases:
        affordable = state["admissions"]["xfamily"]["affordable"]
        return output(
            "FINAL_REVIEW_REQUIRED" if affordable else "BLOCKED",
            (
                "FINAL_DIVERSE_RECHECK_REQUIRED"
                if affordable
                else "FINAL_DIVERSE_RECHECK_UNAFFORDABLE"
            ),
            next_mode="final-full" if affordable else None,
            **common,
        )
    if not current_roots:
        return output(
            "BLOCKED",
            "REPORT_ONLY_READY_FOR_EXISTING_PLATEAU_GATE",
            next_mode=None,
            **common,
        )
    if delta["mass_stalled"]:
        return output("BLOCKED", "BLOCKER_MASS_STALLED", next_mode=None, **common)
    if cycles >= MAX_LOGICAL_CYCLES:
        return output(
            "BLOCKED", "MAX_LOGICAL_CYCLES_WITH_BLOCKERS", next_mode=None, **common
        )
    affordable = state["admissions"]["fanout"]["affordable"]
    return output(
        "CONTINUE" if affordable else "BLOCKED",
        "FULL_TARGET_FIX_REQUIRED" if affordable else "NEXT_FANOUT_UNAFFORDABLE",
        next_mode="full-target-fix" if affordable else None,
        **common,
    )


def evaluate_dual_magi_design(state: dict[str, Any]) -> dict[str, Any]:
    """Evaluate bounded design correction without granting plateau authority."""
    delta = state["delta"]
    current_roots = delta["current_roots"]
    previous_roots = delta["previous_roots"]
    current_phases = state["current_phases"]
    common = _common(state)

    if current_roots & previous_roots:
        return output(
            "REDESIGN", "DESIGN_BLOCKING_ROOT_REPEATED", next_mode=None, **common
        )
    if delta["current_new_subsystems"] & delta["previous_new_subsystems"]:
        return output(
            "SCOPE_SPLIT",
            "DESIGN_SAME_SUBSYSTEM_NEW_ROOTS_RECURRED",
            next_mode=None,
            **common,
        )
    if not current_roots and "xfamily" in current_phases:
        return output(
            "PLATEAU_CANDIDATE",
            "DESIGN_READY_FOR_EXISTING_PLATEAU_GATE",
            next_mode=None,
            **common,
        )
    if delta["mass_stalled"]:
        return output(
            "BLOCKED", "DESIGN_BLOCKER_MASS_STALLED", next_mode=None, **common
        )
    if state["cycles"] >= MAX_LOGICAL_CYCLES:
        return output(
            "BLOCKED", "DESIGN_MAX_LOGICAL_CYCLES_REACHED", next_mode=None, **common
        )
    if current_phases & {"fanout", "targeted"} and "xfamily" not in current_phases:
        affordable = state["admissions"]["xfamily"]["affordable"]
        return output(
            "FINAL_REVIEW_REQUIRED" if affordable else "BLOCKED",
            (
                "DESIGN_FINAL_DIVERSE_RECHECK_REQUIRED"
                if affordable
                else "DESIGN_FINAL_DIVERSE_RECHECK_UNAFFORDABLE"
            ),
            next_mode="design-final-full" if affordable else None,
            **common,
        )
    affordable = state["admissions"]["fanout"]["affordable"]
    return output(
        "CONTINUE" if affordable else "BLOCKED",
        (
            "DESIGN_REVIEW_REQUIRED"
            if affordable
            else "DESIGN_NEXT_FANOUT_UNAFFORDABLE"
        ),
        next_mode="design-full" if affordable else None,
        **common,
    )


def evaluate_profile(profile: str, state: dict[str, Any]) -> dict[str, Any]:
    if profile == "ultramagi-implementation":
        return evaluate_ultramagi_implementation(state)
    if profile == "dual-magi-design":
        return evaluate_dual_magi_design(state)
    raise KernelInputError(f"unknown convergence profile: {profile}")
