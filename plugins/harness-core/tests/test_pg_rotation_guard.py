#!/usr/bin/env python3
"""Tests for pg_rotation_propagation_guard.sh v4 (leading-command-only).
Fires ONLY when the actually-executed program is the rotation script (or via
bash/sh) or psql running ALTER...PASSWORD. A trigger word as an argument (incl.
inside a quoted body) never fires. Chained rotations are under-detected by design.
Run: python3 plugins/harness-core/tests/test_pg_rotation_guard.py"""
import subprocess, json, os, sys
H = os.path.join(os.path.dirname(__file__), "..", "hooks", "pg_rotation_propagation_guard.sh")
RS = "_rotate_mars_pg_roles.sh"; AR = "autorotate_leaked_cred.sh"
def verdict(cmd):
    out = subprocess.run(["bash", H], input=json.dumps({"tool_input": {"command": cmd}}),
                         capture_output=True, text=True).stdout
    return "DENY" if "permissionDecision" in out else "ALLOW"

ALLOW = [
    # reads / search / inspection
    f"grep -rn {RS} .", f"cat scripts/{RS}", f"sed -n '1,20p' {RS}", f"less {RS}",
    f"grep -nE 'HUMAN GATE' plugins/harness-core/hooks/{AR} | head",
    f"cat {AR} | grep -c rotate", f"wc -l {RS}", f"vim {RS}", "ls -la /tmp",
    # over-fire cases that MUST allow (trigger word only inside a quoted argument)
    f'gh issue comment 45 --body "discuss {RS} and ALTER ROLE x PASSWORD y"',
    f'git commit -m "mention {RS}"',
    f'mailbox-send "%32" "dispatch: run {RS} then ; bash {RS} --execute later"',
    'echo "psql -c ALTER ROLE x PASSWORD y"',
    'echo "ALTER ROLE foo PASSWORD bar"',
    # chained rotations: under-detected by design (advisory reminder) -> ALLOW
    f"cd /tmp && {RS} --execute",
    f"ls; bash {RS} --execute",
    # ack + dry-run
    f"PG_ROTATION_PROPAGATION_ACK=1 bash {RS} --execute",
    f"bash {RS} --dry-run",
]
DENY = [  # the rotation program is actually executed as the leading command
    f"bash {RS} --roles prs_ingest --execute",
    f"./{RS} --execute",
    f"PG_FOO=1 bash scripts/{RS} --execute",
    f"sh {AR}",
    f"{RS} --execute",
    'psql -c "ALTER ROLE prs_ingest WITH PASSWORD secretval"',
    'psql "$U" -c "ALTER USER prs_bench WITH PASSWORD newpw"',
    'PGPASSWORD=x psql -c "ALTER ROLE x WITH PASSWORD y"',
]
ok = True
for c in ALLOW:
    r = verdict(c)
    if r != "ALLOW": ok = False; print(f"  ✗ FAIL want ALLOW got {r}: {c[:70]}")
for c in DENY:
    r = verdict(c)
    if r != "DENY": ok = False; print(f"  ✗ FAIL want DENY got {r}: {c[:70]}")
print("pg_rotation_guard v4: ALL PASS ✓" if ok else "pg_rotation_guard v4: FAILURES ✗")
sys.exit(0 if ok else 1)
