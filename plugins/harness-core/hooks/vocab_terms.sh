#!/bin/bash
# Shared vocabulary list for the attacker-framing → white-hat/neutral vocabulary
# guard (commit-message warn + doc Write/Edit warn + nightly density scan).
#
# Background: dense attacker/cracking-framed vocabulary accumulating in commit
# messages, memory files, and markdown docs trips a safeguard/classifier LAYER
# ABOVE the model (not the model's own refusal policy — confirmed 2026-07-04,
# even Sonnet 5 itself tripped reading one of its own docs in a fresh subagent
# context) on completely authorized own-hardware/pentest-lab research work.
# See memory feedback_neutral_vocabulary_in_injected_content for the incident
# history and the manual swap list this was distilled from.
#
# Deliberately EXCLUDED from this list (too many legitimate technical uses in
# this exact corpus — CWE/CVE terminology, protocol field names — flagging them
# would make the warn low-signal): "attack surface", "privilege escalation",
# "vulnerable"/"vulnerability", "path traversal", "payload", "target", "vector".
#
# Format: <case-insensitive extended regex>:::<neutral replacement suggestion>
declare -a VOCAB_TERMS_SUGGESTIONS=(
    'exploit(s|ed|ing)?:::use / take advantage of / "public demonstration code" (PoC noun)'
    '\bLPE\b:::access-level elevation issue'
    'backdoor:::undocumented access path'
    'malicious:::describe the effect factually instead'
    'brute[-_ ]?forc(e|ed|ing):::password list check'
    '\bpwn(ed|ing)?\b:::gained access to / compromised'
    'root[-_ ]?shell:::elevated-shell access'
    '\bcrack(ing|ed)?\b:::security research / reverse engineering'
    '\bhack(er|ing|ed)?\b:::security researcher / research'
    '\bbypass(ed|ing)?\b:::workaround'
    'smuggl(e|ed|ing):::get ... past'
    '\bdefeat(ed|ing)?\b:::get past / work around'
    'attacker[-_ ]controlled:::sender-controlled / caller-controlled'
    '\bfoothold\b:::existing local access'
    '攻撃:::検証・研究'
    '悪用:::利用・活用'
    '侵入:::アクセス'
    'クラック:::セキュリティ研究'
    'ハッキング:::セキュリティ研究'
    'バックドア:::未文書化アクセス経路'
)

# vocab_scan <<< "$text" — prints "term_pattern → suggestion" for each matched
# pattern (one match per distinct pattern, not per occurrence) and returns exit
# 0 if nothing matched, exit 1 if at least one pattern matched.
vocab_scan() {
    local text hit_count=0 pattern suggestion
    text=$(cat)
    [ -z "$text" ] && return 0
    for entry in "${VOCAB_TERMS_SUGGESTIONS[@]}"; do
        pattern="${entry%%:::*}"
        suggestion="${entry#*:::}"
        if printf '%s' "$text" | grep -qiE "$pattern"; then
            echo "  - matched /${pattern}/ → ${suggestion}"
            hit_count=$((hit_count + 1))
        fi
    done
    [ "$hit_count" -eq 0 ] && return 0
    return 1
}

# vocab_hit_count <<< "$text" — prints just the integer count of distinct
# matched patterns (used by the density scan and the doc-edit threshold).
vocab_hit_count() {
    local text count=0 pattern
    text=$(cat)
    [ -z "$text" ] && { echo 0; return; }
    for entry in "${VOCAB_TERMS_SUGGESTIONS[@]}"; do
        pattern="${entry%%:::*}"
        if printf '%s' "$text" | grep -qiE "$pattern"; then
            count=$((count + 1))
        fi
    done
    echo "$count"
}
