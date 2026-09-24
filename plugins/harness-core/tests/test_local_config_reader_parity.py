#!/usr/bin/env python3
"""ローカル設定 file を読む 2 つの経路が同じ判定になることを pin する。

incident 記録の宛先は **3 箇所** が独立に読む:
  * credential_leak_followup.sh   実際に filing する経路
  * credential_scrub.py           leak 検出後の通知文
  * credential_value_scrub.sh     resume context (続行してよいと告げる出口)

どれか 1 つだけが受理すると、「記録済みだから続行してよい」と告げながら実際には
記録されていない状態になる。行末の違いでそこが割れていた。初稿は 2 つだけを揃えており、
cross-family review が 3 つ目を見つけた (#325 BLOCK)。**reader の数を数える工程自体が
抜けていた** ので、この test は 3 経路を並べて同じ答えになることを固定する。

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
VALUE_SCRUB = CORE / "hooks" / "credential_value_scrub.sh"
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
        self.assertIn("%$'\\r'", VALUE_SCRUB.read_text(encoding="utf-8"))

    def test_every_reader_of_this_config_is_covered(self):
        # reader を数え損ねたのが #325 BLOCK の原因だった。設定 file を参照する
        # hook が増えたら、この test が気付けるようにしておく。
        hooks = sorted(
            p.name for p in (CORE / "hooks").iterdir()
            if p.is_file() and "credential-leak-issue-repo" in p.read_text(
                encoding="utf-8", errors="replace")
        )
        self.assertEqual(
            hooks,
            ["credential_leak_followup.sh", "credential_scrub.py",
             "credential_value_scrub.sh"],
            "a hook started reading the local issue-repo config; add it to this parity test",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
