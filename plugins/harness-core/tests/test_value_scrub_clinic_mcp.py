#!/usr/bin/env python3
"""gh #99: the clinic MCP token class must be scrubbed by the value-prefix
catalog, because it is the only layer that can see it — its sops file is a
nested mapping, so the HMAC manifest has no entry (nested values never reach
the environment the builder reads) and the Part 2 keyword rule matches
uppercase KEY=VALUE while that file's keys are lowercase with ':' separators.

The negative case is the point of this test as much as the positive one. The
same prefix is a real identifier in the vendored Parade touchscreen drivers
(CMCP = a capacitance test feature), so a house-style 'cmcp_[A-Za-z0-9_-]{20,}'
pattern would redact driver source out of a transcript. Requiring an uppercase
letter separates tokens (base64url, mixed case) from identifiers (strictly
lowercase_with_underscores).

Run: python3 plugins/harness-core/tests/test_value_scrub_clinic_mcp.py"""
import subprocess, json, os, tempfile

HOOK = os.path.join(os.path.dirname(__file__), "..", "hooks", "credential_value_scrub.sh")

# Synthetic, assembled from character classes — never a real value. Real tokens
# are base64url and may contain '_' in the body, which an earlier attempt at
# this pattern wrongly excluded and thereby failed to find one of two live
# values (gh #155).
TOKEN_PLAIN = "cmcp_" + "a" * 6 + "X" + "b" * 20
TOKEN_WITH_UNDERSCORE = "cmcp_" + "a" * 6 + "Y" + "b" * 8 + "_" + "c" * 12

# Real identifiers copied from the vendored driver. Must survive untouched.
IDENTIFIERS = [
    "cmcp_check_config_fw_match",
    "cmcp_get_case_info_from_threshold_file",
    "cmcp_get_configuration_info",
]


def run(text):
    home = tempfile.mkdtemp()
    tdir = os.path.join(home, ".claude", "projects", "p")
    os.makedirs(tdir)
    jp = os.path.join(tdir, "sess.jsonl")
    open(jp, "w").write(text + "\n")
    inp = json.dumps({"tool_response": {"stdout": text}, "transcript_path": jp,
                      "tool_input": {"command": "echo test"}})
    subprocess.run(["bash", HOOK], input=inp, capture_output=True, text=True,
                   env=dict(os.environ, HOME=home))
    return open(jp).read()


ok = True


def check(label, cond, detail=""):
    global ok
    if cond:
        print(f"ok   {label}")
    else:
        ok = False
        print(f"FAIL {label}{(' — ' + detail) if detail else ''}")


out = run(f"token is {TOKEN_PLAIN} here")
check("a token is redacted", TOKEN_PLAIN not in out and "cmcp_<REDACTED>" in out)

out = run(f"token is {TOKEN_WITH_UNDERSCORE} here")
check("a token containing '_' in the body is redacted",
      TOKEN_WITH_UNDERSCORE not in out and "cmcp_<REDACTED>" in out)

out = run(f"both {TOKEN_PLAIN} and {TOKEN_WITH_UNDERSCORE} appear")
check("two distinct tokens on one line are both redacted",
      TOKEN_PLAIN not in out and TOKEN_WITH_UNDERSCORE not in out)

src = "static int " + IDENTIFIERS[0] + "(void) { return " + IDENTIFIERS[1] + "(); }"
out = run(src)
check("driver identifiers are left intact",
      all(i in out for i in IDENTIFIERS[:2]) and "<REDACTED>" not in out,
      f"got: {out.strip()[:90]}")

out = run(f"{IDENTIFIERS[2]} next to {TOKEN_PLAIN}")
check("an identifier beside a token: only the token is redacted",
      IDENTIFIERS[2] in out and TOKEN_PLAIN not in out)

raise SystemExit(0 if ok else 1)
