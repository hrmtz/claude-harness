#!/usr/bin/env python3
"""autorotate_leaked_cred.sh source-trust GATE (gh #41).
DRYRUN=1 + PATH hiding gh/discord-bot => no real rotation, no external side effects.
'DRYRUN decision' in stdout == auto-rotation reached; absent == escalated/ack-gated.
Run: python3 plugins/harness-core/tests/test_autorotate_gate.py"""
import subprocess, os

H = os.path.join(os.path.dirname(__file__), "..", "hooks", "autorotate_leaked_cred.sh")
BASE = {"PATH": "/usr/bin:/bin", "HOME": os.path.expanduser("~"),
        "AUTOROTATE_DRYRUN": "1", "LEAK_CLASS": "pg_dsn", "LEAK_ROLE": "prs_ingest"}

def run(extra, sid):
    e = dict(BASE); e.update(extra); e["LEAK_SESSION_ID"] = sid
    out = subprocess.run(["bash", H], env=e, capture_output=True, text=True).stdout
    m = os.path.expanduser(f"~/.claude/state/credential_scrub/rotated/{sid}_{e['LEAK_ROLE']}")
    try: os.remove(m)
    except FileNotFoundError: pass
    return "AUTO" if "DRYRUN decision" in out else "GATED"

cases = [
    ("untrusted -> GATED",                  {"LEAK_TRUST": "untrusted"}, "g1", "GATED"),
    ("untrusted + ack -> GATED (absolute)", {"LEAK_TRUST": "untrusted", "AUTOROTATE_ACK_ROLE": "prs_ingest"}, "g2", "GATED"),
    ("trusted -> AUTO",                     {"LEAK_TRUST": "trusted"}, "g3", "AUTO"),
    ("ambiguous, no ack -> GATED",          {"LEAK_TRUST": "ambiguous"}, "g4", "GATED"),
    ("ambiguous + ack -> AUTO",             {"LEAK_TRUST": "ambiguous", "AUTOROTATE_ACK_ROLE": "prs_ingest"}, "g5", "AUTO"),
    ("default (unset) -> GATED",            {}, "g6", "GATED"),
]
ok = True
for name, extra, sid, want in cases:
    got = run(extra, sid)
    if got != want: ok = False; print(f"  ✗ FAIL {got} (want {want}) :: {name}")
print("autorotate gate: ALL PASS ✓" if ok else "autorotate gate: FAILURES ✗")
raise SystemExit(0 if ok else 1)
