#!/usr/bin/env python3
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / "lib"))

from babysit_pr import (  # noqa: E402
    ci_deny_paths,
    classify_checks,
    classify_review,
    is_green,
    lineage_is_owned,
    repairable_path,
)


class BabysitPredicateTests(unittest.TestCase):
    def pr(self):
        oid = "a" * 40
        return {
            "state": "OPEN",
            "headRefOid": oid,
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": [{"conclusion": "SUCCESS", "status": "COMPLETED"}],
            "comments": [{
                "id": "pass",
                "createdAt": "2026-07-28T00:00:00Z",
                "body": f"Independent review verdict: **PASS** @ {oid}",
            }],
        }

    def test_check_conclusion_and_status_are_separate_axes(self):
        pr = self.pr()
        pr["statusCheckRollup"] = [{"conclusion": "", "status": "IN_PROGRESS"}]
        self.assertEqual(classify_checks(pr), "PENDING")
        pr["statusCheckRollup"] = [{"conclusion": "STARTUP_FAILURE", "status": "COMPLETED"}]
        self.assertEqual(classify_checks(pr), "FAILED_OR_UNKNOWN")
        pr["statusCheckRollup"] = []
        self.assertEqual(classify_checks(pr), "UNKNOWN")

    def test_pass_is_bound_to_current_head(self):
        pr = self.pr()
        self.assertEqual(classify_review(pr), "PASS")
        self.assertTrue(is_green(pr))
        pr["headRefOid"] = "b" * 40
        self.assertEqual(classify_review(pr), "REVIEW_UNRECORDED")
        self.assertFalse(is_green(pr))

    def test_oidless_marker_is_unrecorded_and_latest_current_head_marker_wins(self):
        pr = self.pr()
        pr["comments"] = [{
            "createdAt": "2026-07-28T00:00:00Z",
            "body": "Independent review verdict: **PASS**",
        }]
        self.assertEqual(classify_review(pr), "REVIEW_UNRECORDED")
        oid = pr["headRefOid"]
        pr["comments"] = [
            {"id": "1", "createdAt": "2026-07-28T00:00:00Z",
             "body": f"Independent review verdict: **PASS** @ {oid}"},
            {"id": "2", "createdAt": "2026-07-28T00:00:01Z",
             "body": f"Independent review verdict: **BLOCK** @ {oid}"},
        ]
        self.assertEqual(classify_review(pr), "BLOCK")

    def test_state_is_checked_before_mergeability(self):
        pr = self.pr()
        pr.update({"state": "MERGED", "mergeable": "UNKNOWN", "mergeStateStatus": "UNKNOWN"})
        self.assertFalse(is_green(pr))

    def test_anchor_lineage_hands_back_on_any_commit_not_pushed_this_run(self):
        anchor, own, foreign = "a" * 40, "b" * 40, "c" * 40
        self.assertTrue(lineage_is_owned(anchor, anchor, [], []))
        self.assertTrue(lineage_is_owned(anchor, own, [own], [own]))
        self.assertFalse(lineage_is_owned(anchor, foreign, [foreign], []))
        self.assertFalse(lineage_is_owned(anchor, own, [foreign, own], [own]))


class CiMatcherP15Tests(unittest.TestCase):
    def test_formation_source_allowed_but_workflow_and_tests_denied(self):
        exact, prefixes = ci_deny_paths(ROOT)
        self.assertTrue(repairable_path(
            "plugins/harness-formation/lib/mailbox.sh",
            exact_deny=exact, prefix_deny=prefixes,
        ))
        self.assertFalse(repairable_path(
            ".github/workflows/harness-formation.yml",
            exact_deny=exact, prefix_deny=prefixes,
        ))
        self.assertFalse(repairable_path(
            "plugins/harness-formation/tests/test_formation_hardening.sh",
            exact_deny=exact, prefix_deny=prefixes,
        ))
        # A pull_request.paths allowlist entry is not itself a deny rule.
        self.assertNotIn("plugins/harness-formation/**", exact)

    def test_named_ci_dependency_is_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            (workflows / "ci.yml").write_text(
                "jobs:\n  test:\n    steps:\n"
                "      - run: bash plugins/example/bin/check-source\n",
                encoding="utf-8",
            )
            exact, prefixes = ci_deny_paths(root)
            self.assertFalse(repairable_path(
                "plugins/example/bin/check-source",
                exact_deny=exact, prefix_deny=prefixes,
            ))


if __name__ == "__main__":
    unittest.main()
