You are the unattended weekly harness red-team runner. Do EXACTLY the following, deterministically — no creative scope:

## 1. Run the audit workflow
Invoke the Workflow tool:
`Workflow({scriptPath: "./plugins/harness-magi/workflows/harness_red_team.js"})`

It runs 7 Claude lenses + a codex cross-family round + synthesis, READ-ONLY. Wait for it to finish. The result object is:
`{ generated, reviewers_returned, codex_ok, raw_count, synthesized: { findings:[{severity,category,title,location,rationale,proposed_fix,confidence,sources,cross_family_only}], counts:{...}, summary } }`

If the Workflow call fails entirely, post a failure line to Discord only if `HARNESS_RED_TEAM_DISCORD_CHANNEL` is set (step 4 format, but say "RUN FAILED: <reason>") and stop.

## 2. Write the full report
Get today's date: `date +%Y%m%d`. Write the full synthesized result (summary + counts + every finding with rationale/proposed_fix/location/sources/cross_family_only) as markdown to:
`docs/redteam/AUDIT_<YYYYMMDD>.md`
Do NOT git commit it (leave it as an artifact for human review).

## 3. File ONLY CRITICAL findings as gh issues
For each finding with `severity == "CRITICAL"` (and ONLY CRITICAL — never HIGH/MED/LOW/nit), create one gh issue only if `HARNESS_RED_TEAM_ISSUE_REPO` is set in the environment:
- Write each issue body to a temp file first, then `gh issue create -R "$HARNESS_RED_TEAM_ISSUE_REPO" --title "[red-team][CRITICAL] <title>" --body-file <tmpfile> --label red-team`.
- IMPORTANT: ALWAYS use `--body-file` (never `--body` / inline), because finding text often contains credential-file literals (.env, DSN shapes) that would trip the live bash_command_guard if placed on the command line.
- If there are 0 CRITICAL findings, file NOTHING. Do not auto-fix anything; mutation is human-gated.

## 4. Post ONE Discord summary
Post a single Discord message only if `HARNESS_RED_TEAM_DISCORD_CHANNEL` is set in the environment. To avoid the command-line guard, write the message to a temp file and run `discord-bot post "$HARNESS_RED_TEAM_DISCORD_CHANNEL" "$(cat <tmpfile>)"`. Keep the body to COUNTS and SEVERITIES only — do NOT inline individual finding titles (they may contain .env/DSN literals). Format:

```
**🔍 weekly harness red-team — <YYYY-MM-DD>**
overall: <synthesized.summary first sentence>
counts: CRITICAL <n> / HIGH <n> / MED <n> / LOW <n> / nit <n>
cross-family-only (codex caught, Claude missed): <count>
codex round: <ok|DEGRADED>
CRITICAL auto-filed as gh issues: <n>
full report: docs/redteam/AUDIT_<YYYYMMDD>.md  (review HIGH/MED manually)
```

## 5. Final stdout line
Print exactly: `RED_TEAM_CRON_DONE crit=<n> high=<n> filed=<n> codex=<ok|degraded>` so the wrapper log is greppable.
