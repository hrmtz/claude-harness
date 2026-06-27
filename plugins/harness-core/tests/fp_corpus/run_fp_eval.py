#!/usr/bin/env python3
"""Standing FP/FN regression harness for the claude-harness PreToolUse decision guards.

Motivation: heavy hook edits (e.g. the 2026-06-27 red-team batch) can silently raise
the false-positive rate — benign dev commands getting blocked. This feeds a labelled
corpus (benign commands that MUST pass + malicious ones that MUST block) through each
guard and reports per-hook FP/FN. Run it after any guard change.

  python3 plugins/harness-core/tests/fp_corpus/run_fp_eval.py          # table + gate
  python3 plugins/harness-core/tests/fp_corpus/run_fp_eval.py --json    # machine-readable

GATE: exits non-zero if ANY benign command is blocked (FP > 0) on a decision guard, or
if a guard's owned-malicious FN exceeds its known baseline. FN baselines encode accepted
coverage gaps (e.g. cfrg does not catch cookie files — tracked separately).

SCOPE: only the side-effect-free allow/deny/advisory guards. The PostToolUse scrubber
(credential_value_scrub.sh) is intentionally excluded — it rewrites transcripts and can
trigger the leak-followup path; it has its own sandboxed tests (test_value_scrub_*.py).
All corpus secrets are SYNTHETIC.
"""
import json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
CORE = os.path.join(REPO, "plugins", "harness-core", "hooks")
RAILS = os.path.join(REPO, "plugins", "harness-rails", "hooks")

# hook -> (script, input-type bash|read, detect deny|exit2|ctx, FN baseline for owned-malicious)
HOOKS = {
    "bcg":  (os.path.join(CORE, "bash_command_guard.sh"), "bash", "deny", 0),
    "pg":   (os.path.join(CORE, "pg_rotation_propagation_guard.sh"), "bash", "deny", 0),
    "cfrg": (os.path.join(CORE, "credential_file_read_guard.sh"), "read", "exit2", 1),  # .tkc cookie gap (#14 follow-up)
    "lta":  (os.path.join(CORE, "long_task_advisor.sh"), "bash", "ctx", 0),
    "bpg":  (os.path.join(CORE, "branch_policy_guard.sh"), "bash", "deny", 0),
    "ppg":  (os.path.join(RAILS, "pipeline_preflight_gate.sh"), "bash", "exit2", 0),
}


def load_corpus():
    with open(os.path.join(HERE, "corpus.jsonl")) as f:
        return [json.loads(l) for l in f if l.strip()]


def build_input(typ, payload):
    if typ == "read":
        return json.dumps({"tool_name": "Read", "tool_input": {"file_path": payload}})
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": payload}})


def fires(script, typ, detect, payload):
    p = subprocess.run(["bash", script], input=build_input(typ, payload),
                       capture_output=True, text=True, timeout=30)
    if detect == "exit2":
        return p.returncode == 2
    if detect == "deny":
        return '"permissionDecision": "deny"' in p.stdout
    if detect == "ctx":
        return '"additionalContext"' in p.stdout
    return False


def eval_hook(hook, corpus):
    script, typ, detect, _ = HOOKS[hook]
    inp_type = "read" if hook == "cfrg" else "bash"
    benign = fp = mal = fn = 0
    fp_ex, fn_ex = [], []
    for it in corpus:
        if it["type"] != inp_type:
            continue
        blocked = fires(script, typ, detect, it["payload"])
        if it["label"] == "benign":
            benign += 1
            if blocked:
                fp += 1
                if len(fp_ex) < 5:
                    fp_ex.append(it["payload"])
        elif hook in it.get("owners", []):
            mal += 1
            if not blocked:
                fn += 1
                if len(fn_ex) < 5:
                    fn_ex.append(it["payload"])
    return {"benign_total": benign, "fp_count": fp,
            "malicious_total": mal, "fn_count": fn,
            "fp_examples": fp_ex, "fn_examples": fn_ex}


def main():
    corpus = load_corpus()
    results = {h: eval_hook(h, corpus) for h in HOOKS}
    if "--json" in sys.argv:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        print(f"{'hook':5} {'FP':>8}  {'FN':>8}  (owned-malicious)")
        for h, r in results.items():
            print(f"{h:5} {r['fp_count']:>3}/{r['benign_total']:<4} {r['fn_count']:>3}/{r['malicious_total']:<4}")

    fail = []
    for h, r in results.items():
        if r["fp_count"] > 0:
            fail.append(f"{h}: {r['fp_count']} FALSE POSITIVE(s) {r['fp_examples']}")
        baseline = HOOKS[h][3]
        if r["fn_count"] > baseline:
            fail.append(f"{h}: FN {r['fn_count']} exceeds baseline {baseline} {r['fn_examples']}")
    if fail:
        print("\nFP/FN REGRESSION:")
        for f in fail:
            print("  - " + f)
        return 1
    print("\nno FP regression; FN within baseline ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
