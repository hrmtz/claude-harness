#!/usr/bin/env python3
"""harness-grok (gh #55): credential_value_scrub must work on a Grok-shaped
PostToolUse payload — tool output under camelCase `.toolResponse.stdout`, and the
transcript resolved by `.sessionId` / GROK_SESSION_ID to the Grok session file
`~/.grok/sessions/<enc-workspace>/<sid>/chat_history.jsonl` (there is no
transcript_path). If either the parse or the path resolution were Claude-only,
the leak would survive in the Grok transcript.
Run: python3 plugins/harness-core/tests/test_value_scrub_grok.py"""
import subprocess, json, os, tempfile

HOOK = os.path.join(os.path.dirname(__file__), "..", "hooks", "credential_value_scrub.sh")

# Synthetic fake key — never real. Matches the sk-ant-[A-Za-z0-9_-]{20,} pattern.
FAKE_KEY = "sk-ant-" + "A" * 30
SID = "019f2706-012e-74e1-b421-c5b2ef479b06"


def run_grok(leak_line):
    """Drive the scrubber with a Grok-shaped payload + a fake Grok session tree."""
    home = tempfile.mkdtemp()
    # Grok transcript lives under a percent-encoded workspace dir; active_jsonl
    # globs by the unique sessionId so the exact encoding doesn't matter here.
    enc_ws = "%2Fhome%2Fexample%2Fproj"
    sdir = os.path.join(home, ".grok", "sessions", enc_ws, SID)
    os.makedirs(sdir)
    jp = os.path.join(sdir, "chat_history.jsonl")
    open(jp, "w").write(json.dumps({"type": "assistant", "content": leak_line}) + "\n")
    inp = json.dumps({
        "hookEventName": "post_tool_use",
        "sessionId": SID,
        "toolName": "run_terminal_command",
        "toolInput": {"command": "echo test"},
        "toolResponse": {"stdout": leak_line},
    })
    env = dict(os.environ, HOME=home, GROK_SESSION_ID=SID)
    subprocess.run(["bash", HOOK], input=inp, capture_output=True, text=True, env=env)
    return open(jp).read()


ok = True

# 1. Grok camelCase toolResponse.stdout leak -> scrubbed in Grok chat_history.jsonl
out = run_grok(f"leaked key here: {FAKE_KEY}")
if FAKE_KEY in out:
    ok = False
    print("  ✗ FAIL: fake key survived in Grok chat_history.jsonl "
          "(toolResponse/sessionId path not resolved)")
if "sk-ant-<REDACTED>" not in out:
    ok = False
    print("  ✗ FAIL: sk-ant key not replaced with sk-ant-<REDACTED> under Grok payload")

# 2. chat_history.jsonl stays valid JSON after the in-place scrub (no corruption)
try:
    for line in out.splitlines():
        if line.strip():
            json.loads(line)
except Exception as e:  # noqa
    ok = False
    print(f"  ✗ FAIL: scrub corrupted chat_history.jsonl JSON: {e}")

print("value_scrub Grok payload (#55): ALL PASS ✓" if ok
      else "value_scrub Grok payload (#55): FAILURES ✗")
raise SystemExit(0 if ok else 1)
