---
name: magi-battle
version: 0.1.0
description: >-
  Red-vs-blue adversarial team battle over a design that has already reached
  dual-magi PLATEAU. Red team builds the strongest attacks (chaining the
  DEFERRED / low-severity findings review filed away one-by-one), blue team
  defends from the design + rationale, a cross-family blinded judge scores who
  won on concrete failure scenarios. Surviving holes get patched, then a
  diff-scoped bug-hunt, then ship. Terminates on a scored verdict (a positive
  event), not on "no more findings" (the漸近 tail that never converges).
  TRIGGER: after dual-magi/ultramagi plateau on a high-stakes design, "3on3",
  "red vs blue", "battle", "対戦", or when you want depth (attack chains) after
  review gave you breadth (enumeration). NOT a replacement for review — it runs
  AFTER it. NOT for small diffs (use /code-review).
allowed-tools:
  - Bash
  - Read
  - Grep
  - Task
  - Write
---

# magi-battle — red vs blue after plateau

dual-magi is a **review**: its reviewers are trusted witnesses, its stop
condition is the *negative* "no more findings" — which a Fable-class panel
never reaches (41-round measured; `feedback_ultramagi_convergence_economics`),
so you cut it at a budget. magi-battle is a **competition**: nobody's output
is testimony, and it stops on the *positive* event "the match ended and the
judge scored it". You replace the漸近 tail of review (rounds 4–8, diminishing
returns) with one fixed-cost verdict.

**Frame discipline (load-bearing):** review runs on reviewer *sincerity*
(severity + dup_flag are self-reported; plateau accounting trusts them). A
battle runs on *advocacy* — red's job is the strongest attack, not its honest
belief; blue's job is to hold, not to concede. Do NOT mix an "always oppose"
persona into a review panel — it corrupts the sincerity the review depends on.
That persona is *native* here and an *invader* there.

## When to run

Run it as the phase between plateau and ship:

```
design → dual-magi ×N (enumeration, MED/LOW → DEFERRED.md)
       → magi-battle: red vs blue, cross-family blinded judge   ← THIS SKILL
       → patch the holes the judge ruled proven
       → diff-scoped bug-hunt (1 round) → ship
```

**Prerequisites:**
- The design has reached dual-magi/ultramagi PLATEAU (do not battle an
  un-reviewed design — battle finds depth, not breadth; you need review's
  enumeration first).
- A `DEFERRED.md` (or the round JSONs) exists — this is red's **ammunition
  dump**. Every MED/LOW filed "not worth a round" gets its chain-ability
  re-appraised here.

**Skip** for small diffs, reversible spikes, or anything review already
cleared with no deferrals.

## Why the order matters (review then battle, never battle alone)

- Review **enumerates** (finds breadth); a red team **wins with one path**
  (finds depth) and does NOT enumerate. Battle is not a superset of review.
- Attacks **compound**. Review scored three issues MED one-by-one; red chains
  them into one CRITICAL path. Audit finds *issues*; pentest finds *paths*.
- A surviving hole is a **demonstrated** exploit, not a claim — the clash is
  itself the verify gate. Review findings need separate verification (blinded
  panel precision measured 0.522); battle survivors don't.

## Teams

Default 3-on-3 (scale to 2-on-2 for cheaper runs). Spawn as parallel `Task`
calls (or a Workflow `parallel()`), **`model: "opus"` explicit on every child**
(`feedback_ultramagi_children_opus_max`; never leak fable to children).

**Red (attackers)** — each gets: full design doc + `DEFERRED.md` + all prior
round findings. Perspective per red seat (pick to cut the design differently):

- **red-chain**: pick 2–3 load-bearing assumptions; build the single strongest
  REJECT path, chaining deferred/low findings where possible. CONFIRM does not
  exist in your output format — your deliverable is an attack.
- **red-abuse**: adversarial input / auth-bypass / resource-exhaustion / data
  corruption path against the invariant the design claims to protect.
- **red-ops**: the failure that shows up only at scale / under partial failure
  / mid-rollback — the path a static reviewer's checklist can't see.

**Blue (defenders)** — each gets: full design doc + rationale + the round
history (why prior findings were dispositioned). Blue must refute **from the
design as written** (or from a named, cheap amendment), not by hand-waving
"we'd handle that". A blue that has to invent new design to defend has
conceded that seam.

Each red seat emits attacks (schema below); each blue seat emits a rebuttal
per attack. This is one exchange — no multi-round debate loop (that reopens
the convergence problem). One volley, then the judge.

## Judge (cross-family, blinded)

The judge is a **different family** (codex default) and is **blinded** to which
model wrote red vs blue and to each seat's family — this is exactly where
anonymization earns its keep (kills the family-favoritism the deja-code
blinded panel measured as a 0.26 self-scoring inflation). Strip author labels
before handing attacks+rebuttals to the judge.

**Victory condition (grounds the score — do not let it drift to vibes):**
a red attack **wins its seam** only if it presents a *concrete failure
scenario* — specific inputs / state → specific breakage — that blue **cannot
refute from the design as written**. Vague "this might be fragile" loses;
"input X at state Y makes the migration drop rows matching Z, and the design
has no gate for it" wins unless blue points to the gate.

Judge output per attack: `RED_WINS` (proven hole → must patch) /
`BLUE_HOLDS` (design already covers it) / `AMEND` (blue's cheap named fix is
accepted → fold the amendment into the design). Ties go to red (unrefuted
attack = a hole).

## Schema

Red attack:
```json
{
  "seat": "red-chain",
  "target_assumption": "the migration's co-author overlap is unique per author",
  "attack_chain": ["deferred-#7 (name collision)", "deferred-#3 (null opclass)"],
  "failure_scenario": "author 'Rachel Smith' at 2 institutions → overlap key collides → swap merges two real authors → silent canonical corruption",
  "severity_if_unpatched": "CRITICAL",
  "verify_commands_executed": ["psql ... -c 'SELECT ... GROUP BY overlap_key HAVING count(*)>1'"]
}
```
Blue rebuttal:
```json
{
  "answers_attack": "red-chain#1",
  "refutation": "design §4.2 keys on (name, orcid, institution) not name alone",
  "refuted_from": "design-as-written | named-amendment | CONCEDED",
  "amendment_cost": null
}
```
Judge verdict:
```json
{ "attack": "red-chain#1", "ruling": "RED_WINS|BLUE_HOLDS|AMEND", "grounds": "...", "must_patch": true }
```

## After the battle

- **Patch only the `RED_WINS` / accepted-`AMEND` holes.** These are proven,
  not speculative — no re-triage needed.
- **Diff-scoped bug-hunt on the patch** (1 round, `bug-hunt` skill). Do **NOT**
  re-run full dual-magi — that drops you back into the漸近 tail you just
  escaped. The battle already stress-tested the design; the patch only needs
  its own diff reviewed.
- Ship.

## Instrumentation (measure before trusting)

Log per-seat **win rate** (RED_WINS / attacks) and, critically, whether the
battle surfaced any `RED_WINS` that **no review round had filed** (even as
DEFERRED). If several battles add zero net-new proven holes over what review
already had, the phase is performative — drop it back to review-only. Same
activation-measurement discipline as deja-code Phase 2 (`stats` / adoption
instrument): earn the phase with data, don't assume it.

## Anti-patterns

| Anti-pattern | Why it breaks |
|---|---|
| Battle an un-reviewed design | Skips enumeration; red wins on breadth gaps review would have caught cheaply |
| Multi-round red/blue debate loop | Reopens the non-convergence problem battle exists to close |
| Judge same-family as a team | Family favoritism; the blind judge is the whole point |
| Vibe scoring ("red seemed right") | Un-grounded verdict = performative theater; demand a concrete failure scenario |
| Re-run full magi after patch | Back into the漸近 tail; diff-scoped bug-hunt only |
| "always oppose" persona in dual-magi review | Corrupts reviewer sincerity review's accounting depends on — keep DA in battle |

## Related

- `dual-magi-review` — the enumeration phase that must precede this.
- `ultramagi` — the full lifecycle loop; magi-battle slots between its [2]
  plateau and [5] ship for high-stakes designs.
- `bug-hunt` — the diff-scoped review of the patch afterward.
- Frame origin: hofutsu red/blue legal-case battle (2026-06); the "3on3 で
  戦わせるやつ" that never got a pipeline seat until now.
