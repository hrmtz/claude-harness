#!/usr/bin/env bash
# install-codex-skills.sh — install harness-magi-codex skills into the Codex skill dir.
#
# Codex scans $CODEX_HOME/skills/ (default ~/.codex/skills/) for <name>/SKILL.md.
#
# Symlink by default, not rsync: ~/.codex/skills/formation is already a live symlink into a
# repo, so Codex demonstrably resolves symlinked skills, and a symlink cannot drift from the
# repo the way an installed copy does. (The harness-kimi rsync'd persona templates have
# already diverged from their originals -- measured.) Use --copy if you need a detached copy.
#
# Idempotent. Re-run after editing SKILL.md only if you used --copy.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="symlink"
RENAME_NOREPLACE="$HERE/scripts/magi_rename_noreplace.py"

while [ $# -gt 0 ]; do
    case "$1" in
        --copy) MODE="copy"; shift ;;
        -h|--help) echo "usage: $0 [--copy]"; exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 64 ;;
    esac
done

CODEX_HOME="${CODEX_HOME:-}"
if [ -z "$CODEX_HOME" ]; then
    [ -n "${HOME:-}" ] || {
        echo "[harness-magi-codex] error: set CODEX_HOME or HOME before installing skills" >&2
        exit 1
    }
    CODEX_HOME="$HOME/.codex"
fi
TARGET="$CODEX_HOME/skills"

command -v codex >/dev/null 2>&1 || echo "[harness-magi-codex] warning: codex CLI not found on PATH" >&2
command -v claude >/dev/null 2>&1 || {
    echo "[harness-magi-codex] warning: claude CLI not found. The cross-family round will" >&2
    echo "                     fail closed (exit 2) and NO plateau can be granted." >&2
}
command -v flock >/dev/null 2>&1 || { echo "[harness-magi-codex] error: flock(1) required" >&2; exit 1; }
command -v bwrap >/dev/null 2>&1 || { echo "[harness-magi-codex] error: bubblewrap required" >&2; exit 1; }
[ -x "$RENAME_NOREPLACE" ] || {
    echo "[harness-magi-codex] error: no-replace rename helper is missing" >&2; exit 1; }

mkdir -p "$TARGET"
exec 9>"$TARGET/.harness-magi-codex.install.lock" || {
    echo "[harness-magi-codex] error: cannot open installer lock" >&2; exit 1; }
flock -x 9 || { echo "[harness-magi-codex] error: cannot acquire installer lock" >&2; exit 1; }

marker_matches() {
    local expected_value="$1" marker_path="$2"
    [ -f "$marker_path" ] && printf '%s\n' "$expected_value" | cmp -s - "$marker_path"
}

validate_skill_destination() {
    local src="$1" dst="$2"
    local marker expected_marker
    if [ -L "$dst" ]; then
        if [ "$(readlink -f "$dst")" = "$(readlink -f "$src")" ]; then
            : # Owned exact symlink.
        else
            echo "[harness-magi-codex] error: refusing foreign symlink $dst" >&2
            return 1
        fi
    elif [ -d "$dst" ]; then
        marker="$dst/.harness-magi-codex"
        expected_marker="installed by harness-magi-codex from $src"
        if ! marker_matches "$expected_marker" "$marker"; then
            echo "[harness-magi-codex] error: refusing foreign skill directory $dst" >&2
            return 1
        fi
    elif [ -e "$dst" ]; then
        echo "[harness-magi-codex] error: $dst exists and is not a skill dir or symlink" >&2
        return 1
    fi
}

ACTIVE_OLD=""
ACTIVE_DST=""
ACTIVE_STAGE_ROOT=""
ACTIVE_PUBLISHED=0
ACTIVE_SKILL=""
ACTIVE_SRC=""
TX_SKILLS=()
TX_SRCS=()
TX_DSTS=()
TX_OLDS=()
TX_MESSAGES=()
TX_COMMITTED=0

path_exists() {
    [ -e "$1" ] || [ -L "$1" ]
}

remove_owned_destination() {
    local skill="$1" src="$2" dst="$3"
    local quarantine
    path_exists "$dst" || return 0
    quarantine="$TARGET/.${skill}.rollback.$$.$RANDOM"
    path_exists "$quarantine" && {
        echo "[harness-magi-codex] error: rollback quarantine exists: $quarantine" >&2
        return 1
    }
    python3 "$RENAME_NOREPLACE" "$dst" "$quarantine" || {
        echo "[harness-magi-codex] error: cannot quarantine rollback destination $dst" >&2
        return 1
    }
    if ! validate_skill_destination "$src" "$quarantine"; then
        if ! path_exists "$dst"; then
            python3 "$RENAME_NOREPLACE" "$quarantine" "$dst" || {
                echo "[harness-magi-codex] error: foreign rollback entry preserved at $quarantine" >&2
            }
        else
            echo "[harness-magi-codex] error: foreign rollback entry preserved at $quarantine" >&2
        fi
        return 1
    fi
    echo "[harness-magi-codex] preserved rolled-back publication at $quarantine" >&2
}

rollback_one() {
    local skill="$1" src="$2" dst="$3" old="$4"
    local failed=0
    remove_owned_destination "$skill" "$src" "$dst" || failed=1
    if [ -n "$old" ] && path_exists "$old"; then
        if path_exists "$dst"; then
            echo "[harness-magi-codex] error: cannot restore $dst; preserved at $old" >&2
            failed=1
        else
            python3 "$RENAME_NOREPLACE" "$old" "$dst" || failed=1
        fi
    fi
    return "$failed"
}

restore_interrupted_replacement() {
    local original_rc=$? cleanup_failed=0 index
    trap - EXIT
    if [ "$TX_COMMITTED" -eq 0 ]; then
        if [ "$ACTIVE_PUBLISHED" -eq 1 ]; then
            rollback_one "$ACTIVE_SKILL" "$ACTIVE_SRC" "$ACTIVE_DST" "$ACTIVE_OLD" \
                || cleanup_failed=1
        elif [ -n "$ACTIVE_OLD" ] && path_exists "$ACTIVE_OLD"; then
            if ! path_exists "$ACTIVE_DST"; then
                python3 "$RENAME_NOREPLACE" "$ACTIVE_OLD" "$ACTIVE_DST" \
                    || cleanup_failed=1
            else
                echo "[harness-magi-codex] preserved interrupted predecessor at $ACTIVE_OLD" >&2
                cleanup_failed=1
            fi
        fi
        for ((index=${#TX_SKILLS[@]} - 1; index >= 0; index--)); do
            rollback_one \
                "${TX_SKILLS[$index]}" "${TX_SRCS[$index]}" \
                "${TX_DSTS[$index]}" "${TX_OLDS[$index]}" || cleanup_failed=1
        done
    fi
    if [ -n "$ACTIVE_STAGE_ROOT" ] && [ -d "$ACTIVE_STAGE_ROOT" ]; then
        rm -rf "$ACTIVE_STAGE_ROOT" || cleanup_failed=1
    fi
    if [ "$cleanup_failed" -ne 0 ]; then
        echo "[harness-magi-codex] error: interrupted replacement cleanup was incomplete" >&2
        [ "$original_rc" -ne 0 ] || original_rc=1
    fi
    exit "$original_rc"
}
trap restore_interrupted_replacement EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Validate the complete install set before changing any destination. A foreign skill late in the
# list must not leave earlier skills partially replaced.
for skill in magi dual-magi-review ultramagi; do
    src="$HERE/skills/$skill"
    dst="$TARGET/$skill"
    [ -d "$src" ] || { echo "error: source skill not found: $src" >&2; exit 1; }

    validate_skill_destination "$src" "$dst" || exit 1
done

for skill in magi dual-magi-review ultramagi; do
    src="$HERE/skills/$skill"
    dst="$TARGET/$skill"
    stage_root="$(mktemp -d "$TARGET/.${skill}.stage.XXXXXX")" || exit 1
    ACTIVE_STAGE_ROOT="$stage_root"
    ACTIVE_SKILL="$skill"
    ACTIVE_SRC="$src"
    staged="$stage_root/entry"
    if [ "$MODE" = "symlink" ]; then
        ln -s "$src" "$staged" || {
            rmdir "$stage_root"
            echo "[harness-magi-codex] error: cannot stage symlink for $dst" >&2
            exit 1
        }
    else
        mkdir "$staged" || { rmdir "$stage_root"; exit 1; }
        if command -v rsync >/dev/null 2>&1; then
            rsync -a --delete "$src/" "$staged/" || {
                rm -rf "$stage_root"
                echo "[harness-magi-codex] error: staged rsync failed" >&2
                exit 1
            }
        else
            cp -R "$src/." "$staged/" || {
                rm -rf "$stage_root"
                echo "[harness-magi-codex] error: staged copy failed" >&2
                exit 1
            }
        fi
        printf 'installed by harness-magi-codex from %s\n' "$src" \
            > "$staged/.harness-magi-codex" || {
                rm -rf "$stage_root"
                echo "[harness-magi-codex] error: ownership marker write failed" >&2
                exit 1
            }
    fi

    old=""
    ACTIVE_OLD=""
    ACTIVE_DST="$dst"
    ACTIVE_PUBLISHED=0
    if path_exists "$dst"; then
        old="$TARGET/.${skill}.old.$$.$RANDOM"
        if path_exists "$old"; then
            rm -rf "$stage_root"
            echo "[harness-magi-codex] error: replacement backup path exists: $old" >&2
            exit 1
        fi
        ACTIVE_OLD="$old"
        mv "$dst" "$old" || {
            ACTIVE_OLD=""
            ACTIVE_DST=""
            rm -rf "$stage_root"
            echo "[harness-magi-codex] error: cannot preserve destination $dst" >&2
            exit 1
        }
        if ! validate_skill_destination "$src" "$old"; then
            if ! { [ -e "$dst" ] || [ -L "$dst" ]; }; then
                python3 "$RENAME_NOREPLACE" "$old" "$dst" || {
                    echo "[harness-magi-codex] error: cannot restore foreign destination $dst; preserved at $old" >&2
                }
            else
                echo "[harness-magi-codex] error: destination changed again; foreign entry preserved at $old" >&2
            fi
            rm -rf "$stage_root"
            exit 1
        fi
    fi

    if ! python3 "$RENAME_NOREPLACE" "$staged" "$dst"; then
        [ -n "$old" ] && ! { [ -e "$dst" ] || [ -L "$dst" ]; } \
            && python3 "$RENAME_NOREPLACE" "$old" "$dst"
        rm -rf "$stage_root"
        echo "[harness-magi-codex] error: cannot publish staged skill $dst" >&2
        exit 1
    fi
    ACTIVE_PUBLISHED=1
    rmdir "$stage_root" || {
        echo "[harness-magi-codex] error: cannot remove empty stage root $stage_root" >&2
        exit 1
    }
    ACTIVE_STAGE_ROOT=""
    TX_SKILLS+=("$skill")
    TX_SRCS+=("$src")
    TX_DSTS+=("$dst")
    TX_OLDS+=("$old")

    if [ "$MODE" = "symlink" ]; then
        TX_MESSAGES+=("[harness-magi-codex] linked $dst -> $src")
    else
        TX_MESSAGES+=("[harness-magi-codex] copied $dst")
    fi
    ACTIVE_OLD=""
    ACTIVE_DST=""
    ACTIVE_PUBLISHED=0
    ACTIVE_SKILL=""
    ACTIVE_SRC=""
done

TX_COMMITTED=1
for old in "${TX_OLDS[@]}"; do
    [ -n "$old" ] || continue
    if [ -L "$old" ]; then
        rm -f "$old" || {
            echo "[harness-magi-codex] error: installed set but cannot remove old symlink $old" >&2
            exit 1
        }
    else
        rm -rf "$old" || {
            echo "[harness-magi-codex] error: installed set but cannot remove old directory $old" >&2
            exit 1
        }
    fi
done

for message in "${TX_MESSAGES[@]}"; do
    echo "$message"
done
echo "[harness-magi-codex] done. Restart Codex sessions to discover the skills."
echo "[harness-magi-codex] note: the plateau gate detects accidental skips (T1), NOT an"
echo "                     adversarial same-user process (T2). It is not forgery-resistant."
