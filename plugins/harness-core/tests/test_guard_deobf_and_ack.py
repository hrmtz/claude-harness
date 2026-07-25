#!/usr/bin/env python3
"""Credential guard de-obfuscation and Issue #108 legacy-ACK denial regression."""
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
    try:
        envelope = json.loads(r.stdout)
    except json.JSONDecodeError:
        envelope = {}
    denied = (
        envelope.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
        and "READ_ACK_DOES_NOT_AUTHORIZE_OUTPUT"
        in envelope.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
    )
    return ("BLOCK" if denied else "ALLOW"), os.path.exists(mk)

with tempfile.TemporaryDirectory() as h:
    v, _ = rg(h)
    ok &= (v == "BLOCK") or (print("  ✗ FAIL #108 no-marker should BLOCK") or False)
    v, left = rg(h, "fresh")
    ok &= (v == "BLOCK" and left) or (
        print("  ✗ FAIL #108 fresh read marker must BLOCK and remain non-authoritative") or False
    )
with tempfile.TemporaryDirectory() as h:
    v, left = rg(h, "stale")
    ok &= (v == "BLOCK" and left) or (
        print("  ✗ FAIL #108 stale read marker must BLOCK and remain non-authoritative") or False
    )
with tempfile.TemporaryDirectory() as h:
    r = subprocess.run(
        ["bash", RG],
        input=json.dumps({"tool_input": {"file_path": "/x/.env"}}),
        capture_output=True,
        text=True,
        env=dict(os.environ, HOME=h, HARNESS_ACK_CRED_READ="1"),
    )
    envelope = json.loads(r.stdout)
    ok &= (
        envelope.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    ) or (print("  ✗ FAIL #108 exported env must not authorize Read output") or False)

print("guard deobf(#14) + legacy ACK deny(#108): ALL PASS ✓" if ok else "guard deobf(#14) + legacy ACK deny(#108): FAILURES ✗")
raise SystemExit(0 if ok else 1)
