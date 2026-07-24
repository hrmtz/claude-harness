#!/usr/bin/env python3
"""Pure replay and profile-policy tests for the convergence kernel."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN / "scripts"))
import magi_convergence_kernel as kernel  # noqa: E402


TARGET = "c" * 40


def finding(
    root: str,
    subsystem: str,
    *,
    severity: str = "HIGH",
    regression: bool = False,
) -> dict[str, Any]:
    return {
        "finding_id": f"finding-{root}",
        "root_cause_id": root,
        "subsystem": subsystem,
        "severity": severity,
        "dup_flag": "regression" if regression else "new",
        "relation_to_prior": "fix-induced-regression" if regression else "new-root",
    }


def summary(*findings: dict[str, Any]) -> dict[str, Any]:
    return kernel.summarize_revision([{"findings": list(findings)}])


def state(
    revision_findings: list[list[dict[str, Any]]],
    *,
    current_reviewed: bool = True,
    phases: set[str] | None = None,
    cycles: int = 0,
    used: int = 0,
    ceiling: int = 16,
    fanout_affordable: bool = True,
    targeted_affordable: bool = True,
    xfamily_affordable: bool = True,
    deadline: bool = False,
    design_changed: bool = False,
    transition_blocked: bool = False,
    incremental: bool = False,
) -> dict[str, Any]:
    revision_order = [f"{index:040x}" for index in range(1, len(revision_findings) + 1)]
    summaries = {
        revision: summary(*findings)
        for revision, findings in zip(revision_order, revision_findings, strict=True)
    }
    current_target = revision_order[-1] if current_reviewed and revision_order else TARGET
    return {
        "used": used,
        "ceiling": ceiling,
        "target_sha": current_target,
        "cycles": cycles,
        "current_phases": phases or set(),
        "delta": kernel.revision_delta(revision_order, summaries, current_target),
        "deadline_expired": deadline,
        "design_invariant_changed": design_changed,
        "transition_blocked": transition_blocked,
        "incremental_allowed": incremental,
        "targeted_persona": "gnat",
        "admissions": {
            "fanout": {"affordable": fanout_affordable},
            "targeted": {"affordable": targeted_affordable},
            "xfamily": {"affordable": xfamily_affordable},
        },
    }


class SharedPrimitiveTest(unittest.TestCase):
    def test_revision_summary_keeps_highest_severity_per_root(self) -> None:
        result = summary(
            finding("root-a", "parser", severity="HIGH"),
            finding("root-a", "parser", severity="CRITICAL"),
            finding("root-b", "storage", severity="MED"),
        )
        self.assertEqual(set(result["roots"]), {"root-a"})
        self.assertEqual(result["roots"]["root-a"]["severity"], "CRITICAL")
        self.assertEqual(result["mass"], 8)

    def test_revision_summary_rejects_missing_or_contradictory_roots(self) -> None:
        with self.assertRaisesRegex(kernel.KernelInputError, "lacks stable"):
            summary({"finding_id": "bad", "severity": "HIGH"})
        with self.assertRaisesRegex(kernel.KernelInputError, "contradictory"):
            summary(
                finding("same", "parser"),
                finding("same", "scheduler", severity="CRITICAL"),
            )

    def test_revision_delta_reports_new_resolved_repeated_and_regression(self) -> None:
        revisions = ["1" * 40, "2" * 40]
        summaries = {
            revisions[0]: summary(
                finding("resolved", "parser"),
                finding("repeated", "storage", regression=True),
            ),
            revisions[1]: summary(
                finding("repeated", "storage", regression=True),
                finding("new", "scheduler"),
            ),
        }
        delta = kernel.revision_delta(revisions, summaries, revisions[-1])
        self.assertEqual(delta["new_roots"], ["new"])
        self.assertEqual(delta["resolved_roots"], ["resolved"])
        self.assertEqual(delta["current_roots"] & delta["previous_roots"], {"repeated"})
        self.assertTrue(delta["current_summary"]["has_regression"])
        self.assertTrue(delta["previous_summary"]["has_regression"])

    def test_affordability_reserves_the_mandatory_transition(self) -> None:
        self.assertEqual(
            kernel.launch_affordability(
                12, 16, launch_weight=3, reserved_weight=1
            ),
            {"required": 4, "affordable": True},
        )
        self.assertEqual(
            kernel.launch_affordability(
                13, 16, launch_weight=3, reserved_weight=1
            ),
            {"required": 4, "affordable": False},
        )


class UltramagiGoldenReplayTest(unittest.TestCase):
    """Representative issue-107 histories lock complete serialized decisions."""

    def test_initial_full_envelope_is_exact(self) -> None:
        result = kernel.evaluate_profile(
            "ultramagi-implementation", state([], current_reviewed=False)
        )
        self.assertEqual(
            result,
            {
                "mode": "report-only",
                "decision": "CONTINUE",
                "next_mode": "initial-full",
                "reason_code": "INITIAL_FULL_REQUIRED",
                "usage": 0,
                "ceiling": 16,
                "target_git_sha": TARGET,
                "blocker_mass": 0,
                "logical_cycles": 0,
                "new_blocking_roots": [],
                "resolved_blocking_roots": [],
                "next_persona": None,
                "prior_blocking_roots": [],
                "authorizes_shipping": False,
            },
        )

    def test_representative_decision_and_reason_replay(self) -> None:
        cases = [
            (
                "deadline_precedes_design",
                state(
                    [[finding("a", "parser")]],
                    phases={"xfamily"},
                    deadline=True,
                    design_changed=True,
                ),
                ("BLOCKED", "WALL_CLOCK_DEADLINE_EXPIRED", None),
            ),
            (
                "design_precedes_repeated_root",
                state(
                    [[finding("a", "parser")], [finding("a", "parser")]],
                    phases={"xfamily"},
                    design_changed=True,
                ),
                ("REDESIGN", "DESIGN_INVARIANT_CHANGED", None),
            ),
            (
                "consecutive_regression",
                state(
                    [
                        [finding("a", "parser", regression=True)],
                        [finding("b", "storage", regression=True)],
                    ],
                    phases={"xfamily"},
                ),
                ("REDESIGN", "CONSECUTIVE_FIX_INDUCED_REGRESSIONS", None),
            ),
            (
                "repeated_root",
                state(
                    [[finding("a", "parser")], [finding("a", "parser")]],
                    phases={"xfamily"},
                ),
                ("REDESIGN", "BLOCKING_ROOT_REPEATED", None),
            ),
            (
                "same_subsystem",
                state(
                    [[finding("a", "parser")], [finding("b", "parser")]],
                    phases={"xfamily"},
                ),
                ("REDESIGN", "SAME_SUBSYSTEM_NEW_ROOTS_RECURRED", None),
            ),
            (
                "retry_precedes_cycle_limit",
                state(
                    [[finding("a", "parser")]],
                    phases={"xfamily"},
                    cycles=2,
                    transition_blocked=True,
                ),
                ("BLOCKED", "RETRY_BUDGET_EXHAUSTED", None),
            ),
            (
                "cycle_limit_before_new_review",
                state(
                    [[finding("a", "parser")]],
                    current_reviewed=False,
                    cycles=2,
                ),
                ("BLOCKED", "MAX_LOGICAL_CYCLES_REACHED", None),
            ),
            (
                "incremental_fix",
                state(
                    [[finding("a", "parser")]],
                    current_reviewed=False,
                    incremental=True,
                    used=4,
                ),
                ("CONTINUE", "INCREMENTAL_FIX_REVIEW_REQUIRED", "incremental-fix"),
            ),
            (
                "final_diverse_recheck",
                state(
                    [[finding("a", "parser")]], phases={"fanout"}, used=3
                ),
                (
                    "FINAL_REVIEW_REQUIRED",
                    "FINAL_DIVERSE_RECHECK_REQUIRED",
                    "final-full",
                ),
            ),
            (
                "clean_handoff_precedes_limits",
                state([[]], phases={"fanout", "xfamily"}, cycles=2),
                (
                    "BLOCKED",
                    "REPORT_ONLY_READY_FOR_EXISTING_PLATEAU_GATE",
                    None,
                ),
            ),
            (
                "stalled_mass_precedes_cycle_limit",
                state(
                    [
                        [finding("a", "parser")],
                        [finding("b", "scheduler")],
                        [finding("c", "storage")],
                    ],
                    phases={"xfamily"},
                    cycles=2,
                ),
                ("BLOCKED", "BLOCKER_MASS_STALLED", None),
            ),
            (
                "max_cycles_with_blockers",
                state(
                    [
                        [finding("a", "parser", severity="CRITICAL")],
                        [finding("b", "storage")],
                    ],
                    phases={"xfamily"},
                    cycles=2,
                ),
                ("BLOCKED", "MAX_LOGICAL_CYCLES_WITH_BLOCKERS", None),
            ),
            (
                "full_target_fix",
                state([[finding("a", "parser")]], phases={"xfamily"}, cycles=1),
                ("CONTINUE", "FULL_TARGET_FIX_REQUIRED", "full-target-fix"),
            ),
            (
                "reserved_fanout_unaffordable",
                state([], current_reviewed=False, fanout_affordable=False),
                ("BLOCKED", "NEXT_FANOUT_UNAFFORDABLE", None),
            ),
        ]
        for name, projected, expected in cases:
            with self.subTest(name=name):
                result = kernel.evaluate_profile(
                    "ultramagi-implementation", projected
                )
                self.assertEqual(
                    (result["decision"], result["reason_code"], result["next_mode"]),
                    expected,
                )
                self.assertFalse(result["authorizes_shipping"])


class DualMagiDesignPolicyTest(unittest.TestCase):
    def test_bounded_design_policy_table(self) -> None:
        cases = [
            (
                "repeated_root",
                state(
                    [[finding("a", "parser")], [finding("a", "parser")]],
                    phases={"xfamily"},
                ),
                ("REDESIGN", "DESIGN_BLOCKING_ROOT_REPEATED"),
            ),
            (
                "recurrent_subsystem",
                state(
                    [[finding("a", "parser")], [finding("b", "parser")]],
                    phases={"xfamily"},
                ),
                ("SCOPE_SPLIT", "DESIGN_SAME_SUBSYSTEM_NEW_ROOTS_RECURRED"),
            ),
            (
                "stalled_mass",
                state(
                    [
                        [finding("a", "parser")],
                        [finding("b", "scheduler")],
                        [finding("c", "storage")],
                    ],
                    phases={"xfamily"},
                ),
                ("BLOCKED", "DESIGN_BLOCKER_MASS_STALLED"),
            ),
            (
                "maximum_cycles",
                state(
                    [
                        [finding("a", "parser", severity="CRITICAL")],
                        [finding("b", "storage")],
                    ],
                    phases={"xfamily"},
                    cycles=2,
                ),
                ("BLOCKED", "DESIGN_MAX_LOGICAL_CYCLES_REACHED"),
            ),
            (
                "clean_candidate",
                state([[]], phases={"fanout", "xfamily"}, cycles=2),
                (
                    "PLATEAU_CANDIDATE",
                    "DESIGN_READY_FOR_EXISTING_PLATEAU_GATE",
                ),
            ),
            (
                "three_clean_revisions_are_not_stalled_blockers",
                state([[], [], []], phases={"fanout", "xfamily"}, cycles=2),
                (
                    "PLATEAU_CANDIDATE",
                    "DESIGN_READY_FOR_EXISTING_PLATEAU_GATE",
                ),
            ),
            (
                "reserved_transition_unaffordable",
                state(
                    [],
                    current_reviewed=False,
                    fanout_affordable=False,
                    used=13,
                ),
                ("BLOCKED", "DESIGN_NEXT_FANOUT_UNAFFORDABLE"),
            ),
        ]
        for name, projected, expected in cases:
            with self.subTest(name=name):
                result = kernel.evaluate_profile("dual-magi-design", projected)
                self.assertEqual(
                    (result["decision"], result["reason_code"]),
                    expected,
                )
                self.assertFalse(result["authorizes_shipping"])

    def test_unknown_profile_fails_closed(self) -> None:
        with self.assertRaisesRegex(kernel.KernelInputError, "unknown"):
            kernel.evaluate_profile("invented", state([], current_reviewed=False))


if __name__ == "__main__":
    unittest.main()
