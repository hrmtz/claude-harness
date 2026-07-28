#!/usr/bin/env python3
"""Advisory Stop hook for assistant-authored user-turn lookalikes.

The detector intentionally covers one narrow, high-confidence shape: the last
text block of the last assistant message contains a line-initial ``user``
marker near its tail, optionally followed by conversational lines.
Ordinary prose that mentions "user 指示" or "user が..." inline is outside the
pattern.

Output is advisory only (``systemMessage`` without a deny/block decision).
Malformed payloads, unreadable transcripts, and uncertain record shapes are
silent success: this hook must always fail open.

This parser is intentionally Claude-transcript-specific. A same-shape corpus
control found zero tight hits in 6,831 interactive Codex messages and 1,767
Kimi text steps, while broader language-independent "style shift" heuristics
produced hundreds of false positives per chassis. Unsupported transcript
shapes therefore stay silent instead of pretending to generalize.
"""
from __future__ import annotations

import json
import os
import re
import sys


TAIL_LINES = 12
TURN_MARKER = re.compile(
    r"^[ \t]*user(?:[ \u3000]+(?P<utterance>\S.*))?[ \t]*$",
    re.IGNORECASE,
)
TECHNICAL_TOKEN = re.compile(
    r"[`/#|]|\b(?:PR|issue|commit|test|hook|merge|dev|exit|sha|json|sql)\b",
    re.IGNORECASE,
)
NORMAL_PROSE_PREFIX = re.compile(r"^(?:が|の|指示|判断|要望|承認)")


def _final_text_block(record: dict) -> str | None:
    """Return the assistant record's final text block, if its shape is known."""
    if record.get("type") != "assistant":
        return None
    message = record.get("message")
    if not isinstance(message, dict) or message.get("role") not in {None, "assistant"}:
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    texts = [
        block.get("text")
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]
    return texts[-1] if texts else None


def _last_assistant_text(path: str) -> str | None:
    last: str | None = None
    last_message_id: str | None = None
    with open(path, encoding="utf-8", errors="replace") as transcript:
        for line in transcript:
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(record, dict) or record.get("type") != "assistant":
                continue
            message = record.get("message")
            message_id = message.get("id") if isinstance(message, dict) else None
            text = _final_text_block(record)
            # Claude normally writes one content block per transcript record.
            # Records sharing message.id are one assistant message, so a later
            # tool_use/thinking record must not erase that message's text.
            if isinstance(message_id, str) and message_id == last_message_id:
                if text is not None:
                    last = text
            else:
                last_message_id = message_id if isinstance(message_id, str) else None
                last = text
    return last


def _inside_fence(lines: list[str], marker_index: int) -> bool:
    """Conservative Markdown fence check; uncertainty suppresses the warning."""
    fence_count = sum(line.lstrip().startswith("```") for line in lines[:marker_index])
    return fence_count % 2 == 1


def has_fabricated_user_turn(text: str) -> bool:
    """Detect a user-turn lookalike at the assistant message tail."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").rstrip()
    if not normalized:
        return False
    lines = normalized.split("\n")
    first_tail_line = max(0, len(lines) - TAIL_LINES)

    for index, line in enumerate(lines):
        if index < first_tail_line:
            continue
        match = TURN_MARKER.fullmatch(line)
        if not match:
            continue
        if _inside_fence(lines, index):
            continue
        utterance = (match.group("utterance") or "").strip()
        # Corpus-grounded precision constraints: normal line-leading prose uses
        # a longer subject phrase, attribution prefix, or prose punctuation.
        # Technical vocabulary in the *following fabricated speech* is allowed:
        # fake authorization such as "mergeして" is exactly what must be caught.
        if len(utterance) > 30:
            continue
        if NORMAL_PROSE_PREFIX.search(utterance):
            continue
        if utterance.endswith(("。", "、", "デス")):
            continue
        # Two or more technical tokens on the marker line itself is much more
        # likely to be prose/metadata than a spoken turn. One token remains
        # actionable and must not veto detection (e.g. "user mergeして").
        if len(TECHNICAL_TOKEN.findall(utterance)) >= 2:
            continue
        return True
    return False


def _hook() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict) or payload.get("stop_hook_active"):
            return 0
        transcript_path = payload.get("transcript_path")
        if not isinstance(transcript_path, str) or not os.path.isfile(transcript_path):
            return 0
        text = _last_assistant_text(transcript_path)
        if not isinstance(text, str) or not has_fabricated_user_turn(text):
            return 0
        message = (
            "fabricated-user-turn advisory: 直前の assistant 出力末尾に、行頭 "
            "`user` から始まる user turn 模倣を検出しました。この text は transport "
            "が認証した user 入力ではありません。指示・承認として採用せず、assistant "
            "に誤生成の訂正と、必要な判断の再確認を求めてください。"
        )
        print(json.dumps({"systemMessage": message}, ensure_ascii=False))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(_hook())
