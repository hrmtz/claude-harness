---
name: skill-tdd
version: 0.1.0
description: |
  Author or edit an agent skill using the TDD loop: run a baseline pressure
  scenario WITHOUT the skill (RED), write the minimal skill that fixes the
  observed failure (GREEN), then close the loopholes agents actually used
  (REFACTOR).

  USE WHEN creating a new SKILL.md, editing an existing one, or when a
  deployed skill is being ignored / rationalized around by agents.
  SKIP when the rule is mechanically enforceable — write a hook, not a skill
  (see Structural-first gate below).
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Agent
---

# skill-tdd — TDD applied to skill authoring

Distilled from [obra/superpowers](https://github.com/obra/superpowers)
`writing-skills` (MIT, Jesse Vincent). Harness addition: the structural-first
entry gate.

## Structural-first gate (harness addition — run this FIRST)

Before writing any skill, ask: **can a hook, guard, or regex enforce this
instead?**

| Enforceable by | Write | Not a skill because |
|---|---|---|
| Pattern match on a command / path / output | PreToolUse / PostToolUse hook | Hooks fire even when the agent forgot |
| A required step at session boundaries | SessionStart / Stop hook | Same |
| Judgment call, no machine-checkable predicate | Skill (proceed below) | Only the residue belongs in prose |

This is the harness core thesis: behavioral docs decay under context
pressure; structure doesn't. A skill is the fallback, not the default.

## The loop (RED → GREEN → REFACTOR)

**RED — watch the agent fail first.** Run a pressure scenario with a fresh
subagent WITHOUT the skill. Record verbatim: what it did, and the exact
rationalizations it used. If the baseline does NOT exhibit the failure, stop
— there is nothing to fix, don't author the skill.

**GREEN — write the minimal skill.** Address only the observed failures, not
hypothetical ones. Re-run the same scenario WITH the skill; the agent should
now comply.

**REFACTOR — close loopholes.** New rationalization found in testing? Add an
explicit counter. Re-test until it holds. Every excuse harvested from testing
goes into a rationalization table (`| Excuse | Reality |`).

No skill ships without a failing baseline first — same Iron Law as code TDD.

## Match the form to the failure

The form that bulletproofs one failure type backfires on another:

| Baseline failure | Right form | Wrong form |
|---|---|---|
| Knows the rule, skips it under pressure | Prohibition + rationalization table + red-flags list | Soft guidance ("prefer…") |
| Complies, but output has the wrong shape | Positive recipe: state what the output IS, parts in order | Prohibition list ("don't restate…") |
| Omits a required element | REQUIRED slot in the template they already fill | Prose reminder near the template |
| Behavior depends on a condition | Conditional keyed to an observable predicate | Unconditional rule + exemption clauses |

Upstream wording tests found prohibitions on shaping problems produced MORE
of the unwanted content than no guidance at all. Also: no nuance clauses
("don't X unless it matters" reopens the negotiation), and exemption clauses
don't scope ("limit doesn't apply to code blocks" still suppresses them).

## Description field (discovery)

- Start with "USE WHEN …", triggering conditions ONLY — symptoms, contexts.
- **Never summarize the skill's workflow in the description.** Agents follow
  the summary instead of reading the body (observed upstream: a two-stage
  review skill ran one stage because the description said "review between
  tasks").
- Third person, keyword-rich (error strings, symptoms, tool names).

## Keep it small

Target < 500 words for a normal skill; move heavy reference to a sibling
file, reusable code to a script. One excellent example beats five mediocre
ones. No narrative war stories — distill to the reusable pattern.

## Red flags — stop and restart the loop

- Writing the skill before running a baseline
- "It's obviously clear, no need to test"
- Batch-creating several skills, testing none
- Editing a deployed skill without re-running its scenario
- Description that paraphrases the body

---
*Upstream: obra/superpowers `writing-skills` — MIT © 2025 Jesse Vincent.
Distilled and adapted for claude-harness (structural-first gate added).*
