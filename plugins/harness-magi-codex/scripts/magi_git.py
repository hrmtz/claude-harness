#!/usr/bin/env python3
"""Deterministic Git object reads that cannot invoke ambient diff/textconv helpers."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def sanitized_environment() -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    return environment


def run_git(
    repo: Path,
    *args: str,
    text: bool = True,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "core.attributesFile=/dev/null",
            *args,
        ],
        capture_output=True,
        text=text,
        check=False,
        env=sanitized_environment(),
    )
