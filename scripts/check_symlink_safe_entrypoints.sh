#!/usr/bin/env bash
# Every plugin entrypoint must work when invoked through a symlink.
#
# Why this exists: three separate instances of one bug appeared on 2026-07-26.
#   1. harness-hook resolved its plugin root relative to itself, so pointing
#      ~/.local/bin/harness-hook at a pinned runtime generation silently pinned
#      every Codex-dispatched hook to that generation — including a credential
#      guard at 1 fail-closed exit instead of 7.
#   2. mailbox-send sourced lib/wake.sh via an unresolved BASH_SOURCE[0] and
#      died through ~/.local/bin, taking peer messaging down for every agent.
#   3. mailbox_relay.sh carried the same shape, latent.
# bin/formation had the correct idiom the whole time, so the repo held both
# forms side by side. Nothing enforced the resolved one. This does.
#
# Scope is deliberately "entrypoints this repo owns", NOT "symlinks present in
# ~/.local/bin". Scanning the machine picks up vendored third-party tools (e.g.
# quarto) that fail this predicate legitimately, and the allowlist needed to
# silence them becomes the next blind spot.
#
# Detection: invoke each entrypoint through a symlink in a scratch dir. A script
# that resolves correctly never has reason to name that scratch dir. A script
# that resolves relative to the link reports a missing path inside it.
#
# Exit 0 = all clear, 1 = at least one entrypoint is symlink-unsafe.
set -uo pipefail

HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
LINKDIR="$TMP/bin"
mkdir -p "$LINKDIR"

pass=0
fail=0
skip=0

for entry in "$ROOT"/plugins/*/bin/*; do
    [ -f "$entry" ] && [ -x "$entry" ] || continue
    head -1 "$entry" | grep -q '^#!' || continue

    name="$(basename "$entry")"
    plugin="$(basename "$(dirname "$(dirname "$entry")")")"
    ln -sfn "$entry" "$LINKDIR/$name"

    # --help is the least side-effecting probe. A non-zero exit is fine: some
    # entrypoints reject --help or need arguments. Only the resolution failure
    # signature matters, so the exit code is deliberately ignored.
    out="$(cd "$TMP" && timeout 10 "$LINKDIR/$name" --help 2>&1 </dev/null)"

    if printf '%s' "$out" | grep -q -- "$LINKDIR"; then
        printf 'FAIL  %-28s (%s) resolves relative to the symlink\n' "$name" "$plugin"
        printf '%s\n' "$out" | grep -- "$LINKDIR" | head -2 | sed 's/^/        /'
        fail=$((fail + 1))
    elif printf '%s' "$out" | grep -qi 'no such file or directory'; then
        printf 'FAIL  %-28s (%s) missing file when run via symlink\n' "$name" "$plugin"
        printf '%s\n' "$out" | grep -i 'no such file or directory' | head -2 | sed 's/^/        /'
        fail=$((fail + 1))
    else
        printf 'ok    %-28s (%s)\n' "$name" "$plugin"
        pass=$((pass + 1))
    fi
done

printf '\nRESULT: %d ok, %d failed, %d skipped\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]
