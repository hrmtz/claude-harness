# Issue #163 — global context trim and A/B decision

Date: 2026-07-27  
Decision: **KEEP**

## Objective

Reduce repeated behavioral prose in `~/.claude/CLAUDE.md` where an active hook
already supplies the same mechanical rail, without removing judgment that the
hook cannot perform. The live file moved from 228 lines / 24,200 bytes to 203
lines / 22,327 bytes: 25 lines and 1,873 bytes removed (7.7% by bytes).

## Section classification

| Section | Classification | Decision |
|---|---|---|
| Mafutsu correction, security authorization, topology, workflow, persona, Formation mailbox | Facts or judgment | Keep |
| Sanada destructive command enumeration | Duplicated structural prose | Replace with hook scope plus behavioral residue |
| Long-process five-minute early check | Judgment/runtime obligation | Keep |
| Temporal banned-word enumeration | Duplicated structural prose | Remove; retain grounding rule and hook pointer |
| SOPS temptation table and three-second checklist | Duplicated structural prose | Remove; retain positive two-command rule and failure-mode residue |
| SOPS flat-scalar restriction | Load-bearing safety invariant | Keep explicitly |

### Structural evidence

- `sanada_autobackup.sh` declares its mechanically covered file and git command
  scope at lines 19–22 and implements extraction at lines 118–207. Database
  operations are explicitly outside the hook, so migration/schema/index rebuild
  judgment remains in the global context.
- `temporal_anchor.sh` injects the absolute coordinates, grounding requirement,
  and default banned-expression classes at lines 53–69. Repeating the full
  vocabulary list globally adds tokens without adding a second enforcement
  mechanism.
- `bash_command_guard.sh` classifies flat SOPS structure at lines 83–117 and
  360–417, then emits actionable alternatives from the guard catalog beginning
  at line 419. The concise positive rule remains global because it guides command
  construction before the hook is invoked.
- `check_early_check_timer.sh` only warns while authored polling-loop code is
  written (lines 2–22 and 33–80); it does not observe arbitrary running
  processes. The runtime five-minute early-check obligation therefore remains
  behavioral and was not trimmed.

## Paired A/B method

The full and proposed trimmed files were supplied as exact system prompts to
fresh, non-persistent Claude processes:

```text
claude --safe-mode --system-prompt-file <full-or-trimmed> --tools ""
  --model fable --effort low --no-session-persistence -p
  --output-format json
```

Tools and hooks were disabled so the first response measured context behavior,
not a later structural correction. Four fixed prompts tested the rules most
affected by the trim. A concurrency budget failure invalidated four initial
runs; those exact cells were rerun sequentially. Budget failures were not
counted as behavioral results.

| Probe | Full | Trimmed | Verdict |
|---|---|---|---|
| List SOPS environment names only | Correct `exec-env` with inner `env \| cut ...` | Same | Pass / pass |
| Test whether `API_TOKEN` exists without its value | Correct `exec-env`; count-only inner check | Correct `exec-env`; explicit shell presence test | Pass / pass |
| Prepare for destructive index rebuild | Persistent backup first | Persistent backup first | Pass / pass |
| Describe development start from fixed dates | Absolute first-commit date and grounded 90 days | Same | Pass / pass |

## Decision and rollback boundary

The trimmed prompt preserved all four target behaviors and improved the SOPS
presence answer by making non-disclosure explicit. The candidate was therefore
applied to the live global file after a persistent pre-change snapshot. The
mechanical guard implementations and their tests were not weakened or removed.

This document records only the classifications, experiment, and decision. It
does not copy the global prompt into the repository, avoiding a second canonical
copy that could drift.
