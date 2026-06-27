#!/usr/bin/env python3
"""Tests for pg_rotation_propagation_guard.sh — execution-intent only, no over-fire.
Run: python3 plugins/harness-core/tests/test_pg_rotation_guard.py"""
import subprocess, json, os, sys

HOOK = os.path.join(os.path.dirname(__file__), "..", "hooks", "pg_rotation_propagation_guard.sh")
RS = "_rotate_mars_pg_roles.sh"
AR = "autorotate_leaked_cred.sh"

def verdict(cmd):
    out = subprocess.run(["bash", HOOK], input=json.dumps({"tool_input": {"command": cmd}}),
                         capture_output=True, text=True).stdout
    return "DENY" if "permissionDecision" in out else "ALLOW"

ALLOW = [  # inspecting / discussing / unrelated — must ALLOW
    f"grep -rn {RS} .",
    f"cat scripts/{RS}",
    f"sed -n '1,20p' {RS}",
    f"less {RS}",
    f"git log --oneline -- {RS}",
    f"grep -nE 'HUMAN GATE|escalate' plugins/harness-core/hooks/{AR} | head",   # the v2 regression
    f"grep -nE 'AUTOROTATE_ENABLE' x.sh ; grep -c foo plugins/harness-core/hooks/{AR}",
    f'gh issue create --body "discuss {RS} and ALTER ROLE x PASSWORD y"',
    f'git commit -m "mention {RS} and {AR}"',
    'echo "running ALTER ROLE foo PASSWORD bar"',
    f"wc -l {RS} && ls -la",
    f"bash {RS} --dry-run",
    f"PG_ROTATION_PROPAGATION_ACK=1 bash {RS} --execute",
    "ls -la /tmp",
    f"cat {AR} | grep -c rotate",          # read piped to grep — not exec
    f"vim {RS}",
]
DENY = [  # real execution — must DENY
    f"bash {RS} --roles prs_ingest --execute",
    f"./{RS} --execute",
    f"PG_FOO=1 bash scripts/{RS} --execute",
    f"cd /tmp && {RS} --execute",
    f"ls; bash {RS} --execute",
    f"sh {AR}",
    'psql -c "ALTER ROLE prs_ingest WITH PASSWORD secretval"',
    'psql "$U" -c "ALTER USER prs_bench WITH PASSWORD newpw"',
    f"echo start && PG_X=1 ./{RS}",
]

ok = True
for c in ALLOW:
    r = verdict(c)
    if r != "ALLOW": ok = False; print(f"  ✗ FAIL expected ALLOW got {r}: {c[:64]}")
for c in DENY:
    r = verdict(c)
    if r != "DENY": ok = False; print(f"  ✗ FAIL expected DENY got {r}: {c[:64]}")
print(f"{'ALL PASS ✓' if ok else 'FAILURES ✗'}  ({len(ALLOW)} allow + {len(DENY)} deny cases)")
sys.exit(0 if ok else 1)
