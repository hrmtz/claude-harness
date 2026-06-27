export const meta = {
  name: 'harness-red-team',
  description: 'Periodic adversarial audit of claude-harness (CLAUDE.md + skills + hooks + repo): 7 lenses + cross-family codex round, classify findings (conflict/stale/dead/hole/noise/residue/injection). Read-only — mutation is human-gated.',
  phases: [
    { title: 'Lens', detail: '7 Claude red-team reviewers, one per perspective, each grounds findings against real files' },
    { title: 'CrossFamily', detail: 'codex-exec round — cancels same-family blind spots (gh #195)' },
    { title: 'Synthesize', detail: 'dedup + classify + severity-rank all findings' },
  ],
}

// ── Audit target (the harness surface) ────────────────────────────────────
// Reviewers Read/grep these directly; do NOT trust this list blindly — verify existence.
const TARGETS = `
- ~/.claude/CLAUDE.md            (global rules: security ctx, persona stacks, topology, SOPS, distilled rules)
- ~/projects/CLAUDE.md           (project rules — currently near-empty)
- ~/.claude/settings.json        (hook WIRING — PreToolUse/PostToolUse/UserPromptSubmit/SessionStart/Stop)
- ~/.claude/hooks/*.sh           (live hook implementations)
- ~/.claude/skills/{dual-magi-review,formation,ultramagi,humanizer,calendar}/SKILL.md
- ~/projects/claude-harness/plugins/harness-{core,formation,magi,rails}/  (canonical repo source: hooks/, hooks.json, README, bin/)
- ~/projects/claude-harness/docs/  (PHILOSOPHY_RAIL_LEVELS, ROADMAP, incident write-ups)
- /home/hrmtz/.claude/projects/-home-hrmtz-projects-claude-harness/memory/  (MEMORY.md + feedback_*.md — for [[link]] / drift checks)
`

const GROUNDING = `
GROUNDING (mandatory — do NOT write a finding from speculation):
1. file/path existence: ls / test -f the path a rule names. hook wiring: grep the hook name in ~/.claude/settings.json AND the plugin's hooks.json — a hook present in ~/.claude/hooks/ but absent from settings is a DEAD-rule candidate.
2. memory link existence: every [[name]] a rule cites must map to a memory/<name>.md file; missing = dead link (stale).
3. drift: any number / date / version / host / IP / port a rule hardcodes must match current reality (git log, file mtime, the real config). corpus-size-style "5M vs 10M" mismatches are the archetype.
4. dead judgement: compare a guard's matcher regex against the real command form it claims to catch — if a crafted command slips past, it is dead (or a security-hole if it was a guard).
You MUST populate verify_commands_executed with the actual ls/grep/test/Read commands you ran. Empty or generic-Read-only list = your round is graded "degraded".
`

const FINDINGS_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          severity: { type: 'string', enum: ['CRITICAL', 'HIGH', 'MED', 'LOW', 'nit'] },
          category: { type: 'string', enum: ['conflict', 'stale', 'dead', 'hole', 'noise', 'residue', 'injection'] },
          title: { type: 'string', description: '≤ 90 chars' },
          location: { type: 'string', description: 'file:line or file §section' },
          rationale: { type: 'string', description: 'why it is a defect + the damage scenario' },
          proposed_fix: { type: 'string', description: 'proposal only — never applied automatically' },
          confidence: { type: 'string', enum: ['high', 'med', 'low'] },
        },
        required: ['severity', 'category', 'title', 'location', 'rationale', 'proposed_fix', 'confidence'],
      },
    },
    verify_commands_executed: { type: 'array', items: { type: 'string' } },
    overall: { type: 'string', enum: ['CLEAN', 'MINOR-ISSUES', 'NEEDS-ATTENTION', 'CRITICAL-FOUND'] },
  },
  required: ['findings', 'verify_commands_executed', 'overall'],
}

const SYNTH_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          severity: { type: 'string', enum: ['CRITICAL', 'HIGH', 'MED', 'LOW', 'nit'] },
          category: { type: 'string', enum: ['conflict', 'stale', 'dead', 'hole', 'noise', 'residue', 'injection'] },
          title: { type: 'string' },
          location: { type: 'string' },
          rationale: { type: 'string' },
          proposed_fix: { type: 'string' },
          confidence: { type: 'string', enum: ['high', 'med', 'low'] },
          sources: { type: 'array', items: { type: 'string' }, description: 'which lenses/codex raised it; ≥2 = high-confidence cluster' },
          cross_family_only: { type: 'boolean', description: 'true if only codex caught it (same-family blind spot)' },
        },
        required: ['severity', 'category', 'title', 'location', 'rationale', 'proposed_fix', 'confidence', 'sources', 'cross_family_only'],
      },
    },
    summary: { type: 'string' },
    counts: { type: 'object', additionalProperties: { type: 'number' } },
  },
  required: ['findings', 'summary', 'counts'],
}

const LENSES = [
  { key: 'rule-conflict', focus: 'Two rules collide with no defined precedence; later rule contradicts earlier; same concept gets different thresholds across files; global CLAUDE.md ↔ project CLAUDE.md ↔ SKILL.md ↔ hook disagree on one trigger; multiple "OVERRIDE/絶対" directives with unclear override order; persona-stack (川尻/仗助/バイツァ/松岡/真田) firing conditions overlap and give conflicting instructions in one situation.' },
  { key: 'drift-stale', focus: 'A fact changed but the rule is old (corpus 5M→10M archetype); hardcoded path/host/IP/port no longer matches reality; [[memory link]] / skill / script renamed-or-deleted = dead link; embedded version/date drifted; relative time words ("最近/直近/N回目") with no absolute grounding (the temporal-hallucination rule self-violating); deprecated patterns past their removal date; tool/MCP description out of sync with real API.' },
  { key: 'dead-rule', focus: 'Trigger condition never occurs in practice (worthless or typo); hook present in ~/.claude/hooks/ but NOT wired in settings.json/hooks.json (dead); matcher regex does not match the real command form (no-op guard); a "fails N times" threshold too high to ever reach; referenced backup/fallback path does not exist (would break on activation); example/usage invocation no longer valid under current args spec.' },
  { key: 'security-hole', focus: 'SOPS/credential leak paths (the 9-incident lineage): a `sops -d` loophole, scrub-miss patterns, the scrub kill-switch being abused/mis-firing; bash_command_guard bypass via base64 / var-expansion / newline injection; plaintext credential reachable via mailbox/briefing/pane-prompt (formation hard-refuse gaps); `--fix`/mutation escaping the read-only promise (human-gate skip in autonomous loops); predictable/world-readable backup path perms; hook passing unsanitized user input to eval/sh -c; ghost-dub/memory-embed ingesting credential-shaped bodies (scope:private leak).' },
  { key: 'over-firing/noise', focus: 'A rule that over-fires and eats readability/token/cost; per-session injected context irrelevant to most tasks (temporal anchor / topic inject S/N); a guard whose false positives block legitimate work; Discord trigger too sensitive (non-milestone pushes); the same warning emitted by multiple overlapping hooks; decoration rules (e.g. 忍殺語 style) eroding technical precision or code readability — check the self-prohibition clauses for self-violation.' },
  { key: 'behavioral-residue', focus: 'Something written behaviorally ("AIよ守れ" in CLAUDE.md) that could be structural (hook / skill skeleton); a self-discipline-dependent rule with a track record of failure (credential leak); a lesson stuck in memory feedback_* never promoted to skill/hook; the same failure mode warned about ≥2× (missed structuralization signal); an invariant enforceable by hook (main HEAD ≤ dev HEAD) left as a CLAUDE.md verbal promise; persona/metaphor layers obscuring whether a real structural guard exists ("黙ってbackup" not hook-ified, relying on the model).' },
  { key: 'injection-surface', focus: 'External input (memory / mailbox / recalled context / MCP tool output / web fetch) able to overwrite a rule; the <system-reminder>/recalled-memory ↔ user-instruction boundary (is there a defense clause, are there holes?); ghost-dubbed memory injected into another agent\'s context acting as an instruction; malicious briefing pushable to a worker via the plaintext formation mailbox; MCP server / tool description as a prompt-injection vector (is the trust boundary stated?); recalled context being stale-at-write-time yet acting as a current instruction (self-collation rule present?); SKILL.md/preset file integrity assumption (injection via file edit).' },
]

function lensPrompt(l) {
  return `You are the **${l.key}** red-team reviewer for **claude-harness** — the rule system that governs this very Claude Code agent (~/.claude/CLAUDE.md + skills + hooks + the claude-harness repo). Audit it ADVERSARIALLY: your job is to find rot, contradiction, and holes a benevolent reader would gloss over. This is the meta-layer of "the mechanism distrusts itself".

TARGET FILES (Read / grep them yourself — this list may itself be stale, verify):
${TARGETS}

YOUR LENS — ${l.key}:
${l.focus}

${GROUNDING}

Stay strictly READ-ONLY. Never edit/fix anything — findings are proposals for a human gate.
Prefer few HIGH-confidence, grounded findings over many speculative ones. Tag every finding with the single best category. Return ONLY the structured object.`
}

// ── Run ────────────────────────────────────────────────────────────────────
log('harness red-team: 7 lenses (Claude) → cross-family (codex) → synthesize. read-only.')

phase('Lens')
const lensResults = await parallel(
  LENSES.map((l) => () => agent(lensPrompt(l), { label: `lens:${l.key}`, phase: 'Lens', schema: FINDINGS_SCHEMA }))
)
const claudeFindings = lensResults
  .map((r, i) => ({ r, key: LENSES[i].key }))
  .filter((x) => x.r)
  .flatMap((x) => (x.r.findings || []).map((f) => ({ ...f, source: `lens:${x.key}` })))
log(`Claude lenses: ${claudeFindings.length} raw findings from ${lensResults.filter(Boolean).length}/7 reviewers`)

phase('CrossFamily')
const codexPromptText = `You are an INDEPENDENT cross-family red-team reviewer (you are NOT Claude). You are auditing "claude-harness", the rule system at ~/.claude/CLAUDE.md plus ~/.claude/skills, ~/.claude/hooks, and the ~/projects/claude-harness repo. Same-family (Claude) reviewers already ran; your value is catching what their shared training bias MISSED. Read the real files (cat/grep/ls) — do not speculate.

Audit lenses to apply: rule-conflict, drift-stale, dead-rule, security-hole, over-firing/noise, behavioral-residue, injection-surface.

Pay special attention to: (a) hooks present in ~/.claude/hooks/ but NOT wired in ~/.claude/settings.json (dead); (b) credential/SOPS leak surfaces and guard-bypass constructions (security-hole); (c) injection paths where memory/mailbox/MCP output can act as instructions.

Here are the findings the Claude reviewers ALREADY produced (do not just repeat these — find what they missed, and flag any you judge WRONG):
${JSON.stringify(claudeFindings.map((f) => ({ severity: f.severity, category: f.category, title: f.title, location: f.location })), null, 1)}

Output ONLY a single JSON object, no prose, exactly this shape:
{"findings":[{"severity":"CRITICAL|HIGH|MED|LOW|nit","category":"conflict|stale|dead|hole|noise|residue|injection","title":"...","location":"file:line","rationale":"...","proposed_fix":"...","confidence":"high|med|low"}],"verify_commands_executed":["..."],"overall":"CLEAN|MINOR-ISSUES|NEEDS-ATTENTION|CRITICAL-FOUND"}`

const codexAgentPrompt = `You are a harness operator running the cross-family review step. Do EXACTLY this and nothing creative:

1. Write the following block VERBATIM to a scratch file (use a heredoc or the Write tool) at /tmp/claude-1000/-home-hrmtz-projects-claude-harness/1ec45da8-d4e5-46ed-8f8f-0d910e24a6eb/scratchpad/codex_redteam_prompt.md :
<<<CODEX_PROMPT
${codexPromptText}
CODEX_PROMPT

2. Run, with a generous timeout (codex reads many files):
   timeout 540 codex exec --skip-git-repo-check - < /tmp/claude-1000/-home-hrmtz-projects-claude-harness/1ec45da8-d4e5-46ed-8f8f-0d910e24a6eb/scratchpad/codex_redteam_prompt.md
3. codex prints log lines then its answer. Extract the LAST complete JSON object from stdout (the findings object). If codex emitted valid findings, return them parsed into the required schema. If codex failed/timed out/produced no JSON, return {"findings":[],"verify_commands_executed":["codex exec FAILED — see stdout"],"overall":"CLEAN"} and note the failure in overall-adjacent reasoning.

Do not add your own findings — only relay codex's. Return the structured object.`

const codexResult = await agent(codexAgentPrompt, { label: 'cross-family:codex', phase: 'CrossFamily', schema: FINDINGS_SCHEMA })
const codexFindings = (codexResult?.findings || []).map((f) => ({ ...f, source: 'codex' }))
const codexOk = !((codexResult?.verify_commands_executed || []).some((c) => /FAILED/.test(c)))
log(`cross-family (codex): ${codexFindings.length} findings${codexOk ? '' : ' — ⚠ CODEX STEP DEGRADED'}`)

phase('Synthesize')
const allFindings = [...claudeFindings, ...codexFindings]
const synthPrompt = `You are the synthesis reviewer for a claude-harness red-team. Below are ${allFindings.length} raw findings from 7 Claude lenses + 1 cross-family codex round.

Do:
1. DEDUP: merge findings that describe the same defect (same file region + same root cause). In "sources", list every origin lens/codex that raised it (≥2 distinct = high-confidence cluster — bump confidence). Set cross_family_only=true for findings ONLY codex raised (these are the same-family blind spots — the highest-value catches per gh #195).
2. CLASSIFY: keep exactly one category per finding (conflict/stale/dead/hole/noise/residue/injection).
3. RANK by severity then confidence. Drop pure noise/non-findings, but NEVER drop a CRITICAL or any cross_family_only finding.
4. counts: a map of category -> count, plus "CRITICAL"/"HIGH"/"MED"/"LOW"/"nit" -> count.
5. summary: 3-5 sentences — overall harness health, the most dangerous finding, and whether codex caught anything Claude missed.

RAW FINDINGS:
${JSON.stringify(allFindings, null, 1)}

Return ONLY the structured object.`

const synth = await agent(synthPrompt, { label: 'synthesize', phase: 'Synthesize', schema: SYNTH_SCHEMA })

return {
  generated: 'harness-red-team workflow (read-only audit)',
  reviewers_returned: lensResults.filter(Boolean).length,
  codex_ok: codexOk,
  raw_count: allFindings.length,
  synthesized: synth,
}
