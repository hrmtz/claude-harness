#!/usr/bin/env python3
"""gh #14 (bash_command_guard $IFS de-obfuscation) + #19 (credential_file_read_guard
one-time/expiring ack). Run: python3 plugins/harness-core/tests/test_guard_deobf_and_ack.py"""
import subprocess, json, os, tempfile, time

HDIR = os.path.join(os.path.dirname(__file__), "..", "hooks")
BG = os.path.join(HDIR, "bash_command_guard.sh")
RG = os.path.join(HDIR, "credential_file_read_guard.sh")
ok = True

def bg(cmd):
    out = subprocess.run(["bash", BG], input=json.dumps({"tool_input": {"command": cmd}}),
                         capture_output=True, text=True).stdout
    return "DENY" if "permissionDecision" in out else "ALLOW"

# --- #14: ${IFS}/$IFS whitespace obfuscation of `sops -d` must be caught ---
S = "s" + "ops"   # avoid a literal here tripping anything; reconstruct
for cmd, want in [
    (S + "${IFS}-d secrets.enc.yaml", "DENY"),
    (S + "$IFS-d secrets.enc.yaml", "DENY"),
    ("ls -la /tmp", "ALLOW"),
]:
    got = bg(cmd)
    if got != want: ok = False; print(f"  ✗ FAIL #14 want {want} got {got}: {cmd}")

# --- #19: read-guard ack is consumable + expiring (file marker), not a persistent env ---
def rg(home, make_marker=None):
    # make_marker: None=no marker, ('fresh')=now, ('stale')=200s old
    st = os.path.join(home, ".claude", "state"); os.makedirs(st, exist_ok=True)
    mk = os.path.join(st, "cred_read_ack")
    if make_marker == "fresh": open(mk, "w").close()
    elif make_marker == "stale":
        open(mk, "w").close(); old = time.time() - 200; os.utime(mk, (old, old))
    r = subprocess.run(["bash", RG], input=json.dumps({"tool_input": {"file_path": "/x/.env"}}),
                       capture_output=True, text=True, env=dict(os.environ, HOME=home))
    return ("ALLOW" if r.returncode == 0 else "BLOCK"), os.path.exists(mk)

h = tempfile.mkdtemp()
v, _ = rg(h);                       ok &= (v == "BLOCK") or (print("  ✗ FAIL #19 no-marker should BLOCK") or False)
v, left = rg(h, "fresh");           ok &= (v == "ALLOW" and not left) or (print("  ✗ FAIL #19 fresh marker should ALLOW + consume") or False)
v, _ = rg(h);                       ok &= (v == "BLOCK") or (print("  ✗ FAIL #19 after-consume should BLOCK (one-time)") or False)
v, left = rg(h, "stale");           ok &= (v == "BLOCK" and not left) or (print("  ✗ FAIL #19 stale marker should BLOCK + be cleared") or False)
# env var must NOT bypass any more (exportable -> persistent was the bug)
r = subprocess.run(["bash", RG], input=json.dumps({"tool_input": {"file_path": "/x/.env"}}),
                   capture_output=True, text=True, env=dict(os.environ, HOME=tempfile.mkdtemp(), HRMTZ_ACK_CRED_READ="1"))
ok &= (r.returncode != 0) or (print("  ✗ FAIL #19 exported env should NO LONGER bypass") or False)

print("guard deobf(#14) + ack(#19): ALL PASS ✓" if ok else "guard deobf(#14) + ack(#19): FAILURES ✗")
raise SystemExit(0 if ok else 1)
