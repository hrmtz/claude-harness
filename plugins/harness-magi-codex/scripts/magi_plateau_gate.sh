#!/usr/bin/env bash
# magi_plateau_gate.sh — the ONLY thing permitted to write a plateau marker (INV-2).
#
# Design: docs/designs/CODEX_MAGI_MIRROR.md §4.3 (asserts G1..G9).
#
# The model does not declare plateau. This script does, and only if every assert passes.
# gh #195's root cause was a behavioral rule ("remember to run the cross-family round")
# that the orchestrating AI forgot. A sentence in a SKILL.md is not a rail; this is.
#
# Scope of protection: T1 (accidental skip, stale artifact reuse, buggy script).
# NOT T2: a same-UID process can write any of these files. This is not forgery resistance.
#
# Exit codes:
#   0  all asserts passed; marker written
#   1  an assert failed; NO marker written (blocking)
#   64 usage error
set -euo pipefail

usage() { echo "usage: $0 <doc-path> <xfamily-out-prefix> [--orchestrator-family codex]" >&2; exit 64; }
[ $# -ge 2 ] || usage

DOC_PATH="$1"; OUT_PREFIX="$2"; shift 2
ORCH_FAMILY="codex"
while [ $# -gt 0 ]; do
    case "$1" in
        --orchestrator-family) [ $# -ge 2 ] || usage; ORCH_FAMILY="$2"; shift 2 ;;
        *) usage ;;
    esac
done

[ -f "$DOC_PATH" ] || { echo "gate: doc not found: $DOC_PATH" >&2; exit 64; }
STATE_DIR="$(dirname "$OUT_PREFIX")"

python3 - "$DOC_PATH" "$OUT_PREFIX" "$STATE_DIR" "$ORCH_FAMILY" <<'PY'
import glob, hashlib, json, os, sys

doc, prefix, state_dir, orch_family = sys.argv[1:5]
findings_p, meta_p = f"{prefix}.json", f"{prefix}.meta.json"
fails = []

def fail(tag, msg):
    fails.append(f"{tag}: {msg}")

def bail():
    print("PLATEAU DENIED:", *fails, sep="\n  - ", file=sys.stderr)
    sys.exit(1)

# G1 -- the cross-family round exists, is well-formed, and carries an in-enum verdict.
VALID = {"GO", "GO-WITH-REVISE", "REVISE", "REJECT"}
findings = meta = None
if not os.path.exists(findings_p):
    fail("G1", f"no cross-family findings at {findings_p}")
elif not os.path.exists(meta_p):
    fail("G1", f"no provenance meta at {meta_p}")
else:
    try:
        findings = json.load(open(findings_p))
        meta = json.load(open(meta_p))
    except (json.JSONDecodeError, OSError) as e:
        fail("G1", f"unreadable round artifacts: {e}")
    if not isinstance(findings, dict) or not isinstance(meta, dict):
        fail("G1", "round artifacts are not JSON objects")
        findings = meta = None
    elif findings.get("verdict") not in VALID:
        # UNPARSEABLE lives outside the enum by design: a fail-closed sentinel must never
        # satisfy an existence check.
        fail("G1", f"verdict {findings.get('verdict')!r} not in {sorted(VALID)}")

if fails or findings is None or meta is None:
    bail()

verdict = findings["verdict"]

# G2 -- the reviewer really was a different model family than the orchestrator.
# Substring match, not a hard prefix: managed deployments prefix ids (us.anthropic.claude-...,
# anthropic.claude-...), and a strict "claude-" prefix would refuse every legitimate round there.
FAMILY_MARKERS = {"codex": ("claude",), "claude": ("gpt", "o1", "o3", "codex")}
markers = FAMILY_MARKERS.get(orch_family)
model_id = meta.get("model_id") or ""
keys = meta.get("model_usage_keys") or []
def cross_family(name: str) -> bool:
    return any(m in name.lower() for m in markers)
if not markers:
    fail("G2", f"unknown orchestrator family {orch_family!r}")
elif not model_id:
    fail("G2", "meta records no model_id")
elif not cross_family(model_id):
    fail("G2", f"model_id {model_id!r} is not cross-family for orchestrator {orch_family!r}")
elif not keys:
    # An empty key list must not vacuously satisfy all().
    fail("G2", "meta records no model_usage_keys")
elif not all(cross_family(k) for k in keys):
    fail("G2", f"modelUsage keys {keys} are not all cross-family")

# G3 -- the round reviewed THIS revision of THIS doc (kills stale-round reuse).
actual_sha = hashlib.sha256(open(doc, "rb").read()).hexdigest()
if meta.get("artifact_sha") != actual_sha:
    fail("G3", f"artifact_sha mismatch: round reviewed {str(meta.get('artifact_sha'))[:16]}…, "
               f"doc is now {actual_sha[:16]}… (stale round, or doc edited after review)")

# G4 -- the findings bytes were not swapped after the round.
out_sha = hashlib.sha256(open(findings_p, "rb").read()).hexdigest()
if meta.get("output_sha") != out_sha:
    fail("G4", "output_sha mismatch: findings file changed since the adapter wrote it")

# G5 -- a round that took no turns cannot have executed commands.
turns = meta.get("num_turns") or 0
cmds = findings.get("verify_commands_executed") or []
if turns < 1:
    fail("G5", f"num_turns={turns}")
elif turns <= 1 and cmds:
    fail("G5", f"self-contradiction: num_turns={turns} but {len(cmds)} commands reported")

# G6 -- the session_id resolves to a real transcript, i.e. a CLI actually ran.
sid = meta.get("session_id")
transcripts = []
if not sid or not isinstance(sid, str):
    fail("G6", f"session_id {sid!r} missing")
else:
    # glob.escape: a session_id of "*" would otherwise match any transcript.
    transcripts = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{glob.escape(sid)}.jsonl"))
    if not transcripts:
        fail("G6", f"session_id {sid!r} does not resolve to a transcript")

# G7 -- a REJECT is not a plateau, and neither is a REVISE.
# (SKILL.md: "A round that surfaces new criticals -- even at GO-WITH-REVISE -- is not plateau.")
if verdict in {"REJECT", "REVISE"}:
    fail("G7", f"cross-family verdict is {verdict}")

# G8 -- no unresolved blocking findings, whatever the headline verdict says.
# A GO-WITH-REVISE carrying a CRITICAL is not a plateau; the verdict field alone is not enough.
blocking = [f for f in (findings.get("findings") or [])
            if isinstance(f, dict) and f.get("severity") in {"REJECT", "CRITICAL"}]
if blocking:
    titles = ", ".join(str(f.get("title"))[:48] for f in blocking[:3])
    fail("G8", f"{len(blocking)} unresolved REJECT/CRITICAL finding(s): {titles}")

# G9 -- the round was actually grounded.
# Self-reported grounding is honest in the T1 model (the prompt orders a FAIL self-report when
# ungrounded), so a FAIL must block. Additionally: constrained decoding will FABRICATE required
# fields, so cross-check the self-report against the transcript. We do not demand an exact
# command-for-command match -- that would false-fail on paraphrase. We demand the weaker,
# non-flaky invariant: if the reviewer claims it ran commands, the transcript must show it used
# tools at all. Detects omission and inconsistency; NOT semantic truth.
grounding = findings.get("schema_grounding_verdict")
if grounding == "FAIL":
    fail("G9", "reviewer self-reported schema_grounding_verdict=FAIL")
elif cmds and transcripts:
    tool_uses = 0
    try:
        with open(transcripts[0], encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                content = (rec.get("message") or {}).get("content")
                if isinstance(content, list):
                    tool_uses += sum(1 for b in content
                                     if isinstance(b, dict) and b.get("type") == "tool_use")
    except OSError as e:
        fail("G9", f"cannot read transcript to verify grounding: {e}")
    else:
        if tool_uses == 0:
            fail("G9", f"{len(cmds)} commands reported but the transcript shows no tool use "
                       f"(fabricated verify_commands_executed)")

if fails:
    bail()

marker = os.path.join(state_dir, f"PLATEAU.{actual_sha[:16]}")
with open(marker, "w") as f:
    json.dump({"artifact": os.path.basename(doc), "artifact_sha": actual_sha,
               "verdict": verdict, "model_id": model_id,
               "session_id": sid, "grounding": grounding,
               "asserts_passed": ["G1","G2","G3","G4","G5","G6","G7","G8","G9"],
               "protects_against": "T1 (accidental skip). NOT T2 (adversarial same-UID)."},
              f, indent=2)
print(f"PLATEAU GRANTED: {verdict} by {model_id} -> {marker}")
PY
