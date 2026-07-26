#!/usr/bin/env bash
# Every plugin entrypoint must work when invoked through a symlink.
#
# Why this exists: three instances of one bug landed on 2026-07-26.
#   1. harness-hook resolved its plugin root relative to itself, so pointing
#      ~/.local/bin/harness-hook at a pinned runtime generation silently pinned
#      every Codex-dispatched hook to it — including a credential guard running
#      1 fail-closed exit instead of 7.
#   2. mailbox-send sourced lib/wake.sh through an unresolved BASH_SOURCE[0] and
#      died via ~/.local/bin, taking peer messaging down for every agent.
#   3. lib/mailbox_relay.sh carried the same shape, latent.
# bin/formation had the correct idiom throughout, so the repo held both forms
# side by side and nothing enforced the right one.
#
# SCOPE, stated honestly: this checks plugins/*/bin/* only. Instance 3 lives in
# lib/, which is NOT covered — lib files are sourced, so their BASH_SOURCE is
# already the real path whenever the caller resolved its own. That makes lib/
# safe by contract, not by this check: the contract is "callers resolve, libs
# inherit". Creating a path that invokes a lib directly, or symlinking one,
# breaks that contract — but doing so is essentially adding an entrypoint, which
# lands back in this glob. Do not read a green run as "instance 3 is verified".
#
# Scope is also entrypoints this repo owns, NOT symlinks present in
# ~/.local/bin. Scanning the machine picks up vendored third-party tools (e.g.
# quarto) that fail this predicate legitimately, and the allowlist needed to
# silence them becomes the next blind spot.
#
# Detection is behavioural, never a grep for the idiom. Four independent audits
# ran greps on 2026-07-26 and three produced a wrong verdict from a bug in the
# detector itself (double-quoted ${BASH_SOURCE[0]} expanding to empty, BRE
# escaping, an extension filter). A detector that does not parse cannot
# misparse. The scratch dir is deliberately EMPTY: a script that resolves
# relative to the link lands somewhere with nothing in it, which converts a
# silent wrong-file failure into an audible missing-file one.
#
# Two probes are used. `--help` alone is insufficient: an entrypoint that
# answers --help before touching any path exits clean while still being
# symlink-unsafe. The second probe is an unknown subcommand, which most CLIs
# reject only after their init/source has already run.
#
# Run with --self-test to calibrate the detector against known-good and
# known-broken fixtures. A detector reporting "0 failed" proves nothing until it
# has been shown to fail on something.
#
# Exit 0 = all clear, 1 = at least one entrypoint is symlink-unsafe.
set -uo pipefail

HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PROBE_ARGS=("--help" "__symlink_probe__")

# probe_entry <path-to-entrypoint> <linkdir>
# Echoes the offending output when unsafe; silent and returns 1 when safe.
probe_entry() {
    local entry="$1" linkdir="$2" name out arg
    name="$(basename "$entry")"
    ln -sfn "$entry" "$linkdir/$name"
    for arg in "${PROBE_ARGS[@]}"; do
        out="$(cd "$(dirname "$linkdir")" && timeout 10 "$linkdir/$name" "$arg" 2>&1 </dev/null)"
        if printf '%s' "$out" | grep -q -- "$linkdir"; then
            printf '%s' "$out" | grep -- "$linkdir" | head -2
            return 0
        fi
        if printf '%s' "$out" | grep -qi 'no such file or directory'; then
            printf '%s' "$out" | grep -i 'no such file or directory' | head -2
            return 0
        fi
    done
    return 1
}

scan() {  # scan <plugins-root>
    local plugins_root="$1" tmp linkdir entry name plugin bad pass fail
    tmp="$(mktemp -d)"; linkdir="$tmp/bin"; mkdir -p "$linkdir"
    pass=0; fail=0
    for entry in "$plugins_root"/*/bin/*; do
        [ -f "$entry" ] && [ -x "$entry" ] || continue
        head -1 "$entry" | grep -q '^#!' || continue
        name="$(basename "$entry")"
        plugin="$(basename "$(dirname "$(dirname "$entry")")")"
        if bad="$(probe_entry "$entry" "$linkdir")"; then
            printf 'FAIL  %-32s (%s)\n' "$name" "$plugin"
            printf '%s\n' "$bad" | sed 's/^/        /'
            fail=$((fail + 1))
        else
            printf 'ok    %-32s (%s)\n' "$name" "$plugin"
            pass=$((pass + 1))
        fi
    done
    rm -rf "$tmp"
    printf '\nRESULT: %d ok, %d failed\n' "$pass" "$fail"
    [ "$fail" -eq 0 ]
}

self_test() {
    # Calibration fixtures. broken-lazy is the case a --help-only probe misses:
    # the defect is real but only reachable after argument handling.
    local tmp fx rc rcs=0
    tmp="$(mktemp -d)"; fx="$tmp/plugins/fixture/bin"; mkdir -p "$fx"

    cat > "$fx/good-resolved" <<'EOS'
#!/usr/bin/env bash
D="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
[ -d "$D" ] || exit 1
[ "${1:-}" = "--help" ] && { echo usage; exit 0; }
exit 0
EOS
    cat > "$fx/broken-eager" <<'EOS'
#!/usr/bin/env bash
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$D/../lib/nonexistent.sh"
EOS
    cat > "$fx/broken-lazy" <<'EOS'
#!/usr/bin/env bash
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ "${1:-}" = "--help" ] && { echo usage; exit 0; }
source "$D/../lib/nonexistent.sh"
EOS
    chmod +x "$fx"/*

    echo "== detector calibration =="
    local out
    out="$(scan "$tmp/plugins" 2>&1)"
    printf '%s\n' "$out" | sed 's/^/  /'

    expect() {  # expect <name> <ok|FAIL>
        if printf '%s' "$out" | grep -qE "^$2  *$1 "; then
            printf '  PASS  %-14s classified %s\n' "$1" "$2"
        else
            printf '  FAIL  %-14s NOT classified %s — detector is miscalibrated\n' "$1" "$2"
            rcs=1
        fi
    }
    echo "== expectations =="
    expect good-resolved ok
    expect broken-eager FAIL
    expect broken-lazy FAIL

    rm -rf "$tmp"
    return $rcs
}

if [ "${1:-}" = "--self-test" ]; then
    self_test
    exit $?
fi
scan "$ROOT/plugins"
