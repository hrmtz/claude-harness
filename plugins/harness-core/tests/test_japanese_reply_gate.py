#!/usr/bin/env python3
"""Tests for japanese_reply_gate.py."""
import json
import os
import subprocess
import tempfile

HOOK = os.path.join(os.path.dirname(__file__), "..", "hooks", "japanese_reply_gate.py")


def run(payload=None, transcript_text=None):
    payload = dict(payload or {})
    path = None
    if transcript_text is not None:
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"id": "m1", "content": [{"type": "text", "text": transcript_text}]}}) + "\n")
        payload["transcript_path"] = path
    try:
        out = subprocess.run(["python3", HOOK], input=json.dumps(payload), capture_output=True, text=True, check=True).stdout
    finally:
        if path:
            os.unlink(path)
    return json.loads(out) if out.strip() else None


def test_blocks_english_reply():
    r = run({"last_assistant_message": "PR #442 is up for the six worker fixes. Codex is reviewing it now."})
    assert r and r["decision"] == "block"


def test_passes_japanese_reply_with_code_and_paths():
    text = "PR #442 を出した。`workers/i18n-sync/src/index.ts` を直した。\n```\nAll 36 tests passed\n```\n詳細は https://github.com/x/y/pull/442 を見よ。"
    assert run({"last_assistant_message": text}) is None


def test_short_reply_is_ignored():
    assert run({"last_assistant_message": "ok"}) is None


def test_one_retry_only():
    assert run({"last_assistant_message": "This is English.", "stop_hook_active": True}) is None


def test_reads_transcript_when_no_last_message():
    r = run(transcript_text="Waiting for the other three agents to finish.")
    assert r and r["decision"] == "block"


def test_bad_stdin_is_ignored():
    out = subprocess.run(["python3", HOOK], input="not json", capture_output=True, text=True)
    assert out.returncode == 0 and not out.stdout.strip()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
