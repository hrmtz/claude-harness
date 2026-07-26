#!/usr/bin/env bash
# install-pane-messaging-rail.sh — upsert the Formation pane-messaging rail
# (gh #105 / #130) into an always-loaded AGENTS instruction surface.
#
# Usage: install-pane-messaging-rail.sh [<target-AGENTS.md>]
# Default target: $HOME/AGENTS.md (the Codex global instruction surface).
#
# The rail is inserted as a marker-bounded managed block; re-running replaces
# the block in place and leaves all foreign content untouched. Marker state
# must be exactly one start and one end marker, start before end — any other
# combination (orphan, duplicated, inverted) fails closed without touching the
# target. A persistent Sanada backup is taken before modifying an existing
# file, after the marker validation passes.
set -euo pipefail

# readlink -f so a symlinked copy of this installer still finds ../agents/.
# Same idiom as bin/formation; see scripts/check_symlink_safe_entrypoints.sh.
HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
RAIL="$HERE/../agents/pane-messaging-rail.md"
TARGET="${1:-$HOME/AGENTS.md}"
START="<!-- harness-formation:pane-messaging-rail:start -->"
END="<!-- harness-formation:pane-messaging-rail:end -->"

[ -f "$RAIL" ] || { echo "error: rail source not found: $RAIL" >&2; exit 1; }

block="$(mktemp)"; new="$(mktemp)"
trap 'rm -f "$block" "$new"' EXIT
{
    printf '%s\n' "$START"
    # Strip the source's own title/intro: the managed block is the rail body only.
    sed -n '/^---$/,$p' "$RAIL" | tail -n +2
    printf '%s\n' "$END"
} > "$block"
# Fail closed if the rail body extraction came out empty (e.g. the source's
# structure changed) rather than installing a markers-only block.
grep -qF 'mailbox-first' "$block" || {
    echo "error: extracted rail body is empty or unrecognizable; refusing to install" >&2
    exit 1
}

n_start=0; n_end=0
if [ -f "$TARGET" ]; then
    # Markers are whole-line sentinels: the awk upsert below matches $0 exactly.
    # A marker substring embedded in a longer line (trailing comment, indentation)
    # must not count as a managed marker — detect the exact/loose mismatch and
    # fail closed rather than silently appending beside an unreplaceable block.
    n_start="$(grep -cxF "$START" "$TARGET" || true)"
    n_end="$(grep -cxF "$END" "$TARGET" || true)"
    loose_start="$(grep -cF "$START" "$TARGET" || true)"
    loose_end="$(grep -cF "$END" "$TARGET" || true)"
    if [ "$n_start" != "$loose_start" ] || [ "$n_end" != "$loose_end" ]; then
        echo "error: $TARGET has a rail marker embedded in a longer line;" \
            "refusing to rewrite" >&2
        exit 1
    fi
fi
if [ "$n_start" -gt 1 ] || [ "$n_end" -gt 1 ] || \
   { [ "$n_start" -eq 0 ] && [ "$n_end" -gt 0 ]; } || \
   { [ "$n_end" -eq 0 ] && [ "$n_start" -gt 0 ]; }; then
    echo "error: $TARGET has an inconsistent rail marker state" \
        "(start=$n_start end=$n_end); refusing to rewrite" >&2
    exit 1
fi
if [ "$n_start" -eq 1 ]; then
    # Exactly one of each: start must precede end.
    first_start="$(grep -nxF "$START" "$TARGET" | head -n1 | cut -d: -f1)"
    first_end="$(grep -nxF "$END" "$TARGET" | head -n1 | cut -d: -f1)"
    [ "$first_start" -lt "$first_end" ] || {
        echo "error: $TARGET has an inverted rail marker order; refusing to rewrite" >&2
        exit 1
    }
fi

if [ -f "$TARGET" ]; then
    # Atomic uniqueness via mktemp -d: same-second bursts, PID reuse, and
    # pre-existing directory collisions can never overwrite an earlier
    # backup — every modifying run keeps its own restorable preimage.
    mkdir -p "$HOME/sanada_backup_persistent"
    BK="$(mktemp -d "$HOME/sanada_backup_persistent/pane_messaging_rail_$(date +%Y%m%d_%H%M%S).XXXXXX")"
    cp "$TARGET" "$BK/$(basename "$TARGET")"
fi

if [ "$n_start" -eq 1 ]; then
    awk -v start="$START" -v end="$END" -v block_file="$block" '
        $0 == start {
            while ((getline line < block_file) > 0) print line
            close(block_file)
            skip = 1
            next
        }
        skip && $0 == end { skip = 0; next }
        !skip { print }
    ' "$TARGET" > "$new"
else
    [ -f "$TARGET" ] && cat "$TARGET" > "$new" || : > "$new"
    [ -s "$new" ] && printf '\n' >> "$new"
    cat "$block" >> "$new"
fi

cat "$new" > "$TARGET"
echo "pane-messaging rail installed into $TARGET"
