#!/usr/bin/env bash
# pipeline_preflight_gate — PreToolUse hook
#
# Blocks dangerous integration patterns until the operator has run a
# small-batch smoke test, sampled the data, and verified bandwidth at
# each layer. Forces the small-batch-smoke-before-bulk discipline that
# memory-only rules keep failing to enforce under cognitive load.
#
# Trigger patterns (all bash hot paths that compounded into 12-bug
# cascades during the 2026-04-30 vast.ai R2 sync incident):
#   1. Cloud instance creation (vastai/hcloud/aws-ec2/gcloud)
#   2. Multi-component data pipelines (curl|zcat|python|psql or
#      cross-host pg_dump|psql) — N≥3 chained processes through
#      heterogeneous formats
#   3. Bulk parallel shell loops (for ... do ... & ... done with N≥4)
#   4. New jsonl/binary format consumption without prior sampling
#
# Bypass: complete the pre-flight checklist + create ack file at
# ~/.local/state/pipeline-preflight/<trigger>.ack (30 min validity).
#
# This hook is intentionally STRICT. The whole point is that memory rules
# get ignored under pressure; only a hard gate forces the discipline.

set -e

# Read PreToolUse JSON envelope from stdin.
INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // ""' 2>/dev/null || true)
[ "$TOOL_NAME" != "Bash" ] && exit 0

CMD=$(echo "$INPUT" | jq -r '.tool_input.command // ""' 2>/dev/null || true)
[ -z "$CMD" ] && exit 0

# Whitelist commands whose -m/--body args often quote patterns described
# in prose (commit messages, gh issue/PR bodies, gh release notes etc).
# Hooks should not trip on documentation describing the patterns.
if echo "$CMD" | grep -qE '^[[:space:]]*(git[[:space:]]|gh[[:space:]]|cd[[:space:]][^|;&]*[[:space:]]*(&&|;)[[:space:]]*(git|gh)[[:space:]])'; then
  exit 0
fi

# --- detect trigger patterns ---
trigger=""
why=""

# (1) Cloud instance creation
if echo "$CMD" | grep -qE '^[[:space:]]*(vastai create instance|hcloud server create|aws ec2 run-instances|gcloud compute instances create)'; then
  trigger="cloud-instance-create"
  why="cloud rental locks in a per-hour billing clock; 'just rent and try' compounds into hours of debug + cost. Smoke 1 small instance with target workload first."
fi

# (2) Multi-component pipeline involving HTTP fetch + decompress + parser + DB COPY
# The exact pattern that broke 5 times during R2 sync.
if echo "$CMD" | grep -qE 'curl[[:space:]].*https?://' && \
   echo "$CMD" | grep -qE '\|[[:space:]]*(zcat|gunzip|unzstd)' && \
   echo "$CMD" | grep -qE '\|[[:space:]]*(python|python3|jq|awk)' && \
   echo "$CMD" | grep -qE '\|[[:space:]]*(psql|sudo[[:space:]]*-u[[:space:]]*postgres[[:space:]]*psql)'; then
  trigger="multi-component-pipe"
  why="pipe of curl|decompress|parser|DB COPY has 4+ failure modes (partial transfer, EOF, parser-format mismatch, COPY back-pressure). Smoke 1 unit (1 file, 1 row sample) end-to-end first."
fi

# (3) Cross-host PG dump pipe
if echo "$CMD" | grep -qE 'ssh[[:space:]]+[^|]*\bpg_dump\b' || \
   echo "$CMD" | grep -qE 'pg_dump[^|]*\|[[:space:]]*ssh' || \
   echo "$CMD" | grep -qE '\bCOPY[[:space:]]+.*\bFROM[[:space:]]+STDIN[[:space:]]+WITH[[:space:]]+\(FORMAT[[:space:]]+BINARY\)'; then
  trigger="cross-host-pg-stream"
  why="cross-host PG stream subject to network bandwidth ceiling + TCP single-stream limit. Measure mars→target raw bandwidth before designing parallelism."
fi

# (4) Bulk parallel loop kicking N≥4 backgrounded jobs
# Match either:
#  - for X in 0 1 2 3 ... do ... &
#  - for X in $(seq ...) do ... &
if echo "$CMD" | grep -qE 'for[[:space:]]+[a-zA-Z_]+[[:space:]]+in[[:space:]]+(0[[:space:]]+1[[:space:]]+2[[:space:]]+3|.*\$\(seq)' && \
   echo "$CMD" | grep -qE 'do.*&'; then
  trigger="bulk-parallel-loop"
  why="for-loop with N≥4 & jobs amplifies any single-unit bug N times. Run loop with N=1 first to verify the single-unit happy path."
fi

[ -z "$trigger" ] && exit 0

# --- check ack file ---
ACK_DIR="$HOME/.local/state/pipeline-preflight"
ACK_FILE="$ACK_DIR/${trigger}.ack"
ACK_VALID_SECS=1800  # 30 min

if [ -f "$ACK_FILE" ]; then
  age=$(( $(date +%s) - $(stat -c %Y "$ACK_FILE" 2>/dev/null || echo 0) ))
  if [ "$age" -lt "$ACK_VALID_SECS" ]; then
    # ack still valid; let it through
    exit 0
  fi
fi

# --- BLOCK with instructions ---
cat >&2 <<EOF
🚧 pipeline-preflight-gate: $trigger

Detected pattern: $trigger
Why this matters: $why

Required pre-flight (small-batch smoke before bulk):
  1. Sample 1 unit of source data:
     - For new jsonl/binary format: head -1 | parse | print keys + types
     - For new instance: spec, bandwidth from public CDN, disk IO
  2. Measure bandwidth at each layer end-to-end with 1 unit
  3. Verify pipeline happy path 1× before parallelism
  4. List 1-3 implicit assumptions; verify each

After pre-flight, create ack file:
  mkdir -p $ACK_DIR
  echo "preflight done \$(date -u +%Y-%m-%dT%H:%M:%SZ): \$ASSUMPTIONS_VERIFIED" > $ACK_FILE

Validity: 30 min. Re-create after major changes (new offer, new file format, new host).

Bypass for emergency only: touch $ACK_FILE  (assume responsibility)

References:
  - feedback_small_batch_smoke_before_bulk_migration
  - feedback_magi_preflight_for_major_updates
  - feedback_no_repeat_mistakes
  - 2026-04-30 vast.ai R2 sync incident: 12 bugs, 6h debug, \$2.50+ sunk before discovering chunks_extracted has no paper_id
EOF
exit 2
