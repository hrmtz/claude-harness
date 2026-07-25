#!/usr/bin/env python3
"""Atomically rename one filesystem entry without replacing or nesting into dst."""

from __future__ import annotations

import ctypes
import os
import sys


AT_FDCWD = -100
RENAME_NOREPLACE = 1


def rename_noreplace(source: str, destination: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        renameat2 = libc.renameat2
    except AttributeError as exc:
        raise OSError("renameat2 is unavailable; refusing non-atomic publication") from exc
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        AT_FDCWD,
        os.fsencode(source),
        AT_FDCWD,
        os.fsencode(destination),
        RENAME_NOREPLACE,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: magi_rename_noreplace.py <source> <destination>", file=sys.stderr)
        return 64
    try:
        rename_noreplace(argv[1], argv[2])
    except OSError as exc:
        print(f"magi-rename-noreplace: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
