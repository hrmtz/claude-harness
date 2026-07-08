#!/usr/bin/env python3
"""Tests for sr_depth_gate.py (promoted from hippocampus-mcp v1.15.0).

The gate blocks a /security-review that returns a no_findings verdict without
opening every changed file with Read; it fails open on everything else. A "block"
== the hook prints {"decision":"block",...} on stdout with exit 0. Silent pass ==
empty stdout, exit 0. The hook must NEVER exit non-zero (that would hard-trap the
session). Run: python3 plugins/harness-core/tests/test_sr_depth_gate.py"""
import subprocess, json, os, tempfile

HOOK = os.path.join(os.path.dirname(__file__), "..", "hooks", "sr_depth_gate.py")


def _jsonl(lines):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as fh:
        for o in lines:
            fh.write(o if isinstance(o, str) else json.dumps(o))
            fh.write("\n")
    return path


def _sr_prompt(changed):
    bullets = "".join(f"  - {c}\n" for c in changed)
    return ("Review this change for security vulnerabilities.\n\n"
            f"Changed files (you may Read these):\n{bullets}\n=== DIFF")


def run_hook(lines):
    tp = _jsonl(lines)
    try:
        p = subprocess.run(["python3", HOOK, "--hook"],
                           input=json.dumps({"transcript_path": tp}),
                           capture_output=True, text=True)
        return p
    finally:
        os.unlink(tp)


ok = True


def expect_block(lines, label, want=True):
    global ok
    p = run_hook(lines)
    blocked = bool(p.stdout.strip()) and '"decision"' in p.stdout and '"block"' in p.stdout
    if p.returncode != 0:
        ok = False
        print(f"  ✗ FAIL exit={p.returncode} (must be 0) :: {label}")
    if blocked != want:
        ok = False
        print(f"  ✗ FAIL want_block={want} got={blocked} :: {label} :: {p.stdout[:120]}")


def U(content):  # user message
    return {"type": "user", "message": {"content": content}}


def A_tools(*tools):  # assistant with tool_use blocks
    return {"type": "assistant", "message": {"content": list(tools)}}


def read(fp):
    return {"type": "tool_use", "name": "Read", "input": {"file_path": fp}}


def struct(findings):
    return {"type": "tool_use", "name": "StructuredOutput", "input": {"findings": findings}}


# --- SHOULD block: clean verdict, a changed file never opened ---
expect_block([U(_sr_prompt(["a/x.py"])), A_tools(struct([]))],
             "clean, zero reads")
expect_block([U(_sr_prompt(["a/x.py"])), A_tools(read("/repo/b/x.py"), struct([]))],
             "basename collision: read b/x.py, changed a/x.py")
expect_block([U(_sr_prompt(["a/x.py"])),
              A_tools(struct([{"sev": "high"}]), struct([]))],
             "last StructuredOutput wins: draft findings then clean, unread")
expect_block([U(_sr_prompt(["a/x.py", "b/y.py"])), A_tools(read("/repo/a/x.py"), struct([]))],
             "one of two changed files unread")

# --- should NOT block ---
expect_block([U(_sr_prompt(["a/x.py"])), A_tools(read("/home/u/repo/a/x.py"), struct([]))],
             "clean and the changed file was read (abs suffix match)", want=False)
expect_block([U(_sr_prompt(["a/x.py"])), A_tools(struct([{"sev": "high"}]))],
             "findings verdict (already did work)", want=False)
expect_block([U("just a normal chat, not a review"), A_tools(struct([]))],
             "not a security-review session", want=False)
expect_block(["null", "42", "[1,2]", "not json",
              U(_sr_prompt(["a/x.py"])), A_tools(read("/repo/a/x.py"), struct([]))],
             "malformed/non-object jsonl lines fail open + still parse", want=False)

# --- stop_hook_active re-entry guard: never block even on a failing session ---
_g = _jsonl([U(_sr_prompt(["a/x.py"])), A_tools(struct([]))])
_p = subprocess.run(["python3", HOOK, "--hook"],
                    input=json.dumps({"transcript_path": _g, "stop_hook_active": True}),
                    capture_output=True, text=True)
os.unlink(_g)
if _p.returncode != 0 or _p.stdout.strip():
    ok = False
    print(f"  ✗ FAIL stop_hook_active must be silent :: exit={_p.returncode} out={_p.stdout[:80]}")

# --- missing/empty transcript_path fails open ---
_p2 = subprocess.run(["python3", HOOK, "--hook"], input=json.dumps({}),
                     capture_output=True, text=True)
if _p2.returncode != 0 or _p2.stdout.strip():
    ok = False
    print(f"  ✗ FAIL empty payload must be silent :: exit={_p2.returncode}")

print("sr_depth_gate: OK" if ok else "sr_depth_gate: FAILURES ABOVE")
raise SystemExit(0 if ok else 1)
