#!/usr/bin/env bash
# ssh_fanout_canonical_check.sh — PreToolUse Write/Edit hook
#
# Detects ssh fanout / GPU rental transport anti-patterns in new/modified .sh files
# and warns about canonical pattern adoption.
#
# Triggers (= patterns that flag for canonical-grep review):
#   - `while read.*ssh`           = stdin consumption bug class (v3 incident)
#   - `scp.*root@.*vast`           = GPU rental transport (should be R2 per feedback_gpu_rental_r2_only)
#   - `ssh.*vast.ai.*scp`          = vast transport
#   - `touch .setup_done`          = setup bypass anti-pattern (v3 incident)
#   - new orchestrator-shape file  = > 200 lines + `for.*shard|launch.*worker` pattern
#
# Bypass: add `# canonical-pattern-reviewed: <ref>` comment in the script
#
# Layer: L2 structural (= harness-time hook、 AI bypass 不能)
# Replaces / supplements L1 behavioral memory rules:
#   - feedback_gpu_rental_r2_only (= L1 rule、 5/22 私が override で violated)
#   - feedback_ssh_fanout_existing_pattern_grep_mandatory (= L1 rule)
#   - feedback_script_saves_tokens (= L1 R7)
#
# Per feedback_defense_in_depth_not_scattered_shields: L1 alone = single override → silent.
# L2 hook = AI cannot bypass without explicit annotation. 真の多層防御の 2nd layer.

set -uo pipefail

payload=$(head -c 131072 || true)
tool=$(echo "$payload" | jq -r '.tool_name // ""' 2>/dev/null || echo "")
file_path=$(echo "$payload" | jq -r '.tool_input.file_path // ""' 2>/dev/null || echo "")

# Scope: Write or Edit on .sh files
[[ "$tool" == "Write" || "$tool" == "Edit" ]] || exit 0
[[ "$file_path" =~ \.sh$ ]] || exit 0

# Extract content being written
if [[ "$tool" == "Write" ]]; then
  content=$(echo "$payload" | jq -r '.tool_input.content // ""' 2>/dev/null)
else
  content=$(echo "$payload" | jq -r '.tool_input.new_string // ""' 2>/dev/null)
fi
[[ -n "$content" ]] || exit 0

# Bypass: explicit annotation
if echo "$content" | grep -q "canonical-pattern-reviewed"; then
  exit 0
fi

# Detect anti-patterns
flags=()

if echo "$content" | grep -qE 'while read[^<]*ssh|while[^|]*read[^<]*\$\(\s*ssh'; then
  flags+=("while-read+ssh stdin consumption (= v3 bug class)")
fi

if echo "$content" | grep -qE 'scp[^|]*-P[^|]*root@|scp[^|]*root@[^|]*vast'; then
  flags+=("scp to GPU rental (= violates feedback_gpu_rental_r2_only, use R2 transport)")
fi

if echo "$content" | grep -qE 'ssh[^|]*vast\.ai'; then
  if ! echo "$content" | grep -qE 'rclone|r2:'; then
    flags+=("ssh to vast.ai without R2 transport in same script")
  fi
fi

if echo "$content" | grep -qE '^\s*touch \.setup_done|touch\s+/[^|]*\.setup_done'; then
  flags+=("touch .setup_done pre-set (= setup bypass anti-pattern from v3 incident)")
fi

# Novel orchestrator detection (= > 200 lines + worker launch pattern)
line_count=$(echo "$content" | wc -l)
if (( line_count > 200 )) && echo "$content" | grep -qE 'launch.*worker|worker.*nohup|phase_b[0-9]_'; then
  flags+=("novel orchestrator-shape (= $line_count lines + worker-launch); existing canonical: farm_deploy.sh / vastai_kickoff_1node_8gpu.sh / vastai_bge_m3_poc.sh / vastai_respinup.sh")
fi

# No flags → silent exit
[[ ${#flags[@]} -eq 0 ]] && exit 0

# Build warning message
msg="⚠️ ssh fanout / GPU rental anti-pattern(s) detected in $file_path:"
for flag in "${flags[@]}"; do
  msg="$msg\n  - $flag"
done
msg="$msg\n\nBefore proceeding:\n"
msg="$msg  1. grep canonical: \`grep -l 'ssh.*root@' scripts/*.sh | head\`\n"
msg="$msg  2. read 4 primitives in farm_deploy.sh / vastai_kickoff_1node_8gpu.sh / vastai_bge_m3_poc.sh\n"
msg="$msg  3. graft (not reinvent) — delta only\n"
msg="$msg  4. Bypass after review: add \`# canonical-pattern-reviewed: <ref>\` comment\n"
msg="$msg\nRefs: feedback_ssh_fanout_existing_pattern_grep_mandatory, feedback_gpu_rental_r2_only, feedback_defense_in_depth_not_scattered_shields"

# Emit as additionalContext (= warning、 not blocking)
cat <<EOF
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "additionalContext": $(printf '%s' "$msg" | jq -Rs .)
  }
}
EOF
exit 0
