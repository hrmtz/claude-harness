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

usage() {
    echo "usage: $0 <doc-path> <xfamily-out-prefix> [--orchestrator-family codex] [--reviewer-family claude|grok]" >&2
    exit 64
}
[ $# -ge 2 ] || usage

DOC_PATH="$1"; OUT_PREFIX="$2"; shift 2
ORCH_FAMILY="codex"
REVIEWER_FAMILY="claude"
while [ $# -gt 0 ]; do
    case "$1" in
        --orchestrator-family) [ $# -ge 2 ] || usage; ORCH_FAMILY="$2"; shift 2 ;;
        --reviewer-family) [ $# -ge 2 ] || usage; REVIEWER_FAMILY="$2"; shift 2 ;;
        *) usage ;;
    esac
done
case "$REVIEWER_FAMILY" in claude|grok) ;; *) usage ;; esac

[ -f "$DOC_PATH" ] || { echo "gate: doc not found: $DOC_PATH" >&2; exit 64; }
DOC_CONTROL_DIR="$(dirname "$(realpath "$DOC_PATH")")/.dual-magi"
mkdir -p "$DOC_CONTROL_DIR"

python3 - "$DOC_PATH" "$OUT_PREFIX" "$DOC_CONTROL_DIR" "$ORCH_FAMILY" "$REVIEWER_FAMILY" <<'PY'
import glob, hashlib, json, os, sys

doc, prefix, control_dir, orch_family, reviewer_family = sys.argv[1:6]
findings_p, meta_p = f"{prefix}.json", f"{prefix}.meta.json"
fails = []

def fail(tag, msg):
    fails.append(f"{tag}: {msg}")

actual_sha = hashlib.sha256(open(doc, "rb").read()).hexdigest()
doc_id = hashlib.sha256(os.path.realpath(doc).encode()).hexdigest()[:16]
marker_glob = os.path.join(control_dir, f"PLATEAU.{doc_id}.*")
marker = os.path.join(control_dir, f"PLATEAU.{doc_id}.{actual_sha[:16]}")

def revoke_doc_markers():
    revoked = []
    for old in glob.glob(marker_glob):
        if os.path.isfile(old):
            os.unlink(old)
            revoked.append(os.path.basename(old))
    return revoked

def bail():
    # A denial revokes every marker for this document, including prior revisions/campaigns.
    revoked = revoke_doc_markers()
    if revoked:
        fails.append(f"revoked stale marker(s): {', '.join(revoked)}")
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

# G2 -- the selected reviewer really was the requested different model family.
# Substring match accommodates managed prefixes such as us.anthropic.claude-*.
FAMILY_MARKERS = {
    ("codex", "claude"): ("claude",),
    ("codex", "grok"): ("grok",),
    ("claude", "codex"): ("gpt", "o1", "o3", "codex"),
    ("claude", "grok"): ("grok",),
}
markers = FAMILY_MARKERS.get((orch_family, reviewer_family))
model_id = meta.get("model_id") or ""
keys = meta.get("model_usage_keys") or []
recorded_family = meta.get("reviewer_family") or "claude"  # legacy Claude artifacts
def cross_family(name: str) -> bool:
    return any(m in name.lower() for m in markers)
if not markers:
    fail("G2", f"unsupported family route {orch_family!r}->{reviewer_family!r}")
elif recorded_family != reviewer_family:
    fail("G2", f"meta reviewer_family {recorded_family!r} != requested {reviewer_family!r}")
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

# G6 -- the session_id resolves to a provider-correct transcript, i.e. that CLI actually ran.
sid = meta.get("session_id")
transcripts = []
if not sid or not isinstance(sid, str):
    fail("G6", f"session_id {sid!r} missing")
else:
    if reviewer_family == "claude":
        pattern = f"~/.claude/projects/*/{glob.escape(sid)}.jsonl"
    else:
        pattern = f"~/.grok/sessions/*/{glob.escape(sid)}/chat_history.jsonl"
    transcripts = glob.glob(os.path.expanduser(pattern))
    if not transcripts:
        fail("G6", f"session_id {sid!r} does not resolve to a {reviewer_family} transcript")
    elif len(transcripts) != 1:
        fail("G6", f"session_id {sid!r} resolves to {len(transcripts)} transcripts")
    else:
        recorded_path = meta.get("transcript_path")
        if recorded_path and os.path.realpath(recorded_path) != os.path.realpath(transcripts[0]):
            fail("G6", "meta transcript_path does not match provider transcript resolution")
        recorded_sha = meta.get("transcript_sha")
        actual_transcript_sha = hashlib.sha256(open(transcripts[0], "rb").read()).hexdigest()
        if reviewer_family == "grok" and not recorded_sha:
            fail("G6", "Grok meta records no transcript_sha")
        elif recorded_sha and recorded_sha != actual_transcript_sha:
            fail("G6", "transcript_sha mismatch: transcript changed after adapter completion")
        # Model provenance. transcript_models = the models the transcript actually recorded
        # (Claude at message.model, Grok at an assistant record's model_id). Two independent checks
        # ride on it, symmetric across providers so the preferred Claude path is no weaker.
        transcript_models = set()
        read_ok = True
        try:
            with open(transcripts[0], encoding="utf-8") as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if reviewer_family == "claude":
                        m = (rec.get("message") or {}).get("model")
                    else:
                        m = rec.get("model_id") if rec.get("type") == "assistant" else None
                    if m:
                        transcript_models.add(m)
        except OSError as e:
            fail("G6", f"cannot read {reviewer_family} transcript model provenance: {e}")
            read_ok = False
        if read_ok:
            def label_consistent(a, models):
                # Loose, symmetric: for a mislabel/stale-meta check where either side may carry a
                # managed prefix (us.anthropic.claude-*) or an alias (opus -> claude-opus-4-8).
                return any(a in x or x in a for x in models)
            def served_satisfies(requested, models):
                # DIRECTIONAL: a served id must be the requested id or a MORE-specific form of it
                # (alias/managed-prefix expansion: requested ⊆ served). A served id that is merely a
                # TRUNCATION of requested (claude-opus-4 for claude-opus-4-8) is a downgrade, not a
                # match — r15-xfamily-1. So we require requested == served or requested in served,
                # never served in requested.
                return any(served == requested or requested in served for served in models)
            if not transcript_models:
                fail("G6", f"{reviewer_family} transcript records no assistant model")
            else:
                # (i) meta.model_id must appear in the transcript it claims to summarize. This is a
                # CONSISTENCY check: it catches a model_id ABSENT from the resolved transcript
                # (hand-edited / stale / cross-round-reuse meta). It does NOT catch an adapter that
                # derives a wrong-but-PRESENT model_id — model_id is itself taken from this
                # transcript's last message.model, so (i) cannot police the derivation, only its reuse.
                if not label_consistent(model_id, transcript_models):
                    fail("G6", f"meta model_id {model_id!r} inconsistent with {reviewer_family} "
                               f"transcript models {sorted(transcript_models)}")
                # (ii) the REQUESTED model must be among the models the transcript actually ran.
                # requested_model is recorded independently of the transcript (from --model / env),
                # so this is the NON-circular check for a silent same-family downgrade (requested
                # opus, CLI served haiku/claude-opus-4). model_id alone cannot make it — it is
                # derived from this transcript. The guarantee is scoped to what the transcript
                # records as the served model (message.model = the API response's model, observed
                # to track the response not the --model echo; see design §1.2 P-i); a CLI that echoed
                # --model under a downgrade would defeat it. That is a T1 signal, not T2 attestation.
                requested = meta.get("requested_model")
                if not requested:
                    fail("G6", "meta records no requested_model")
                elif not served_satisfies(requested, transcript_models):
                    fail("G6", f"requested model {requested!r} did not run: transcript ran "
                               f"{sorted(transcript_models)} (silent same-family downgrade)")

# G7 -- a REJECT is not a plateau, and neither is a REVISE.
# (SKILL.md: "A round that surfaces new criticals -- even at GO-WITH-REVISE -- is not plateau.")
if verdict in {"REJECT", "REVISE"}:
    fail("G7", f"cross-family verdict is {verdict}")

# G8 -- no unresolved blocking findings, whatever the headline verdict says.
# A GO-WITH-REVISE carrying HIGH-or-worse findings is not a plateau; verdict alone is insufficient.
blocking = [f for f in (findings.get("findings") or [])
            if isinstance(f, dict) and f.get("severity") in {"REJECT", "CRITICAL", "HIGH"}]
if blocking:
    titles = ", ".join(str(f.get("title"))[:48] for f in blocking[:3])
    fail("G8", f"{len(blocking)} unresolved REJECT/CRITICAL/HIGH finding(s): {titles}")

# G9 -- the round was actually grounded.
#
# Three independent ways a round can be ungrounded, and all three must block. Checking only
# `grounding == "FAIL"` is not enough: constrained decoding FABRICATES required fields, so a
# reviewer that ran nothing can still emit PASS with an empty command list, and every other
# assert passes. An empty verify_commands_executed IS the ungrounded state -- the prompt
# contract makes "PASS with zero commands" impossible for an honest reviewer.
#
# We do not demand a command-for-command transcript match: that would false-fail on paraphrase.
# We demand the weaker, non-flaky invariant that the reviewer used tools at all.
# Detects omission and inconsistency; NEVER semantic truth.
grounding = findings.get("schema_grounding_verdict")
if grounding == "FAIL":
    fail("G9", "reviewer self-reported schema_grounding_verdict=FAIL")
elif not cmds:
    fail("G9", f"grounding={grounding} but verify_commands_executed is empty "
               f"(a grounded round must have run commands)")
elif transcripts:
    tool_uses = 0
    try:
        with open(transcripts[0], encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if reviewer_family == "claude":
                    content = (rec.get("message") or {}).get("content")
                    if isinstance(content, list):
                        tool_uses += sum(1 for b in content
                                         if isinstance(b, dict) and b.get("type") == "tool_use")
                else:
                    calls = rec.get("tool_calls")
                    if isinstance(calls, list):
                        tool_uses += len(calls)
    except OSError as e:
        fail("G9", f"cannot read transcript to verify grounding: {e}")
    else:
        if tool_uses == 0:
            fail("G9", f"{len(cmds)} commands reported but the transcript shows no tool use "
                       f"(fabricated verify_commands_executed)")

if fails:
    bail()

revoke_doc_markers()
with open(marker, "w") as f:
    json.dump({"artifact": os.path.basename(doc), "artifact_sha": actual_sha,
               "verdict": verdict, "model_id": model_id, "reviewer_family": reviewer_family,
               "session_id": sid, "grounding": grounding,
               "asserts_passed": ["G1","G2","G3","G4","G5","G6","G7","G8","G9"],
               "protects_against": "T1 (accidental skip). NOT T2 (adversarial same-UID)."},
              f, indent=2)
print(f"PLATEAU GRANTED: {verdict} by {model_id} -> {marker}")
PY
