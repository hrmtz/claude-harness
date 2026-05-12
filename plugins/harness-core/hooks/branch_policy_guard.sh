#!/bin/bash
# PreToolUse Bash hook: block git commit / push on main branch.
#
# 背景: 2026-05-11 "main 居座り" failure mode — deploy 後 main checkout のまま
# 続行 commit、 dev に戻り忘れる悪癖が systemic。 local pre-commit hook は
# per-repo install が要るため未配置 repo で素通り、 harness 経路で repo-agnostic
# に止めることで結合 cover (CLAUDE.md § Branch policy layer 1.5)。
#
# Block 条件:
#   - command が `git commit` を含み (= --no-verify 無し)
#   - 該当 repo HEAD が main
#   - または `git push ... main` で main 居座り中 (= main が dev より ahead)
#
# Bypass:
#   - HRMTZ_ACK_MAIN_COMMIT=1 prefix (= hot fix / merge commit 等の意識的 main 直)
#   - git commit --no-verify (= 既存 local hook bypass と同 semantic)
#   - HRMTZ_ACK_MAIN_PUSH=1 prefix (= push 用 bypass)

source "$(dirname "$0")/lib.sh"

INPUT=$(cat)
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)

[ -z "$CMD" ] && exit 0

emit_deny() {
    local msg="$1"
    jq -n --arg msg "$msg" '{
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": $msg
        }
    }'
    exit 0
}

# cwd 抽出 (= `cd <dir> && git ...` 対応、 default は PWD)
extract_cwd() {
    local cmd="$1"
    local cd_extract
    cd_extract=$(echo "$cmd" | grep -oE 'cd[[:space:]]+[^[:space:]&|;]+' | head -1 | sed 's/^cd[[:space:]]*//')
    if [[ -n "$cd_extract" ]]; then
        cd_extract="${cd_extract/#\~/$HOME}"
        echo "$cd_extract"
    else
        echo "$PWD"
    fi
}

# ── git commit on main ──
if echo "$CMD" | grep -qE 'HRMTZ_ACK_MAIN_COMMIT=1'; then
    : # ack bypass
elif echo "$CMD" | grep -qE '(^|[^a-zA-Z_/])git[[:space:]]+(-[^[:space:]]+[[:space:]]+)*commit([[:space:]]|$)'; then
    if ! echo "$CMD" | grep -qE '(--no-verify|[[:space:]]-n([[:space:]]|$))'; then
        target_dir=$(extract_cwd "$CMD")
        current_branch=$(git -C "$target_dir" symbolic-ref --short HEAD 2>/dev/null)
        if [[ "$current_branch" == "main" ]]; then
            hook_log "branch_policy_guard" "blocked commit on main in $target_dir"
            emit_deny "git checkout dev && git commit ... で dev に切替えてから。 hot fix 等の意識的 main 直は HRMTZ_ACK_MAIN_COMMIT=1 prefix で bypass"
        fi
    fi
fi

# ── git push on main (= 居座り 徴候 ahead check) ──
if echo "$CMD" | grep -qE 'HRMTZ_ACK_MAIN_PUSH=1'; then
    : # ack bypass
elif echo "$CMD" | grep -qE 'git[[:space:]]+push.*([[:space:]]|:)main([[:space:]]|$|:)'; then
    target_dir=$(extract_cwd "$CMD")
    current_branch=$(git -C "$target_dir" symbolic-ref --short HEAD 2>/dev/null)
    if [[ "$current_branch" == "main" ]] && git -C "$target_dir" rev-parse --verify dev >/dev/null 2>&1; then
        ahead=$(git -C "$target_dir" rev-list --count dev..main 2>/dev/null || echo "0")
        if [[ "$ahead" -gt 0 ]]; then
            hook_log "branch_policy_guard" "blocked push: main ahead of dev by $ahead in $target_dir"
            emit_deny "main が dev より $ahead commit 先行 (= 居座り中の徴候)。 git checkout dev && git merge --ff-only main で dev 追従させてから push。 意識的 bypass は HRMTZ_ACK_MAIN_PUSH=1"
        fi
    fi
fi

exit 0
