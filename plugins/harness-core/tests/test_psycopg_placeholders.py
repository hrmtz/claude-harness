#!/usr/bin/env python3
"""Regression tests for the psycopg placeholder PreToolUse guard."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


HOOK = Path(__file__).parents[1] / "hooks" / "check_psycopg_placeholders.sh"


def run_hook(content: str, tool_name: str = "Write") -> subprocess.CompletedProcess[str]:
    content_field = "content" if tool_name == "Write" else "new_string"
    payload = {
        "tool_name": tool_name,
        "tool_input": {"file_path": "/tmp/repro.py", content_field: content},
    }
    return subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("tool_name", ["Write", "Edit"])
@pytest.mark.parametrize("method", ["execute", "executemany"])
def test_separate_statements_do_not_share_percent_evidence(
    method: str, tool_name: str
) -> None:
    """The exact #248 shape must stay within each execute call's boundary."""
    content = f'''
import psycopg

def main():
    with psycopg.connect("") as c:
        cur = c.cursor()
        cur.{method}("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
              AND (table_name ILIKE '%log%' OR table_name ILIKE '%ingest%')
            ORDER BY table_name
        """)
        for t in [r[0] for r in cur.fetchall()]:
            cur.{method}("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = %s ORDER BY ordinal_position
            """, (t,))
'''

    result = run_hook(content, tool_name)

    assert result.returncode == 0, result.stdout + result.stderr


def test_multiline_utility_with_params_is_still_blocked() -> None:
    content = '''
cur.execute(
    """
    SET statement_timeout = %s
    """,
    (timeout,),
)
'''

    result = run_hook(content)

    assert result.returncode == 2
    assert "[utility-with-params]" in result.stdout


def test_single_quoted_triples_do_not_cross_statement_boundaries() -> None:
    content = """
cur.execute('''
    SELECT 1 WHERE 'x' LIKE '%literal%'
''')
cur.execute('''
    SELECT 1 WHERE value = %s
''', (value,))
"""

    result = run_hook(content)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("method", ["execute", "executemany"])
def test_multiline_bare_percent_with_params_is_still_blocked(method: str) -> None:
    content = f'''
cur.{method}(
    """
    SELECT *
    FROM jobs
    WHERE application_name LIKE 'mafutsu:pro%'
      AND created_at >= %s
    """,
    (cutoff,),
)
'''

    result = run_hook(content)

    assert result.returncode == 2
    assert "[bare-%-with-params]" in result.stdout


def test_single_quoted_triple_true_positive_is_still_blocked() -> None:
    content = """
cur.execute('''
    SELECT 1
    WHERE application_name LIKE 'mafutsu:pro%'
      AND created_at >= %s
''', (cutoff,))
"""

    result = run_hook(content)

    assert result.returncode == 2
    assert "[bare-%-with-params]" in result.stdout
