# Hook install verification — making "committed" and "in effect" the same fact

Status: draft for review
Issue: #165 (drift census), #164 (the incident that exposed it), #118 (names this
failure in its motivation)
Date: 2026-07-26

## 1. The defect

`~/.claude/hooks/` holds **copies** of the hooks that live in
`plugins/*/hooks/`. Nothing compares the two trees, so they diverge silently in
both directions. Measured on chichibu, 2026-07-26:

```
compared   26
identical   4
differs    22
  source newer than installed : 20
  installed newer than source :  2
orphans (installed, no source at all) : 5
```

Only 4 of 26 hooks are the file their source says they are.

The consequence is not "some files are stale". It is that **"I committed the
fix" and "the fix is running" are independent facts with no signal connecting
them**, and neither direction announces itself:

- **source newer (20)** — a fix was written, reviewed, committed, and never took
  effect. The author has every normal reason to believe it is live.
- **installed newer (2)** — a fix was applied directly to the running copy under
  incident pressure and exists nowhere in git. The next install reverts it, also
  silently.

### 1.1 How it surfaced

During the formation runtime-home cutover (#164) the legacy mailbox path was
fixed in `plugins/harness-core/hooks/admission_reminder.sh` and committed in
`3b121bd`. The live rail kept injecting the retired path, because the file that
actually runs is `~/.claude/hooks/admission_reminder.sh`.

It fired **in the same turn that delivered the cutover notice** — telling the
receiving agent to `tail` the retired mailbox at the moment the notice said to
stop using it. A peer worker (`ember-vireo`) hit the injection in its own session
and patched the installed copy by hand, which is how `admission_reminder.sh`
became one of the two installed-newer files.

### 1.2 The second, larger gap

The reference sweep that preceded the cutover ran over the `claude-harness`
checkout. `~/.claude/hooks/` is outside it, so the sweep was **structurally
incapable** of seeing the surviving reference — not careless, blind.

This generalises past this incident. Every "did we catch all the references?"
check we run is repo-scoped, and the installed tree is invisible to all of them.
Any fix here should make the installed tree *enumerable from the repo*, or the
next cutover repeats this exactly.

## 2. Constraints discovered while scoping

**Five installed hooks have no source anywhere in the plugin tree:**

```
check_psycopg_placeholders.sh
vocab_commit_warn.sh   vocab_density_scan.sh
vocab_doc_warn.sh      vocab_terms.sh
```

Nothing to install them *from* and nothing to restore them *to*. They are one
`rm` from gone. (The `vocab_*` set is the machinery #56 is about, so it is not
abandoned code.)

**The two installed-newer files are substantial**, not whitespace drift:
`ghost_inject.sh` (46 changed lines) and `recent_topics_inject.sh` (47), both
SessionStart injectors, installed 07-20 against a 07-10 source. Ten days of
behaviour that exists only on this box.

Both sets are copied to
`~/sanada_backup_persistent/hooks_installed_newer_20260726_161500/` so no
decision here is time-pressured.

## 3. Failure taxonomy — why the obvious fix is the wrong one

The tidy answer is to replace the copies with symlinks into the plugin tree.
Divergence then becomes impossible. It should still be rejected, on evidence
from the same day.

Four `~/.local/bin` symlinks were found dangling because their target checkout
(`projects/_formation_wt/formation-live`) had been deleted: `formation`,
`safety-rails-beat`, `safety-rails-preflight`, `safety-rails-watcher`. The
`formation` CLI had not been running **at all**, and nobody had noticed.

Symlinking rails into a git checkout makes rail *availability* depend on that
checkout existing and being on a branch that contains the file:

| event | copies | symlinks |
|---|---|---|
| repo moved or deleted | rail runs, stale | rail **absent** |
| `git checkout <other branch>` | unaffected | rail silently becomes that branch's version |
| mid-rebase / bisect | unaffected | rail changes under the session |
| fix committed | **does not land** | lands immediately |

For a credential scrubber the asymmetry is decisive: **copies fail stale, links
fail open.** A scrubber running last week's patterns still scrubs. A scrubber
that is not there scrubs nothing, and its absence is indistinguishable from
"nothing matched".

The defect to fix is therefore not "the trees can diverge" — it is "divergence
is unobservable". Keep the copies; make the relationship verifiable.

## 4. Proposed contract

### 4.1 Install manifest

Installation writes `~/.claude/hooks/.install-manifest.json`:

```json
{
  "schema_version": "harness-hook-install/v1",
  "generated_at": "2026-07-26T16:20:00+09:00",
  "source_commit": "3b121bd",
  "entries": [
    {
      "name": "admission_reminder.sh",
      "sha256": "…",
      "origin": "plugin",
      "source_plugin": "harness-core",
      "source_path": "plugins/harness-core/hooks/admission_reminder.sh",
      "installed_at": "2026-07-26T16:20:00+09:00"
    },
    {
      "name": "vocab_terms.sh",
      "sha256": "…",
      "origin": "local",
      "reason": "operator-local; no plugin source (gh #56)"
    }
  ]
}
```

`origin: "local"` is the load-bearing part. It makes "this file legitimately has
no source" a *declared* state rather than an unexplained anomaly, which is what
the five orphans are today. An installed file that is neither in the manifest
nor declared local is an unknown, and unknowns are reportable.

### 4.2 Verification

A checker compares three hashes per entry — installed, manifest, source — and
classifies:

| verdict | condition | meaning |
|---|---|---|
| `match` | installed == manifest == source | nothing to say |
| `source-ahead` | source ≠ installed, installed == manifest | a committed fix that never landed |
| `installed-modified` | installed ≠ manifest | hand-patch; next install reverts it |
| `both-moved` | installed ≠ manifest ≠ source | needs a merge, not an install |
| `local-only` | declared `origin: local` | fine; listed so it stays visible |
| `undeclared` | present, not in manifest | unknown provenance |
| `missing` | in manifest, file absent | **a rail is not running** |

`source-ahead` and `installed-modified` are the two states that caused #164 and
they are currently both invisible. `missing` is the state symlinking would have
made common.

### 4.3 Where it runs

SessionStart, reporting through `additionalContext`. Hashing ~26 small files is
cheap enough to run every session, and session start is the moment the
information is actionable.

It **reports and does not block**. A hook-verification hook that can block would
add a new way for the harness to wedge itself, which is a poor trade against a
condition that has been latent for weeks. `missing` on a credential rail is the
one case that arguably deserves escalation; see open questions.

### 4.4 Solving the sweep blindness

The manifest is a machine-readable list of every installed location, committed
to the repo as a template and present on disk as the live record. A reference
sweep can then enumerate installed trees instead of being limited to the
checkout. Concretely, the sweep that missed `admission_reminder.sh` would have
been a two-line change: read the manifest, grep those paths too.

This is the part that outlives the immediate bug.

## 5. Reconciliation — prerequisite, one-time

Order matters; skipping step 1 destroys work under any option.

1. **Adopt the 5 orphans.** Either move them into a plugin (`harness-core`, or a
   `harness-vocab` plugin for the four `vocab_*` files, which are one coherent
   feature), or declare them `origin: local` in the manifest with a reason. Both
   are acceptable; leaving them undeclared is not.
2. **Reconcile the 2 installed-newer files.** Review the 46/47-line diffs, land
   the live behaviour in git. Until this is done, no re-install may run.
3. **Re-install the 20 source-ahead files**, so the running rails match their
   reviewed source.
4. **Generate the first manifest** and turn on verification.

Steps 1–2 are the risky ones and are pure git work. Steps 3–4 are mechanical.

## 6. Rollout and revert

Revert is deleting `.install-manifest.json` and the SessionStart entry; the
copies keep running exactly as they do today. The change adds an observer and
does not alter how hooks resolve, which is the main reason to prefer it to
symlinking — it cannot itself take a rail down.

## 7. What this does not fix

- Hooks installed for other CLIs (Codex, Kimi, Grok) have their own trees; this
  design covers `~/.claude/hooks/` only. The manifest shape should generalise,
  but claiming that without probing those installers would repeat the mistake
  `probe the interface before speccing it` records.
- It does not make installation atomic. A crash mid-install leaves a manifest
  disagreeing with disk — which verification then reports, so the failure is
  loud rather than silent, but it is not prevented.
- It does not decide *policy* on `installed-modified`. Reporting it is in scope;
  auto-reverting or auto-filing is not.

## 8. Open questions for review

1. **Report vs block.** Is report-only right for `missing` on a credential rail
   (`credential_scrub.py`, `credential_value_scrub.sh`,
   `credential_file_read_guard.sh`)? Blocking is dangerous, but a silently
   absent scrubber is the exact fail-open this design claims to avoid — so the
   design may be soft precisely where it matters most.
2. **Should `installed-modified` auto-file an issue**, mirroring the credential
   leak auto-followup? That rail exists because incident-time edits get lost;
   this is the same shape.
3. **Where do the four `vocab_*` hooks belong** — `harness-core`, a new plugin,
   or declared permanently local? #56 suggests they are contested behaviour, so
   adopting them into the repo may pull that debate forward.
4. **Is SessionStart sufficient**, or should verification also run immediately
   *after* install, so a bad install is caught at the moment it happens rather
   than at the next session?
5. **Does the manifest belong in git at all?** It records machine-local state.
   A committed template plus a local live file may be two things pretending to
   be one.
