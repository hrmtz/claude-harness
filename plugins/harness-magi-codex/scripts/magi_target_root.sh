#!/usr/bin/env bash
# magi_target_root.sh — resolve the repository/worktree that owns a review document.
#
# Design: docs/designs/CODEX_MAGI_MIRROR.md §3.2 · gh #57 (fan-out), gh #151 (cross-family,
# pre-flight). Extracted verbatim from the fan-out implementation so every reviewer arm
# grounds identically; the fan-out arm is the reference behavior and must not drift from it.
#
# Reviewer verification commands must run in the repository that owns the document, not in
# the harness checkout: a relative doc path in repo B launched from repo A must still ground
# reviewers in repo B. A reviewer grounded in the wrong tree still reports
# verify_commands_executed that succeeded — the commands pass, so nothing looks wrong.
#
# GIT_DIR/GIT_WORK_TREE are stripped for the lookup: an inherited pair would make rev-parse
# ignore on-disk discovery and silently re-narrow grounding to the document directory.
# The lookup fails CLOSED (return 64) on anything other than "not a git repository" (e.g.
# dubious ownership), since the fallback narrows what reviewers can ground against.
#
# Usage (source, do not exec):
#   source magi_target_root.sh
#   TARGET_ROOT="$(magi_target_root "$DOC_PATH" fanout)" || exit 64
#   # Callers should then `unset GIT_DIR GIT_WORK_TREE` so ambient repository overrides that
#   # this lookup deliberately ignored cannot contaminate later git reads or reviewer
#   # subprocesses either.

# magi_target_root <document-path> [label]
#   stdout: the git top-level owning the document, else the document's own directory
#   returns 0 on resolution, 64 when the git lookup failed for a reason other than
#   "not a git repository" (caller must not fall back to a narrowed grounding)
magi_target_root() {
    local doc_path="$1" label="${2:-magi}" doc_dir git_toplevel
    [ -n "$doc_path" ] || {
        echo "$label: target root lookup requires a document path" >&2
        return 64
    }
    doc_dir="$(dirname "$doc_path")"
    if git_toplevel="$(env -u GIT_DIR -u GIT_WORK_TREE git -C "$doc_dir" \
            rev-parse --show-toplevel 2>&1)"; then
        # A bare repository resolves with an empty top-level: fall back rather than
        # handing the reviewer an empty cwd.
        if [ -n "$git_toplevel" ]; then
            printf '%s\n' "$git_toplevel"
            return 0
        fi
    else
        case "$git_toplevel" in
            *"not a git repository"*) : ;;
            *) echo "$label: git toplevel lookup failed; refusing narrowed grounding:" >&2
               echo "        $git_toplevel" >&2
               return 64 ;;
        esac
    fi
    printf '%s\n' "$doc_dir"
    return 0
}
