# harness-craft

**Behavioral craft skills, distilled from [obra/superpowers](https://github.com/obra/superpowers).**

claude-harness's thesis is *structural-first*: behavioral rules decay under
context pressure, so the core plugins ship hooks and guards that fire even
when the agent forgets. superpowers attacks the same problem from the
opposite direction — a software-development methodology expressed entirely
as prose skills, bulletproofed against rationalization with pressure-tested
wording.

The two approaches are orthogonal, not competing: **superpowers teaches,
harness enforces.** This plugin imports the three superpowers skills with the
highest teaching value, distilled to harness idiom and wired into the
enforcing half (magi escalation, formation briefings, structural-first
gates).

## Skills

| Skill | Distilled from | Harness addition |
|---|---|---|
| `skill-tdd` | `writing-skills` | Structural-first entry gate: if a hook/regex can enforce the rule, write a hook, not a skill |
| `atomized-briefing` | `writing-plans` | Reframed as the briefing format for harness-formation workers and subagent dispatch |
| `root-cause-debugging` | `systematic-debugging` | 3-failed-fixes circuit breaker routes to dual-magi-review; complements the 仗助 fix loop (speed) with aim |

Deliberately NOT imported: TDD / code-review / worktree skills (covered by
Claude Code built-ins and harness-magi), brainstorming (harness-magi's
preflight covers the high-stakes end; the everyday end didn't survive
distillation), and the getting-started scaffolding.

## Install

```bash
/plugin install harness-craft@claude-harness
```

## Attribution

Skill content derives from [obra/superpowers](https://github.com/obra/superpowers)
— MIT License, © 2025 Jesse Vincent. Each SKILL.md carries a per-file
upstream note. Distillation (compression, harness wiring, structural-first
framing) © claude-harness contributors, same MIT terms.
