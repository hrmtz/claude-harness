#!/usr/bin/env python3
"""Claude-only response counter and durable, bounded continuation checkpoint.

Native auto-compaction controls context size. This hook never restarts a CLI,
injects input, consumes mail, or treats a notification as an ASK acknowledgement.
Only usage/identity metadata is retained from transcripts; message bodies are not.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

FIELDS = ("goal", "unfinished", "next_step", "files", "validation", "jobs", "asks", "receipts")


def atomic(path, value):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".checkpoint-")
    try:
        with os.fdopen(fd, "w") as out:
            json.dump(value, out, ensure_ascii=False)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def scan(path, state):
    """Incremental scan; leave partial JSONL tails for the next invocation."""
    with open(path, "rb") as stream:
        stat = os.fstat(stream.fileno())
        identity = [stat.st_dev, stat.st_ino]
        if state.get("identity") != identity or stat.st_size < state.get("offset", 0):
            state.clear()
            state["identity"] = identity
        stream.seek(state.get("offset", 0))
        for line in stream:
            if not line.endswith(b"\n"):
                break
            state["offset"] = state.get("offset", 0) + len(line)
            try:
                row = json.loads(line)
                if row.get("type") == "system" and row.get("subtype") == "compact_boundary":
                    state.update(responses={}, reminded=False, context_tokens=0)
                    state["generation"] = state.get("generation", 0) + 1
                if row.get("type") != "assistant":
                    continue
                message = row.get("message") or {}
                usage = message.get("usage")
                mid = message.get("id")
                if not isinstance(usage, dict) or not isinstance(mid, str) or not mid:
                    continue
                counters = [usage.get(k, 0) for k in (
                    "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")]
                if not all(isinstance(n, int) and n >= 0 for n in counters):
                    continue
                if not sum(counters):
                    continue  # synthetic/error messages are not paid responses
                key = json.dumps([mid, row.get("requestId")])
                responses = state.setdefault("responses", {})
                responses[key] = max(responses.get(key, 0), sum(counters))
                state["context_tokens"] = responses[key]
            except (ValueError, TypeError, AttributeError):
                continue
    return len(state.get("responses", {}))


def main():
    chassis = os.environ.get("HARNESS_CHASSIS", "")
    if chassis and chassis != "claude":
        return
    if not chassis and os.environ.get("PLUGIN_ROOT"):
        return  # legacy native Codex host; dispatcher never invents this variable
    payload = json.load(sys.stdin)
    session = payload.get("session_id")
    transcript = payload.get("transcript_path")
    event = payload.get("hook_event_name")
    if not isinstance(session, str) or not session or not isinstance(transcript, str):
        return
    # Hash the opaque runtime ID: no path traversal or alias collision.
    directory = Path.home() / ".claude" / "context-checkpoints" / hashlib.sha256(session.encode()).hexdigest()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_path = directory / "state.json"
    packet_path = directory / "handoff.json"
    with (directory / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        try:
            count = scan(transcript, state)
        except FileNotFoundError:
            count = len(state.get("responses", {}))  # startup may precede JSONL creation
        packet = None
        if packet_path.exists() and packet_path.stat().st_size <= 8192:
            try:
                candidate = json.loads(packet_path.read_text())
                if (isinstance(candidate, dict)
                        and all(isinstance(candidate.get(k), str) and candidate[k].strip()
                                for k in ("goal", "next_step"))
                        and all(isinstance(candidate.get(k), list)
                                and all(isinstance(item, str) for item in candidate[k])
                                for k in FIELDS if k not in ("goal", "next_step"))):
                    packet = candidate
            except (ValueError, OSError):
                pass
        if event == "PreCompact":
            # Keep a point-in-time packet and references, not copies of mailbox
            # bodies, process environments, prompts, or credential-bearing files.
            atomic(directory / "before-compact.json", {
                "session_id": session, "cwd": payload.get("cwd"), "at": time.time(),
                "responses": count, "context_tokens": state.get("context_tokens", 0),
                "formation": {k: os.environ.get(k) for k in (
                    "FORMATION_SELF", "FORMATION_PARENT", "FORMATION_SESSION_ID")},
                "handoff": packet,
            })
        message = None
        if event == "SessionStart":
            message = (
                f"Continuation checkpoint: {packet_path}. At task boundaries write a short JSON "
                f"object (<=8192 bytes) with keys {', '.join(FIELDS)}. "
                "goal/next_step: nonempty strings; others: string arrays, [] for none. "
                "Keep exact job/ASK/review IDs and log/file paths; never credentials. "
                "After compact/resume verify these references against live state before continuing. "
                "A checkpoint or compact is not task completion or permission."
            )
            if packet is not None:
                message += (f" A saved handoff exists (mtime={packet_path.stat().st_mtime}); "
                            "read it now. This is a historical snapshot, not current-state evidence.")
        elif event in ("PostToolUse", "Stop") and count >= 50 and not state.get("reminded"):
            state["reminded"] = True
            message = (
                f"{count} API responses since compact; refresh {packet_path} at this task boundary. "
                f"Required keys: {', '.join(FIELDS)}. Keep it <=8192 bytes; preserve remaining work, "
                "jobs, ASK IDs and exact verification receipts. Native compact manages the context window."
            )
        state["response_count"] = count
        state["configured_window_env"] = os.environ.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW")
        atomic(state_path, state)
        if message:
            if event == "Stop":
                print(json.dumps({"systemMessage": message}))
            else:
                print(json.dumps({"hookSpecificOutput": {
                    "hookEventName": event, "additionalContext": message}}))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError, AttributeError):
        pass  # A cost-control observer must never interrupt authorized work.
