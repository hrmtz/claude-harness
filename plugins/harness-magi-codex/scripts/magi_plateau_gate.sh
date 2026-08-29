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
DOC_REAL="$(realpath "$DOC_PATH")"
DOC_LOCK_ID="$(printf '%s' "$DOC_REAL" | sha256sum | cut -c1-16)"
# Serialize verification, stale-marker revocation, and publication with fan-out,
# synthesis, and cross-family phases for this exact document.
# shellcheck source=magi_lock.sh
source "$SCRIPT_DIR/magi_lock.sh"
lock_rc=0
magi_lock_acquire "$DOC_CONTROL_DIR/.review.${DOC_LOCK_ID}.lock" || lock_rc=$?
case "$lock_rc" in
    0) ;;
    1) echo "gate: document review lock is held" >&2; exit 3 ;;
    *) echo "gate: cannot acquire document review lock" >&2; exit 2 ;;
esac

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}" \
python3 - "$DOC_PATH" "$OUT_PREFIX" "$DOC_CONTROL_DIR" "$ORCH_FAMILY" "$REVIEWER_FAMILY" <<'PY'
import glob
import hashlib
import json
import errno
import os
import re
import sys
import tempfile
from pathlib import Path

from magi_verify_round import verify_round
from magi_protocol import protocol_sha, strict_json_loads
from magi_validate_findings import validate, validate_prior_envelope
import magi_validate_findings

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


def fsync_control_dir():
    try:
        directory_fd = os.open(control_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        unsupported = {errno.EINVAL, errno.ENOTSUP}
        if hasattr(errno, "EOPNOTSUPP"):
            unsupported.add(errno.EOPNOTSUPP)
        if exc.errno not in unsupported:
            raise


def gate_number(failure):
    match = re.match(r"G(\d+):", failure)
    return int(match.group(1)) if match else 99


def carried_prior_blockers(current_findings, out_prefix):
    """Return unresolved prior HIGH+ findings carried by the current round.

    The current reviewer may recalibrate a duplicated finding downward, but a
    carried/duplicate disposition is not a resolution. Bind those dispositions
    back to the validated immediately preceding SYNTHESIS envelope and retain
    its blocking severity for G8.
    """
    carried = [
        item
        for item in (current_findings.get("dispositions") or [])
        if isinstance(item, dict)
        and item.get("disposition") in {"carried", "duplicate"}
    ]
    if not carried:
        return []
    current_round = current_findings.get("round")
    if type(current_round) is not int or current_round <= 1:
        raise ValueError("carried dispositions require a preceding review round")
    state_dir = Path(out_prefix).resolve().parent
    candidates = sorted(state_dir.glob(f"round_{current_round - 1}_*_synthesis.json"))
    schema_path = (
        Path(magi_validate_findings.__file__).resolve().parent.parent
        / "schemas"
        / "finding.schema.json"
    )
    schema = strict_json_loads(schema_path.read_bytes())
    valid_priors = []
    for candidate in candidates:
        try:
            prior = strict_json_loads(candidate.read_bytes())
            validate(
                prior,
                schema,
                doc=Path(doc).resolve(),
                same_doc_only=True,
                expected_reviewer="SYNTHESIS",
                expected_round=current_round - 1,
            )
            if prior.get("artifact_sha") != current_findings.get("artifact_sha"):
                raise ValueError("prior synthesis artifact revision mismatch")
            validate_prior_envelope(
                prior,
                candidate,
                schema,
                Path(doc).resolve(),
                current_round,
                state_dir,
            )
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        valid_priors.append(prior)
    if len(valid_priors) != 1:
        raise ValueError(
            f"expected exactly one validated prior synthesis, found {len(valid_priors)}"
        )
    prior = valid_priors[0]
    prior_findings = {
        item.get("finding_id"): item
        for item in (prior.get("findings") or [])
        if isinstance(item, dict) and isinstance(item.get("finding_id"), str)
    }
    prior_dispositions = {
        item.get("source_ref"): item
        for item in (prior.get("dispositions") or [])
        if isinstance(item, dict) and isinstance(item.get("source_ref"), str)
    }
    blocking = []
    for item in carried:
        source_ref = item.get("source_ref")
        finding_id = item.get("synthesis_finding_id")
        prior_disposition = prior_dispositions.get(source_ref)
        if (
            not isinstance(finding_id, str)
            or not finding_id
            or not isinstance(prior_disposition, dict)
            or prior_disposition.get("synthesis_finding_id") != finding_id
        ):
            raise ValueError(f"carried disposition is not bound to prior synthesis: {source_ref}")
        prior_finding = prior_findings.get(finding_id)
        if not isinstance(prior_finding, dict):
            raise ValueError(f"carried prior finding is missing: {finding_id}")
        if prior_finding.get("severity") in {"REJECT", "CRITICAL", "HIGH"}:
            blocking.append(prior_finding)
    return blocking


try:
    result = verify_round(
        Path(doc),
        Path(prefix),
        orch_family,
        reviewer_family,
        require_successful_claim=True,
    )
except Exception as exc:
    findings = meta = None
    fails = [f"G1: shared verifier failed closed: {type(exc).__name__}: {exc}"]
else:
    findings = result["findings"]
    meta = result["meta"]
    verified_protocol_sha = result.get("protocol_sha")
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
    try:
        prior_blocking = carried_prior_blockers(findings, prefix)
    except Exception as exc:
        fails.append(
            f"G8: carried-prior verification failed closed: {type(exc).__name__}: {exc}"
        )
        prior_blocking = []
    blocking.extend(prior_blocking)
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
publish_sha = hashlib.sha256(Path(doc).read_bytes()).hexdigest()
if publish_sha != actual_sha:
    revoked = revoke_doc_markers()
    detail = f"; revoked stale marker(s): {', '.join(revoked)}" if revoked else ""
    print(
        "PLATEAU DENIED:\n  - G3: document changed during gate evaluation" + detail,
        file=sys.stderr,
    )
    raise SystemExit(1)
verdict = findings["verdict"]
model_id = meta.get("model_id") or ""
sid = meta.get("session_id")
grounding = findings.get("schema_grounding_verdict")
if not isinstance(verified_protocol_sha, str) or not verified_protocol_sha:
    print("PLATEAU DENIED:\n  - G3: verifier returned no protocol identity", file=sys.stderr)
    raise SystemExit(1)
publish_protocol_sha = protocol_sha()
if publish_protocol_sha != verified_protocol_sha:
    revoked = revoke_doc_markers()
    detail = f"; revoked stale marker(s): {', '.join(revoked)}" if revoked else ""
    print(
        "PLATEAU DENIED:\n  - G3: runtime protocol changed during gate evaluation" + detail,
        file=sys.stderr,
    )
    raise SystemExit(1)
revoke_doc_markers()
fd, temporary = tempfile.mkstemp(
    prefix=f".{os.path.basename(marker)}.", suffix=".tmp", dir=control_dir
)
try:
    with os.fdopen(fd, "w") as fh:
        json.dump(
            {
                "artifact": os.path.basename(doc),
                "artifact_sha": actual_sha,
                "protocol_sha": verified_protocol_sha,
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
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temporary, marker)
    try:
        fsync_control_dir()
    except OSError:
        try:
            os.unlink(marker)
        except FileNotFoundError:
            pass
        raise
    post_publish_sha = hashlib.sha256(Path(doc).read_bytes()).hexdigest()
    if post_publish_sha != actual_sha:
        try:
            os.unlink(marker)
        except FileNotFoundError:
            pass
        fsync_control_dir()
        print(
            "PLATEAU DENIED:\n  - G3: document changed during marker publication",
            file=sys.stderr,
        )
        raise SystemExit(1)
finally:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
print(f"PLATEAU GRANTED: {verdict} by {model_id} -> {marker}")
PY
