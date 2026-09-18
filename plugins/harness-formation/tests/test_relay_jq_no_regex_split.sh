#!/usr/bin/env bash
# Regression guard for the WSL-host-heavy incident (2026-09-18):
# mailbox_relay.sh computed the mailbox seq high-water with
#   jq -Rs '[splits("\n") | ...]'
# jq 1.7's splits() is the ONIGURUMA regex splitter; on a slurped ~1MB log it
# took ~4s/call vs ~0.01s for the literal split("\n")[] (~400x). The relay
# driver re-runs this every <=1s (inotify -t 1 backstop), so a 4s scan burns
# one core continuously. Two live relays pegged two cores for days.
#
# The pathology is invisible on small fixtures (a tiny log parses fast even with
# splits), so this is a STATIC guard: no jq call in bin/ or lib/ may split a
# literal newline via the regex splits(). Use split("\n") instead.
#
# Run: bash plugins/harness-formation/tests/test_relay_jq_no_regex_split.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$HERE/.."
FAIL=0

# Match splits("\n") allowing arbitrary whitespace, single/double quotes, and
# either an escaped-newline literal (\n) or a real newline in the pattern arg.
hits="$(grep -rnE 'splits\(\s*["'"'"']\\?n' "$ROOT/bin" "$ROOT/lib" 2>/dev/null || true)"
if [[ -n "$hits" ]]; then
  printf '\033[31mFAIL\033[0m regex splits("\\n") reintroduced — use split("\\n")[] (jq splits() is ~400x slower on large slurp):\n%s\n' "$hits"
  FAIL=1
else
  printf '\033[32mPASS\033[0m no jq splits("\\n") regex-split in bin/ or lib/\n'
fi

exit "$FAIL"
