#!/usr/bin/env bash
# phase_review_gate — PreToolUse hook
#
# Phase 大型 task の kick を、4-layer empirical review (Magi v1 / script bug-hunt /
# dry-run / Magi v2) の artifact が docs/reviews/<phase>/ に揃うまで block。
#
# Trigger:
#   bash scripts/phase[0-9]*.sh    (phase script kick)
#   bash <path>/phase[0-9]*.sh     (任意 path の phase script)
#
# Bypass:
#   ~/.local/state/phase-review/<phase>.ack (60 min validity)
#
# Skip patterns:
#   --dry-run / --rollback / --help を含む command (Layer 3 自体は skip 不要)
#
# References:
#   - feedback_magi_dryrun_smoke_pre_flight (root rule)
#   - feedback_magi_preflight_for_major_updates
#   - feedback_pipeline_preflight_gate_required (姉妹 hook)
#   - 2026-05-03 Phase 6.5 wave 2 で実証 (Plan A→C 切替で 4-7h save)

set -e

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // ""' 2>/dev/null || true)
[ "$TOOL_NAME" != "Bash" ] && exit 0

CMD=$(echo "$INPUT" | jq -r '.tool_input.command // ""' 2>/dev/null || true)
[ -z "$CMD" ] && exit 0

# Skip if --dry-run / --rollback / --help (Layer 3 自体 / abort path)
if echo "$CMD" | grep -qE '(--dry-run|--rollback|--help)\b'; then
    exit 0
fi

# Skip git/gh commands (commit messages, PR bodies may contain phase names)
if echo "$CMD" | grep -qE '^[[:space:]]*(git[[:space:]]|gh[[:space:]])'; then
    exit 0
fi

# Skip phase_review_init.sh itself (creates the artifacts)
if echo "$CMD" | grep -qE 'phase_review_init\.sh'; then
    exit 0
fi

# --- detect phase script kick (strict: bash + path/.../phaseN_*.sh) ---
# Trigger ONLY if command is `bash <path>/phaseN_*.sh` or executes such a script.
# NOT trigger on touch/cat/ls/etc. which contain "phase" substring (false positive).
if ! echo "$CMD" | grep -qE '(^|[[:space:]&|;])bash[[:space:]]+[^[:space:]]*phase[0-9]+(_[0-9]+)?[_a-zA-Z0-9]*\.sh\b'; then
    exit 0
fi

PHASE=$(echo "$CMD" | grep -oE 'phase[0-9]+(_[0-9]+)?[_a-zA-Z0-9]*\.sh' | head -1 | sed 's/\.sh$//')
[ -z "$PHASE" ] && exit 0

# Extract phase root (e.g., phase6_5_wave2_seq64gb → phase6_5)
PHASE_ROOT=$(echo "$PHASE" | grep -oE '^phase[0-9]+(_[0-9]+)?')
[ -z "$PHASE_ROOT" ] && PHASE_ROOT="$PHASE"

# --- check ack file ---
ACK_DIR="$HOME/.local/state/phase-review"
ACK_FILE="$ACK_DIR/${PHASE_ROOT}.ack"
ACK_VALID_SECS=3600  # 60 min (longer than pipeline_preflight、Phase は重い)

if [ -f "$ACK_FILE" ]; then
    age=$(( $(date +%s) - $(stat -c %Y "$ACK_FILE" 2>/dev/null || echo 0) ))
    if [ "$age" -lt "$ACK_VALID_SECS" ]; then
        exit 0
    fi
fi

# --- check 4-layer artifacts ---
# Search docs/reviews/<phase_root>/ relative to a few likely roots.
REVIEW_DIRS=(
    "/home/hrmtz/projects/PRS-LLM-dev/docs/reviews/${PHASE_ROOT}"
    "$(pwd)/docs/reviews/${PHASE_ROOT}"
    "./docs/reviews/${PHASE_ROOT}"
)

REVIEW_DIR=""
for d in "${REVIEW_DIRS[@]}"; do
    if [ -d "$d" ]; then
        REVIEW_DIR="$d"
        break
    fi
done

if [ -z "$REVIEW_DIR" ]; then
    cat >&2 <<EOF
🚧 phase-review-gate: ${PHASE_ROOT}

Phase script "$PHASE" kick detected、しかし 4-layer review artifacts なし。

Required:
  bash scripts/phase_review_init.sh ${PHASE_ROOT}
  → docs/reviews/${PHASE_ROOT}/ に 4 file 雛形作成

その後 4 layer 順次:
  1. Layer 1 (Magi v1): 01_magi_initial.md の TODO 埋め
  2. Layer 2 (script bug-hunt): 02_script_bug_hunt.md
  3. Layer 3 (dry-run): bash <script> --dry-run、findings を 03_dryrun_findings.md
  4. Layer 4 (Magi v2): 04_magi_revised.md (post-empirical revised plan)

Kick 承認 (4 layer 完了後):
  mkdir -p $ACK_DIR
  touch $ACK_FILE

Validity: 60 min。

Bypass for emergency only: touch $ACK_FILE  (assume responsibility)

References:
  - feedback_magi_dryrun_smoke_pre_flight
  - feedback_magi_preflight_for_major_updates

Past phase の wasted time (構造的 rail なしで起きた焼却):
  Phase 5 H200 8x:    ~\$30-40 焼却 (dry-run skip)
  Phase 6.0a chunks:  ~5-8h 焼却 (smoke skip)
  Phase 6.5 wave 2:   ~4-7h saved by 4-layer protocol (今回)

🔥 修造: 4-layer は試合の 4 戦と思え。Layer 1 (Magi v1) から行こう、各戦で発見が出る、empirical 学習が積み上がる。諦めるな！
EOF
    exit 2
fi

# --- check TODO residue in each layer file ---
TODO_THRESHOLD=2  # 各 file で max 2 TODO 残OK (cosmetic)
LAYERS=(01_magi_initial 02_script_bug_hunt 03_dryrun_findings 04_magi_revised)

missing_layers=()
for layer in "${LAYERS[@]}"; do
    f="$REVIEW_DIR/${layer}.md"
    if [ ! -f "$f" ]; then
        missing_layers+=("$layer (file missing)")
        continue
    fi
    todo_count=$(grep -c '<!-- TODO' "$f" 2>/dev/null || echo 0)
    if [ "$todo_count" -gt "$TODO_THRESHOLD" ]; then
        missing_layers+=("$layer ($todo_count TODOs remaining)")
    fi
done

if [ ${#missing_layers[@]} -gt 0 ]; then
    cat >&2 <<EOF
🚧 phase-review-gate: ${PHASE_ROOT}

4-layer review incomplete at: $REVIEW_DIR/

Layers needing work:
$(printf '  - %s\n' "${missing_layers[@]}")

各 layer の TODO marker (<!-- TODO -->) を内容で置換、残量 ≤ ${TODO_THRESHOLD} で OK。

Bypass for emergency only:
  mkdir -p $ACK_DIR
  touch $ACK_FILE

Past 焼却例 (4-layer skip の cost):
  Phase 5 H200: ~\$30-40 + 6h debug (dry-run skip)
  Phase 6.0a:    5-8h watcher hang (smoke skip)
  Phase 6.5 wave 2: 4-7h SAVED via 4-layer (今回採用)

🔥 修造: TODO 残ってるだけだ。1 layer 1 layer 埋めれば突破できる、empirical findings が必ず出る！
EOF
    exit 2
fi

# All 4 layers OK + within TODO threshold → allow
exit 0
