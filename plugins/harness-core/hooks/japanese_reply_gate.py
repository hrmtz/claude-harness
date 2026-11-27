#!/usr/bin/env python3
"""Stop hook: block a final reply that is not mainly Japanese.

Counts letters outside code (fenced blocks, inline code, URLs, paths) and blocks
when kana/kanji make up less than THRESHOLD of them. One retry only: when the
hook already blocked this stop (stop_hook_active), it lets the reply through.
Run with --self-test to check the scoring.
"""
import json
import re
import sys

THRESHOLD = 0.3
MIN_LETTERS = 8  # below this there is nothing to judge

FENCE_OPEN = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
INLINE = re.compile(r"(`{1,3})[^`\n]+?\1")
ASCII_RUN = re.compile(r"[!-~]+")
CODE_CHARS = set("/\\_:#=<>(){}[]@$%&*+|~^`'\"")
JA = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f]")
LATIN = re.compile(r"[A-Za-z]")


def strip_code(text: str) -> str:
    """Drop fenced blocks (CommonMark: close needs same char, length >= open)."""
    out, fence = [], None
    for line in text.splitlines():
        m = FENCE_OPEN.match(line)
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not line[m.end():].strip():
                fence = None
            continue
        # A backtick fence's info string cannot contain a backtick (```foo``` is inline code).
        if m and not (m.group(1)[0] == "`" and "`" in line[m.end():]):
            fence = m.group(1)
            continue
        out.append(line)
    return "\n".join(out)


def code_like(run: str) -> bool:
    """URL, path, file name or identifier: not prose. Linear, no regex backtracking."""
    word = run.strip("*_~").rstrip(".,;:!?)").lstrip("(").strip("*_~")  # markdown emphasis is prose
    if not word:
        return False
    if any(c in CODE_CHARS or c.isdigit() for c in word) or "." in word:
        return True
    if any(a.islower() and b.isupper() for a, b in zip(word, word[1:])):
        return True  # camelCase
    return len(word) >= 2 and word.isupper()  # ALLCAPS / acronyms


def ja_ratio(text: str) -> tuple[float, int]:
    text = strip_code(text)
    text = INLINE.sub(" ", text)
    text = ASCII_RUN.sub(lambda m: " " if code_like(m.group()) else m.group(), text)
    ja = len(JA.findall(text))
    latin = len(LATIN.findall(text))
    total = ja + latin
    return (ja / total if total else 1.0), total


def last_assistant_text(path: str) -> str | None:
    last_id, parts = None, []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if not isinstance(rec, dict) or rec.get("type") != "assistant":
                continue
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            texts = [c["text"] for c in content if isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str)] if isinstance(content, list) else []
            mid = msg.get("id")
            # A record without an id is its own message; only records sharing an id are joined.
            if mid is None or mid != last_id:
                last_id, parts = mid, []
            parts += texts
    return "\n".join(parts) or None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(payload, dict) or payload.get("stop_hook_active"):
        return 0
    text = payload.get("last_assistant_message")
    if not isinstance(text, str) or not text.strip():
        path = payload.get("transcript_path")
        if not isinstance(path, str):
            return 0
        try:
            text = last_assistant_text(path)
        except OSError:
            return 0
    if not text:
        return 0
    ratio, total = ja_ratio(text)
    if total < MIN_LETTERS or ratio >= THRESHOLD:
        return 0
    print(json.dumps({
        "decision": "block",
        "reason": (
            f"直前の返答が日本語になっていない (日本語比率 {ratio:.0%}、基準 {THRESHOLD:.0%})。"
            "user への返答は常に日本語 (忍殺語+原始人圧縮)。同じ内容を日本語で書き直して返せ。"
            "コード・パス・識別子はそのままでよい。"
        ),
    }, ensure_ascii=False))
    return 0


def self_test() -> None:
    assert ja_ratio("PR #442 is up. Codex is reviewing it now.")[0] < THRESHOLD
    assert ja_ratio("PR #442 を出した。Codex が review 中。")[0] >= THRESHOLD
    assert ja_ratio("修正は `workers/i18n-sync/src/index.ts` と https://example.com/a/b の通り")[0] >= THRESHOLD
    assert ja_ratio("結果:\n```\nAll tests passed in the English output block\n```\n全部通った。")[0] >= THRESHOLD
    assert ja_ratio("ok")[1] < MIN_LETTERS
    assert ja_ratio("修正作業完了。対象はsrc/index.tsを確認。 Status check passed.")[0] >= THRESHOLD
    assert ja_ratio("関数 parse_the_complete_assistant_transcript_and_validate_language を修正。")[0] >= THRESHOLD
    assert ja_ratio("結果:\n```\nlong English text here\n````\n全部通った。")[0] >= THRESHOLD
    assert ja_ratio("``a `b` English words inside``を確認した。")[0] >= THRESHOLD
    assert ja_ratio("**Please review** /tmp/a and /tmp/b.")[0] < THRESHOLD
    assert ja_ratio("```foo``` This is an English explanation of the fix and its behavior.")[0] < THRESHOLD
    assert ja_ratio("Summary: tests passed\n|構文検査|通過|\n|機能検査|通過|")[0] >= THRESHOLD
    print("self-test ok")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    else:
        sys.exit(main())
