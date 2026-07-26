#!/bin/bash
# Nightly vocabulary-density scan (log-only, no alerting) — cron counterpart of
# vocab_commit_warn.sh / vocab_doc_warn.sh. Those two hooks catch new content as
# it's written; this catches density that crept in some other way (manual edits
# outside Claude Code, files pulled from elsewhere, etc.) and just logs current
# counts so a density creep is visible on inspection, without paging anyone.
#
# Scope: all git repos directly under ~/projects (global vocabulary-guard
# scope, matching the hook scope decision). Only *.md files; skips
# .git/.venv/node_modules/__pycache__.

source "$(dirname "$0")/vocab_terms.sh"

LOG_DIR="$HOME/.local/log"
LOG_FILE="$LOG_DIR/vocab_density_scan.log"
mkdir -p "$LOG_DIR"

{
    echo "=== vocab_density_scan $(date +%F_%T) ==="
    for repo in "$HOME"/projects/*/; do
        [ -d "$repo/.git" ] || continue
        repo_name=$(basename "$repo")
        while IFS= read -r -d '' md_file; do
            count=$(vocab_hit_count < "$md_file")
            [ "$count" -gt 0 ] && printf '%-30s %-60s %s\n' "$repo_name" "${md_file#"$repo"}" "$count distinct terms"
        done < <(find "$repo" \( -path '*/.git' -o -path '*/.venv' -o -path '*/node_modules' -o -path '*/__pycache__' \) -prune -o -iname '*.md' -type f -print0 2>/dev/null)
    done
    echo
} >> "$LOG_FILE"
