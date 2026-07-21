#!/usr/bin/env python3
"""
kimi_session_scrub.py — periodic credential scrubber for Kimi Code CLI sessions.

Kimi's native PostToolUse hook (>= 0.28) is observe-only — it cannot block and
fires per tool call — so this script runs out-of-band (cron or systemd
timer) as the standing detective layer, scanning every active Kimi wire.jsonl
for known credential literals.
Matches are redacted in-place with a generic <REDACTED> marker.

It reuses the HMAC manifest/salt system from claude-harness harness-core
(~/projects/claude-harness/plugins/harness-core/hooks/credential_scrub.py) so
no duplicate secret storage is needed.

Performance note: instead of scanning the entire raw wire.jsonl (which includes
large system prompts), we parse JSONL and extract only the credential-bearing
fields (Bash command, tool stdout/stderr/output, message content). This keeps
the HMAC budget bounded.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
KIMI_SESSIONS = Path.home() / ".kimi-code" / "sessions"
KILL_SWITCH = Path.home() / ".kimi-code" / "harness-scrub.disabled"

# wire.jsonl is append-only and may be actively written by a live Kimi session.
# Rewriting it (read-all + os.replace) under an open writer drops concurrently
# appended records and can strand the writer on the unlinked inode, corrupting
# the transcript (code-review #52). Only touch files that have been idle at
# least this long, and re-check mtime just before redacting; a still-active
# file is left for a later run.
QUIESCENCE_SECONDS = float(os.environ.get("HARNESS_KIMI_SCRUB_QUIESCENCE", "120"))

# Reuse claude-harness credential_scrub module.
CORE_HOOKS = Path.home() / "projects" / "claude-harness" / "plugins" / "harness-core" / "hooks"
sys.path.insert(0, str(CORE_HOOKS))

try:
    import credential_scrub as cs  # type: ignore
except Exception as exc:
    # Fail-safe: if the dependency is missing, log and exit 0.
    print(f"kimi_session_scrub: failed to import credential_scrub: {exc}", file=sys.stderr)
    sys.exit(0)


# ----------------------------------------------------------------------------
# Corpus extraction
# ----------------------------------------------------------------------------
def _collect_strings(node: Any, out: list[str]) -> None:
    """Recursively collect all string leaves from a JSON value."""
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, list):
        for item in node:
            _collect_strings(item, out)
    elif isinstance(node, dict):
        for v in node.values():
            _collect_strings(v, out)


# Record types that never carry user/tool credential text and are either large
# (static system prompt) or pure metrics/schema — skipped wholesale to bound the
# HMAC budget. Everything NOT in this set is scanned by collecting all string
# leaves, so the scrubber is robust to Kimi wire.jsonl schema drift (the earlier
# version dispatched on record types — tool_call/tool_response/user/assistant —
# that do not exist in real wire.jsonl, so it scanned nothing: code-review #52).
_SKIP_RECORD_TYPES = frozenset({
    "config.update",            # systemPrompt: large + static
    "usage.record",            # token metrics
    "tools.update_store",       # tool schema definitions
    "tools.set_active_tools",   # tool name lists
    "metadata",                 # session metadata
    "permission.set_mode",
    "permission.record_approval_result",
})

# Within context.append_loop_event, these .event.type values are pure timing /
# control with no credential text.
_SKIP_EVENT_TYPES = frozenset({
    "step.begin",
    "step.end",
})


def _extract_record_corpus(record: dict[str, Any]) -> str:
    """Extract the credential-bearing text from a wire.jsonl record.

    Denylist approach: skip known-noise record types entirely, otherwise collect
    every string leaf. Real Kimi record types carry credential text as:
      - context.append_loop_event / .event  (tool.call args incl. Bash .command,
        Write .content, Edit .old/new_string; tool.result .output; content.part
        .text/.think)
      - context.append_message / .message   (assistant content + toolCalls)
      - turn.prompt / .input                 (user prompt)
    Collecting all string leaves from these keeps us correct even if the nested
    shapes change again.
    """
    rec_type = record.get("type")
    if rec_type in _SKIP_RECORD_TYPES:
        return ""

    parts: list[str] = []

    if rec_type == "context.append_loop_event":
        event = record.get("event")
        if isinstance(event, dict):
            if event.get("type") in _SKIP_EVENT_TYPES:
                return ""
            _collect_strings(event, parts)
        else:
            _collect_strings(event, parts)
    else:
        # append_message, turn.prompt, and any unknown/new type: scan the whole
        # record except the type tag. Unknown types are scanned deliberately so
        # a future schema change cannot silently drop credentials.
        for key, val in record.items():
            if key == "type":
                continue
            _collect_strings(val, parts)

    return "\n".join(parts)


def extract_corpus(path: Path) -> bytes:
    """Parse a Kimi wire.jsonl and return the credential-bearing corpus as bytes."""
    try:
        with path.open("r", encoding="utf-8", errors="surrogateescape") as f:
            lines = f.readlines()
    except OSError as exc:
        cs.hook_log(f"kimi_scrub read error {path}: {type(exc).__name__}")
        return b""

    parts: list[str] = []
    for line in lines:
        stripped = line.rstrip("\n")
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            # Malformed line; leave it for redact_jsonl to handle.
            continue
        if isinstance(record, dict):
            corpus = _extract_record_corpus(record)
            if corpus:
                parts.append(corpus)

    return "\n".join(parts).encode("utf-8", errors="surrogateescape")


# ----------------------------------------------------------------------------
# Scrubbing
# ----------------------------------------------------------------------------
def scan_file(path: Path) -> tuple[list[tuple[bytes, list[str]]], bool]:
    """Read a wire.jsonl and scan it for known credential literals."""
    content = extract_corpus(path)
    if not content:
        return [], True

    salt = cs.load_salt()
    if salt is None:
        return [], True

    by_length, algorithm = cs.load_manifests()
    if not by_length or not algorithm:
        return [], True

    if not cs.algorithm_available(algorithm):
        cs.hook_log(f"kimi_scrub algorithm_unavailable {algorithm}")
        return [], True

    return cs.scan_output(content, by_length, salt, algorithm)


def scrub_file(path: Path) -> tuple[int, bool]:
    """Scan a single Kimi wire.jsonl and redact any matched credential literals.

    Returns (replaced_count, scan_complete).
    """
    try:
        hits, scan_complete = scan_file(path)
    except Exception as exc:
        cs.hook_log(f"kimi_scrub scan error on {path}: {type(exc).__name__}")
        return 0, True

    if not hits:
        return 0, scan_complete

    # Race guard: if the file changed between the scan and now, a live session is
    # writing it — skip this cycle so we never os.replace() over an active writer.
    # A later run (once the session is idle) will redact it.
    try:
        if (time.time() - path.stat().st_mtime) < QUIESCENCE_SECONDS:
            cs.hook_log(f"kimi_scrub skip active (mtime<{QUIESCENCE_SECONDS:.0f}s) {path}")
            return 0, scan_complete
    except OSError:
        return 0, scan_complete

    literals = [w for w, _ in hits]
    try:
        replaced = cs.redact_jsonl(path, literals)
    except Exception as exc:
        cs.hook_log(f"kimi_scrub redact error on {path}: {type(exc).__name__}")
        return 0, scan_complete

    if replaced > 0:
        key_names = sorted({k for _, names in hits for k in names})
        cs.hook_log(
            f"kimi_scrub redacted path={path} matches={len(hits)} "
            f"replaced={replaced} keys={','.join(key_names[:20])}"
        )

    return replaced, scan_complete


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main() -> int:
    if KILL_SWITCH.exists():
        return 0

    if not KIMI_SESSIONS.is_dir():
        return 0

    total_replaced = 0
    files_scanned = 0
    incomplete = 0

    start = time.monotonic()
    for wire in sorted(KIMI_SESSIONS.glob("*/*/agents/main/wire.jsonl")):
        files_scanned += 1
        try:
            replaced, scan_complete = scrub_file(wire)
            total_replaced += replaced
            if not scan_complete:
                incomplete += 1
        except Exception as exc:
            cs.hook_log(f"kimi_scrub unexpected error on {wire}: {type(exc).__name__}")

    elapsed = time.monotonic() - start
    cs.hook_log(
        f"kimi_scrub done files={files_scanned} replaced={total_replaced} "
        f"incomplete={incomplete} elapsed={elapsed:.2f}s"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException:
        try:
            cs.hook_log("kimi_scrub top_level_exception suppressed")
        except Exception:
            pass
        sys.exit(0)
