# i95-kimi-probe — tmux identity surface measurements (kimi / grok chassis)

Date: 2026-07-26. Host: chichibu, tmux 3.4. All experiments in throwaway
sessions `i95kimi-*` created by this worker; session `0` untouched. Grok CLI
version 0.2.112 [stable] (`grok inspect`). Snapshot format per row:

```
win=[#{window_name}] title=[#{pane_title}] fid=[#{@formation_id}] lock=[#{@formation_identity_locked}] autorename=#{automatic-rename} allowrename=#{allow-rename}
```

Baseline option values (global, this host): `set-titles off`,
`automatic-rename on`, `allow-rename off`. `@formation_identity_locked` was
empty in every scenario (nothing in the wild sets it today).

---

## 1. Fact table (BEFORE matrix)

Legend: CLEAN = no surface changed that the scenario's CLI didn't own;
DRIFT = the command rewrote identity surfaces.

| # | scenario | before | after | verdict |
|---|----------|--------|-------|---------|
| 1 | `kimi --help` on `claude-testname` | `win=[claude-testname] title=[chichibu] fid=[] lock=[] autorename=0 allowrename=0` | identical | **CLEAN** |
| 2 | `kimi doctor` on `claude-testname` | `win=[claude-testname] title=[chichibu] fid=[] lock=[] autorename=0` | `win=[kimi-silent-tanuki] title=[kimi-silent-tanuki] fid=[silent-tanuki] lock=[] autorename=0` | **DRIFT** — renamed window AND pane title, minted fid |
| 3 | kimi interactive, fresh pane | `win=[zsh] title=[chichibu] fid=[] autorename=1` | +10s: `win=[kimi-silent-otter] title=[kimi-silent-otter] fid=[silent-otter] autorename=0`; +20s: `title=[Kimi Code]` | **DRIFT** (claims pane), then **kimi's own TUI overwrites the hook-set pane title** with `Kimi Code` |
| 4 | in kimi pane: `claude --help` | `win=[kimi-silent-otter] title=[Kimi Code] fid=[silent-otter] autorename=0` | identical | CLEAN |
| 4 | in kimi pane: `claude -p 'say ok'` | as above | `win=[claude-silent-otter] title=[claude-silent-otter] fid=[silent-otter] autorename=0` | **DRIFT** — claude re-prefixes chassis, **keeps the kimi-minted codename** |
| 5 | `grok --help` on `claude-testname` | `win=[claude-testname] title=[chichibu] fid=[] autorename=0` | identical (checked at +5s and +10s) | **CLEAN** — confirms lead's 2026-07-26 measurement |
| 6 | grok interactive, fresh pane | `win=[zsh] title=[chichibu] fid=[] autorename=1` | `win=[grok] title=[grok] fid=[] lock=[] autorename=1 allowrename=0` | **CLEAN, with nuance** — see below |
| 7 | in grok pane (after exit): `claude -p 'say ok'` | `win=[zsh] title=[grok] fid=[] autorename=1` | mid-run: `win=[claude] fid=[sable-koto] autorename=1`; final: `win=[claude-sable-koto] title=[claude-sable-koto] fid=[sable-koto] autorename=0` | **DRIFT** — claude claims the pane, mints a new fid (pane had none) |
| 8 | in claude pane (after exit): `grok --help` | `win=[zsh] title=[] fid=[moss-heron] autorename=1` | identical | **CLEAN** |
| 8 | in claude pane: grok one-shot `grok --prompt-json '[{"type":"text","text":"say ok"}]'` | `win=[zsh] title=[] fid=[moss-heron] autorename=1` | identical (printed `ok`, exited) | **CLEAN** — grok never touches fid; claude's `moss-heron` preserved |

### Scenario 6 nuance (refines the lead's claim)

The lead measured: "interactive grok leaves `window_name` alone but sets
`pane_title` to `grok`." **Confirmed, with one correction in mechanism:**
`win=[grok]` appears with `autorename=1` — that is **tmux's own
automatic-rename tracking the foreground command**, not grok renaming the
window. Proof: after grok exits, the window flips back to `win=[zsh]`
(`POST-EXIT: win=[zsh] title=[grok] ... autorename=1`). A manual
`rename-window` while grok runs is never restored or overwritten (section 2).
Grok itself touches **only** `pane_title`, via OSC from the TUI.

### Other raw observations

- kimi's TUI emits its own OSC title (`Kimi Code`) ~10-20s after boot,
  overwriting the harness hook's `kimi-<codename>` pane title. Window name
  keeps the identity.
- claude interactive sets `title=[✳ Claude Code]` and `fid=[moss-heron]` but
  leaves `autorename=1` and does **not** rename the window while idle at the
  prompt; on exit it clears the pane title (`title=[]`). The
  `claude-<codename>` window rename for one-shot `claude -p` lands late
  (window showed bare `win=[claude]` mid-run, full rename only at completion).
- grok's one-shot (`--prompt-json`) and `--help` emit **no** OSC title at all;
  only the interactive TUI does.
- kimi honors the chassis-prefix convention symmetrically: `claude -p` on a
  kimi pane produced `claude-silent-otter` — codename preserved across
  chassis takeover.

## 2. The grok surface answer (highest-value item)

Setup: fresh pane, `grok` interactive. Boot state:
`win=[grok] title=[grok] fid=[] autorename=1 allowrename=0`.

### 2a. Does `select-pane -T` survive? — **No, not across real use.**

Applied `tmux select-pane -T grok-testid` at t0. Timeline:

| event | elapsed | pane_title |
|---|---|---|
| `select-pane -T grok-testid` | t0 | `grok-testid` |
| idle | ~90s | `grok-testid` |
| `resize-pane` (200x50 → 150x40) | — | `grok-testid` |
| keystroke typed + deleted | — | `grok-testid` |
| copy-mode scroll PageUp | — | `grok-testid` |
| idle | ~6 min total | `grok-testid` |
| **submit prompt "reply with exactly: ok"** | — | **`User Requests Exact "ok" Reply Only - grok`** |

Grok's OSC emission is **event-driven, not redraw-driven**: passive redraws
(resize, input echo, scroll) never re-emit, so a hand-set title looks stable
for minutes — but the next prompt submission regenerates
`<session-title> - grok` and clobbers it. Observed across the rest of the
session: the regenerated title persists indefinitely afterward.

### 2b. Does `rename-window` stick while grok runs? — **Yes.**

`tmux rename-window -t %194 grok-testwin` → prompt submitted → +30s:
`win=[grok-testwin] ... autorename=0` throughout. Grok never writes the window
name. A second run with the window renamed `grok-testwin2` and
`allow-rename=on` gave the same result: `win=[grok-testwin2]` unchanged across
a prompt. **No tmux option changes this answer** — grok's OSC targets the pane
title only, and never attempts a window rename even when `allow-rename=on`
would permit one.

### 2c. Option values recorded

`allow-rename = off`, `set-titles = off`, `automatic-rename = on` (global,
this host). None of them gates grok's pane-title OSC (pane titles are always
settable); `allow-rename` would only matter if grok tried to rename the
window, which it never does (2b).

### Verdict

**A grok pane can hold a `grok-<codename>` identity on `window_name` only.**
`rename-window` at SessionStart (or spawn time) survives arbitrary prompts,
redraws, and resizes. `pane_title` is owned by grok's TUI: any externally set
value — hook or human — is overwritten on the next prompt submission. Setting
pane_title additionally is harmless but must be treated as cosmetic and
ephemeral. (kimi behaves the same way via its `Kimi Code` title; claude is the
only chassis whose TUI title carries branding without clobbering on prompts —
it clears on exit instead.)

## 3. Grok SessionStart hook reality check

Method: isolated, zero writes to `~/.grok`. Project-scoped hook
`/tmp/i95/proj/.grok/hooks/i95probe.json` (SessionStart → script recording
timestamp + selected env + current tmux surfaces, then
`tmux select-pane -T grok-i95probe`), launched with `GROK_FOLDER_TRUST=0`.
Findings:

1. **Project hooks require a git root.** `grok inspect` in a plain
   `/tmp/i95/proj` directory reported `Hooks (40)`, all `user` scope — the
   project hook silently undiscovered despite `Project trusted: yes`. After
   `git init`: `Hooks (41)` with one `project`-scoped entry. This git-root
   requirement is not stated in `~/.grok/docs/user-guide/10-hooks.md` (the
   discovery table says only `<project>/.grok/hooks/*.json` + trust).
2. **SessionStart fires at first prompt submission, not at TUI boot.**
   Launched 1785057803.66; hook fired 1785057856.45 — 52.8s later, exactly
   when the first prompt was submitted. The TUI sits at the prompt with no
   session (and no hook) until then. Debug log corroborates: boot-time
   discovery logged `total_hooks=0`; `session.spawn{... start_type="new"}`
   with `global_sources=2 project_sources=2` appears only at first prompt.
3. **TMUX_PANE is present in the hook environment.** Recorded at fire time:
   `TMUX_PANE=%205`, `GROK_HOOK_EVENT=session_start`, `GROK_SESSION_ID` set,
   `GROK_WORKSPACE_ROOT=/tmp/i95/proj/`, `PWD=/tmp/i95/proj`.
4. **But the hook loses the pane-title race.** At hook execution grok's boot
   title was already in place (`pane_title_at_hook=grok`); the hook's
   `select-pane -T grok-i95probe` was then overwritten by grok's session-title
   generation for that same prompt — final state
   `title=[User Requests Exact Ok Reply Only - grok]`. Consistent with §2:
   SessionStart cannot secure pane_title; it must `rename-window` (proven
   stable in §2b).
5. `GROK_HOME`-based isolation is **not** viable for authenticated tests: a
   fresh `GROK_HOME` loses auth (device-code login screen), and SessionStart
   does not fire on the login screen. Project-dir + `GROK_FOLDER_TRUST=0` is
   the working isolation pattern.

## 4. Kill-switch reality check — `GROK_TMUX_NAME_DISABLE`

- **Claim "exported by `harness-cross-cli`, read by nobody": confirmed for
  the shipped tree.** Exporter: `plugins/harness-core/bin/harness-cross-cli`
  (sets `=1` at lines 50/67/95/118/135, `-u` unsets elsewhere). Repo-wide grep
  finds readers only in `plugins/harness-core/lib/identity_owner.sh` —
  **untracked, the lead's in-flight WIP** (its own comment: "Before this,
  GROK_TMUX_NAME_DISABLE was exported by harness-cross-cli and read by
  nobody"). So the briefing's claim describes the pre-fix state accurately.
- **Verified by measurement, not grep alone:** fresh pane,
  `GROK_TMUX_NAME_DISABLE=1 grok` → `win=[grok] title=[grok] fid=[] autorename=1`.
  The var has no effect on grok's OSC title emission; grok doesn't know it exists.
- **Does grok honor any env var that suppresses its OSC title?** **No.**
  Docs grep for `GROK_*TITLE*` across `~/.grok/docs/` returns nothing; there
  is no `--config` CLI flag either. The only knob is config.toml
  `[ui.notifications.title] enabled = false` (documented in
  `05-configuration.md`, "Set the terminal title to reflect agent state").
  Measured via isolated `GROK_HOME=/tmp/i95/grokhome2` with that key set:
  the boot/login-screen title is **still** emitted (`title=[grok]`). Whether
  it suppresses the in-session `<session-title> - grok` regeneration could
  not be tested without editing `~/.grok/config.toml` (needs lead sign-off);
  even if it works there, it is a global user config change, not a per-launch
  kill switch.

## Implications for `identity_owner.sh` (storm-raven)

1. Grok adapter: put identity on **`window_name`** (`rename-window`); treat
   `pane_title` as grok-owned, overwritten on first prompt.
2. A grok naming hook on SessionStart fires at first prompt with `TMUX_PANE`
   set — late enough that grok's boot title already exists, early enough to
   rename the window before the user reads it as unowned. For pre-prompt
   identity, the launcher (`formation spawn`) must rename the window itself;
   no hook will run at TUI boot.
3. Same pane_title caveat applies to kimi (`Kimi Code` overwrites hook-set
   titles ~10-20s after boot); kimi identity already lives on window_name.
4. The legacy per-chassis kill-switch names are inert against the CLIs
   themselves; harness-side readers (as in the WIP `identity_owner.sh`) are
   the only place they can ever work.

## Cleanup

All `i95kimi-*` throwaway sessions killed. Artifacts under `/tmp/i95/`
(measurement scratch, hook JSON, debug log) left for inspection; nothing
written outside the repo report, `/tmp`, and this file.
