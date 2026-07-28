#!/usr/bin/env python3
"""Detect section-level drift between installed AGENTS.md files and the template.

Baseline: plugins/harness-kimi/AGENTS.md.template. Only files whose first ~40
lines carry the `Agent harness — behavioral rails` marker are in scope (project-
local AGENTS.md without the rails are structurally excluded). Worktrees (parent
`.git` is a file, not a dir) are skipped by default; `--include-worktrees` lists
them as INFO without affecting the exit code.

STALE      = section in template but missing in installed (distribution lag)
UNREFLUXED = section in installed but missing in template (local evolution that
             an overwrite would silently destroy)
DIVERGED   = same heading, different body

exit 0 = every canonical install is section-identical to the template.
exit 1 = confirmed drift (any STALE / UNREFLUXED / DIVERGED).
exit 2 = the check itself could not run (template unreadable, no marker-matched
files, missing roots, decode error) so a cron observer can tell a broken check
apart from confirmed drift. Zero marker matches is an error, not a green: a
misconfigured glob must never report "in sync".
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATE = ROOT / "plugins" / "harness-kimi" / "AGENTS.md.template"
DEFAULT_ROOTS = [Path.home() / "projects"]

MARKER = "Agent harness — behavioral rails"
MARKER_HEAD_LINES = 40
MAX_DEPTH = 3  # root/proj/AGENTS.md = 2, root/_formation_wt/<wt>/AGENTS.md = 3
TEMPLATE_MAX_DEPTH = 5  # root/<repo>/plugins/harness-kimi/AGENTS.md.template = 4, _formation_wt/<wt>/... = 5
SKIP_DIRS = {".git", "node_modules", "__pycache__"}
PREAMBLE = "(preamble)"

# Provenance stamp emitted by the template; metadata, not section content.
STAMP_RE = re.compile(r"(?m)^<!-- harness-agents-template: [^>]* -->\s*")

# (project_dir_name, heading) pairs allowed to diverge. Empty by default so
# reflux pressure stays on; add only for genuinely project-local sections.
ALLOW_PROJECT_LOCAL: set[tuple[str, str]] = set()


def normalize(text: str) -> str:
    """Rstrip each line and drop leading/trailing blank lines."""
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip("\n")


def split_sections(text: str) -> dict[str, str]:
    """Split into {heading: normalized_body}; preamble is a pseudo-section."""
    parts = re.split(r"(?m)^(?=## )", STAMP_RE.sub("", text))
    sections = {PREAMBLE: normalize(parts[0])}
    for part in parts[1:]:
        heading = part.splitlines()[0].strip()
        sections[heading] = normalize(part)
    return sections


def first_diff(template_body: str, installed_body: str) -> tuple[int, str]:
    """1-based line number and leading 60 chars of the first differing line."""
    t_lines = template_body.splitlines()
    i_lines = installed_body.splitlines()
    for index, (t_line, i_line) in enumerate(zip(t_lines, i_lines), start=1):
        if t_line != i_line:
            return index, i_line[:60]
    shorter, longer = (t_lines, i_lines) if len(t_lines) < len(i_lines) else (i_lines, t_lines)
    extra = longer[len(shorter)] if len(longer) > len(shorter) else ""
    return len(shorter) + 1, extra[:60]


def is_worktree(project_dir: Path) -> bool:
    """`.git` file = worktree, dir = canonical, absent = non-git (canonical)."""
    return (project_dir / ".git").is_file()


def has_marker(path: Path) -> bool:
    with path.open(encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index >= MARKER_HEAD_LINES:
                break
            if MARKER in line:
                return True
    return False


def iter_files(roots: list[Path], name: str, max_depth: int = MAX_DEPTH) -> tuple[list[Path], list[str]]:
    """Find `name` under roots up to max_depth, pruning SKIP_DIRS."""
    found: list[Path] = []
    errors: list[str] = []
    for root in roots:
        if not root.is_dir():
            errors.append(f"root does not exist: {root}")
            continue
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                children = sorted(current.iterdir())
            except OSError:
                continue
            for child in children:
                if child.is_dir() and not child.is_symlink():
                    if child.name in SKIP_DIRS:
                        continue
                    depth = len(child.relative_to(root).parts)
                    if depth < max_depth:
                        stack.append(child)
                elif child.name == name and child.is_file():
                    if len(child.relative_to(root).parts) <= max_depth:
                        found.append(child)
    return sorted(found), errors


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()[:8]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    result.add_argument("--roots", type=Path, nargs="+", default=DEFAULT_ROOTS)
    result.add_argument(
        "--include-worktrees",
        action="store_true",
        help="list worktree installs as INFO (never affects the exit code)",
    )
    result.add_argument("--verbose", action="store_true", help="unified diffs for DIVERGED")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    template = args.template.expanduser().resolve()
    roots = [root.expanduser().resolve() for root in args.roots]

    try:
        template_text = template.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"AGENTS DRIFT CHECK ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    template_sections = split_sections(template_text)

    candidates, root_errors = iter_files(roots, "AGENTS.md")
    if root_errors:
        for error in root_errors:
            print(f"AGENTS DRIFT CHECK ERROR: ValueError: {error}", file=sys.stderr)
        return 2

    canonical: list[tuple[Path, dict[str, str]]] = []
    worktrees: list[Path] = []
    for candidate in candidates:
        project_dir = candidate.parent
        try:
            if not has_marker(candidate):
                continue  # project-local AGENTS.md without the rails: out of scope
            text = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"AGENTS DRIFT CHECK ERROR: {type(exc).__name__}: {candidate}: {exc}", file=sys.stderr)
            return 2
        if is_worktree(project_dir):
            worktrees.append(candidate)
        else:
            canonical.append((candidate, split_sections(text)))

    if not canonical and not worktrees:
        print(
            f"AGENTS DRIFT CHECK ERROR: ValueError: no marker-matched AGENTS.md under {roots}",
            file=sys.stderr,
        )
        return 2

    stale: list[tuple[str, str]] = []
    unrefluxed: list[tuple[str, str]] = []
    diverged: list[tuple[str, str, int, str, str, str]] = []
    in_sync = 0
    for candidate, installed_sections in canonical:
        project = candidate.parent.name
        drifted = False
        for heading in template_sections:
            if (project, heading) in ALLOW_PROJECT_LOCAL:
                continue
            if heading not in installed_sections:
                stale.append((project, heading))
                drifted = True
            elif installed_sections[heading] != template_sections[heading]:
                line_no, snippet = first_diff(
                    template_sections[heading], installed_sections[heading]
                )
                diverged.append(
                    (project, heading, line_no, snippet,
                     template_sections[heading], installed_sections[heading])
                )
                drifted = True
        for heading in installed_sections:
            if (project, heading) in ALLOW_PROJECT_LOCAL:
                continue
            if heading not in template_sections:
                unrefluxed.append((project, heading))
                drifted = True
        if not drifted:
            in_sync += 1

    # Template split WARN: stale worktree templates are a mis-distribution
    # hazard. WARN only — never flips the exit code.
    template_copies, _ = iter_files(roots, "AGENTS.md.template", max_depth=TEMPLATE_MAX_DEPTH)
    split_templates = [p for p in template_copies if p.resolve() != template and md5(p) != md5(template)]

    drifted_count = len(canonical) - in_sync
    print(
        f"templates: {len(template_copies) or 1}  canonical-installed: {len(canonical)}  "
        f"in-sync: {in_sync}  drifted: {drifted_count}  (worktrees skipped: {len(worktrees)})"
    )
    if stale:
        print("\nSTALE (in template, missing in installed — distribution lag):")
        for project, heading in stale:
            print(f"  {project}: {heading}")
    if unrefluxed:
        print("\nUNREFLUXED (in installed, missing in template — overwrite would destroy):")
        for project, heading in unrefluxed:
            print(f"  {project}: {heading}")
    if diverged:
        print("\nDIVERGED (same heading, different body):")
        for project, heading, line_no, snippet, t_body, i_body in diverged:
            print(f"  {project}: {heading}  first diff at line {line_no}: {snippet!r}")
            if args.verbose:
                diff = difflib.unified_diff(
                    t_body.splitlines(), i_body.splitlines(),
                    fromfile=f"template:{heading}", tofile=f"{project}:{heading}",
                    lineterm="",
                )
                for line in diff:
                    print(f"    {line}")
    if split_templates:
        print("\nWARN template split (md5 differs from canonical template):")
        for path in split_templates:
            print(f"  {path} (md5 {md5(path)} != {md5(template)})")
    if args.include_worktrees and worktrees:
        print("\nINFO worktree installs (skipped, rebase lag is normal git state):")
        for path in worktrees:
            print(f"  {path}")
    if not stale and not unrefluxed and not diverged:
        print("\nIN SYNC ✓ (canonical installs == template, section-level)")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
