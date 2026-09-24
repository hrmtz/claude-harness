#!/usr/bin/env python3
"""ローカル設定 file を読む 2 つの経路が同じ判定になることを pin する。

incident 記録の宛先は shell 側 (credential_leak_followup.sh) と python 側
(credential_scrub.py) の両方が独立に読む。片方だけが受理すると、通知文は
「filing 有効」と告げながら実際には filed されない状態になる。行末の違いで
そこが割れていたので、同じ入力に対して同じ答えを返すことを test で固定する。

判定だけを見るため、shell 側の読み取り部分と python 側の読み取り部分をそれぞれ
切り出して評価する。GitHub を叩く経路には入らない。
"""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
FOLLOWUP = CORE / "hooks" / "credential_leak_followup.sh"
SCRUB = CORE / "hooks" / "credential_scrub.py"

SLUG_RE = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"

# shell 側の読み取りを、本体と同じ順序で再現する。
# (本体を丸ごと走らせると gh 呼び出しまで進むため、読み取り部分だけを取り出す)
SHELL_READER = r"""
set -u
LOCAL_REPO_FILE="$1"
LOCAL_REPO=""
IFS= read -r LOCAL_REPO < "$LOCAL_REPO_FILE" || :
LOCAL_REPO="${LOCAL_REPO%$'\r'}"
if printf '%s' "$LOCAL_REPO" | grep -qE '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'; then
    printf 'accept %s\n' "$LOCAL_REPO"
else
    printf 'reject\n'
fi
"""


def shell_decision(path):
    result = subprocess.run(
        ["bash", "-c", SHELL_READER, "reader", str(path)],
        capture_output=True, text=True, timeout=15,
    )
    return result.stdout.strip()


def python_decision(path):
    # python 側と同じ読み方 (splitlines の先頭行 + 同じ slug regex)
    try:
        candidate = Path(path).read_text(encoding="utf-8").splitlines()[0]
    except (OSError, UnicodeError, IndexError):
        return "reject"
    return f"accept {candidate}" if re.fullmatch(SLUG_RE, candidate) else "reject"


class LocalConfigReaderParity(unittest.TestCase):
    def check(self, contents, expected, *, binary=False):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "credential-leak-issue-repo"
            if binary:
                path.write_bytes(contents)
            else:
                path.write_text(contents, encoding="utf-8")
            shell = shell_decision(path)
            python = python_decision(path)
            self.assertEqual(
                shell, python,
                f"readers disagree for {contents!r}: shell={shell!r} python={python!r}",
            )
            self.assertEqual(shell, expected, f"unexpected decision for {contents!r}")

    def test_line_endings_do_not_change_the_answer(self):
        self.check("owner/repo\n", "accept owner/repo")
        self.check("owner/repo\r\n", "accept owner/repo")
        self.check("owner/repo", "accept owner/repo")

    def test_trailing_content_after_the_first_line_is_ignored_the_same_way(self):
        self.check("owner/repo\nsecond-line\n", "accept owner/repo")
        self.check("owner/repo\r\nsecond-line\r\n", "accept owner/repo")

    def test_invalid_values_are_rejected_by_both(self):
        self.check("", "reject")
        self.check("\n", "reject")
        self.check("not-a-slug\n", "reject")
        self.check("owner/repo extra\n", "reject")
        self.check("owner//repo\n", "reject")

    def test_non_utf8_is_rejected_by_both(self):
        self.check(b"\xff\xfe owner/repo\n", "reject", binary=True)

    def test_both_readers_still_strip_carriage_returns_in_source(self):
        # 実装が分岐に戻ったら落ちるようにする。上の parity test は切り出した
        # reader を評価するので、本体側の一致もここで確かめる。
        self.assertIn("%$'\\r'", FOLLOWUP.read_text(encoding="utf-8"))
        self.assertIn("splitlines()", SCRUB.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
