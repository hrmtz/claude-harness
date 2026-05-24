#!/bin/bash
# sops_edit_wrapper.sh — wrapper that runs `sops edit` THEN regenerates manifest
#
# Place as `~/.local/bin/sops-rotate` (or shell alias) so the user's rotation
# muscle memory is preserved while staleness is structurally enforced.
#
# Usage:
#   sops-rotate <file.enc.yaml>
#
# Behavior:
#   1. `sops edit <file>`  (= user's normal flow)
#   2. If the file mtime changed (= edit was saved), regenerate manifest under
#      `sops exec-env`. Uses the same baseline-env protection as build_all.
#   3. On manifest build failure, the rotated sops file is untouched (= safe).
#   4. Surface result via stdout; non-zero exit on manifest build failure to
#      force the user to notice.

set -u
set -o pipefail

if [ $# -lt 1 ]; then
    echo "usage: sops-rotate <file.enc.yaml>" >&2
    exit 2
fi

SOPS_FILE="$1"
BUILD_PY="${BUILD_PY:-$HOME/.claude/hooks/credential_scrub_build.py}"
ALGORITHM="${ALGORITHM:-sha256-hmac}"   # default = stdlib HMAC-SHA256

if [ ! -f "$SOPS_FILE" ]; then
    echo "file not found: $SOPS_FILE" >&2
    exit 2
fi

# Snapshot mtime BEFORE edit
mtime_before=$(stat -c %Y "$SOPS_FILE" 2>/dev/null || stat -f %m "$SOPS_FILE" 2>/dev/null || echo "0")

# Run sops edit (= user's interactive flow)
sops edit "$SOPS_FILE"
rc=$?
if [ "$rc" != "0" ]; then
    echo "sops edit exited rc=$rc; manifest not regenerated" >&2
    exit "$rc"
fi

# Check mtime — if unchanged, the user discarded changes, skip rebuild
mtime_after=$(stat -c %Y "$SOPS_FILE" 2>/dev/null || stat -f %m "$SOPS_FILE" 2>/dev/null || echo "0")
if [ "$mtime_before" = "$mtime_after" ]; then
    echo "no edit detected (mtime unchanged); manifest not regenerated"
    exit 0
fi

# Baseline env snapshot for ambient-collision detection
BASELINE_ENV=$(mktemp --tmpdir credential_scrub_baseline.XXXXXX)
trap 'rm -f "$BASELINE_ENV"' EXIT
env | cut -d= -f1 | sort -u > "$BASELINE_ENV"

include_manifest="${SOPS_FILE%.enc.yaml}.scrub.yaml"
include_args=()
if [ -f "$include_manifest" ]; then
    include_args+=(--include-manifest "$include_manifest")
fi

cmd=(python3 "$BUILD_PY"
     --source-file "$SOPS_FILE"
     --algorithm "$ALGORITHM"
     --baseline-env-file "$BASELINE_ENV"
     "${include_args[@]}")

inner=$(printf '%q ' "${cmd[@]}")
inner="${inner% }"

if sops exec-env "$SOPS_FILE" "$inner"; then
    echo "manifest regenerated for $(basename "$SOPS_FILE")"
    exit 0
else
    rc=$?
    echo "manifest regeneration FAILED for $(basename "$SOPS_FILE") (rc=$rc)" >&2
    echo "→ sops file is rotated but Layer-2 scrubber is now STALE for new value(s)" >&2
    echo "→ Investigate logs at ~/.claude/state/hook_logs/hooks.log + rerun manually" >&2
    exit "$rc"
fi
