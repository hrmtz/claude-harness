#!/bin/bash
# Encrypted backup of ~/.claude/projects/*.jsonl (hippocampus/Claude memory jsonl) to rclone.
# Local stays PLAINTEXT (easy owner read); the R2 copy is age-encrypted (no plaintext
# secret in cloud). Incremental via a local mtime manifest — no remote ListObjects (the
# Object-RW R2 token can't list at account level). Restore: see docs (age -d the .age).
#
#   bash scripts/backup_jsonl_to_r2.sh           # incremental sync
#   bash scripts/backup_jsonl_to_r2.sh --full    # ignore manifest, re-encrypt+upload all
set -u

SRC="$HOME/.claude/projects"
DEST="${HARNESS_JSONL_BACKUP_DEST:-}"
REC="${HARNESS_JSONL_BACKUP_RECIPIENT:-}"
STATE="${HARNESS_JSONL_BACKUP_STATE:-$HOME/.local/state/hippocampus_backup}"
MANIFEST="$STATE/manifest.tsv"            # relpath \t mtime \t bytes
LOG="${HARNESS_JSONL_BACKUP_LOG:-$HOME/.local/log/hippocampus_backup.log}"
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"

if [ -z "$DEST" ] || [ -z "$REC" ]; then
  echo "error: set HARNESS_JSONL_BACKUP_DEST (rclone dest) and HARNESS_JSONL_BACKUP_RECIPIENT (age recipient)" >&2
  exit 2
fi
mkdir -p "$STATE" "$(dirname "$LOG")"
[ -f "$MANIFEST" ] || : > "$MANIFEST"

FULL=0; [ "${1:-}" = "--full" ] && FULL=1
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

uploaded=0; skipped=0; failed=0; bytes=0
while IFS= read -r -d '' f; do
  rel="${f#$SRC/}"
  mt=$(stat -c %Y "$f" 2>/dev/null || echo 0)
  sz=$(stat -c %s "$f" 2>/dev/null || echo 0)
  if [ "$FULL" -eq 0 ] && grep -qF "$rel	$mt	$sz" "$MANIFEST" 2>/dev/null; then
    skipped=$((skipped+1)); continue
  fi
  enc="$TMP/enc.age"
  if ! age -r "$REC" -o "$enc" "$f" 2>/dev/null; then failed=$((failed+1)); continue; fi
  if timeout 120 rclone copyto "$enc" "$DEST/$rel.age" 2>>"$LOG"; then
    # update manifest: drop old line for rel, append new
    grep -vF "	$rel	" "$MANIFEST" 2>/dev/null | grep -v "^$rel	" > "$MANIFEST.new" || true
    printf '%s\t%s\t%s\n' "$rel" "$mt" "$sz" >> "$MANIFEST.new"
    mv "$MANIFEST.new" "$MANIFEST"
    uploaded=$((uploaded+1)); bytes=$((bytes+sz))
  else
    failed=$((failed+1))
  fi
done < <(find "$SRC" -name '*.jsonl' -print0 2>/dev/null)

total=$(find "$SRC" -name '*.jsonl' 2>/dev/null | wc -l)
# manifest.json artifact (canonical_artifact_preservation): counts only, no secrets
NOW=$(date +%s)
printf '{"updated_epoch":%s,"total_jsonl":%s,"uploaded_this_run":%s,"skipped":%s,"failed":%s,"recipient":"%s"}\n' \
  "$NOW" "$total" "$uploaded" "$skipped" "$failed" "$REC" > "$TMP/_manifest.json"
timeout 30 rclone copyto "$TMP/_manifest.json" "$DEST/_manifest.json" 2>>"$LOG" || true

echo "$(date -Iseconds) backup: total=$total uploaded=$uploaded skipped=$skipped failed=$failed bytes=$bytes" | tee -a "$LOG"
[ "$failed" -eq 0 ]
