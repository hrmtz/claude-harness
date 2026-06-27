#!/usr/bin/env python3
"""gh #13: credential_value_scrub allowlist must not be poisoned — a REAL secret
co-occurring with a placeholder must still be scrubbed from the jsonl.
Run: python3 plugins/harness-core/tests/test_value_scrub_poisoning.py"""
import subprocess, json, os, tempfile

HOOK = os.path.join(os.path.dirname(__file__), "..", "hooks", "credential_value_scrub.sh")
# real secret built so it is never a literal in this file's "secret" sense beyond test data
REAL = "ghp_" + "a" * 36                      # matches ghp_[a-zA-Z0-9]{30,}, not allowlisted
PLACEHOLDER = "ghp_" + "changeme" * 4 + "xx"  # matches pattern AND allowlist ('changeme')
REAL_KW = "MY_SECRET_KEY=" + "Z" * 24         # keyword=value, real
PH_KW = "DEMO_SECRET_KEY=changeme_placeholder_value"  # keyword=value, placeholder

def run(jsonl_line):
    home = tempfile.mkdtemp()
    tdir = os.path.join(home, ".claude", "projects", "p"); os.makedirs(tdir)
    jp = os.path.join(tdir, "sess.jsonl"); open(jp, "w").write(jsonl_line + "\n")
    inp = json.dumps({"tool_response": {"stdout": jsonl_line}, "transcript_path": jp,
                      "tool_input": {"command": "echo test"}})
    subprocess.run(["bash", HOOK], input=inp, capture_output=True, text=True,
                   env=dict(os.environ, HOME=home))
    return open(jp).read()

ok = True
# placeholder FIRST then real (pre-#13: head-1=placeholder -> whole pattern skipped -> real leaks)
out = run(f"{PLACEHOLDER} and also {REAL}")
if REAL in out:
    ok = False; print("  ✗ FAIL #13: real ghp_ secret survived next to a placeholder")
out = run(f"{PH_KW} then {REAL_KW}")
if REAL_KW.split("=")[1] in out:
    ok = False; print("  ✗ FAIL #13: real keyword=value survived next to a placeholder")
# pure placeholder must NOT be scrubbed (still allowlisted)
out = run(PLACEHOLDER)
if "<REDACTED>" in out:
    ok = False; print("  ✗ FAIL: pure placeholder was scrubbed (should stay)")

print("value_scrub poisoning (#13): ALL PASS ✓" if ok else "value_scrub poisoning (#13): FAILURES ✗")
raise SystemExit(0 if ok else 1)
