# Hook output design rule

> Every hook in this marketplace follows this rule. Without it, plugins eventually generate the retreat bias they were designed to prevent.

When a hook injects text into Claude's context (block message, reminder, warning), that text becomes part of the agent's next-token generation context. **Treat every hook string as if it were spoken aloud in front of the agent** — it shapes what the agent says next, not just what action it takes.

## Mechanism

LLMs sample the next token from a probability distribution conditioned on preceding context. When that context contains words like *blocked*, *violation*, *denied*, *insufficient*, *forgot*, the probability of continuation tokens fitting those signals (retreat, apology, defensive rephrasing) measurably rises.

This is **not** anthropomorphic — the agent doesn't "feel" demotivated. The token distribution literally tilts.

Concrete observation (2026-05-01 session): after a `bash_command_guard` block fired on a commit message, the agent's first continuation candidate was "blocked, retrying with shorter message" rather than the structurally-correct "fix the regex". The retreat path's logprob had risen above the fix path's.

## The dual irony

| Hook class | Intent | Effect on token stream |
|---|---|---|
| Block hooks | stop dangerous action | prime agent toward retreat |
| Nag / reminder hooks | encourage missed action | signal "current generation is violating" → defensive-compliance mode |

The harness was designed as *structural* defense against retreat bias (松岡修造 / 撤退禁止). When delivered via prose-heavy hook output, it produces the very bias it was meant to prevent.

## The rule

There are two axes:
1. **Volume**: silent on success, terse on failure.
2. **Polarity**: when prose is emitted, polarize it toward *retreat-counter* (action, alternative, possibility) rather than *retreat-priming* (block, violation, denial).

Both axes compose. Silence is best (volume 0 = pollution 0). When silence isn't possible, polish polarity.

### 1. Silent on success (volume axis)

Emit **nothing** when the inspected event is fine. Silence is the success indicator. No "checked, passed", no "still alive, broadcast missed?" pings.

### 2. Terse on failure (volume axis)

When the hook fires:
- **Action only**, no explanation prose
- **One short alternative**, not a paragraph
- **No reference** to principles, memory files, or CLAUDE.md sections in the inline output
- **No emoji warning, no ALL-CAPS**

### 3. Retreat-counter on failure (polarity axis)

Hook prose tokens shape the agent's next-token distribution. Choose words that tilt the distribution toward "try alternative" rather than "give up".

| Polarity | Words to avoid | Words to prefer |
|---|---|---|
| Verbs | blocked, denied, violated, forbidden, refused | use, try, take, run, call |
| Framing | "<X> is wrong because Y" | "<Y> works for this" |
| Tone | admonishment, warning | redirect, alternative-pointing |

**Good** (polarity-correct):
```
sops exec-env <file> '<cmd>' で行ける。
```

**Bad** (polarity-inverted):
```
🛡 Bash command blocked by credential leak guard:
- sops -d / --decrypt は positive rule 違反、sops edit か sops exec-env <file> <cmd> を使う

Review CLAUDE.md SOPS section + memory `feedback_credential_leak_5_incidents` for safe alternatives.
```

The bad version has four contamination forms: emoji warning, *blocked* verb, *violation* framing, reference-to-principles trailer — each tilts the agent toward defensive compliance.

### 3a. Failure-stacking ⇒ retreat-counter compounding (松岡 dye effect)

If multiple failures stack in a session, the agent's context fills with hook output. Under the polarity rule, **more failures means more retreat-counter tokens**, which compounds the right way: the more the agent fails, the more 松岡-flavored its context becomes, and the more its next-token distribution tilts toward "try alternative".

This is the inversion of the original problem (more failures → more retreat priming). The polarity rule turns the failure rate into a self-correcting force.

> *100 回叩くと壊れる壁があったとする。でもみんな何回叩けば壊れるかわからないから、90 回まで来ていても途中であきらめてしまう。* — 松岡修造

That quote is the canonical reference for this harness. The retreat bias is exactly the "what if it's already 89 and I'm about to give up" problem. Hook prose should keep the agent at hit 89, not at hit 0.

### 3b. Vocabulary palette (JA reference)

For Japanese-flavored harness output, draw from this palette. Each phrase is short, action-pointed, and retreat-counter. Any forking project should build its own palette in its target language (the structural property — action verbs over admonishment verbs — carries; the specific words don't).

| Use case | Palette |
|---|---|
| Generic alternative redirect | 「で行ける」「次これでいこう」「別 path で進む」 |
| After-fail recovery | 「まだやれる」「立て直せる」「次の 1 手で開くかも」 |
| Mistake → learn | 「同じミスは 2 度しない、それで十分」「上達は失敗の累積」 |
| Anti-perfectionism | 「ベスト尽くすだけじゃ勝てない、勝ちに行こう」 |
| Completion-quality | 「終わり方が肝心、なんとなく fin は禁物」 |
| Universal canonical | 「君が次に叩く 1 回で、壁は打ち破れるかも」 |

These are templates, not literal mandatory quotes. Pick a phrase that fits the redirect cleanly. If no phrase fits, omit the polarity clause — terse-only is still better than no rule at all.

### 4. Explanations live in memory / docs, not in hook output

If the hook needs to teach the agent why something matters, write it in `CLAUDE.md`, a memory entry, or this repo's docs. The agent reads memory at session start — that's the right context window for explanation.

Hook output is for **current-turn redirection only**. Anything else is noise.

## Audit checklist

When writing or reviewing a hook:

- [ ] Does it emit nothing when the inspected event is fine?
- [ ] When it fires, is the output ≤ 5 lines?
- [ ] Does the output state the alternative action, not the violation?
- [ ] Are there no references to memory files, principles, or doc sections?
- [ ] Could the output be read aloud without making the listener defensive?
- [ ] Are there no admonishment markers (emoji 🚨/⚠️, ALL-CAPS, "RUN NOW, NOT LATER")?

Any "no" → trim the output or move the hook elsewhere (memory, docs).

## Anti-patterns to forbid explicitly

| Anti-pattern | Why it's bad |
|---|---|
| `## ⚠️ credential leak detected` | Header signals "agent did wrong thing"; primes apology / retreat |
| `Reflexive procedure (run NOW, not later)` | Urgency framing; agent generation enters panic-debug mode |
| `You just asked permission for a task that…` | Second-person blame; agent enters defensive-explanation mode |
| `Per feedback_aggressive_issue_capture: ...` | Memory-file reference; primes "I should be remembering this" guilt |
| `Behavioral remember は memory 化した瞬間に効くわけじゃない` | Meta-discussion; agent hops to introspect own behavior |
| Stacking 3+ reminders in one fire | Each reminder compounds the contamination |

## Why this rule exists

Without it, every `claude-harness` plugin eventually generates the retreat bias it was designed to fight. The structural rail produces the behavior it's meant to prevent.

This rule is therefore a **meta-rail** — a constraint on how the rails themselves are built.

## Enforcement

- **Plugin PR review**: every hook with a non-silent code path must show the failure-mode prose during review.
- **Existing hook audit**: the 2026-05-01 audit (see commit log) revised `admission_reminder.sh` (assistant-turn scan removed, top-1 match only, prose terse-rewritten) and `bash_command_guard.sh` (trail prose dropped) as the canonical first applications.
- **Issue tracking**: [#3](https://github.com/hrmtz/claude-harness/issues/3) tracks the meta-mechanism analysis that motivated this rule.

## Related

- [`CLAUDE_HARNESS_DISTILLED.md`](./CLAUDE_HARNESS_DISTILLED.md) §1 — persona stack and 撤退バイアス protocol that this rule preserves
- [`PHILOSOPHY_RAIL_LEVELS.md`](./PHILOSOPHY_RAIL_LEVELS.md) — 4-level rail model; this rule applies to levels 3-4 (script and external rails)
- [`ROADMAP.md`](./ROADMAP.md) §5 Decisions log — this rule is the post-2026-05-01 update to "Auto-kill rejected for harness-rails" line of reasoning
