# Broad SAST + secret layer (gitleaks + semgrep) — claude-harness #19

Off-the-shelf breadth that sits **UNDER** a project's narrow, hand-rolled guards.
A project's custom rules (e.g. a DSN-literal gate, a SQL-injection gate, a branch
+ credential pre-commit hook) only catch the patterns you anticipated. gitleaks
(broad secret rules) + semgrep (`--config auto` general SAST) catch the rest. Run
both layers; they overlap intentionally (defense-in-depth, not redundancy).

Source: Zenn `zittiandbuoni/632ff0709247f6`. First consumer: PRS-LLM (commit b8a7c1ec).

## Files
- `gitleaks.toml` → copy to `<project>/.gitleaks.toml`, tune the allowlist.
- `security-scan.yml` → copy to `<project>/.github/workflows/`, set the scoped dirs.

## Deploy to a project (3 steps)
1. `cp templates/security/gitleaks.toml <project>/.gitleaks.toml`
2. **Calibrate the allowlist**: `gitleaks detect --no-git --redact -c .gitleaks.toml --source .`
   then add the FP classes you see (data/embeddings, gitignored secret files, review
   dumps, lock files, illustration/fixture sources) to `[allowlist].paths` until the
   *source surface* is 0. Real findings → fix, don't allowlist.
3. `cp templates/security/security-scan.yml <project>/.github/workflows/` and replace
   the scoped dirs (`api core scripts ui server`) with the project's source roots.

## Why semgrep is diff-aware (not hard-fail)
`--config auto` on a mature repo yields hundreds of findings (most are accepted
conventions, e.g. trusted-literal SQL). Hard-failing on all of them blocks every PR.
The workflow uses `--baseline-commit` so only findings **introduced by the PR** fail;
the pre-existing baseline is accepted. gitleaks, by contrast, is calibrated to 0 on
the source surface, so it hard-fails on any new committed secret.

## Pre-commit (2nd layer, optional follow-up)
The CI gate catches on push/PR. For a local pre-commit layer, either adopt the
`pre-commit` framework (`.pre-commit-config.yaml` with gitleaks + semgrep hooks) —
**but** migrate any existing raw `.git/hooks/pre-commit` rails (branch policy, cred
guard) into it as `local` hooks first, or the framework's `pre-commit install` will
clobber them — or extend the existing raw hook to run `gitleaks protect --staged`.
