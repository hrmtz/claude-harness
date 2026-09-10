"""Offline lifecycle hook contracts; subprocesses see only a temporary HOME."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


HOOK = Path(__file__).resolve().parents[1] / "hooks" / "context_lifecycle.py"


@pytest.mark.parametrize("host", [{"HARNESS_CHASSIS": "codex"},
                                  {"HARNESS_CHASSIS": "kimi"}, {"PLUGIN_ROOT": "/native/plugin"}])
def test_non_claude_hosts_have_no_side_effects(tmp_path, host):
    result = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({
        "session_id": "other-cli", "transcript_path": str(tmp_path / "missing"),
        "hook_event_name": "SessionStart"}), text=True, capture_output=True,
        env={"HOME": str(tmp_path), "PATH": os.defpath, **host}, check=True)
    assert result.stdout == "" and result.stderr == ""
    assert not (tmp_path / ".claude").exists()


def response(number, block="text", **usage):
    return {"type": "assistant", "requestId": f"req_{number}", "message": {
        "id": f"msg_{number}", "content": [{"type": block, "text": "BODY_NOT_TO_PERSIST"}],
        "usage": {"input_tokens": 10, "cache_creation_input_tokens": 20,
                  "cache_read_input_tokens": 900, **usage},
    }}


@pytest.fixture
def runtime(tmp_path):
    transcript = tmp_path / "session.jsonl"
    transcript.touch()
    session = "opaque/session:id"
    checkpoint = (tmp_path / ".claude" / "context-checkpoints" /
                  hashlib.sha256(session.encode()).hexdigest())

    def run(event, raw=None, **extra):
        payload = {"session_id": session, "transcript_path": str(transcript),
                   "hook_event_name": event, "cwd": str(tmp_path), **extra}
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(payload) if raw is None else raw, text=True,
            capture_output=True, timeout=5, cwd=tmp_path,
            env={"HOME": str(tmp_path), "PATH": os.defpath,
                 "FORMATION_SELF": "worker-test", "FORMATION_PARENT": "parent-test",
                 "FORMATION_SESSION_ID": "formation-test",
                 "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "100000"},
        )
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        output = json.loads(result.stdout) if result.stdout.strip() else {}
        assert "decision" not in output and "permissionDecision" not in result.stdout
        return output

    def append(*rows):
        with transcript.open("a") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")

    return transcript, checkpoint, run, append


def read_state(checkpoint):
    return json.loads((checkpoint / "state.json").read_text())


def packet():
    return {"goal": "finish validation", "unfinished": ["integration"],
            "next_step": "inspect job log", "files": ["src/change.py"],
            "validation": ["unit checks passed"], "jobs": ["job-17:logs/job.log"],
            "asks": ["ASK-42:WAITING_PARENT"], "receipts": ["review:sha-exact"]}


def test_threshold_counts_responses_once_and_reminds_once(runtime):
    transcript, checkpoint, run, append = runtime
    append(*(response(i) for i in range(49)))
    append(response(48, block="thinking"), response(48, block="tool_use", input_tokens=12))
    assert run("PostToolUse") == {}
    assert read_state(checkpoint)["response_count"] == 49
    assert read_state(checkpoint)["context_tokens"] == 932
    append(response(49))
    before = transcript.read_bytes()
    result = run("PostToolUse")["hookSpecificOutput"]
    assert result["hookEventName"] == "PostToolUse"
    assert "50 API responses" in result["additionalContext"]
    assert "ASK IDs" in result["additionalContext"]
    assert run("Stop") == {}
    assert run("PostToolUse") == {}
    assert transcript.read_bytes() == before
    assert read_state(checkpoint)["configured_window_env"] == "100000"
    assert "BODY_NOT_TO_PERSIST" not in (checkpoint / "state.json").read_text()


def test_partial_tail_malformed_rows_and_compaction_reset(runtime):
    transcript, checkpoint, run, append = runtime
    append(*(response(i) for i in range(49)))
    with transcript.open("a") as handle:
        handle.write('{"broken":\nnull\n')
        handle.write(json.dumps(response(49)))
    assert run("Stop") == {}
    previous_offset = read_state(checkpoint)["offset"]
    assert previous_offset < transcript.stat().st_size
    with transcript.open("a") as handle:
        handle.write("\n")
    assert "50 API responses" in run("Stop")["systemMessage"]
    append({"type": "system", "subtype": "compact_boundary"}, response(50))
    assert run("PostToolUse") == {}
    state = read_state(checkpoint)
    assert state["response_count"] == 1 and state["generation"] == 1
    assert state["reminded"] is False
    append(*(response(i) for i in range(51, 100)))
    assert "50 API responses" in run("Stop")["systemMessage"]


def test_precompact_snapshot_restores_references_without_changing_jobs_or_asks(runtime):
    transcript, checkpoint, run, append = runtime
    append(response(0))
    run("SessionStart")
    handoff = packet()
    (checkpoint / "handoff.json").write_text(json.dumps(handoff))
    pending = transcript.parent / "pending-ask.json"
    pending.write_text('{"id":"ASK-42","state":"WAITING_PARENT"}\n')
    draft = transcript.parent / "user-draft.txt"
    draft.write_text("UNSUBMITTED_USER_TEXT")
    original = {path: path.read_bytes() for path in (transcript, pending, draft)}
    assert run("PreCompact") == {}
    snapshot = json.loads((checkpoint / "before-compact.json").read_text())
    assert snapshot["handoff"] == handoff
    assert snapshot["responses"] == 1 and snapshot["context_tokens"] == 930
    assert snapshot["formation"] == {
        "FORMATION_SELF": "worker-test", "FORMATION_PARENT": "parent-test",
        "FORMATION_SESSION_ID": "formation-test"}
    assert snapshot["cwd"] == str(transcript.parent)
    result = run("SessionStart", source="compact")["hookSpecificOutput"]["additionalContext"]
    assert "saved handoff exists" in result
    assert "verify these references against live state" in result
    assert "not task completion or permission" in result
    assert {path: path.read_bytes() for path in original} == original
    assert "BODY_NOT_TO_PERSIST" not in (checkpoint / "before-compact.json").read_text()


def test_missing_transcript_still_restores_session_checkpoint(runtime):
    transcript, checkpoint, run, append = runtime
    append(response(0))
    run("SessionStart")
    (checkpoint / "handoff.json").write_text(json.dumps(packet()))
    result = run("SessionStart", source="resume",
                 transcript_path=str(transcript.parent / "not-created-yet.jsonl"))
    context = result["hookSpecificOutput"]["additionalContext"]
    assert str(checkpoint / "handoff.json") in context
    assert "saved handoff exists" in context
    assert read_state(checkpoint)["response_count"] == 1


@pytest.mark.parametrize("contents", [
    '{', '{}', '[]', '"' + 'x' * 8192 + '"',
    json.dumps(packet() | {"goal": " "}),
    json.dumps(packet() | {"next_step": []}),
    json.dumps(packet() | {"asks": "ASK-42"}),
    json.dumps(packet() | {"jobs": [17]}),
])
def test_invalid_or_oversized_handoff_is_not_restored(runtime, contents):
    _, checkpoint, run, _ = runtime
    run("SessionStart")
    (checkpoint / "handoff.json").write_text(contents)
    context = run("SessionStart")["hookSpecificOutput"]["additionalContext"]
    assert "saved handoff exists" not in context
    assert run("PreCompact") == {}
    assert json.loads((checkpoint / "before-compact.json").read_text())["handoff"] is None


def test_bad_hook_payload_and_missing_scan_do_not_block(runtime):
    transcript, _, run, _ = runtime
    for raw in ('{', 'null', '[]', '{}'):
        assert run("PostToolUse", raw=raw) == {}
    assert run("PostToolUse", transcript_path=str(transcript.parent / "missing")) == {}


def test_stale_handoff_is_never_presented_as_current_authority(runtime):
    _, checkpoint, run, append = runtime
    run("SessionStart")
    handoff = checkpoint / "handoff.json"
    handoff.write_text(json.dumps(packet()))
    os.utime(handoff, (1, 1))
    append({"type": "system", "subtype": "compact_boundary"})
    context = run("SessionStart", source="compact")["hookSpecificOutput"]["additionalContext"]
    # Re-reading stale references is allowed only with explicit live revalidation;
    # a hook must never ACK an ASK or declare work complete from this file.
    assert "verify these references against live state" in context
    assert "not task completion or permission" in context
    assert "mtime=1.0" in context and "historical snapshot" in context
    assert read_state(checkpoint)["generation"] == 1
