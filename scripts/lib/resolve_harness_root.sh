#!/bin/bash
# Resolve the durable primary claude-harness checkout for live registrations.

harness_resolve_canonical_root() {
    local invoked_root="$1"
    local candidate listing first_block

    if ! listing="$(git -C "$invoked_root" worktree list --porcelain 2>/dev/null)"; then
        echo "error: cannot resolve canonical checkout from $invoked_root" >&2
        return 1
    fi
    first_block="${listing%%$'\n\n'*}"
    if grep -Fqx "bare" <<<"$first_block"; then
        echo "error: canonical checkout is bare; no durable primary worktree" >&2
        return 1
    fi
    candidate="$(sed -n 's/^worktree //p' <<<"$first_block" | head -n 1)"

    if [[ -z "$candidate" || ! -d "$candidate" ]]; then
        echo "error: canonical checkout does not exist: ${candidate:-<empty>}" >&2
        return 1
    fi
    candidate="$(cd "$candidate" && pwd -P)"
    if [[ ! -f "$candidate/plugins/cross_cli_hooks.json" ||
          ! -d "$candidate/scripts/lib" ]]; then
        echo "error: canonical checkout lacks required harness artifacts: $candidate" >&2
        return 1
    fi
    printf '%s\n' "$candidate"
}
