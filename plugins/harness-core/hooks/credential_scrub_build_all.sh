#!/usr/bin/env bash
# credential_scrub_build_all.sh — build an HMAC manifest for every sops file.
#
# credential_scrub_build.py's own docstring has told you to run this since it
# was written. It did not exist (gh #99). Manifests were therefore only ever
# created as a side effect of `sops edit` through sops_edit_wrapper.sh, so
# coverage was "whatever happened to be edited since the scrubber shipped" —
# and a file last touched before that never got one. The scrubber then reports
# success for a file it has never seen, which is worse than an unbound gate.
#
# Usage:
#   bash credential_scrub_build_all.sh            # build missing + refresh all
#   bash credential_scrub_build_all.sh --check    # report coverage, build nothing
#
# Env:
#   SECRETS_DIR  default ~/projects/creds-migration/secrets-template
#   BUILD_PY     default ~/.claude/hooks/credential_scrub_build.py
set -uo pipefail

SECRETS_DIR="${SECRETS_DIR:-$HOME/projects/creds-migration/secrets-template}"
BUILD_PY="${BUILD_PY:-$HOME/.claude/hooks/credential_scrub_build.py}"
MANIFEST_DIR="${MANIFEST_DIR:-$HOME/.claude/state/credential_scrub/manifest}"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

[ -d "$SECRETS_DIR" ] || { echo "no secrets dir: $SECRETS_DIR" >&2; exit 64; }
[ -f "$BUILD_PY" ] || { echo "no builder: $BUILD_PY" >&2; exit 64; }

# Classify a sops file's top-level structure WITHOUT decrypting it.
#
# This check exists to keep us from running `sops exec-env` on a file it cannot
# handle. sops cannot export a nested mapping and fails with the DECRYPTED
# CONTENT inside its error message (gh #156) — so the naive form of this script,
# which just loops sops exec-env over every file, leaks every complex-type
# file's secrets on every run. The structure is readable without decrypting
# anything: sops encrypts values and leaves key names in plaintext.
#
# Exit codes are three-valued and MUST be tested with `$?`, not with `if`.
# Written as `if classify_file "$f"; then skip; fi`, the unreadable case (2) is
# just "nonzero" and falls through to the sops call — i.e. straight into the
# path this function exists to prevent.
#   0 = nested top-level value  (builder cannot cover it; never invoke sops)
#   1 = flat mapping            (builder can cover it)
#   2 = unreadable / not a mapping (unsupported; never invoke sops)
classify_file() {
    python3 - "$1" <<'PY'
import sys, yaml
try:
    doc = yaml.safe_load(open(sys.argv[1]))
except Exception:
    sys.exit(2)          # unreadable: treat as unsupported, do not decrypt
if not isinstance(doc, dict):
    sys.exit(2)
nested = [k for k, v in doc.items() if k != "sops" and isinstance(v, (dict, list))]
sys.exit(0 if nested else 1)
PY
}

built=0; skipped_complex=0; missing=0; present=0; failed=0; unreadable=0
complex_files=()
unreadable_files=()

for f in "$SECRETS_DIR"/*.enc.yaml; do
    [ -f "$f" ] || continue
    base="$(basename "$f" .yaml)"
    manifest="$MANIFEST_DIR/${base}.scrub.json"

    classify_file "$f"
    case "$?" in
        0)  skipped_complex=$((skipped_complex + 1))
            complex_files+=("$(basename "$f")")
            continue ;;
        1)  : ;;
        *)  unreadable=$((unreadable + 1))
            unreadable_files+=("$(basename "$f")")
            continue ;;
    esac

    if [ -f "$manifest" ]; then present=$((present + 1)); else missing=$((missing + 1)); fi
    [ "$CHECK_ONLY" = "1" ] && continue

    if sops exec-env "$f" "python3 '$BUILD_PY' --source-file '$f'" >/dev/null 2>&1; then
        built=$((built + 1))
    else
        # Do not print sops' stderr: on the failure paths it can contain
        # decrypted content (gh #156).
        echo "BUILD FAILED (output suppressed deliberately): $(basename "$f")" >&2
        failed=$((failed + 1))
    fi
done

echo "sops files: $((present + missing + skipped_complex + unreadable))  manifest present: $present  missing: $missing  complex-type: $skipped_complex  unreadable: $unreadable"
[ "$CHECK_ONLY" = "1" ] || echo "built: $built  failed: $failed"

if [ "$unreadable" -gt 0 ]; then
    echo
    echo "NO HMAC COVERAGE — unreadable or not a YAML mapping; sops was NOT invoked:" >&2
    for u in "${unreadable_files[@]}"; do echo "  - $u" >&2; done
fi

if [ "$skipped_complex" -gt 0 ]; then
    echo
    echo "NO HMAC COVERAGE — nested mapping, unsupported by the builder (gh #99):" >&2
    for c in "${complex_files[@]}"; do echo "  - $c" >&2; done
    echo "  These files' secrets are protected ONLY by the value-prefix catalog in" >&2
    echo "  credential_value_scrub.sh. Every secret in them needs a prefix entry" >&2
    echo "  there, or it has no scrubber coverage at all." >&2
fi

# Fail when coverage is incomplete. A silent partial build is what let a file
# sit unprotected for two months without anyone noticing.
if [ "$failed" -gt 0 ] || [ "$skipped_complex" -gt 0 ] || [ "$unreadable" -gt 0 ] || { [ "$CHECK_ONLY" = "1" ] && [ "$missing" -gt 0 ]; }; then
    exit 1
fi
exit 0
