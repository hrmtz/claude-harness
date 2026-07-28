#!/usr/bin/env python3
import copy, json, os, pathlib, subprocess, sys, tempfile, unittest
import importlib.machinery, importlib.util
from unittest import mock
HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE.parent / "bin" / "formation-integration-audit"
FIXTURE = HERE / "fixtures" / "integration_audit_slice0.json"
loader = importlib.machinery.SourceFileLoader("integration_audit", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
audit = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = audit
loader.exec_module(audit)
class AuditTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(FIXTURE.read_text())
    def report(self, data=None):
        return audit.audit(data or self.data)
    def codes(self, data=None):
        return {item["code"] for item in self.report(data)["findings"]}
    def test_required_drift_fixture(self):
        codes = self.codes()
        self.assertIn("DEV_MERGE_ISSUE_OPEN", codes)
        self.assertIn("DONE_WITH_UNMERGED_PR", codes)
        self.assertIn("DONE_WORKTREE_PRESENT", codes)
        self.assertIn("DIRTY_PARENT_BEHIND", codes)
        self.assertEqual(audit.issue_numbers(
            "Closes https://github.com/owner/repo/issues/140; refs other/repo#141", "owner/repo", audit.CLOSE_RE
        ), [140])
        self.data["prs"][0]["closing_issue_numbers"] = []
        self.assertNotIn("DEV_MERGE_ISSUE_OPEN", self.codes())
        self.data["prs"][0]["body"] = "Closes #140\n\n## Verification\n- pytest passed"
        self.assertIn("TEST_EVIDENCE_RECORDED", self.codes())
    def test_check_states_fail_closed_without_rejecting_legacy_success(self):
        self.assertNotIn("github_pat_", audit.safe("fatal: https://me:github_pat_abcdefgh@example.test/x", "x"))
        for conclusion in ("STARTUP_FAILURE", "STALE", "UNRECOGNIZED"):
            data = copy.deepcopy(self.data)
            data["prs"][0]["statusCheckRollup"] = [{"conclusion": conclusion}]
            self.assertIn("CHECKS_FAILED_OR_UNKNOWN", self.codes(data))
        data["prs"][0]["statusCheckRollup"] = [{"state": "SUCCESS"}]
        self.assertNotIn("CHECKS_FAILED_OR_UNKNOWN", self.codes(data))
        data["prs"][0]["comments"].append({
            "id": "block",
            "authorAssociation": "OWNER",
            "createdAt": "2026-07-24T00:00:01Z",
            "body": "Independent review verdict: **BLOCK** @ " + data["prs"][0]["headRefOid"],
        })
        self.assertIn("REVIEW_BLOCKED", self.codes(data))
    def test_unknown_issue_on_open_pr_is_action(self):
        data = copy.deepcopy(self.data)
        data["issues"]["141"] = "UNKNOWN"
        findings = self.report(data)["findings"]
        self.assertTrue(any(
            item["code"] == "ISSUE_STATE_UNKNOWN"
            and "PR #201" in item["message"]
            for item in findings
        ))
    def test_workers_without_selected_pr_are_audited(self):
        data = copy.deepcopy(self.data)
        data["workers"] += [
            {"id": "ask-orphan", "state": "ASK", "pane_alive": True,
             "issue_numbers": [999], "pr_numbers": []},
            {"id": "dead-orphan", "state": "RUNNING", "pane_alive": False, "repo_known": False, "issue_numbers": [], "pr_numbers": []},
            {"id": "done-issue-only", "state": "DONE", "pane_alive": False, "issue_numbers": [998], "pr_numbers": []},
        ]
        codes = self.codes(data)
        self.assertIn("WORKER_ASK", codes)
        self.assertIn("WORKER_REPO_UNKNOWN", codes)
        self.assertIn("DONE_OWNER_UNKNOWN", codes)
    def test_detached_worktree_matches_head_oid(self):
        data = copy.deepcopy(self.data)
        data["worktrees"] = [{
            "HEAD": data["prs"][0]["headRefOid"],
            "detached": True,
            "worktree": "/fixture/detached",
        }]
        self.assertIn("MERGED_WORKTREE_PRESENT", self.codes(data))
    def test_fork_pr_does_not_require_origin_branch(self):
        data = copy.deepcopy(self.data)
        pr = data["prs"][1]
        pr["isCrossRepository"] = True
        data["remote_branches"].pop(pr["headRefName"])
        self.assertNotIn("OPEN_PR_BRANCH_DRIFT", self.codes(data))
        pr["isCrossRepository"] = False
        data["remote_known"] = False
        self.assertNotIn("OPEN_PR_BRANCH_DRIFT", self.codes(data))
        data["prs"][0]["headRefName"] = "dev"
        self.assertNotIn("MERGED_REMOTE_BRANCH", self.codes(data))
        data = copy.deepcopy(self.data)
        data["remote_branches"]["agent/merged-140"] = "new-commit"
        data["worktrees"] = [{"HEAD": data["prs"][0]["headRefOid"], "branch": "refs/heads/dev"}]
        self.assertNotIn("MERGED_WORKTREE_PRESENT", self.codes(data))
    @mock.patch.object(audit, "json_command")
    @mock.patch.object(audit, "command")
    def test_pr_cap_is_reported(self, command_mock, json_mock):
        command_mock.return_value = ""
        json_mock.side_effect = [[{"number": n, "body": ""} for n in range(101)], []]
        data = audit.read_github("fixture/repo")
        self.assertIn("PR_INVENTORY_TRUNCATED", {x[1] for x in data["errors"]})
    def test_dirty_checkout_is_not_mutated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            subprocess.run(["git", "init", "-q", "-b", "dev"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "fixture"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=root, check=True)
            (root / "tracked").write_text("base\n")
            subprocess.run(["git", "add", "tracked"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
            subprocess.run(["git", "remote", "add", "origin", str(root)], cwd=root, check=True)
            (root / "tracked").write_text("dirty\n")
            (root / "untracked").write_text("new\n")
            before = subprocess.check_output(
                ["git", "status", "--porcelain=v1"], cwd=root, text=True
            )
            result = audit.read_git(str(root))
            after = subprocess.check_output(
                ["git", "status", "--porcelain=v1"], cwd=root, text=True
            )
            self.assertEqual(result["parent"]["dirty_paths"], 2)
            self.assertEqual(result["parent"]["behind"], 0)
            self.assertEqual(before, after)
    def test_clean_ahead_parent_is_drift(self):
        self.data["parent"] = {"dirty_paths": 0, "ahead": 1, "behind": 0}
        self.assertIn("PARENT_DRIFT", self.codes())
    def test_cli_does_not_create_formation_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "absent"
            result = subprocess.run(
                [str(HERE.parent / "bin" / "formation"), "integration-audit",
                 "--repo", "fixture/repo", "--fixture", str(FIXTURE), "--json"],
                stdout=subprocess.DEVNULL, env={**os.environ, "FORMATION_HOME": str(home)}
            )
            self.assertEqual(result.returncode, 1)
            self.assertFalse(home.exists())
            bad = subprocess.run([str(SCRIPT), "--repo", "fixture/repo",
                                  "--fixture", str(home / "missing")], stderr=subprocess.DEVNULL)
            self.assertEqual(bad.returncode, 2)
            malformed = pathlib.Path(tmp) / "malformed.json"
            for payload in ({}, {**self.data, "prs": [None]}):
                malformed.write_text(json.dumps(payload))
                bad = subprocess.run([str(SCRIPT), "--repo", "fixture/repo",
                                      "--fixture", str(malformed)], stderr=subprocess.DEVNULL)
                self.assertEqual(bad.returncode, 2)
if __name__ == "__main__":
    unittest.main()
