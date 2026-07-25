#!/bin/bash
# install-kimi-skills.sh — install harness-kimi review skills and the shared Magi runtime link.
#
# Kimi Code CLI scans:
#   - $KIMI_CODE_HOME/skills/  (default ~/.kimi-code/skills/)
#   - ~/.agents/skills/
# for SKILL.md files.
set -uo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")" || {
    echo "error: cannot resolve installer path: ${BASH_SOURCE[0]}" >&2; exit 1; }
HERE="$(cd "$(dirname "$SCRIPT_PATH")" && pwd -P)"
KIMI_HOME="${KIMI_CODE_HOME:-}"
if [ -z "$KIMI_HOME" ]; then
    [ -n "${HOME:-}" ] || {
        echo "error: set KIMI_CODE_HOME or HOME before installing Kimi skills" >&2
        exit 1
    }
    KIMI_HOME="$HOME/.kimi-code"
fi
TARGET="$KIMI_HOME/skills"
RUNTIME="$KIMI_HOME/harness-magi-runtime"
MAGI_RUNTIME_SOURCE="$(cd "$HERE/../harness-magi-codex" && pwd -P)" || {
    echo "error: shared Magi runtime source is missing" >&2; exit 1; }
[ -n "$MAGI_RUNTIME_SOURCE" ] && [ -d "$MAGI_RUNTIME_SOURCE" ] || {
    echo "error: shared Magi runtime source is invalid" >&2; exit 1; }
RENAME_NOREPLACE="$MAGI_RUNTIME_SOURCE/scripts/magi_rename_noreplace.py"
for runtime_script in \
    magi_fanout_codex.sh \
    magi_xfamily.sh \
    magi_plateau_gate.sh \
    magi_protocol.py \
    magi_rename_noreplace.py \
    magi_synthesize.py \
    magi_verify_canonical_templates.py \
    magi_verify_xfamily_artifacts.py
do
    [ -x "$MAGI_RUNTIME_SOURCE/scripts/$runtime_script" ] || {
        echo "error: shared Magi runtime entry point is missing: $runtime_script" >&2
        exit 1
    }
done
python3 "$MAGI_RUNTIME_SOURCE/scripts/magi_protocol.py" sha >/dev/null || {
    echo "error: shared Magi runtime protocol manifest is invalid or stale" >&2
    exit 1
}

mkdir -p "$TARGET"
exec 9>"$KIMI_HOME/.harness-kimi-skills.install.lock" || {
    echo "error: cannot open Kimi skill installer lock" >&2; exit 1; }
flock -x 9 || { echo "error: cannot acquire Kimi skill installer lock" >&2; exit 1; }

marker_matches() {
    local expected_value="$1" marker_path="$2"
    [ -f "$marker_path" ] && printf '%s\n' "$expected_value" | cmp -s - "$marker_path"
}

validate_skill_destination() {
    local skill="$1" src="$2" dst="$3"
    local marker="$dst/.harness-kimi-skill"
    local expected="installed by harness-kimi skill=$skill from=$src"
    local diff_rc
    if [ -L "$dst" ]; then
        echo "error: refusing foreign skill symlink: $dst" >&2
        return 1
    elif [ -d "$dst" ] && ! marker_matches "$expected" "$marker"; then
        # Safely adopt pre-marker installs only when they byte-match this plugin's source.
        diff -qr "$src" "$dst" >/dev/null 2>&1
        diff_rc=$?
        case "$diff_rc" in
            0) ;;
            1) echo "error: refusing foreign skill directory: $dst" >&2; return 1 ;;
            *) echo "error: cannot compare skill directory $dst (diff exit $diff_rc)" >&2; return 1 ;;
        esac
    elif [ -e "$dst" ] && [ ! -d "$dst" ]; then
        echo "error: refusing foreign skill path: $dst" >&2
        return 1
    fi
}

validate_runtime_destination() {
    if [ -L "$RUNTIME" ]; then
        [ "$(readlink -f "$RUNTIME")" = "$MAGI_RUNTIME_SOURCE" ] || {
            echo "error: refusing foreign runtime symlink: $RUNTIME" >&2
            return 1
        }
    elif [ -e "$RUNTIME" ]; then
        echo "error: refusing foreign runtime path: $RUNTIME" >&2
        return 1
    fi
}

# Phase 1: validate the complete install set before mutating any destination. A foreign runtime or
# late skill name must not leave earlier skills partially updated.
for skill in magi bug-hunt dual-magi-review ultramagi; do
    src="$HERE/skills/$skill"
    if [ ! -d "$src" ]; then
        echo "error: source skill dir not found: $src" >&2
        exit 1
    fi
    dst="$TARGET/$skill"
    validate_skill_destination "$skill" "$src" "$dst" || exit 1
done

validate_runtime_destination || exit 1

# Phase 2: every destination is now known to be absent or owned by this installer.
ACTIVE_OLD=""
ACTIVE_DST=""
ACTIVE_STAGE=""
ACTIVE_PUBLISHED=0
ACTIVE_SKILL=""
ACTIVE_SRC=""
TX_SKILLS=()
TX_SRCS=()
TX_DSTS=()
TX_OLDS=()
TX_MESSAGES=()
TX_COMMITTED=0
RUNTIME_CREATED=0

path_exists() {
    [ -e "$1" ] || [ -L "$1" ]
}

remove_owned_skill() {
    local skill="$1" src="$2" dst="$3"
    local quarantine
    path_exists "$dst" || return 0
    quarantine="$TARGET/.${skill}.rollback.$$.$RANDOM"
    path_exists "$quarantine" && {
        echo "error: rollback quarantine exists: $quarantine" >&2
        return 1
    }
    python3 "$RENAME_NOREPLACE" "$dst" "$quarantine" || {
        echo "error: cannot quarantine rollback destination: $dst" >&2
        return 1
    }
    if ! validate_skill_destination "$skill" "$src" "$quarantine"; then
        if ! path_exists "$dst"; then
            python3 "$RENAME_NOREPLACE" "$quarantine" "$dst" || {
                echo "error: foreign rollback entry preserved at $quarantine" >&2
            }
        else
            echo "error: foreign rollback entry preserved at $quarantine" >&2
        fi
        return 1
    fi
    echo "error: preserved rolled-back publication at $quarantine" >&2
}

rollback_one() {
    local skill="$1" src="$2" dst="$3" old="$4"
    local failed=0
    remove_owned_skill "$skill" "$src" "$dst" || failed=1
    if [ -n "$old" ] && path_exists "$old"; then
        if path_exists "$dst"; then
            echo "error: cannot restore $dst; predecessor preserved at $old" >&2
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
        if [ "$RUNTIME_CREATED" -eq 1 ] && path_exists "$RUNTIME"; then
            if validate_runtime_destination; then
                rm -f "$RUNTIME" || cleanup_failed=1
            else
                echo "error: rollback preserved foreign runtime destination: $RUNTIME" >&2
                cleanup_failed=1
            fi
        fi
        if [ "$ACTIVE_PUBLISHED" -eq 1 ]; then
            rollback_one "$ACTIVE_SKILL" "$ACTIVE_SRC" "$ACTIVE_DST" "$ACTIVE_OLD" \
                || cleanup_failed=1
        elif [ -n "$ACTIVE_OLD" ] && path_exists "$ACTIVE_OLD"; then
            if ! path_exists "$ACTIVE_DST"; then
                python3 "$RENAME_NOREPLACE" "$ACTIVE_OLD" "$ACTIVE_DST" \
                    || cleanup_failed=1
            else
                echo "error: interrupted predecessor preserved at $ACTIVE_OLD" >&2
                cleanup_failed=1
            fi
        fi
        for ((index=${#TX_SKILLS[@]} - 1; index >= 0; index--)); do
            rollback_one \
                "${TX_SKILLS[$index]}" "${TX_SRCS[$index]}" \
                "${TX_DSTS[$index]}" "${TX_OLDS[$index]}" || cleanup_failed=1
        done
    fi
    if [ -n "$ACTIVE_STAGE" ] && path_exists "$ACTIVE_STAGE"; then
        rm -rf "$ACTIVE_STAGE" || {
            echo "error: cannot remove interrupted stage: $ACTIVE_STAGE" >&2
            cleanup_failed=1
        }
    fi
    if [ "$cleanup_failed" -ne 0 ]; then
        echo "error: interrupted Kimi skill cleanup was incomplete" >&2
        [ "$original_rc" -ne 0 ] || original_rc=1
    fi
    exit "$original_rc"
}
trap restore_interrupted_replacement EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Close the runtime preflight-to-commit gap before changing any skill destination. The final
# post-publication check below remains necessary to detect a non-cooperating late writer.
validate_runtime_destination || exit 1

for skill in magi bug-hunt dual-magi-review ultramagi; do
    src="$HERE/skills/$skill"
    dst="$TARGET/$skill"
    expected="installed by harness-kimi skill=$skill from=$src"
    stage="$(mktemp -d "$TARGET/.${skill}.stage.XXXXXX")" || exit 1
    ACTIVE_STAGE="$stage"
    ACTIVE_SKILL="$skill"
    ACTIVE_SRC="$src"
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete --exclude .harness-kimi-skill "$src/" "$stage/" || {
            rm -rf "$stage"
            exit 1
        }
    else
        cp -R "$src/." "$stage/" || { rm -rf "$stage"; exit 1; }
    fi
    # Ownership is part of the staged tree. A first installation therefore appears atomically
    # as a complete, owned skill instead of exposing a markerless directory during rsync.
    printf '%s\n' "$expected" > "$stage/.harness-kimi-skill" || {
        rm -rf "$stage"
        exit 1
    }

    # The install-set preflight can go stale while the stage is built. Revalidate the exact
    # destination immediately before any namespace mutation so a late foreign replacement is
    # refused rather than preserved and then recursively deleted as an "owned" predecessor.
    validate_skill_destination "$skill" "$src" "$dst" || {
        rm -rf "$stage"
        exit 1
    }
    old=""
    ACTIVE_OLD=""
    ACTIVE_DST="$dst"
    ACTIVE_PUBLISHED=0
    if path_exists "$dst"; then
        old="$TARGET/.${skill}.old.$$.$RANDOM"
        if path_exists "$old"; then
            echo "error: replacement backup path already exists: $old" >&2
            rm -rf "$stage"
            exit 1
        fi
        ACTIVE_OLD="$old"
        mv "$dst" "$old" || {
            ACTIVE_OLD=""
            ACTIVE_DST=""
            echo "error: cannot preserve previous skill directory: $dst" >&2
            rm -rf "$stage"
            exit 1
        }
        if ! validate_skill_destination "$skill" "$src" "$old"; then
            if ! { [ -e "$dst" ] || [ -L "$dst" ]; }; then
                python3 "$RENAME_NOREPLACE" "$old" "$dst" || {
                    echo "error: cannot restore foreign skill destination: preserved at $old" >&2
                }
            else
                echo "error: skill destination changed again; foreign entry preserved at $old" >&2
            fi
            ACTIVE_OLD=""
            ACTIVE_DST=""
            rm -rf "$stage"
            exit 1
        fi
        if ! python3 "$RENAME_NOREPLACE" "$stage" "$dst"; then
            echo "error: cannot publish staged skill directory: $dst" >&2
            python3 "$RENAME_NOREPLACE" "$old" "$dst" || {
                echo "error: cannot restore previous skill directory: $dst" >&2
            }
            rm -rf "$stage"
            exit 1
        fi
        ACTIVE_STAGE=""
        ACTIVE_PUBLISHED=1
    else
        python3 "$RENAME_NOREPLACE" "$stage" "$dst" || {
            rm -rf "$stage"
            exit 1
        }
        ACTIVE_STAGE=""
        ACTIVE_PUBLISHED=1
    fi
    TX_SKILLS+=("$skill")
    TX_SRCS+=("$src")
    TX_DSTS+=("$dst")
    TX_OLDS+=("$old")
    ACTIVE_OLD=""
    ACTIVE_DST=""
    ACTIVE_PUBLISHED=0
    ACTIVE_SKILL=""
    ACTIVE_SRC=""
    TX_MESSAGES+=("[harness-kimi] installed $dst")
done

validate_runtime_destination || exit 1
if [ ! -L "$RUNTIME" ]; then
    # ln(2) refuses an existing name, so a late foreign creator is never overwritten.
    ln -s "$MAGI_RUNTIME_SOURCE" "$RUNTIME" || exit 1
    RUNTIME_CREATED=1
fi
validate_runtime_destination || exit 1
TX_COMMITTED=1
for old in "${TX_OLDS[@]}"; do
    [ -n "$old" ] || continue
    rm -rf "$old" || {
        echo "error: installed set but cannot remove preserved predecessor: $old" >&2
        exit 1
    }
done
for message in "${TX_MESSAGES[@]}"; do
    echo "$message"
done
echo "[harness-kimi] runtime $RUNTIME -> $MAGI_RUNTIME_SOURCE"

echo "[harness-kimi] skills ready. Restart Kimi sessions to discover them."
