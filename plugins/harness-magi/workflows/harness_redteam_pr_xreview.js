export const meta = {
  name: 'harness-redteam-pr-xreview',
  description: 'Cross-family (codex) security review of the 3 draft red-team PRs (#42/#43/#44) per discipline #3. Each agent runs codex on the PR diff and posts the verdict as a PR comment. Read-only review — no merge.',
  phases: [{ title: 'XReview', detail: 'codex reviews each PR diff, posts verdict comment' }],
}

const SC = '/tmp/claude-1000/-home-hrmtz-projects-claude-harness/1ec45da8-d4e5-46ed-8f8f-0d910e24a6eb/scratchpad'

const PRS = [
  {
    n: 42, label: 'xreview:PR42-bashguard',
    ctx: 'PR #42 fixes gh #36: bash_command_guard.sh missed relative .env paths (cat .env, grep KEY .env, python open(".env")) and non-enumerated readers. Fix adds a reader-independent basename-based catalog pattern. Concern axes: false positives (does it block legit commands containing "env"? printenv/exec-env/environment.md/venv), bypass (can a credential read still slip past? quoting, $IFS, base64, path tricks), and whether the ack bypass (HRMTZ_ACK_CRED_READ) is sound.',
  },
  {
    n: 43, label: 'xreview:PR43-formation',
    ctx: 'PR #43 fixes gh #32 (replace banned sops -d|jq idiom with sops exec-env), #34 (redact.sh is_credential_like extend to PG DSN / POSTGRES_PASSWORD / TURSO_AUTH_TOKEN / R2_SECRET_ACCESS_KEY / *_SECRET= *_TOKEN=), #37 (formation inbox wraps mailbox body in UNTRUSTED-DATA envelope to stop prompt injection), #38 (spawn now defaults to normal sandbox; bypassPermissions / --dangerously-bypass gated behind opt-in --bypass-sandbox). Concern axes: (a) #34 regex completeness + false-positive/ReDoS; (b) #37 — can a crafted body still break out of the envelope (fence injection, the banner string itself in the body, multi-line)?; (c) #38 BEHAVIOR CHANGE — does the new default break codex workers that need to write ~/.njslyr7 mailbox? is the opt-in wired correctly, backward compatible?; (d) any banned sops idiom left unreplaced.',
  },
  {
    n: 44, label: 'xreview:PR44-scrub',
    ctx: 'PR #44 fixes gh #39: credential_scrub.py failed OPEN on large outputs (>256KB skipped entirely; candidate runs >4096B skipped) — warned but did not redact. Fix: always scan, chunked/streaming with overlap windows, head+tail+candidate for oversized, flags scan INCOMPLETE honestly. Concern axes: correctness (can a secret straddling a window boundary be missed? is overlap >= longest known secret?), performance/DoS (does always-scanning huge output blow the time budget? is MAX_SCAN_WINDOWS sane?), honesty (does it ever still imply auto-handled when redaction did NOT occur?), and regressions to the existing HMAC known-secret matching.',
  },
]

function agentPrompt(p) {
  const diff = `${SC}/pr_${p.n}.diff`
  const prompt = `${SC}/pr_${p.n}_codexprompt.md`
  const out = `${SC}/pr_${p.n}_codexout.txt`
  return `You are coordinating a CROSS-FAMILY (codex) security review of claude-harness PR #${p.n} and posting the verdict as a PR comment. The live credential scrubber+autorotate are ARMED on this host, and this diff touches credential-pattern code, so you MUST NOT print the raw diff to stdout — only ever redirect it to a file. Do EXACTLY this:

1. Capture the diff to a file WITHOUT printing it:
   gh pr diff ${p.n} -R hrmtz/claude-harness > ${diff} 2>/dev/null ; wc -l ${diff}
   (only the line count reaches stdout, never the diff body.)

2. Build the codex prompt file (use the Write tool, do not cat the diff):
   Write to ${prompt} this header text, then APPEND the diff file to it via Bash:
   header = "You are an INDEPENDENT cross-family security reviewer (NOT Claude). Adversarially review this claude-harness PR diff. ${p.ctx.replace(/"/g, "'")} Output findings as CRITICAL/HIGH/MED/LOW with file:line and a fix, then end with exactly one line: VERDICT: APPROVED / REVISE / REJECT. Be tight. DIFF:"
   Then: cat ${diff} >> ${prompt}   (this appends to a file, nothing to stdout)

3. Run codex, output to a file (never stdout):
   timeout 420 codex exec --skip-git-repo-check - < ${prompt} > ${out} 2>/dev/null ; echo "codex_exit=$?"
   Then read ${out} with the Read tool (NOT cat) to get codex's findings.

4. Post the review as a PR comment (write the body via the Write tool to ${SC}/pr_${p.n}_comment.md first — prefix it with "🔬 **Cross-family (codex) review** — automated per red-team discipline #3 (gh #27 Phase 2). Not a merge gate; for the human reviewer." then the codex findings verbatim), then:
   gh pr comment ${p.n} -R hrmtz/claude-harness --body-file ${SC}/pr_${p.n}_comment.md
   Capture the comment URL.

5. Return the structured result: the codex VERDICT, counts of CRITICAL/HIGH, a 2-3 sentence summary of the most important findings, and the comment URL. If codex failed/empty, say so.

Stay read-only on the repo (no edits, no merge). Return ONLY the structured object.`
  return prompt
}

phase('XReview')
const results = await parallel(
  PRS.map((p) => () =>
    agent(agentPrompt(p), {
      label: p.label,
      phase: 'XReview',
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          pr: { type: 'number' },
          verdict: { type: 'string', enum: ['APPROVED', 'REVISE', 'REJECT', 'CODEX_FAILED'] },
          critical: { type: 'number' },
          high: { type: 'number' },
          summary: { type: 'string' },
          comment_url: { type: 'string' },
        },
        required: ['pr', 'verdict', 'critical', 'high', 'summary', 'comment_url'],
      },
    })
  )
)

return { reviews: results.filter(Boolean) }
