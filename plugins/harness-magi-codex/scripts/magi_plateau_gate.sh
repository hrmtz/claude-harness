#!/usr/bin/env bash
# magi_plateau_gate.sh — the ONLY thing permitted to write a plateau marker (INV-2).
#
# G1..G6/G9 are write-free shared verification. This wrapper owns G7/G8, stale-marker
# revocation, and marker publication. Scope remains T1 accidental omission/staleness, not T2
# adversarial same-UID forgery.
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
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}" \
python3 - "$DOC_PATH" "$OUT_PREFIX" "$DOC_CONTROL_DIR" "$ORCH_FAMILY" "$REVIEWER_FAMILY" <<'PY'
import glob
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from magi_verify_round import verify_round

MAGI_GATE_OWNERSHIP = ("G7", "G8")

doc, prefix, control_dir, orch_family, reviewer_family = sys.argv[1:6]
actual_sha = hashlib.sha256(Path(doc).read_bytes()).hexdigest()
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


def gate_number(failure):
    match = re.match(r"G(\d+):", failure)
    return int(match.group(1)) if match else 99


try:
    result = verify_round(
        Path(doc),
        Path(prefix),
        orch_family,
        reviewer_family,
    )
except Exception as exc:
    findings = meta = None
    fails = [f"G1: shared verifier failed closed: {type(exc).__name__}: {exc}"]
else:
    findings = result["findings"]
    meta = result["meta"]
    fails = list(result["failures"])

if not fails and findings is not None and meta is not None:
    verdict = findings["verdict"]
    if verdict in {"REJECT", "REVISE"}:
        fails.append(f"G7: cross-family verdict is {verdict}")
    blocking = [
        finding
        for finding in (findings.get("findings") or [])
        if isinstance(finding, dict)
        and finding.get("severity") in {"REJECT", "CRITICAL", "HIGH"}
    ]
    if blocking:
        titles = ", ".join(str(finding.get("title"))[:48] for finding in blocking[:3])
        fails.append(
            f"G8: {len(blocking)} unresolved REJECT/CRITICAL/HIGH finding(s): {titles}"
        )

if fails:
    fails.sort(key=gate_number)
    revoked = revoke_doc_markers()
    if revoked:
        fails.append(f"revoked stale marker(s): {', '.join(revoked)}")
    print("PLATEAU DENIED:", *fails, sep="\n  - ", file=sys.stderr)
    raise SystemExit(1)

assert findings is not None and meta is not None
verdict = findings["verdict"]
model_id = meta.get("model_id") or ""
sid = meta.get("session_id")
grounding = findings.get("schema_grounding_verdict")
revoke_doc_markers()
with open(marker, "w") as fh:
    json.dump(
        {
            "artifact": os.path.basename(doc),
            "artifact_sha": actual_sha,
            "verdict": verdict,
            "model_id": model_id,
            "reviewer_family": reviewer_family,
            "session_id": sid,
            "grounding": grounding,
            "asserts_passed": ["G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9"],
            "protects_against": "T1 (accidental skip). NOT T2 (adversarial same-UID).",
        },
        fh,
        indent=2,
    )
print(f"PLATEAU GRANTED: {verdict} by {model_id} -> {marker}")
PY
