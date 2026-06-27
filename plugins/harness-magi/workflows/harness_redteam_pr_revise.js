export const meta = {
  name: 'harness-redteam-pr-revise',
  description: 'Address cross-family REVISE findings on draft PRs #42/#43/#44 in isolated worktrees, re-verify, re-codex to APPROVED, push to the existing branch. No merge.',
  phases: [{ title: 'Revise', detail: 'apply codex findings, re-verify, re-codex, push branch' }],
}

const SC = '/tmp/claude-1000/-home-hrmtz-projects-claude-harness/1ec45da8-d4e5-46ed-8f8f-0d910e24a6eb/scratchpad'

const COMMON = `You are revising a DRAFT red-team PR in claude-harness to address a cross-family (codex) security review that returned REVISE. You are in a FRESH GIT WORKTREE. Discipline: this is review-driven iteration on an existing PR branch; stay surgical; the live credential scrubber+autorotate are ARMED so NEVER print credential-pattern diffs or DSN-shaped strings to stdout — redirect to files.

SETUP:
  git fetch origin <branch> 2>/dev/null ; git checkout <branch>   (the existing PR branch; base is dev)

APPLY the findings below (fix every HIGH and MED; fix LOW if cheap; if a finding needs a product decision, use the DECISION given). Keep changes minimal and reviewable.

VERIFY: run bash -n on edited .sh, python3 -m py_compile on .py, and any repo test under plugins/harness-*/tests/. Add a test for each HIGH where a test harness exists. Record results.

RE-REVIEW (cross-family, mandatory for these security fixes):
  git diff origin/dev...HEAD -- <changed files> > ${SC}/revise_<pr>.diff   (redirect, do not cat)
  Write a codex prompt to ${SC}/revise_<pr>_prompt.md: a header telling codex it is an INDEPENDENT cross-family reviewer re-checking whether the listed REVISE findings are resolved without new regressions, ending "VERDICT: APPROVED / REVISE / REJECT", then append the diff file to it via 'cat ... >>'.
  timeout 420 codex exec --skip-git-repo-check - < ${SC}/revise_<pr>_prompt.md > ${SC}/revise_<pr>_codexout.txt 2>/dev/null ; echo exit=$?
  Read the codex out file with the Read tool.
  If codex still returns REVISE/REJECT on a HIGH, apply one more fix round and re-run codex once more.

COMMIT + PUSH to the SAME branch:
  git commit (conventional msg, body lists what was addressed + the codex re-review verdict, end with: Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>)
  git push origin <branch>
  Post a PR comment via: gh pr comment <pr> -R hrmtz/claude-harness --body-file <a file you Write> summarizing the revision + final codex verdict.

Return ONLY the structured object.`

const TASKS = [
  {
    pr: 44, branch: 'redteam/scrub-no-failopen', label: 'revise:PR44-scrub',
    findings: `codex REVISE on credential_scrub.py:
- HIGH (line ~339): oversized-blob TAIL scan computes tail_start = run_len - MAX_CANDIDATE_RUN, so a max-length (4096B) secret ending just inside the tail begins BEFORE tail_start and is never hashed. FIX: back the tail window start off by (max(known-secret lengths) - 1) so a secret straddling the boundary is covered.
- MED: the "delimited runs scanned first so ordinary creds always covered" guarantee is FALSE under global budget exhaustion. Make the code comments honest (coverage is best-effort once budget exhausts; scan_complete=False already signals it).
- MED: an incomplete scan that DID find+redact hits still routes through resume_context()'s "No manual steps needed" wording, contradicting the manual-review caveat. FIX: when scan incomplete, the message must NOT imply fully auto-handled.
- LOW: MAX_SCAN_WINDOWS=2,000,000 may exceed the hook timeout (re-creating fail-open). Consider a wall-clock guard. LOW: add boundary/budget/incomplete-messaging tests.`,
    decision: 'No product decision needed; implement the correctness + honesty fixes. For the MAX_SCAN_WINDOWS LOW, add a wall-clock soft-deadline that flips scan_complete=False rather than running unbounded.',
  },
  {
    pr: 43, branch: 'redteam/formation-hardening', label: 'revise:PR43-formation',
    findings: `codex REVISE on plugins/harness-formation/:
- HIGH (bin/formation ~line 206): the new normal-sandbox default for CODEX workers prevents them writing ~/.njslyr7/mailbox, so a codex worker dies before its required 'formation report' ack = regression.
- MED (#37): the UNTRUSTED-DATA envelope prefixes body lines but raw ANSI/control chars in a body can still corrupt the terminal display. FIX: strip/escape non-printable control chars (keep newlines) when rendering the body.
- LOW: a banned 'sops -d | jq -r' idiom still remains at skills/formation/templates/briefing.md:22 — replace with sops exec-env.
- LOW: redact.sh is_credential_like regex misses lowercase keys, 'export KEY=...', and whitespace around '='. Broaden to catch these.`,
    decision: `#38 DECISION (decided, do NOT ask): default sandbox for CLAUDE workers (security win), but default BYPASS for CODEX workers because codex genuinely needs to write ~/.njslyr7/mailbox for the formation protocol. Implement: spawn picks default by cli — claude => normal sandbox/approval, codex => bypass (current behavior). Keep the explicit --bypass-sandbox (force bypass) and add --sandbox (force normal) overrides for either cli. Emit a one-line stderr notice stating which mode was chosen and why. Document the residual: codex-bypass is a known residual of #38; note a follow-up for a narrow ~/.njslyr7-writable carve-out instead of full bypass (mention it in the PR comment, do NOT open an issue).`,
  },
  {
    pr: 42, branch: 'redteam/bash-guard-relative-env', label: 'revise:PR42-bashguard',
    findings: `codex REVISE on bash_command_guard.sh:
- HIGH (line ~63): the basename regex needs a literal contiguous '.env'/'credentials.ext', so reads built via shell token construction bypass it: cat .e"nv" , python3 -c 'open("."+"env")' , cat \${PWD}/.e\${X:-nv} , ANSI-C $'\\056env'. FIX (pragmatic, defense-in-depth — full shell parsing is out of scope): strip common obfuscation before matching (remove quotes, collapse $'...' ANSI-C decode of \\056, strip \${...:-} defaults best-effort) so the most obvious bypasses are caught; document remaining residual honestly in a comment (this guard is one layer, not the only one).
- MED: the pattern is operation-independent so it over-blocks benign metadata commands (test -f .env, ls .env, git status -- .env, find -name .env). FIX: scope the block to read/exfil-like usage (cat/grep/less/head/tail/interpreter readers/cp to stdout) OR explicitly allow pure-metadata verbs (test/ls/find/git status/stat) for the credential-file pattern.
- MED: HRMTZ_ACK_CRED_READ ack bypass whitelists ANY pattern whose reason string merely contains the token (so printenv/kubectl get secret/pass show get whitelisted). FIX: make ack apply per-pattern via explicit ack_ok metadata, not free-text substring.
- LOW: add tests for the bypass encodings + the metadata false-positive surface.`,
    decision: 'No product decision needed. Prefer the explicit-allow-metadata-verbs approach for the MED (keep blocking reads independent of reader, but allow test/ls/find/stat/git-status on the path). Keep the existing absolute-path + literal-form blocks.',
  },
]

phase('Revise')
const results = await parallel(
  TASKS.map((t) => () =>
    agent(
      `${COMMON}\n\n========\nPR #${t.pr}  branch: ${t.branch}\n\nFINDINGS TO ADDRESS:\n${t.findings}\n\nDECISION:\n${t.decision}\n\n(Replace <pr> with ${t.pr} and <branch> with ${t.branch} in the SETUP/RE-REVIEW commands.)`,
      {
        label: t.label, phase: 'Revise', isolation: 'worktree',
        schema: {
          type: 'object', additionalProperties: false,
          properties: {
            pr: { type: 'number' },
            final_codex_verdict: { type: 'string', enum: ['APPROVED', 'REVISE', 'REJECT', 'CODEX_FAILED'] },
            pushed: { type: 'boolean' },
            verify: { type: 'string' },
            changed: { type: 'array', items: { type: 'string' } },
            summary: { type: 'string' },
            residual: { type: 'string' },
            error: { type: 'string' },
          },
          required: ['pr', 'final_codex_verdict', 'pushed', 'verify', 'changed', 'summary', 'residual', 'error'],
        },
      }
    )
  )
)
return { revisions: results.filter(Boolean) }
