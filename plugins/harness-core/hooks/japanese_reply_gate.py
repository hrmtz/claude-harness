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

FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,}).*?^[ \t]*\1[ \t]*$", re.M | re.S)
INLINE = re.compile(r"`[^`\n]*`")
URL = re.compile(r"https?://\S+|\S*/\S+|\S+\.\w{1,5}\b")
JA = re.compile(r"[぀-ヿ㐀-鿿ｦ-ﾟ]")
LATIN = re.compile(r"[A-Za-z]")


def ja_ratio(text: str) -> tuple[float, int]:
    text = FENCE.sub(" ", text)
    text = INLINE.sub(" ", text)
    text = URL.sub(" ", text)
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
            except ValueError:
                continue
            if not isinstance(rec, dict) or rec.get("type") != "assistant":
                continue
            msg = rec.get("message") or {}
            texts = [c.get("text", "") for c in msg.get("content") or [] if isinstance(c, dict) and c.get("type") == "text"]
            if msg.get("id") != last_id:
                last_id, parts = msg.get("id"), []
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
    print("self-test ok")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    else:
        sys.exit(main())
