#!/usr/bin/env python3
"""
kimi_session_scrub.py — periodic credential scrubber for Kimi Code CLI sessions.

Kimi has no PostToolUse hook, so this script runs out-of-band (cron or systemd
timer) and scans every active Kimi wire.jsonl for known credential literals.
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


def _extract_record_corpus(record: dict[str, Any]) -> str:
    """Extract only the credential-bearing text from a wire.jsonl record.

    We deliberately skip the large `config.update.systemPrompt` record and any
    other fields that are unlikely to carry credentials but are expensive to
    hash (large code/docs pasted into context are still scanned via message
    content, which is the only place user-provided blobs typically appear).
    """
    rec_type = record.get("type")
    parts: list[str] = []

    if rec_type == "config.update":
        # System prompt is large and static; skip it entirely.
        return ""

    if rec_type == "tool_call":
        tool_input = record.get("tool_input") or {}
        if isinstance(tool_input, dict):
            # Bash command strings are the main leak vector.
            cmd = tool_input.get("command")
            if isinstance(cmd, str):
                parts.append(cmd)

    elif rec_type == "tool_response":
        tr = record.get("tool_response") or {}
        if isinstance(tr, dict):
            for field in ("stdout", "stderr", "output", "content"):
                val = tr.get(field)
                if val is not None:
                    _collect_strings(val, parts)

    elif rec_type in ("user", "assistant"):
        # User/admission prompts and assistant replies may contain pasted secrets.
        content = record.get("content") or record.get("message", {}).get("content")
        if content is not None:
            _collect_strings(content, parts)

    # Fallback: for any other record type, still scan a few known keys but avoid
    # dumping the entire record (which would re-include system prompts via metadata).
    else:
        for key in ("prompt", "content", "text"):
            val = record.get(key)
            if val is not None:
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
