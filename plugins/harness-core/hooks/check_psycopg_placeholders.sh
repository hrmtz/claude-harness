#!/usr/bin/env bash
# check_psycopg_placeholders.sh — PreToolUse Write/Edit hook
#
# Blocks two psycopg misuses that raise at execute() time, i.e. AFTER the surrounding gate
# has shipped and been reviewed. Both bit gh #363 on 2026-07-17, and both had the same
# devastating shape: the check raised before it ever queried, so the gate had **never once
# run green** — indistinguishable from having no gate at all, while reading as a gate.
#
#   1. utility-with-params
#        cur.execute("SET statement_timeout = %s", (t,))
#        → syntax error at or near "$1"
#      PG utility statements (SET/SHOW/RESET/...) take no bind params. The placeholder
#      syntax is fine; the statement type is not. Inline the value instead:
#        cur.execute(f"SET statement_timeout = '{int(n)}s'")
#      (core/retriever_pg.py documents this for `SET LOCAL ivfflat.probes`.)
#      Victim: _pro_flip_optiond_363.sh's identity sentinel — the gate that exists so a
#      mis-pointed DSN cannot reach production.
#
#   2. bare-%-with-params
#        cur.execute("... application_name LIKE 'mafutsu:pro%' AND t >= %s", (v,))
#        → only '%s', '%b', '%t' are allowed as placeholders, got '%'
#      Once params are passed psycopg parses the WHOLE string, so a literal % must be
#      doubled ('mafutsu:pro%%'). With NO params, % is literal and safe.
#      Victim: the gate-4 routing hard-verify — the check written specifically because a
#      bare count(*) was "VACUOUS".
#
# NOT flagged (verified against the live repo, 2026-07-17):
#   - execute(q) with no params      → % is literal (premium_optiond_stage1_runner.py:340)
#   - sqlite3 / `?` placeholders     → % never interpreted (admin/twitter.py:48, api/app.py:643)
#   - properly doubled %%            → (paper_id %% 8) = %s  (scripts/_hnsw_recall_gate.py)
# The scanner requires a psycopg-style placeholder (%s/%b/%t/%(name)s) in the query as proof
# of the driver before judging — an earlier draft without that check false-flagged all the
# sqlite3 call sites.
#
# Layer: L2 structural (harness-time, AI-bypass-proof) — sibling of check_zsh_reserved_vars.sh.
# Blocks (exit 2): both classes are always defects; there is no valid use.

set -uo pipefail

payload=$(head -c 262144 || true)
tool=$(echo "$payload" | jq -r '.tool_use_name // .tool_name // ""' 2>/dev/null || echo "")
file_path=$(echo "$payload" | jq -r '.tool_input.file_path // ""' 2>/dev/null || echo "")

[[ "$tool" == "Write" || "$tool" == "Edit" ]] || exit 0

# Scope: python, or shell scripts with embedded python heredocs (the #363 bugs lived in a
# PYHELPER heredoc inside a .sh — restricting to *.py would have missed both).
if [[ -n "$file_path" ]]; then
  [[ "$file_path" =~ \.(py|sh|bash)$ ]] || exit 0
fi

if [[ "$tool" == "Write" ]]; then
  content=$(echo "$payload" | jq -r '.tool_input.content // ""' 2>/dev/null)
else
  content=$(echo "$payload" | jq -r '.tool_input.new_string // ""' 2>/dev/null)
fi
[[ -n "$content" ]] || exit 0

# Cheap prefilter: no execute( with a comma-separated params arg → nothing to judge.
echo "$content" | grep -qE 'execute(many)?\(' || exit 0

findings=$(CONTENT="$content" python3 - <<'PY' 2>/dev/null || true
import os, re, sys

src = os.environ.get("CONTENT", "")
UTILITY = re.compile(r"^\s*(SET|SHOW|RESET|BEGIN|COMMIT|ROLLBACK|VACUUM|ANALYZE|DISCARD)\b", re.I)
PSYCOPG_PARAM = re.compile(r"%(?:s|b|t|\([^)]+\)s)")

def bare_pct(q):
    return bool(re.search(r"%(?![sbt(])", q.replace("%%", "")))

def is_psycopg(q):
    return bool(PSYCOPG_PARAM.search(q.replace("%%", "")))

out = []
# Not ast.parse: Edit payloads are usually fragments, not parseable modules. This is a lint —
# a false negative is acceptable, a false positive is not.
#
# The query arg is scanned as a run of adjacent string literals (python implicit concat), and
# the run MUST be matched piecewise. A `["\'].*?["\']` with re.S stops at the first closing
# quote, so the real #363 routing-verify bug — whose `LIKE 'mafutsu:pro%'` and `%s` sit on
# DIFFERENT physical lines — slipped straight through the first version of this hook. Both of
# the bugs this exists for are multi-line; matching only single-line literals would make the
# hook decorative.
# Triple-quoted branches must not backtrack past their first closing delimiter.
# Otherwise a params-free execute("""...""") can be joined to a later
# execute("""...%s...""", params), mixing two statements into one finding.
STR = r'(?:[rbu]*f?)(?:"""(?:(?!""")(?:[^\\]|\\.))*"""|\'\'\'(?:(?!\'\'\')(?:[^\\]|\\.))*\'\'\'|"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\')'
CALL = re.compile(r'execute(?:many)?\(\s*((?:' + STR + r'\s*)+)\s*,', re.S)
LIT = re.compile(STR, re.S)

for m in CALL.finditer(src):
    blob = m.group(1)
    parts = []
    for lit in LIT.finditer(blob):
        t = lit.group(0)
        t = re.sub(r'^[rbu]*f?', '', t)
        for qq in ('"""', "'''", '"', "'"):
            if t.startswith(qq) and t.endswith(qq) and len(t) >= 2 * len(qq):
                t = t[len(qq):-len(qq)]
                break
        parts.append(t)
    q = "".join(parts)
    if not q or not is_psycopg(q):
        continue
    line = src[:m.start()].count("\n") + 1
    snip = " ".join(q.split())[:74]
    if UTILITY.match(q):
        out.append(f"L{line} [utility-with-params] {snip}")
    elif bare_pct(q):
        out.append(f"L{line} [bare-%-with-params] {snip}")

print("\n".join(out))
PY
)

if [[ -n "${findings// }" ]]; then
  echo "🛑 psycopg placeholder misuse — this raises at execute() time, so the check around it"
  echo "   never runs. A gate that never runs green reads as a gate but is not one."
  echo ""
  echo "   Matched:"
  echo "$findings" | sed 's/^/     /'
  echo ""
  echo "   utility-with-params  SET/SHOW take no bind params → syntax error at or near \"\$1\""
  echo "     ✗ cur.execute(\"SET statement_timeout = %s\", (t,))"
  echo "     ✓ cur.execute(f\"SET statement_timeout = '{int(n)}s'\")"
  echo ""
  echo "   bare-%-with-params   params present → psycopg parses the whole string"
  echo "     ✗ \"... LIKE 'mafutsu:pro%' AND t >= %s\", (v,)"
  echo "     ✓ \"... LIKE 'mafutsu:pro%%' AND t >= %s\", (v,)"
  echo ""
  echo "   (No params? % is literal — safe. sqlite3/? — safe, not flagged.)"
  echo ""
  echo "   Root incident: gh #363, 2026-07-17 — the /pro cutover's identity sentinel AND the"
  echo "   gate-4 routing hard-verify had each never once run green. The flip then reported"
  echo "   COMPLETE while production kept serving the old corpus."
  exit 2
fi

exit 0
