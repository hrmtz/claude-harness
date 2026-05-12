---
name: versioning
version: 0.1.0
description: |
  Auto-detect the right semver bump (MAJOR / MINOR / PATCH) from commits since
  the last tag, create an annotated git tag, and push. Reads the project's
  CLAUDE.md § Versioning policy (if present) to honor project-specific bump
  triggers; otherwise applies the conventional-commits + breaking-change
  heuristic.

  USE WHEN finishing a milestone, after a deploy, after a chain of related
  commits land, or when the user asks to "bump the version" / "tag a release".
  SKIP for WIP, single-commit doc fixes, or anything that's already tagged.

  Cardinal rule: NEVER auto-push a tag the user hasn't confirmed. Propose the
  bump + the reasoning, wait for `OK` / `go` / explicit version override.
allowed-tools:
  - Bash
  - Read
  - Grep
---

# versioning — semver auto-bump + tag

Distilled from PRS-LLM-dev's 15-tag retroactive backfill (2026-05-11). Removes
the "what version is this?" decision friction without removing user veto.

## When to invoke

Trigger when **any** of:

- User says "bump version" / "tag a release" / "cut v1.x"
- A deploy just completed and the work since the last tag is non-trivial
- A multi-commit feature chain just merged to main
- Milestone in chat (e.g. "全部 deploy 完了", "Phase X done")

**Skip for**:
- Single doc-only commit (no behavioral change → no version bump)
- WIP / mid-feature commits (wait for stable point)
- Already tagged at HEAD
- The repo has no prior tag AND the user hasn't asked for an initial release

## Bump rules

Order: check MAJOR first, then MINOR, then PATCH. Whichever fires first wins.

| Bump  | Triggers                                                    |
|-------|-------------------------------------------------------------|
| MAJOR | Breaking API change · schema migration that needs downstream code change · removed feature · `BREAKING CHANGE:` footer · `feat!` / `fix!` commit · 1.0.0 graduation from 0.x |
| MINOR | New feature · new endpoint · new CLI subcommand · new plugin / skill · `feat:` commit |
| PATCH | Bug fix · refactor with no behavioral change · doc fix that ships to users · perf · `fix:` / `refactor:` / `perf:` / `chore:` commit |

If the project has a `CLAUDE.md § Versioning policy` section, **read it first
and let it override** these defaults. (Some projects elevate schema changes to
MAJOR, others to MINOR.)

## Protocol

### 1. Discover state

```bash
cd <repo>
LAST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
if [ -z "$LAST_TAG" ]; then
    echo "no prior tag — propose v0.1.0 as initial"
else
    echo "last tag: $LAST_TAG"
    git log --oneline "$LAST_TAG"..HEAD
fi
```

If no tag exists and the user hasn't asked for an initial release, **stop
here**. Don't surprise the user with a v0.1.0.

### 2. Classify commits

For each commit since `$LAST_TAG`:

- Parse the subject for conventional-commits prefix (`feat:`, `fix:`,
  `feat!:`, etc.) — if present, use that
- Otherwise infer from the diff (read 3-5 changed files) and the message body
- Look for explicit `BREAKING CHANGE:` footer

Aggregate to the **highest** bump level any single commit triggers.

### 3. Compute next version

```
current: 1.2.3
MAJOR bump → 2.0.0
MINOR bump → 1.3.0
PATCH bump → 1.2.4

0.x special case: MINOR bumps stay in 0.x unless user explicitly graduates.
```

### 4. Propose

Show the user:

```
last tag:   v1.2.3
commits:    7 (3 feat, 2 fix, 1 refactor, 1 docs)
bump:       MINOR (3 feat — user-visible new behavior)
next:       v1.3.0

annotated message (draft):
─────────────────────────
v1.3.0 — <one-line theme>

Features:
- <feat 1>
- <feat 2>

Fixes:
- <fix 1>
- <fix 2>
─────────────────────────

OK to tag + push?
```

Wait for explicit confirmation. Acceptable: `OK`, `go`, `yes`, an alternative
version (`make it v2.0.0`), or `pick a different theme`.

### 5. Tag + push

```bash
git tag -a v1.3.0 -m "$(cat <<'EOF'
v1.3.0 — <theme>

<body from step 4>
EOF
)"
git push origin v1.3.0
```

If the project uses GitHub Releases (`gh release`), offer to create one:

```bash
gh release create v1.3.0 --notes-file <(cat <<'EOF'
<release notes>
EOF
)
```

## Retroactive backfill

If the user asks to "version the history retroactively" (e.g. existing
mature repo with no tags), use this flow:

1. List all commits with `git log --reverse --oneline`
2. Identify natural milestone boundaries (commits with "完了" / "milestone" /
   "Phase X" / "deploy" / large diffs / one week gaps)
3. Propose a tag plan: `v0.1.0 @ <SHA>`, `v0.2.0 @ <SHA>`, ... → `v1.0.0 @ HEAD`
4. Show the plan, wait for confirmation
5. Create tags with `git tag -a vX.Y.Z <SHA> -m "..."` for each
6. Push all: `git push origin --tags` (only after confirm)

Heuristic for retroactive: most repos with N months of solo work map to
N/2-ish minor versions and 1 final `v1.0.0` at "feels production-ready"
boundary. PRS-LLM-dev did 15 tags for ~3 months of pre-tag history.

## Anti-patterns

- **Auto-push without confirming** — violates "actions visible to others or
  affecting shared state" rule in the global Doing-tasks doctrine.
- **PATCH for a feature** — undersells user-visible work and breaks downstream
  changelog tooling.
- **MINOR for a schema change that needs downstream code** — should be MAJOR
  because it's breaking.
- **Stacking many bumps in one tag** — if 3 features and 5 fixes shipped over
  2 weeks, that's one MINOR, not three.
- **0.x MAJOR bump for breaking change** — by SemVer convention, 0.x is "no
  stability promise"; breaking changes only force MINOR in 0.x. Don't 1.0.0
  graduate unless the user wants it.
- **Tagging a dirty tree** — verify `git status` is clean before tagging.
- **Tagging on main when CLAUDE.md § Branch policy forbids main commits** —
  the tag is fine (tags are on commits, not branches), but the SHA being
  tagged should already be on dev → main fast-forwarded.

## Related

- `magi` — pre-flight review for high-stakes changes (sibling discipline)
- `bug-hunt` — post-change adversarial review (sibling discipline)
- Project `CLAUDE.md § Versioning policy` — project-specific overrides
