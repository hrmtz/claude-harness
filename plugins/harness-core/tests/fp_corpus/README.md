# FP/FN corpus — credential-guard regression harness

A labelled corpus + runner that measures the **false-positive** (benign command wrongly
blocked) and **false-negative** (malicious command wrongly allowed) rate of the
PreToolUse decision guards. Run it after any guard edit — heavy hook changes can
silently raise the FP rate (which manifests as the agent getting blocked on normal dev
work), and this catches that.

```
python3 plugins/harness-core/tests/fp_corpus/run_fp_eval.py          # table + pass/fail gate
python3 plugins/harness-core/tests/fp_corpus/run_fp_eval.py --json    # machine-readable
```

- **`corpus.jsonl`** — one labelled item per line: `{id, label: benign|malicious, type: bash|read|output, payload, note, owners}`. `owners` lists which hooks are expected to block a malicious item (so an FN is only counted against the guard that owns that vector). Secrets are **synthetic**.
- **`run_fp_eval.py`** — feeds the corpus through each guard, prints per-hook FP/FN, and **exits non-zero** if any benign command is blocked (FP) or an owned-malicious FN exceeds the hook's baseline. FN baselines encode accepted coverage gaps (e.g. `cfrg` does not yet catch cookie files).

Scope: only side-effect-free allow/deny/advisory guards (`bash_command_guard`,
`pg_rotation_propagation_guard`, `credential_file_read_guard`, `long_task_advisor`,
`branch_policy_guard`, `pipeline_preflight_gate`). The PostToolUse scrubber is excluded
(it rewrites transcripts / can trigger the leak-followup path) — see `test_value_scrub_*.py`.

Origin: 2026-06-28 FP sweep that measured the 06-27 batch (1 net-new FP, fixed in #48).
Add a corpus line whenever a new FP/FN shape is found, then keep this green.
