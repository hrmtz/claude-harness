#!/usr/bin/env bash
# check_zsh_reserved_vars.sh — PreToolUse Write/Edit hook
#
# Detects assignments to zsh read-only reserved variables in shell scripts.
# Zsh silently crashes (or exits 1) when these variables are assigned:
#   - status   (= the actual crash culprit; caused 8h idle billing 2026-06-07)
#   - LINENO   (= read-only in zsh)
#   - PPID     (= read-only in zsh; zsh -c 'PPID=$(cmd)' → exit 1)
#   - HISTCMD  (= read-only in zsh)
#
# Safe alternatives: node_status / http_status / api_status / exit_code /
#                    lineno / ppid / hist_cmd etc.
#
# Skip if shebang is #!/bin/bash or #!/usr/bin/env bash (bash files are safe).
# Strip comment lines before pattern match (avoid false-positives from docs).
# Only matches bare assignment start: (^|space|;||&) + varname + =
# Compound names like node_status= or h_status= do NOT match.
#
# Layer: L2 structural (harness-time, AI-bypass-proof)
# Blocks (exit 2): naming a variable 'status=' is always a mistake in zsh context.
# Root incident: vastai watcher status=$(vastai show …) in zsh → silent crash → 8h idle

set -uo pipefail

source "$(dirname "$0")/lib.sh"

HOOK_INPUT=$(head -c 131072 || true)
export HOOK_INPUT
tool=$(parse_tool_name)
file_path=$(parse_tool_file_path)

# Scope: Write/Edit or Codex apply_patch only
[[ "$tool" == "Write" || "$tool" == "Edit" || "$tool" == "apply_patch" ]] || exit 0

# Scope: shell scripts only (.sh, .bash, .zsh, or no extension for unrecognised shebang)
if [[ -n "$file_path" ]]; then
  [[ "$file_path" =~ \.(sh|bash|zsh)$ ]] || exit 0
fi

content=$(parse_tool_content)
[[ -n "$content" ]] || exit 0

if [[ "$tool" == "apply_patch" ]]; then
  file_path=$(printf '%s\n' "$content" |
    awk '/^\*\*\* (Add|Update) File: / { sub(/^\*\*\* (Add|Update) File: /, ""); print; exit }')
  content=$(printf '%s\n' "$content" |
    sed -n 's/^+\([^+].*\)$/\1/p')
  # apply_patch often carries only a hunk, not the file shebang. Keep the
  # blocking zsh rail conservative under Codex to avoid false positives on bash
  # .sh files whose shebang is outside the patch.
  if [[ ! "$file_path" =~ \.zsh$ ]] && ! printf '%s\n' "$content" | head -5 | grep -qE '^#!.*zsh'; then
    exit 0
  fi
fi

# Skip if explicit bash shebang (zsh reserved vars don't apply to bash)
first_line=$(echo "$content" | head -1)
if [[ "$first_line" == "#!/bin/bash"* || "$first_line" == "#!/usr/bin/env bash"* ]]; then
  exit 0
fi

# Strip comment lines before pattern match
# (prevents false-positives from doc comments like "# never use status=")
clean=$(echo "$content" | grep -v '^[[:space:]]*#')

# Detect bare assignment to zsh read-only vars.
# Pattern: (start-of-line | whitespace | ; | | | &) followed by varname=
# This ensures compound names like node_status= or h_status= do NOT match.
RESERVED='(^|[[:space:]]|;|\||&&|&)(status|LINENO|PPID|HISTCMD)='
if echo "$clean" | grep -qE "$RESERVED"; then
  matched=$(echo "$clean" | grep -E "$RESERVED" | head -5)
  echo "⚠️  zsh read-only variable assignment detected in ${file_path:-<content>}"
  echo "   The following variables are read-only in zsh — assigning them causes silent crash:"
  echo "     status, LINENO, PPID, HISTCMD"
  echo ""
  echo "   Matched line(s):"
  echo "$matched" | sed 's/^/     /'
  echo ""
  echo "   Use safe alternatives instead:"
  echo "     node_status=  http_status=  api_status=  exit_code="
  echo "     lineno=       ppid=         hist_cmd="
  echo ""
  echo "   Root incident: status=\$(vastai show …) in zsh watcher → silent crash → 8h idle billing (2026-06-07)"
  exit 2
fi

exit 0
