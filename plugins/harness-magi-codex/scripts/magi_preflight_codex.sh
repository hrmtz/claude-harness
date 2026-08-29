#!/usr/bin/env bash
# Structural one-shot Magi pre-flight: exactly three isolated Codex reviewers, then synthesis.
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SELF_DIR/.." && pwd)"
SCHEMA="$PLUGIN_DIR/schemas/preflight-review.schema.json"
SCHEMA_PREFLIGHT="$SELF_DIR/magi_codex_schema_preflight.py"
SCRUB="$SELF_DIR/magi_scrub.py"
EVALUATOR="$SELF_DIR/magi_preflight.py"
# shellcheck source=magi_target_root.sh
source "$SELF_DIR/magi_target_root.sh"

usage() {
    echo "usage: $0 <absolute-brief-path> <output-directory>" >&2
    exit 64
}
[ "$#" -eq 2 ] || usage

BRIEF="$1"
OUT_DIR="$2"
case "$BRIEF" in /*) ;; *) echo "preflight: brief path must be absolute" >&2; exit 64 ;; esac
[ ! -L "$OUT_DIR" ] || { echo "preflight: output directory must not be a symlink" >&2; exit 64; }
# Reject provider-incompatible output schemas before output-directory mutation or model spend.
python3 "$SCHEMA_PREFLIGHT" "$SCHEMA" || exit $?
mkdir -p "$OUT_DIR"
OUT_DIR="$(realpath "$OUT_DIR")"
# Reviewers must be able to verify the brief's claims against the repository that owns it, not
# just against the brief's own directory (gh #151). Same derivation as the fan-out and
# cross-family arms; the document directory remains the documented non-git fallback.
TARGET_ROOT="$(magi_target_root "$BRIEF" preflight)" || exit 64
# The lookup deliberately ignores ambient repository overrides; keep them out of the reviewer
# processes as well.
unset GIT_DIR GIT_WORK_TREE

command -v codex >/dev/null 2>&1 || {
    echo "preflight: codex CLI not found" >&2
    exit 1
}
command -v timeout >/dev/null 2>&1 || {
    echo "preflight: timeout utility not found" >&2
    exit 1
}
command -v flock >/dev/null 2>&1 || {
    echo "preflight: flock utility not found" >&2
    exit 1
}
command -v bwrap >/dev/null 2>&1 || {
    echo "preflight: bubblewrap is required for reviewer isolation" >&2
    exit 1
}
TIMEOUT_S="${MAGI_PREFLIGHT_TIMEOUT_S:-900}"
case "$TIMEOUT_S" in
    ''|*[!0-9]*) echo "preflight: MAGI_PREFLIGHT_TIMEOUT_S must be an integer" >&2; exit 64 ;;
esac
[ "$TIMEOUT_S" -ge 1 ] && [ "$TIMEOUT_S" -le 900 ] || {
    echo "preflight: MAGI_PREFLIGHT_TIMEOUT_S must be in 1..900" >&2
    exit 64
}
CODEX_MODEL="${MAGI_PREFLIGHT_MODEL:-gpt-5.6-luna}"
[ -n "$CODEX_MODEL" ] || {
    echo "preflight: MAGI_PREFLIGHT_MODEL must not be empty" >&2
    exit 64
}
CODEX_REASONING_EFFORT="${MAGI_PREFLIGHT_REASONING_EFFORT:-low}"
case "$CODEX_REASONING_EFFORT" in
    low|medium|high|xhigh|max|ultra) ;;
    *)
        echo "preflight: MAGI_PREFLIGHT_REASONING_EFFORT must be low, medium, high, xhigh, max, or ultra" >&2
        exit 64
        ;;
esac
CODEX_STATE="${CODEX_HOME:-${HOME:?HOME must be set}/.codex}"
[ -d "$CODEX_STATE" ] || {
    echo "preflight: Codex state directory is unavailable: $CODEX_STATE" >&2
    exit 1
}
CODEX_STATE="$(realpath "$CODEX_STATE")"

LOCK_PATH="$OUT_DIR/.preflight.lock"
exec 9>"$LOCK_PATH"
flock -n 9 || { echo "preflight: another run owns $OUT_DIR" >&2; exit 3; }
STAGE="$(mktemp -d "$OUT_DIR/.preflight-stage.XXXXXX")"
SNAPSHOT="$STAGE/brief.snapshot"

mapfile -t BRIEF_FIELDS < <(
    PYTHONDONTWRITEBYTECODE=1 python3 - "$SELF_DIR" "$BRIEF" "$SNAPSHOT" <<'PY'
import json
import os
import pathlib
import sys

sys.path.insert(0, sys.argv[1])
import magi_preflight as preflight

brief = preflight.stable_read(pathlib.Path(sys.argv[2]), limit=preflight.MAX_BRIEF_BYTES)
brief.raw.decode("utf-8")
lines = brief.raw.splitlines(keepends=True)
if not lines:
    raise preflight.UnsafeInput("brief must contain at least one line")
if len(lines) > 200:
    raise preflight.UnsafeInput("brief exceeds the one-shot limit of 200 lines")
identity = preflight.brief_identity(brief)
snapshot = pathlib.Path(sys.argv[3])
with snapshot.open("wb") as handle:
    handle.write(brief.raw)
    handle.flush()
    os.fsync(handle.fileno())
print(identity["canonical_path"])
print(identity["artifact_id"])
print(identity["sha256"])
print(json.dumps(identity, sort_keys=True, separators=(",", ":")))
PY
) || { rm -rf -- "$STAGE"; echo "preflight: unsafe or invalid brief" >&2; exit 2; }
[ "${#BRIEF_FIELDS[@]}" -eq 4 ] || { echo "preflight: brief identity failed" >&2; exit 2; }
BRIEF="${BRIEF_FIELDS[0]}"
BRIEF_ID="${BRIEF_FIELDS[1]}"
BRIEF_SHA="${BRIEF_FIELDS[2]}"
BRIEF_JSON="${BRIEF_FIELDS[3]}"

PERSONAS=(MELCHIOR BALTHASAR CASPAR)
FINAL_MANIFEST="$OUT_DIR/preflight-run.json"
FAILURE_DIAGNOSTIC="$OUT_DIR/preflight-failure.json"
FINAL_OUTPUTS=(
    "$OUT_DIR/preflight-melchior.json"
    "$OUT_DIR/preflight-balthasar.json"
    "$OUT_DIR/preflight-caspar.json"
)
for path in "$FINAL_MANIFEST" "$FAILURE_DIAGNOSTIC" "${FINAL_OUTPUTS[@]}"; do
    [ ! -e "$path" ] || {
        echo "preflight: one-shot output already exists: $path" >&2
        exit 5
    }
done

PIDS=()
SCRUB_PIDS=()
FIFO_FDS=()
PROMPTS=()
SAFE_OUTPUTS=()
RUNTIMES=()
COMMITTED=0
cleanup() {
    local pid fd path
    for pid in "${PIDS[@]:-}" "${SCRUB_PIDS[@]:-}"; do
        [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null || true
    done
    for pid in "${PIDS[@]:-}" "${SCRUB_PIDS[@]:-}"; do
        [ -n "$pid" ] && wait "$pid" 2>/dev/null || true
    done
    for fd in "${FIFO_FDS[@]:-}"; do
        [ -n "$fd" ] && eval "exec ${fd}>&-" 2>/dev/null || true
    done
    if [ "$COMMITTED" -eq 0 ]; then
        for path in "${FINAL_OUTPUTS[@]}"; do rm -f -- "$path"; done
    fi
    rm -rf -- "$STAGE"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Construct every prompt and FIFO before any provider starts.
for index in 0 1 2; do
    persona="${PERSONAS[$index]}"
    prompt="$STAGE/${persona,,}.prompt"
    fifo="$STAGE/${persona,,}.raw.fifo"
    safe="$STAGE/${persona,,}.json"
    runtime="$STAGE/${persona,,}.runtime"
    PROMPTS+=("$prompt")
    SAFE_OUTPUTS+=("$safe")
    RUNTIMES+=("$runtime")
    mkdir -p "$runtime/tmp"
    mkfifo "$fifo"
    PYTHONDONTWRITEBYTECODE=1 python3 - \
        "$SELF_DIR" "$SNAPSHOT" "$persona" "$prompt" "$BRIEF_JSON" <<'PY'
import json
import pathlib
import sys
sys.path.insert(0, sys.argv[1])
import magi_preflight as preflight
brief = preflight.stable_read(pathlib.Path(sys.argv[2]), limit=preflight.MAX_BRIEF_BYTES)
pathlib.Path(sys.argv[4]).write_bytes(
    preflight.review_prompt(brief, sys.argv[3], identity=json.loads(sys.argv[5]))
)
PY
done

# Keep each FIFO open in the parent. Providers can start without a reader and cannot deadlock
# before all three isolated processes exist.
for index in 0 1 2; do
    fifo="$STAGE/${PERSONAS[$index],,}.raw.fifo"
    exec {fd}<>"$fifo"
    FIFO_FDS+=("$fd")
done

# Structural independence boundary: start exactly three provider processes before any output is
# collected, parsed, or validated. Each process gets a private mount/PID namespace in which the
# shared staging directory is hidden and only its own immutable prompt and FIFO are rebound.
for index in 0 1 2; do
    prompt="${PROMPTS[$index]}"
    fifo="$STAGE/${PERSONAS[$index],,}.raw.fifo"
    runtime="${RUNTIMES[$index]}"
    (
        exec 9>&-
        for inherited_fd in "${FIFO_FDS[@]}"; do eval "exec ${inherited_fd}>&-"; done
        exec bwrap --die-with-parent --unshare-pid --ro-bind / / \
            --proc /proc --dev-bind /dev /dev --bind "$CODEX_STATE" "$CODEX_STATE" \
            --tmpfs "$STAGE" \
            --bind "$runtime" "$runtime" \
            --ro-bind "$prompt" "$prompt" --bind "$fifo" "$fifo" \
            -- timeout --signal=TERM --kill-after=2s "$TIMEOUT_S" \
            env -u TMUX_PANE TMPDIR="$runtime/tmp" \
            codex exec --ignore-user-config --ignore-rules \
            --skip-git-repo-check -s read-only --ephemeral \
            -m "$CODEX_MODEL" \
            -c "model_reasoning_effort=\"$CODEX_REASONING_EFFORT\"" \
            -C "$TARGET_ROOT" --output-schema "$SCHEMA" -o "$fifo" - < "$prompt" \
            >/dev/null 2>&1
    ) &
    PIDS+=("$!")
done

# Only after all three providers have started do scrubbers collect their isolated outputs.
for index in 0 1 2; do
    fifo="$STAGE/${PERSONAS[$index],,}.raw.fifo"
    safe="${SAFE_OUTPUTS[$index]}"
    (
        exec 9>&-
        for inherited_fd in "${FIFO_FDS[@]}"; do eval "exec ${inherited_fd}>&-"; done
        exec env PYTHONDONTWRITEBYTECODE=1 python3 "$SCRUB" < "$fifo" > "$safe"
    ) &
    SCRUB_PIDS+=("$!")
done

rc=0
PROVIDER_RCS=()
for pid in "${PIDS[@]}"; do
    provider_rc=0
    wait "$pid" || provider_rc=$?
    PROVIDER_RCS+=("$provider_rc")
    [ "$provider_rc" -eq 0 ] || rc=1
done
for fd in "${FIFO_FDS[@]}"; do eval "exec ${fd}>&-"; done
FIFO_FDS=()
SCRUB_RCS=()
for pid in "${SCRUB_PIDS[@]}"; do
    scrub_rc=0
    wait "$pid" || scrub_rc=$?
    SCRUB_RCS+=("$scrub_rc")
    [ "$scrub_rc" -eq 0 ] || rc=1
done
PIDS=()
SCRUB_PIDS=()
if [ "$rc" -ne 0 ]; then
    FAILURE_STAGE="$STAGE/preflight-failure.json"
    PYTHONDONTWRITEBYTECODE=1 python3 - \
        "$SELF_DIR" "$FAILURE_STAGE" "${PROVIDER_RCS[@]}" "${SCRUB_RCS[@]}" <<'PY'
import json
import pathlib
import sys

sys.path.insert(0, sys.argv[1])
import magi_preflight as preflight

destination = pathlib.Path(sys.argv[2])
provider_codes = [int(value) for value in sys.argv[3:6]]
scrubber_codes = [int(value) for value in sys.argv[6:9]]
personas = ("MELCHIOR", "BALTHASAR", "CASPAR")
reviewers = []
for persona, provider_code, scrubber_code in zip(
    personas, provider_codes, scrubber_codes
):
    reviewers.append(
        {
            "reviewer": persona,
            "classification": preflight.classify_reviewer_process(
                provider_code, scrubber_code
            ),
            "provider_exit_code": provider_code,
            "scrubber_exit_code": scrubber_code,
        }
    )
destination.write_text(
    json.dumps(
        {
            "schema": "magi-preflight-failure/v1",
            "status": "failed",
            "reviewers": reviewers,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    + "\n",
    encoding="utf-8",
)
PY
    mv -- "$FAILURE_STAGE" "$FAILURE_DIAGNOSTIC"
    echo "preflight: reviewer process failed; diagnostic: $FAILURE_DIAGNOSTIC" >&2
    exit 1
fi

# Validate staged bytes before any canonical output name appears.
VALIDATION_FAILURE_STAGE="$STAGE/preflight-validation-failure.json"
validation_rc=0
PYTHONDONTWRITEBYTECODE=1 python3 - \
    "$SELF_DIR" "$BRIEF" "$VALIDATION_FAILURE_STAGE" "${SAFE_OUTPUTS[@]}" <<'PY' \
    || validation_rc=$?
import json
import pathlib
import sys

sys.path.insert(0, sys.argv[1])
import magi_preflight as preflight

brief = preflight.stable_read(pathlib.Path(sys.argv[2]), limit=preflight.MAX_BRIEF_BYTES)
lines = brief.raw.splitlines(keepends=True)
schema = preflight.load_schema("preflight-review.schema.json")
diagnostic_path = pathlib.Path(sys.argv[3])
personas = ("MELCHIOR", "BALTHASAR", "CASPAR")
reviewers = []
failed = False
for expected, raw in zip(personas, sys.argv[4:]):
    classification = "ok"
    try:
        source = preflight.stable_read(
            pathlib.Path(raw), limit=preflight.MAX_REVIEW_BYTES
        )
    except preflight.UnsafeInput:
        classification = "invalid-reviewer-output"
    else:
        try:
            payload = preflight.parse_object(source)
        except preflight.UnsafeInput:
            classification = "invalid-reviewer-json"
        else:
            if preflight.bind_provider_evidence_digests(payload, lines):
                pathlib.Path(raw).write_text(
                    json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                source = preflight.stable_read(
                    pathlib.Path(raw), limit=preflight.MAX_REVIEW_BYTES
                )
            try:
                preflight.validate_review(
                    payload,
                    source=source,
                    brief=brief,
                    brief_lines=lines,
                    review_schema=schema,
                )
            except preflight.UnsafeInput:
                classification = "invalid-reviewer-content"
            else:
                if payload["reviewer"] != expected:
                    classification = "reviewer-mismatch"
    failed = failed or classification != "ok"
    reviewers.append(
        {
            "reviewer": expected,
            "classification": classification,
            "provider_exit_code": 0,
            "scrubber_exit_code": 0,
        }
    )
if failed:
    diagnostic_path.write_text(
        json.dumps(
            {
                "schema": "magi-preflight-failure/v1",
                "status": "failed",
                "reviewers": reviewers,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    raise SystemExit(1)
PY
if [ "$validation_rc" -ne 0 ]; then
    if [ -f "$VALIDATION_FAILURE_STAGE" ]; then
        mv -- "$VALIDATION_FAILURE_STAGE" "$FAILURE_DIAGNOSTIC"
        echo "preflight: reviewer validation failed; diagnostic: $FAILURE_DIAGNOSTIC" >&2
    else
        echo "preflight: reviewer validation failed before safe classification" >&2
    fi
    exit 1
fi

RUN_ID="$(python3 - <<'PY'
import secrets
print(secrets.token_hex(16))
PY
)"
STAGED_MANIFEST="$STAGE/preflight-run.json"
PYTHONDONTWRITEBYTECODE=1 python3 - \
    "$SELF_DIR" "$BRIEF_JSON" "$RUN_ID" "$STAGED_MANIFEST" \
    "${FINAL_OUTPUTS[@]}" "${SAFE_OUTPUTS[@]}" "${PROMPTS[@]}" <<'PY'
import hashlib
import json
import pathlib
import sys

script_dir = sys.argv[1]
brief = json.loads(sys.argv[2])
run_id = sys.argv[3]
destination = pathlib.Path(sys.argv[4])
finals = [pathlib.Path(item).resolve() for item in sys.argv[5:8]]
outputs = [pathlib.Path(item) for item in sys.argv[8:11]]
prompts = [pathlib.Path(item) for item in sys.argv[11:14]]
personas = ("MELCHIOR", "BALTHASAR", "CASPAR")
payload = {
    "schema": "magi-preflight-run/v1",
    "runner": "magi-preflight-codex/v1",
    "run_id": run_id,
    "round": 1,
    "brief": brief,
    "started_before_output_collection": True,
    "allows_second_round": False,
    "reviewers": [
        {
            "reviewer": persona,
            "path": str(final),
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "prompt_sha256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
        }
        for persona, final, output, prompt in zip(personas, finals, outputs, prompts)
    ],
}
sys.path.insert(0, script_dir)
import magi_preflight as preflight
preflight.validate_schema(payload, preflight.load_schema("preflight-run.schema.json"), "run")
destination.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
PY

# Refuse publication if the original brief changed after the immutable prompt snapshot.
PYTHONDONTWRITEBYTECODE=1 python3 - "$SELF_DIR" "$BRIEF" "$BRIEF_SHA" <<'PY'
import pathlib
import sys
sys.path.insert(0, sys.argv[1])
import magi_preflight as preflight
brief = preflight.stable_read(pathlib.Path(sys.argv[2]), limit=preflight.MAX_BRIEF_BYTES)
if brief.sha256 != sys.argv[3]:
    raise preflight.UnsafeInput("brief changed after reviewer prompt snapshot")
PY

# Manifest-last publication is the commit point: consumers never observe a complete run manifest
# until all three scrubbed artifacts have reached their canonical names.
for index in 0 1 2; do mv -- "${SAFE_OUTPUTS[$index]}" "${FINAL_OUTPUTS[$index]}"; done
mv -- "$STAGED_MANIFEST" "$FINAL_MANIFEST"
COMMITTED=1

rm -rf -- "$STAGE"
trap - EXIT INT TERM
exec env PYTHONDONTWRITEBYTECODE=1 \
    python3 "$EVALUATOR" evaluate "$BRIEF" "$FINAL_MANIFEST"
