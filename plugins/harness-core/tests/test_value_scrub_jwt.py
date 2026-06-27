#!/usr/bin/env python3
"""gh #14: credential_value_scrub must catch the JWT / Supabase sb-...-auth-token
value class that the `cut -f` JSON-cookie-export leak exposed (format-assumption
fall-through). JWT structure (two base64url 'eyJ' segments + sig) is unmistakable,
so a real token is scrubbed but an allowlisted example is left intact.
Run: python3 plugins/harness-core/tests/test_value_scrub_jwt.py"""
import subprocess, json, os, tempfile

HOOK = os.path.join(os.path.dirname(__file__), "..", "hooks", "credential_value_scrub.sh")

# Synthetic fake tokens — never real. Each JWT segment >= 10 [A-Za-z0-9_=-] chars.
REAL_JWT = "eyJ" + "A" * 20 + ".eyJ" + "B" * 20 + "." + "C" * 20   # -> <REDACTED_JWT>
SB_COOKIE = "sb-abcdefgh12-auth-token"                            # -> sb-<REDACTED>-auth-token
# allowlisted: JWT-shaped but contains the 'example' placeholder token
EXAMPLE_JWT = "eyJexample" + "A" * 12 + ".eyJ" + "B" * 12 + "." + "C" * 12


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

# 1. real JWT (e.g. Supabase access token value) must be scrubbed
out = run(f'cookie value: {REAL_JWT}')
if REAL_JWT in out:
    ok = False; print("  ✗ FAIL #14: real JWT survived (cut -f JSON-cookie leak class)")
if "<REDACTED_JWT>" not in out:
    ok = False; print("  ✗ FAIL #14: JWT not replaced with <REDACTED_JWT>")

# 2. Supabase auth-token cookie name must be redacted
out = run(f'"name":"{SB_COOKIE}"')
if SB_COOKIE in out:
    ok = False; print("  ✗ FAIL #14: sb-...-auth-token cookie name survived")

# 3. real JWT co-occurring with a placeholder must STILL be scrubbed (#13 poisoning rule)
out = run(f'{EXAMPLE_JWT} and also {REAL_JWT}')
if REAL_JWT in out:
    ok = False; print("  ✗ FAIL #14: real JWT survived next to an example JWT (allowlist poisoning)")

# 4. pure example/placeholder JWT must NOT be scrubbed (allowlisted)
out = run(EXAMPLE_JWT)
if "<REDACTED_JWT>" in out:
    ok = False; print("  ✗ FAIL #14: example JWT was scrubbed (should stay — allowlisted)")

print("value_scrub JWT/Supabase (#14): ALL PASS ✓" if ok else "value_scrub JWT/Supabase (#14): FAILURES ✗")
raise SystemExit(0 if ok else 1)
