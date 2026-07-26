#!/usr/bin/env bash
# test_identity_owner.sh — the ownership core's decision table (#95).
#
# Two things this suite is built to avoid.
#
# First, a green run that proves nothing. Three of four audits on 2026-07-26
# produced wrong verdicts from bugs in the detector itself, so this file starts
# with a calibration block: a known-positive that MUST claim and a known-positive
# that MUST refuse. If either flips, every other assertion below is suspect.
#
# Second, a fixture that passes for the wrong reason. The regression this core
# exists to kill was a `claude -p` running under a binary called
# `claude-harness-codex-real`, which the old `ps comm` classifier could not see
# both because the name was absent from its list and because `comm` truncates at
# 15 characters. The nested-launch fixtures here therefore run through a wrapper
# with exactly that shape: if someone reintroduces name matching, these fail.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
LIB="$ROOT/plugins/harness-core/hooks/identity_owner.sh"

PASS=0
FAIL=0
ok()   { PASS=$((PASS + 1)); printf '  ok   %s\n' "$1"; }
bad()  { FAIL=$((FAIL + 1)); printf '  FAIL %s\n     %s\n' "$1" "${2:-}"; }
check() { # label expected actual
    if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected '$2', got '$3'"; fi
}

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"; jobs -p | xargs -r kill 2>/dev/null' EXIT

# ── fake tmux ────────────────────────────────────────────────────────────────
# Pane state is a key=value file per pane. The expander understands exactly the
# formats the core asks for, so a new format string in the core surfaces here as
# an empty value rather than as a silent pass.
mkdir -p "$WORK/bin"
cat > "$WORK/bin/tmux" <<'FAKE'
#!/usr/bin/env bash
STATE="$FAKE_TMUX_STATE"
mkdir -p "$STATE"

pane_get() { # pane key
    [ -f "$STATE/$1" ] || return 0
    awk -F= -v k="$2" '$1 == k { sub("^" k "=", ""); print; exit }' "$STATE/$1"
}
pane_set() { # pane key value
    local tmp="$STATE/.tmp.$$"
    touch "$STATE/$1"
    awk -F= -v k="$2" '$1 != k' "$STATE/$1" > "$tmp"
    printf '%s=%s\n' "$2" "$3" >> "$tmp"
    mv "$tmp" "$STATE/$1"
}
expand() { # pane fmt
    local pane="$1" fmt="$2" locked alias out
    locked="$(pane_get "$pane" @formation_identity_locked)"
    alias="$(pane_get "$pane" @formation_id)"
    out="${fmt//'#{?#{@formation_identity_locked},#{@formation_identity_locked},#{@formation_id}}'/${locked:-$alias}}"
    while [[ "$out" =~ \#\{(@?[a-z_]+)\} ]]; do
        local key="${BASH_REMATCH[1]}"
        out="${out//\#\{$key\}/$(pane_get "$pane" "$key")}"
    done
    printf '%s\n' "$out"
}

cmd="${1:-}"; shift || true
case "$cmd" in
    display-message)
        pane=""; fmt=""
        while [ $# -gt 0 ]; do
            case "$1" in
                -p) shift ;;
                -t) pane="$2"; shift 2 ;;
                *)  fmt="$1"; shift ;;
            esac
        done
        [ -f "$STATE/$pane" ] || exit 1
        expand "$pane" "$fmt"
        ;;
    set-option)
        pane=""; opt=""; val=""; unset_opt=0
        while [ $# -gt 0 ]; do
            case "$1" in
                -t) pane="$2"; shift 2 ;;
                -*u*) unset_opt=1; shift ;;
                -*) shift ;;
                *)  if [ -z "$opt" ]; then opt="$1"; else val="$1"; fi; shift ;;
            esac
        done
        if [ "$unset_opt" = "1" ]; then
            tmp="$STATE/.tmp.$$"
            touch "$STATE/$pane"
            awk -F= -v k="$opt" '$1 != k' "$STATE/$pane" > "$tmp"
            mv "$tmp" "$STATE/$pane"
        else
            pane_set "$pane" "$opt" "$val"
        fi
        ;;
    rename-window)
        pane=""; name=""
        while [ $# -gt 0 ]; do
            case "$1" in
                -t) pane="$2"; shift 2 ;;
                *)  name="$1"; shift ;;
            esac
        done
        pane_set "$pane" window_name "$name"
        ;;
    select-pane)
        pane=""; title=""
        while [ $# -gt 0 ]; do
            case "$1" in
                -t) pane="$2"; shift 2 ;;
                -T) title="$2"; shift 2 ;;
                *)  shift ;;
            esac
        done
        pane_set "$pane" pane_title "$title"
        ;;
    list-panes)
        fmt=""
        while [ $# -gt 0 ]; do
            case "$1" in
                -F) fmt="$2"; shift 2 ;;
                *)  shift ;;
            esac
        done
        for f in "$STATE"/*; do
            [ -f "$f" ] || continue
            case "$(basename "$f")" in .*) continue ;; esac
            expand "$(basename "$f")" "$fmt"
        done
        ;;
    *) exit 0 ;;
esac
FAKE
chmod +x "$WORK/bin/tmux"

export FAKE_TMUX_STATE="$WORK/state"
export PATH="$WORK/bin:$PATH"
export HARNESS_IDENTITY_SENTINEL_DIR="$WORK/sentinels"
mkdir -p "$FAKE_TMUX_STATE" "$HARNESS_IDENTITY_SENTINEL_DIR"

# Defaults yield to anything the caller supplied. Emitting both and letting a
# first-match reader pick would make an override silently do nothing — which it
# did, and every "expected X, got bash" it produced looked like a core bug.
pane_reset() { # pane [key=value ...]
    local pane="$1"; shift
    local kv key defaults given=" "
    for kv in "$@"; do given="$given${kv%%=*} "; done
    rm -f "$FAKE_TMUX_STATE/$pane"
    {
        for defaults in "pane_id=$pane" "pane_pid=$$" "window_panes=1" \
                        "window_name=bash" "pane_title=bash"; do
            key="${defaults%%=*}"
            case "$given" in *" $key "*) continue ;; esac
            printf '%s\n' "$defaults"
        done
        for kv in "$@"; do printf '%s\n' "$kv"; done
    } > "$FAKE_TMUX_STATE/$pane"
}
pane_get() { awk -F= -v k="$2" '$1 == k { sub("^" k "=", ""); print; exit }' "$FAKE_TMUX_STATE/$1" 2>/dev/null; }

# The core walks $PPID upward, and bash does not update $PPID inside `( )` or
# `$( )`. Resolving through a real child process is therefore not a stylistic
# choice: it is the only way the ancestry code under test sees this script, and
# `pane_pid=$$` then makes the pane-root derivation exercise its real path
# rather than a fallback.
cat > "$WORK/bin/hi-resolve" <<HELPER
#!/usr/bin/env bash
. "$LIB"
apply=0
[ "\$1" = "--apply" ] && { apply=1; shift; }
pane="\$1"; chassis="\$2"; mode="\$3"; shift 3
harness_identity_resolve --pane "\$pane" --chassis "\$chassis" --mode "\$mode" "\$@"
[ "\$apply" = "1" ] && [ "\$HARNESS_IDENTITY_DECISION" = "CLAIM" ] && harness_identity_apply
printf '%s|%s|%s|%s|%s\n' "\$HARNESS_IDENTITY_DECISION" "\$HARNESS_IDENTITY_REASON" \\
    "\$HARNESS_IDENTITY_NAME" "\$HARNESS_IDENTITY_ROUTING_ID" "\$HARNESS_IDENTITY_RESUMED"
HELPER
chmod +x "$WORK/bin/hi-resolve"

cat > "$WORK/bin/hi-proc-key" <<HELPER
#!/usr/bin/env bash
. "$LIB"
_hi_proc_key "\$1"
HELPER
chmod +x "$WORK/bin/hi-proc-key"

resolve()       { "$WORK/bin/hi-resolve" "$@"; }
resolve_apply() { "$WORK/bin/hi-resolve" --apply "$@"; }
field()         { printf '%s' "$1" | cut -d'|' -f"$2"; }
proc_key_of()   { "$WORK/bin/hi-proc-key" "$1"; }

# ── calibration ──────────────────────────────────────────────────────────────
echo "calibration (a detector that cannot fail proves nothing)"

pane_reset %cal1
R=$(resolve %cal1 codex session-start)
check "known-positive CLAIM: a free pane is claimed" "CLAIM" "$(field "$R" 1)"
check "known-positive CLAIM: reason is first-claim"  "first" "$(field "$R" 2)"

pane_reset %cal2 "@harness_chassis=codex" "@harness_owner_key=$(proc_key_of $$)"
R=$(resolve %cal2 claude session-start)
check "known-positive REFUSE: a live foreign owner keeps its pane" "REFUSE" "$(field "$R" 1)"
check "known-positive REFUSE: reason names the nesting" "foreign-owner-nested" "$(field "$R" 2)"

# ── the reported regression, through a wrapper the old classifier could not see
echo
echo "nested launch under a long-named wrapper (the 2026-07-26 regression)"

# `comm` is truncated by the kernel; establish the truncation here rather than
# asserting a remembered number, then prove the decision does not depend on it.
cat > "$WORK/bin/claude-harness-codex-real" <<WRAP
#!/usr/bin/env bash
# Stands in for the codex surface that defeated the name-list classifier: a
# long-named wrapper that stays alive while a nested CLI runs underneath it.
"$WORK/bin/hi-resolve" --apply "\$1" codex session-start > /dev/null
# Now a nested claude, exactly as \`claude -p\` would run inside this process.
printf 'NESTED %s\n' "\$("$WORK/bin/hi-resolve" "\$1" claude session-start)"
WRAP
chmod +x "$WORK/bin/claude-harness-codex-real"

pane_reset %wrap
OUT=$("$WORK/bin/claude-harness-codex-real" %wrap)
NESTED=$(printf '%s' "$OUT" | awk '/^NESTED/ { print $2 }')
check "nested claude under the wrapper is refused" "REFUSE" "$(field "$NESTED" 1)"
check "refusal comes from recorded ownership, not a name" "foreign-owner-nested" "$(field "$NESTED" 2)"
check "the wrapper's own claim held the window" "codex-$(pane_get %wrap @formation_identity_locked)" "$(pane_get %wrap window_name)"

# The negative control only controls anything if the fixture's name is out of
# reach of the check it is meant to defeat. Measure the truncation, don't recall
# it: `comm` is 15 characters on Linux and longer elsewhere.
sleep 60 &
LONGPID=$!
OBSERVED_COMM=$(ps -o comm= -p $LONGPID 2>/dev/null | tr -d ' \n')
kill $LONGPID 2>/dev/null
COMM_WINDOW=${#OBSERVED_COMM}
WRAPPER_LEN=${#OUT}
WRAPPER_LEN=$(printf 'claude-harness-codex-real' | wc -c | tr -d ' ')
printf '       (observed comm window here: %s chars via "%s")\n' "$COMM_WINDOW" "$OBSERVED_COMM"
if [ "$WRAPPER_LEN" -gt 15 ]; then
    ok "the fixture name is $WRAPPER_LEN chars — past the 15-char comm window the old check read"
else
    bad "fixture name is short enough for comm" "the negative control is not controlling anything"
fi

# ── one-shot ─────────────────────────────────────────────────────────────────
echo
echo "one-shot invocations"

pane_reset %oneshot
R=$(resolve %oneshot kimi one-shot)
check "a one-shot does not claim even a free pane" "PRESERVE" "$(field "$R" 1)"
check "one-shot reason is explicit" "one-shot" "$(field "$R" 2)"
check "a one-shot leaves the window name alone" "bash" "$(pane_get %oneshot window_name)"

pane_reset %oneshot2 "window_name=claude-testname" "pane_title=claude-testname"
resolve %oneshot2 kimi one-shot >/dev/null
check "the original repro: kimi --help keeps the parent's window" "claude-testname" "$(pane_get %oneshot2 window_name)"
check "the original repro: pane title survives too" "claude-testname" "$(pane_get %oneshot2 pane_title)"

# ── sequential reuse ─────────────────────────────────────────────────────────
echo
echo "sequential reuse after the previous CLI exits"

DEAD=$( (sleep 0 & echo $!) ); wait 2>/dev/null
pane_reset %seq "@harness_chassis=codex" "@harness_owner_key=$DEAD:99999999"
R=$(resolve %seq claude session-start)
check "a dead owner does not hold the pane" "CLAIM" "$(field "$R" 1)"

pane_reset %reuse "@harness_chassis=codex" "@harness_owner_key=$$:1"
R=$(resolve %reuse claude session-start)
check "a recycled pid with a stale start token counts as dead" "CLAIM" "$(field "$R" 1)"

# ── a live foreign owner elsewhere ───────────────────────────────────────────
echo
echo "a live owner that is not an ancestor"

sleep 120 &
OTHER=$!
pane_reset %other "@harness_chassis=kimi" "@harness_owner_key=$(proc_key_of $OTHER)"
R=$(resolve %other claude session-start)
check "an unrelated live owner still holds its pane" "REFUSE" "$(field "$R" 1)"
check "and is distinguished from the nested case" "owner-live-elsewhere" "$(field "$R" 2)"

# A second codex started while the first is suspended is live-but-not-ancestor.
# Restricting this refusal to foreign chassis let it take the pane, overwrite the
# owner token, exit, and leave the pane claimable while the original was still
# resumable.
pane_reset %sibling "@harness_chassis=codex" "@harness_owner_key=$(proc_key_of $OTHER)"
R=$(resolve %sibling codex session-start)
check "a same-chassis sibling cannot take a suspended owner's pane" "REFUSE" "$(field "$R" 1)"
check "and is refused for the same reason" "owner-live-elsewhere" "$(field "$R" 2)"

# The escape hatch for a deliberate handoff.
(
    # shellcheck disable=SC1090
    . "$LIB"
    harness_identity_release %sibling
)
R=$(resolve %sibling codex session-start)
check "an explicit release hands the pane over" "CLAIM" "$(field "$R" 1)"
kill $OTHER 2>/dev/null

# A nested launch of our own family must not repoint the owner token at itself:
# the outer process is the one that actually holds the pane.
pane_reset %samenest "@harness_chassis=codex" "@harness_owner_key=$(proc_key_of $$)" \
    "@formation_identity_locked=iron-rook" "@formation_id=iron-rook"
BEFORE_KEY=$(pane_get %samenest @harness_owner_key)
resolve_apply %samenest codex session-start >/dev/null
check "a nested same-chassis launch leaves the owner token alone" "$BEFORE_KEY" "$(pane_get %samenest @harness_owner_key)"

# ── owner key composition ────────────────────────────────────────────────────
echo
echo "owner key"

KEY=$(proc_key_of $$)
case "$KEY" in
    "$$":*:*) ok "the owner key is pid, start token and boot id" ;;
    *)        bad "the owner key is pid, start token and boot id" "got '$KEY'" ;;
esac
KEY_FIELDS=$(printf '%s' "$KEY" | awk -F: '{ print NF }')
check "three fields, so a reboot cannot resurrect a claim" "3" "$KEY_FIELDS"

# ── serialization ────────────────────────────────────────────────────────────
echo
echo "concurrent claims"

# Two claimants racing a free pane. Without a lock both resolve CLAIM(first) and
# interleave their writes, leaving one claimant's chassis beside the other's
# owner token. The pane must end up wholly one claimant's.
pane_reset %race
cat > "$WORK/bin/hi-claim" <<HELPER
#!/usr/bin/env bash
. "$LIB"
harness_identity_claim --pane "\$1" --chassis "\$2" --mode session-start
printf '%s|%s|%s\n' "\$HARNESS_IDENTITY_DECISION" "\$HARNESS_IDENTITY_REASON" "\$HARNESS_IDENTITY_NAME"
HELPER
chmod +x "$WORK/bin/hi-claim"

"$WORK/bin/hi-claim" %race codex > "$WORK/race.a" &
A=$!
"$WORK/bin/hi-claim" %race claude > "$WORK/race.b" &
B=$!
wait $A $B 2>/dev/null
RACE_CHASSIS=$(pane_get %race @harness_chassis)
RACE_NAME=$(pane_get %race window_name)
case "$RACE_NAME" in
    "$RACE_CHASSIS"-*) ok "a contended pane ends up wholly one claimant's ($RACE_CHASSIS)" ;;
    *) bad "a contended pane ends up wholly one claimant's" "chassis=$RACE_CHASSIS window=$RACE_NAME" ;;
esac
WON=$(grep -c CLAIM "$WORK/race.a" "$WORK/race.b" 2>/dev/null | awk -F: '{ s += $2 } END { print s }')
check "exactly one claimant wins" "1" "$WON"

# ── stale TMUX_PANE ──────────────────────────────────────────────────────────
echo
echo "stale or inherited TMUX_PANE"

pane_reset %stale "pane_pid=1"
R=$(resolve %stale claude session-start)
check "a pane whose process is not in our ancestry is refused" "REFUSE" "$(field "$R" 1)"
check "and says so" "not-in-pane" "$(field "$R" 2)"

# ── kill switch, every name, every chassis ───────────────────────────────────
echo
echo "kill switch coverage"

for var in HARNESS_TMUX_SELF_NAME_DISABLE CLAUDE_TMUX_NAME_DISABLE \
           CODEX_TMUX_NAME_DISABLE KIMI_TMUX_NAME_DISABLE \
           GROK_TMUX_NAME_DISABLE HIPPOCAMPUS_TMUX_NAME_DISABLE; do
    for chassis in claude codex kimi grok; do
        pane_reset %ks
        R=$(env "$var=1" bash -c "
            . '$LIB'
            harness_identity_resolve --pane %ks --chassis $chassis --mode session-start
            printf '%s|%s\n' \"\$HARNESS_IDENTITY_DECISION\" \"\$HARNESS_IDENTITY_REASON\"
        ")
        if [ "$(field "$R" 1)" = "REFUSE" ] && [ "$(field "$R" 2)" = "disabled" ]; then
            :
        else
            bad "$var stops $chassis" "got $R"
        fi
    done
done
ok "all six kill-switch names stop all four chassis (24 combinations)"

# ── formation ────────────────────────────────────────────────────────────────
echo
echo "formation workers"

pane_reset %fm "@formation_identity_locked=iron-wren" "@formation_id=iron-wren"
R=$(FORMATION_SELF=iron-wren resolve %fm codex session-start)
check "a worker on its own pane claims its locked id" "CLAIM" "$(field "$R" 1)"
check "and the name carries its own chassis" "codex-iron-wren" "$(field "$R" 3)"

pane_reset %fm2 "@formation_identity_locked=slate-rook" "@formation_id=slate-rook"
R=$(FORMATION_SELF=iron-wren resolve %fm2 kimi session-start)
check "a worker on someone else's pane refuses" "REFUSE" "$(field "$R" 1)"
check "and names the mismatch" "formation-mismatch" "$(field "$R" 2)"

# The 2026-07-24 regression: a kimi child stamped `kimi-` onto a codex parent.
pane_reset %parent "@harness_chassis=codex" "@harness_owner_key=$(proc_key_of $$)" \
    "@formation_identity_locked=issue107-codex" "@formation_id=issue107-codex" \
    "window_name=codex-issue107-codex" "pane_title=codex-issue107-codex"
FORMATION_SELF=issue107-codex resolve_apply %parent kimi session-start >/dev/null
check "a kimi child cannot restamp a codex parent's window" "codex-issue107-codex" "$(pane_get %parent window_name)"

# ── locked identity across compact/resume ────────────────────────────────────
echo
echo "resume and compact"

pane_reset %resume "@formation_identity_locked=amber-koto" "@formation_id=amber-koto"
R=$(resolve %resume claude session-start)
check "a locked routing id is reused, not regenerated" "amber-koto" "$(field "$R" 4)"
check "and the session is marked resumed" "1" "$(field "$R" 5)"

pane_reset %idem
R1=$(resolve_apply %idem codex session-start)
N1=$(pane_get %idem window_name)
R2=$(resolve_apply %idem codex session-start)
N2=$(pane_get %idem window_name)
check "re-running the same chassis is idempotent" "$N1" "$N2"

# ── shared windows ───────────────────────────────────────────────────────────
echo
echo "split windows"

# A pane sharing a window whose name belongs to another chassis is inert, not
# partially claimed (#101). Half an identity — a routing id that no display
# surface can show, sitting beside the lead's in formation's view — was worse
# than none, so this is deliberate rather than a limitation.
pane_reset %split "window_panes=2" "window_name=claude-lead-name"
R=$(resolve_apply %split codex session-start)
check "a split pane under a foreign lead claims nothing" "REFUSE" "$(field "$R" 1)"
check "the shared window keeps its name" "claude-lead-name" "$(pane_get %split window_name)"
check "and the pane title is left alone too" "bash" "$(pane_get %split pane_title)"

# Its own chassis leading the window is a different matter: the window name is
# already ours, so the second pane takes an identity and only the window name
# stays put.
pane_reset %split_same "window_panes=2" "window_name=codex-lead-name"
R=$(resolve_apply %split_same codex session-start)
check "a split pane under our own lead does claim" "CLAIM" "$(field "$R" 1)"
check "without renaming the shared window" "codex-lead-name" "$(pane_get %split_same window_name)"
SPLIT_TITLE=$(pane_get %split_same pane_title)
case "$SPLIT_TITLE" in
    codex-*) ok "and takes its own pane title" ;;
    *)       bad "and takes its own pane title" "pane_title=$SPLIT_TITLE" ;;
esac

# ── transaction ordering ─────────────────────────────────────────────────────
echo
echo "transaction"

pane_reset %tx
resolve_apply %tx grok session-start >/dev/null
for opt in @harness_chassis @harness_owner_key @formation_identity_locked @formation_id; do
    V=$(pane_get %tx "$opt")
    if [ -n "$V" ]; then ok "apply writes $opt"; else bad "apply writes $opt" "empty"; fi
done
check "window name matches the claimed identity" "grok-$(pane_get %tx @formation_identity_locked)" "$(pane_get %tx window_name)"
check "pane title matches the window name" "$(pane_get %tx window_name)" "$(pane_get %tx pane_title)"
check "the pane records the claiming chassis" "grok" "$(pane_get %tx @harness_chassis)"

# ── routing id collisions ────────────────────────────────────────────────────
echo
echo "routing id collisions"

pane_reset %holder "@formation_identity_locked=dusk-otter" "@formation_id=dusk-otter"
pane_reset %taker  "@formation_identity_locked=dusk-otter" "@formation_id=dusk-otter"
R=$(resolve %taker claude session-start)
check "two panes cannot hold one routing id" "REFUSE" "$(field "$R" 1)"
check "and the reason is the collision" "routing-id-taken" "$(field "$R" 2)"

# ── legacy panes ─────────────────────────────────────────────────────────────
echo
echo "panes claimed before this core existed"

# A pane claimed by the previous generation of adapters has no @harness_chassis
# to consult, so ancestry by name is all that is left for it. That fallback is
# transitional and weaker than the owner key by design; these two cases pin what
# it is still expected to do, so that removing it later is a visible change.
# `comm` reports the exec'd binary's basename, so a shebang script would appear
# as `bash` and the fallback would never fire. A bash symlink named `codex` is
# what the old classifier actually saw.
ln -sf "$(command -v bash)" "$WORK/bin/codex"

# `bash script` interprets in-process, and `bash -c 'one command'` execs it, so
# either shape would leave no codex-named ancestor at all and the fixture would
# pass by having nothing to detect. The trailing `exit` defeats that
# optimisation and forces a real child.
pane_reset %legacy "window_name=codex-old-name" "pane_title=codex-old-name"
R=$("$WORK/bin/codex" -c '"$@"; exit $?' _ "$WORK/bin/hi-resolve" %legacy claude session-start)
check "a legacy label plus a live foreign ancestor is refused" "REFUSE" "$(field "$R" 1)"
check "and says which signal it used" "legacy-foreign-ancestor" "$(field "$R" 2)"

pane_reset %legacy_gone "window_name=codex-old-name" "pane_title=codex-old-name"
R=$(resolve %legacy_gone claude session-start)
check "a legacy label whose owner has exited allows sequential reuse" "CLAIM" "$(field "$R" 1)"

pane_reset %legacy_shared "window_name=codex-old-name" "window_panes=3"
R=$(resolve_apply %legacy_shared claude session-start)
check "a legacy foreign label on a shared window is never taken" "REFUSE" "$(field "$R" 1)"
check "and the window it does not own is untouched" "codex-old-name" "$(pane_get %legacy_shared window_name)"

pane_reset %legacy_same "window_name=claude-old-name" "pane_title=claude-old-name"
R=$(resolve %legacy_same claude session-start)
check "a legacy label of our own chassis is adopted" "CLAIM" "$(field "$R" 1)"

echo
echo "─────────────────────────────────────────"
printf '%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
