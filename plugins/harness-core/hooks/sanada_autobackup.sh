#!/bin/bash
# PreToolUse Bash hook: 真田 silent auto-backup (gh #35 — behavioral -> structural).
#
# Before a DESTRUCTIVE file command runs, silently snapshot the target(s) to
# ~/sanada_backup_persistent/. Structuralizes the global-CLAUDE.md 真田 protocol
# ("黙って backup を取る") so it no longer depends on the model remembering.
#
# Invariants:
#   - NEVER blocks; never exits non-zero. Insurance, not a guard. Always exit 0;
#     every fs op that could WALK a big tree (du/cp/find/chmod) is `timeout`-bounded.
#     (Basic stat/test/cd on a DEAD network mount can still block, but the user's own
#     command targeting that mount would block equally — the hook is not the added
#     bottleneck. codex #35 CRITICAL was the unbounded du/cp tree-walk, now bounded.)
#   - SILENT. No stdout. Mentioned only when a restore is needed ("こんなこともあろうかと").
#   - Backups are PRIVATE (umask 077 + chmod 700/go-rwx; copied files may hold secrets).
#   - Over-fire here is LOW-stakes (a harmless spurious copy), bounded by existence,
#     50MB/target cap, per-invocation file-count cap, glob-skip, and timeouts.
#
# Scope: rm / redirect-overwrite(> , >|) / sed -i / truncate / mv|cp (incl dir dest) /
# dd of= / tee / find -delete|-exec rm + git working-tree-destroying ops (reset --hard,
# clean -fdx [saves untracked], checkout/restore, stash drop|clear|pop [saves stash]).
# DB ops (DROP/TRUNCATE/DELETE) need a pg_dump and are OUT of scope here.

umask 077
source "$(dirname "$0")/lib.sh"

INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
[ -z "$CMD" ] && exit 0
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
[ -n "$CWD" ] && cd "$CWD" 2>/dev/null

BK_ROOT="$HOME/sanada_backup_persistent"
MAX_BYTES=$((50 * 1024 * 1024))   # per-target size cap
MAX_FILES=60                      # per-invocation file-count cap (aggregate disk guard)
TO_FILE=5                         # timeout (s) for a single regular-file copy
TO_DIR=8                          # timeout (s) for directory size/copy
TS=$(date +%Y%m%d_%H%M%S)
# BK_DIR is DETERMINISTIC (computed in parent scope) so the end-of-script chmod can
# see it — the `printf | while` loops below run _backup in a SUBSHELL, so a lazily-
# assigned var would be lost. DISABLED is set if BK_ROOT is a symlink (codex #35-2:
# refuse to follow it into an arbitrary dir).
BK_DIR="$BK_ROOT/auto_${TS}_$$"
DISABLED=0
# refuse if the backup root, or a pre-existing BK_DIR, is a symlink / non-dir (codex
# #35-4: writes would follow it outside the intended tree). Checked in parent scope
# so DISABLED propagates into the subshell `| while` loops.
[ -L "$BK_ROOT" ] && { DISABLED=1; hook_log "sanada_autobackup" "BK_ROOT is a symlink — refusing"; }
[ -L "$BK_DIR" ] && { DISABLED=1; hook_log "sanada_autobackup" "BK_DIR is a symlink — refusing"; }
[ -e "$BK_DIR" ] && [ ! -d "$BK_DIR" ] && DISABLED=1

_ensure_dir() {   # returns 0 ONLY if BK_DIR is a safe (real, non-symlink) private dir
    [ "$DISABLED" = 1 ] && return 1
    [ -d "$BK_DIR" ] || mkdir -p "$BK_DIR" 2>/dev/null
    chmod 700 "$BK_ROOT" "$BK_DIR" 2>/dev/null    # always (codex #35-5: a pre-existing dir was left un-tightened)
    # success ONLY if it is a real non-symlink dir whose mode is VERIFIED 700 (codex
    # #35-6: don't write secrets if chmod silently failed / was ineffective).
    [ -d "$BK_DIR" ] && [ ! -L "$BK_DIR" ] && [ "$(stat -c %a "$BK_DIR" 2>/dev/null)" = 700 ]
}

_cap_reached() {   # aggregate guard: stop after MAX_FILES copies this invocation
    [ "$DISABLED" = 0 ] || return 1
    local n; n=$(timeout 2 find "$BK_DIR" -type f 2>/dev/null | head -n $((MAX_FILES + 1)) | wc -l)
    [ "$n" -ge "$MAX_FILES" ]
}

TOTAL_CAP=$((200 * 1024 * 1024))   # per-invocation AGGREGATE byte budget (disk-fill guard)
MAX_DIR_FILES=5000                 # per-dir-target inode/file-count cap
_remaining() {     # bytes left under TOTAL_CAP for this invocation (0 if over)
    [ -d "$BK_DIR" ] || { echo "$TOTAL_CAP"; return; }
    local used; used=$(timeout 2 du -sb "$BK_DIR" 2>/dev/null | cut -f1); [ -z "$used" ] && used=0
    local r=$((TOTAL_CAP - used)); [ "$r" -lt 0 ] && r=0; echo "$r"
}

_backup() {   # $1 = path to snapshot if it exists, named (no glob), size/time-bounded
    local p="$1" sz
    [ -z "$p" ] && return 0
    p="${p%\"}"; p="${p#\"}"; p="${p%\'}"; p="${p#\'}"          # strip surrounding quotes
    case "$p" in *[\*\?\[\]\{\}\$\~\`]*) return 0 ;; esac        # skip glob/expansion
    case "$p" in *..*) return 0 ;; esac                          # skip `..` (codex #35-2: cp --parents ../x escapes BK_DIR)
    [ -e "$p" ] || return 0
    _ensure_dir || return 0
    _cap_reached && { hook_log "sanada_autobackup" "cap: >=$MAX_FILES files, skip $p"; return 0; }
    local rem cap; rem=$(_remaining)
    [ "$rem" -le 0 ] && { hook_log "sanada_autobackup" "budget exhausted this run, skip $p"; return 0; }
    cap=$MAX_BYTES; [ "$rem" -lt "$cap" ] && cap=$rem   # never write past the remaining aggregate budget
    if [ -f "$p" ] && [ ! -L "$p" ]; then
        sz=$(stat -c %s -- "$p" 2>/dev/null || echo 0)             # cheap (no tree walk)
        [ "$sz" -gt "$cap" ] && { hook_log "sanada_autobackup" "skip (>cap/remaining): $p"; return 0; }
        timeout "$TO_FILE" cp -a --parents -- "$p" "$BK_DIR/" 2>/dev/null \
            || timeout "$TO_FILE" cp -a -- "$p" "$BK_DIR/$(printf '%s' "$p" | tr '/ ' '__')" 2>/dev/null
    elif [ -d "$p" ] && [ ! -L "$p" ]; then
        sz=$(timeout 3 du -sb -- "$p" 2>/dev/null | cut -f1)        # bounded walk
        { [ -z "$sz" ] || [ "$sz" -gt "$cap" ]; } && { hook_log "sanada_autobackup" "skip dir (>cap/remaining/slow): $p"; return 0; }
        # inode guard: skip a dir with too many files (codex #35-8: 50MB can hold millions of tiny files)
        nf=$(timeout 2 find "$p" -type f 2>/dev/null | head -n $((MAX_DIR_FILES + 1)) | wc -l)
        [ "$nf" -gt "$MAX_DIR_FILES" ] && { hook_log "sanada_autobackup" "skip dir (>$MAX_DIR_FILES files): $p"; return 0; }
        timeout "$TO_DIR" cp -a --parents -- "$p" "$BK_DIR/" 2>/dev/null \
            || timeout "$TO_DIR" cp -a -- "$p" "$BK_DIR/$(printf '%s' "$p" | tr '/ ' '__')" 2>/dev/null
    else
        return 0   # symlink / special: don't follow
    fi
    hook_log "sanada_autobackup" "backed up: $p"   # perms tightened once at script end (bounded)
}

# Normalize the clobber redirect `>|` to `>` FIRST, so the `|` in it is not mistaken
# for a pipe by the segment splitter below (which would strip the redirect target).
CMD_N=$(printf '%s' "$CMD" | sed -E 's/>\|/>/g')
# Split into segments on control operators; judge each by its leading verb so a mere
# mention/read (grep/echo/cat with these words as args) does not extract targets.
SEGMENTS=$(printf '%s' "$CMD_N" | sed -E 's/(\|\||&&)/\n/g; s/[;|]/\n/g')
while IFS= read -r seg; do
    [ -z "${seg//[[:space:]]/}" ] && continue

    # (R) redirect OVERWRITE — verb-independent. Neutralize append (`>>`), keep clobber
    # (`>|`) and fd/&  overwrites, then extract targets. Not `>>` (append).
    seg_r=$(printf '%s' "$seg" | sed -E 's/>>+/ __APPEND__ /g; s/>\|/> /g')
    printf '%s' "$seg_r" | grep -oE '([0-9]*|&)>[[:space:]]*[^[:space:]<>&|]+' | sed -E 's/^([0-9]*|&)>[[:space:]]*//' \
        | while IFS= read -r t; do _backup "$t"; done

    body=$(printf '%s' "$seg" | sed -E 's/^[[:space:]]*(env[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)*//')
    v=$(printf '%s' "$body" | awk 'NR==1{print $1}'); v="${v##*/}"
    ops=$(printf '%s' "$body" | awk 'NR==1{for(i=2;i<=NF;i++) print $i}')

    case "$v" in
        rm)
            printf '%s' "$ops" | grep -vE '^-' | while IFS= read -r t; do _backup "$t"; done ;;
        truncate)
            printf '%s' "$ops" | grep -vE '^-|^[0-9]+[KMG]?$|^[+-@]?%?[0-9]' | while IFS= read -r t; do _backup "$t"; done ;;
        sed)
            if printf '%s' "$body" | grep -qE '(^|[[:space:]])-i'; then
                printf '%s' "$ops" | grep -vE '^-' | while IFS= read -r t; do _backup "$t"; done
            fi ;;
        mv|cp|install)
            # back up the DESTINATION (last operand). If it is an existing dir and there
            # is a single source, the real overwrite target is dest/basename(src).
            d=$(printf '%s' "$ops" | grep -vE '^-' | tail -1)
            nsrc=$(printf '%s' "$ops" | grep -vE '^-' | wc -l)
            if [ -d "$d" ] && [ "$nsrc" -eq 2 ]; then
                s=$(printf '%s' "$ops" | grep -vE '^-' | head -1)
                _backup "${d%/}/$(basename "$s")"
            else
                _backup "$d"
            fi ;;
        dd)
            printf '%s' "$body" | grep -oE 'of=[^[:space:]]+' | sed 's/^of=//' | while IFS= read -r t; do _backup "$t"; done ;;
        tee)
            if ! printf '%s' "$body" | grep -qE '(^|[[:space:]])(-a|--append)([[:space:]]|$)'; then
                printf '%s' "$ops" | grep -vE '^-' | while IFS= read -r t; do _backup "$t"; done
            fi ;;
        find)
            if printf '%s' "$body" | grep -qE '(-delete([[:space:]]|$)|-exec[[:space:]]+rm)'; then
                root=$(printf '%s' "$ops" | grep -vE '^-' | head -1); [ -n "$root" ] && _backup "$root"
            fi ;;
        git)
            sub=$(printf '%s' "$ops" | grep -vE '^-' | head -1)
            # honor DISABLED (codex #35-3: git writes must not run when BK_ROOT is a
            # symlink) and bound rev-parse with a timeout (no unbounded git/fs op).
            if [ "$DISABLED" = 0 ] && timeout 3 git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
                case "$sub" in
                    reset|checkout|restore)
                        if printf '%s' "$body" | grep -qE 'reset[[:space:]].*--hard|checkout([[:space:]]|$)|restore([[:space:]]|$)'; then
                            _ensure_dir || continue; rem=$(_remaining); [ "$rem" -le 0 ] && continue
                            timeout 5 git diff HEAD 2>/dev/null | head -c "$rem" > "$BK_DIR/git_worktree_$$.patch"
                            timeout 5 git status --porcelain 2>/dev/null | head -c 1000000 > "$BK_DIR/git_status_$$.txt"
                            hook_log "sanada_autobackup" "git worktree patch saved (reset/checkout/restore)"
                        fi ;;
                    clean)
                        if printf '%s' "$body" | grep -qE 'clean[[:space:]].*-[a-zA-Z]*f'; then
                            _ensure_dir || continue; rem=$(_remaining); [ "$rem" -le 0 ] && continue
                            # clean destroys untracked (+ignored with -x); tar them (bounded)
                            # --null -T - reads a NUL-delimited file list (no xargs, no option injection from hostile filenames)
                            timeout "$TO_DIR" bash -c 'git ls-files -o --exclude-standard -z | tar --null -T - -cf - 2>/dev/null | head -c '"$rem"' > "'"$BK_DIR"'/untracked_$$.tar"' 2>/dev/null
                            timeout 5 git status --porcelain 2>/dev/null | head -c 1000000 > "$BK_DIR/git_status_$$.txt"
                            hook_log "sanada_autobackup" "git clean: untracked tarred (size-capped)"
                        fi ;;
                    stash)
                        if printf '%s' "$body" | grep -qE 'stash[[:space:]]+(drop|clear|pop)'; then
                            _ensure_dir || continue; rem=$(_remaining); [ "$rem" -le 0 ] && continue
                            timeout 5 git stash list 2>/dev/null | head -c 1000000 > "$BK_DIR/stash_list_$$.txt"
                            # per-iteration remaining-budget check (codex #35-9: 20×50MB could blow TOTAL_CAP)
                            timeout 8 bash -c '
                              i=0; bk="'"$BK_DIR"'"; cap='"$TOTAL_CAP"'; mx='"$MAX_BYTES"'
                              git stash list --format=%gd 2>/dev/null | while read s; do
                                used=$(du -sb "$bk" 2>/dev/null | cut -f1); used=${used:-0}
                                rem=$((cap - used)); [ "$rem" -le 0 ] && break
                                [ "$rem" -gt "$mx" ] && rem="$mx"
                                git stash show -p --include-untracked "$s" 2>/dev/null | head -c "$rem" > "$bk/stash_${i}_$$.patch"
                                i=$((i+1)); [ $i -ge 20 ] && break
                              done' 2>/dev/null
                            hook_log "sanada_autobackup" "git stash diffs saved before drop/clear/pop"
                        fi ;;
                esac
            fi ;;
    esac
done <<EOF
$SEGMENTS
EOF

# Tighten backup perms ONCE, bounded (codex #35-2: per-copy `chmod -R` was an
# unbounded recursive walk that could block). BK_DIR is small (<= MAX_FILES).
[ "$DISABLED" = 0 ] && [ -d "$BK_DIR" ] && timeout 3 chmod -R go-rwx "$BK_DIR" 2>/dev/null

exit 0
