# Handout — next session across claude-harness and hippocampus-mcp

Written 2026-07-27 by the coordinator (indigo-lantern) at the close of the
v1.13.0 → v1.13.2 session. Everything below was measured at that moment, not
recalled. Re-measure before acting: several of these facts are about live
machine state, which changes the moment anyone runs an installer.

---

## Where things stand

**claude-harness** — `dev` = `1be8d0e`, `main` = `a354c26`, released through
**v1.13.2**. Nine PRs merged (#169 #170 #171 #172 #175 #178 #179 #181 #182).
Closed: #95, #136, #166, #177.

**hippocampus-mcp** — `dev` = `7304e6b`. Four PRs merged (#219 #197 #199 #226).

No workers, no relays, no background jobs are running. Nine worktrees remain
across both repos, each 1-3 commits ahead of `dev`; none were touched.

### One fact that shapes the whole plan

**The v1.13.1/v1.13.2 fix is not live yet.** Comparing `~/.claude/hooks/*`
against the plugin tree right now: 39 files compared, **2 differ**, and they are
exactly `tmux_self_name.sh` and `codex_hippocampus_session_start.sh` — the two
files the chassis fix changed. Live is stale relative to the repo because no
installer has run since the merge.

That single fact is why the ordering below starts where it does. Any hook
observation taken before re-installing is an observation of v1.13.0-era code.

---

## Ordering principle

Work in this order, and do not reorder without a reason you can state:

1. **Make live match the repo, and prove it.** Until that holds, every
   hook-layer measurement is untrustworthy — and one issue (#180) is explicitly
   blocked on it.
2. **Fix what silently returns wrong answers**, before what returns slow ones.
3. **Defuse schema landmines** — divergence between the live database and
   `migrations/` detonates on the next migration run, not today.
4. **Then design work**, which is the only category safe to leave half-done.

---

## Phase 1 — claude-harness: reconcile live with repo (do this first)

Small, mechanical, and it unblocks the rest.

### 1a. Re-install, then re-observe #180

`#180` (Codex dispatches no SessionStart hook at all) **cannot be diagnosed from
existing evidence.** The v1.13.1 stamp defect supplied a path where a
config-layer command could exit 0 immediately, so "never dispatched" and
"dispatched, then silently no-oped" are currently indistinguishable. The handoff
is recorded on the issue itself.

```
bash install-codex-hooks.sh && bash install-grok-hooks.sh && bash install-kimi-hooks.sh
python3 scripts/sync_hooks_to_live.py
# then, in a throwaway pane:
codex            # and check BOTH:
#   ~/.local/log/codex_session_start.log        (mtime moved?)
#   ~/.local/state/tmux_self_name/decisions.jsonl (a row for this pane?)
```

Pre-install backup of all four live surfaces already exists at
`~/sanada_backup_persistent/i95_live_deploy_20260726_193802/`. Take a fresh one
anyway — it is one command and the old one is a day stale.

- **Fires** → close #180 as "caused by the #181 blocker", and you have also just
  made the #177 fix live. Verify claude self-naming works by running `claude -p`
  in a throwaway pane and checking that no `chassis=codex REFUSE` row appears.
- **Does not fire** → genuine non-dispatch; the five candidate causes on the
  issue apply, and the timeline (box-wide since 2026-07-25 19:41) is the bisect
  anchor.

### 1b. Re-scope #165 with the new number

`#165` says "22 of 26 hooks differ". **Measured now: 2 of 39.** Last session's
`sync_hooks_to_live.py` run closed most of it, and the remaining two are the
stale chassis files that step 1a fixes. Re-measure, update the issue with the
real number, and shrink it to what is left:

- 5 live hooks have **no counterpart in `plugins/`** at all —
  `check_psycopg_placeholders.sh`, `vocab_commit_warn.sh`, `vocab_density_scan.sh`,
  `vocab_doc_warn.sh`, `vocab_terms.sh`. Decide per file: adopt into the plugin
  tree, or delete from live. Do not leave them unclassified — an unowned live
  hook is exactly the shape of defect #177 turned out to be.

### 1c. #174 — still real, re-verify after 1a

The drift checker still reports `DRIFT: codex managed block has duplicate or
unexpected hooks` (reproduced at handout time). #182 taught it about the chassis
stamp; this is a **different** mismatch — the checker's expected form versus the
dispatcher form the installer writes. Re-run after 1a and fix whichever side is
wrong.

Also noted by the same run: `skip: no harness-kimi hook block in
~/.kimi-code/config.toml` — Kimi hooks are not installed on this box. Confirm
that is intentional before treating any Kimi hook behaviour as evidence.

### 1d. #183 — grok compat path

Small and in the same code you will already have open. The broader rail is worth
preferring: make "no chassis declared" **refuse** rather than default to claude,
instead of only stamping one more path.

**Phase 1 exit criteria:** live hooks byte-match the plugin tree (or every
exception is classified), #180 is answered either way, the drift checker is
green, and `claude -p` in a throwaway pane produces a CLAIM row rather than a
REFUSE.

---

## Phase 2 — hippocampus

Independent of Phase 1; can run in parallel by a different worker.

Thirty issues are open, but they resolve into one storyline: **retrieval is
silently covering less than it appears to, and the instruments that would have
said so were themselves wrong.** That dictates the order — instrument, then the
real losses it can now measure, then the landmines, then anything built on top.

### 2a. Reconcile the ledger first (about five minutes)

**#205 is finished but still open.** PR #208 merged 2026-07-26 and the acceptance
items are present in the tree — `library/web_client.py`, `library/html_extract.py`,
`library/blog_ingest.py`, `scripts/personal/ingest_claude_blog.py`, migration 041,
and `claude_blog` in `_LIBRARY_BOOK_SOURCES`. Only the checkboxes were never
ticked. Verify each, then close, so the backlog stops overstating itself.

### 2b. The silent losses — #220, #216, #221

Three independent defects of the same kind: the query succeeds and quietly
returns less than exists.

- **#220 — start here.** `work='gutenberg'` returns **0 results** across 52 books
  / 69,675 chunks, while `work=None` returns the right book for the same query.
  Adding a filter makes the corpus vanish. Smallest fix, largest user-visible
  severity: silence reads as "nothing exists", the worst failure a retrieval
  system has.
- **#216.** `personal.conversations` holds 61,478 rows and only **8,888 carry a
  `conv_dense` vector** — 85% is unreachable from `search_conversations`, which is
  on the connector allowlist. No recall setting can reach them; this is coverage,
  not tuning. `doctor` already prints the ratio as informational, which is how it
  stayed invisible.
- **#221.** The media hybrid's RRF dense leg asks for `LIMIT 100` and gets 38, so
  fusion weighs a truncated ranking against a complete one.

### 2c. Fix the instrument before trusting it — #225, then #209

`#226` (merged) made the bench assert the plan rather than assume it, which is
what revealed that "40/40 recall" cells had been measuring a sequential scan.
**#225 is the next hole in the same instrument**: probes never land in a minority
cluster, so a high recall number can still hide an unreachable one. Fix that
first, or the re-measurement below inherits the flaw.

Then re-measure **#209** (recall at ef=40 drops the correct top-1;
`_LIBRARY_EF_SEARCH=200` still leaves two English technical queries at 0/10;
ef=600 held the index in every probe). **Do not close #209** until the ef=600
finding is either shipped or explicitly deferred with a reason.

**#217** (measure `hnsw.iterative_scan` before adopting it) belongs to this same
instrument-quality group.

A caution that earned itself last session: two agents independently discovered
their own measurements were wrong here — one had compared identity by `book_id`,
the other had read `ef>=700` numbers that were sequential scans. Both corrected
themselves in writing. Treat any single unverified number in this area with
suspicion, including your own.

### 2d. #214 / #215 — schema landmines

Two HNSW indexes exist **only in the live database**, not in `migrations/`, and
`009b_ghost_hnsw` is queued to land unarmed with a different opclass. Neither
hurts today; both hurt on the next migration run, which is precisely when
someone is least able to reason about them.

Canonical database changes need **the operator's own confirmation immediately
before execution** — not a relayed approval, not one bundled into a planning
decision. That rail held last session and should keep holding.

### Suggested session split

1. Close #205, fix **#220**, fix the instrument (**#225**). Ends with one silent
   failure gone and a bench you can believe.
2. Re-measure and fix **#209 / #221 / #216** with that bench. Close #209 on a
   decision, not on fatigue.
3. **#214 / #215**, with the operator present for anything canonical.

The diary-reflections chain (#167 as precondition, Drift A-D in #168-#171, slices
4-6 in #120/#122/#123, epic #97) is a **separate vertical lane**. It does not
touch retrieval coverage and does not contend with the work above, so give it to
a different worker rather than interleaving it.

---

## Phase 3 — the review machinery: #184 with #143

**Do these two together. They are one question asked twice.**

- **#184** — branch to slice-scoped review when a design doc outgrows whole-doc
  rounds.
- **#143** — bound design/review artifact growth by information gain, not only
  round count.

Evidence from the deja-review campaign, which is the whole reason both exist:
the doc reached **6,640 lines / 29 sections**, seven rounds ran, and **every
synthesis returned REVISE** — PLATEAU was never reached and the campaign ended on
the round-8 hard stop. Findings per round: **11, 5, 6, 3, 5, 5, 6** — flat from
round 2 onward, no downward trend across four further rounds. The *kind* changed
too: rounds 3-4 found "the doc claims what it never builds", rounds 5-7 found
"individually correct fixes contradicting each other at the seams", and three of
round 7's four HIGHs were propagation misses introduced by the previous
revision's own edits.

That is revision churn, and more rounds mostly manufacture work for the next
round. Both issues are asking: *what signal says another round has stopped
paying?* Answer it once.

The cheapest usable signal identified so far is **the share of new HIGHs that are
seams from the previous revision rather than fresh defects** — cheap to detect if
reviewers tag findings seam-vs-fresh, and it tracks churn directly. Document size
alone would wrongly slice a large but healthy doc.

Whatever the rule, keep the one behaviour that campaign got right: **terminate
honestly.** It recorded `terminal_state: not PLATEAU`, listed every round's
verdict, and enumerated three DEFERRED items with reasons rather than claiming
convergence.

Adjacent, worth a glance while in this code: #152 (invalid_json_schema has no
failure class), #138 (finding schema uses unsupported `allOf`), #139 (fan-out
reviewers outside the target checkout — **possibly already fixed** by `d9cf2b0`
and `68a9d1c`; verify before working it).

---

## Phase 4 — formation follow-ups

- **#168** — the deferred `formation-mail-nudge` / `formation-window-status`
  helpers. Needs a design pass first: `test_formation_hardening.sh` now **fails
  the build** if `bin/formation` references the nudge helper, so re-landing it is
  a deliberate design decision, not a re-wire. Field evidence is already on the
  issue: an idle worker sat on an unread badge for ~30 minutes because a fully
  idle agent has no turn boundary at which to check its inbox. Any design needs
  an escalation path for ignored badges that does not reopen the
  prompt-injection route the exclusive-inject contract closed.
  Parked patches: `~/sanada_backup_persistent/i95_wiring_leftovers_20260726_190632/`.
- **#176** — bound durable request-event growth without losing unresolved ASK
  state.
- **#173** — per-tmux-session coordinator pane. The design note on the issue
  matters: a coordinator can already see across sessions, so this is not about
  visibility — it is about authority blast radius and coordinator context budget.
  Single cross-session coordinator below N sessions, hierarchy above.
- **#133** — detect worker approval prompts and notify the parent.

---

## Phase 5 — design work that can wait

The hippocampus entries here share a property worth naming: **none of them can
be decided by working harder on them.** #218 needs a measurement, #222 needs an
owner's scope call, #223 needs someone to pick among three written remedies.
Schedule them as decision sessions, not implementation sessions — an
implementation worker pointed at any of these will produce analysis nobody asked
for.

- **#222** (hippocampus) — reconcile DEJA_REVIEW with the Slice-0 implementation
  already shipped in claude-harness (`deja_review_slice0.py`, 2,601 lines, plus
  two schemas and tests, landed `87210ca` on 2026-07-24). The doc's §18.1 still
  describes Slice 0 as future work, and the two disagree on the stage set,
  published outputs, and manifest required fields. Choosing extend / migrate /
  replace is an owner's scope call about shipped code.
- **#218** (hippocampus) — the register axis. The fan-out design was **retracted
  by its own author** after review measurements falsified its premise, and the
  reviewer's objection stands: the "occurrence rate is zero" argument rests on
  numbers taken through a broken ANN path. Re-measure with an exact method before
  redesigning.
- **#223** (hippocampus) — `injection_risk` and `sanitize_flags` are written at
  ingest and never read at retrieval. Real data exists: 199 books / 1,487 chunks
  all `status='active'`, max injection_risk 0.90, five chunks tagged
  `direct_override` / `system_prompt_spoof` / `authority_hijack`. Three remedies
  are listed with no recommendation; it needs an owner more than it needs
  analysis.
- **#224** (hippocampus) — `DENSE_RECALL_AUDIT.md` is stale. Do not act on it
  without re-measuring; its branch is pushed so the content is fetchable.
- **#210** (hippocampus) — `search_personal_memory` has no recency weighting, so a
  pre-2015 archive competes equally with last week. Related in spirit to #218:
  both are about what belongs in the candidate set before ranking touches it.

---

## Traps, from things that actually went wrong last session

- **Do not trust `tmux display-message -t %ID` to test whether a pane exists.**
  It falls back to the current pane and answers for the wrong one. Use
  `tmux list-panes -a -F '#{pane_id}'` and check membership. This nearly caused
  two wrong kills.
- **Before merging, check for pushes that arrived after the CI you looked at:**
  `git fetch && git log <merged-sha>..origin/<branch>`. A merge overtook a
  worker's later commit twice; the second time it stranded a whole fix on a
  branch that was about to be deleted.
- **Waking an idle agent needs `tmux_send_submit`**, not `send-keys` plus Enter —
  text left visible-but-unsubmitted looks identical to a busy agent. Source
  `plugins/harness-formation/lib/wake.sh`. Clear a stale draft with `C-u` first.
- **A skipped test suite and a passing one are the same green in CI.** The
  real-pane dispatch suite now sets `HARNESS_TEST_REQUIRE_TMUX=1` so absence of
  tmux fails instead of skipping — apply the same reasoning to any suite with an
  environment precondition.
- **Cross-family review is not ceremony.** v1.13.1 passed same-family review, an
  independent coordinator check, and CI, and still shipped a defect that the
  codex reviewer found afterwards: an env assignment prefix reaches only the
  first simple command, and every generated hook command is compound. Keep at
  least one non-Claude reviewer on anything touching another CLI's execution
  path.
