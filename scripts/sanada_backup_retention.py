#!/usr/bin/env python3
"""Apply bounded retention to top-level Sanada backup directories (gh #193)."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import os
from pathlib import Path
import shutil
import stat
import sys
import time


AUTO_DAYS = 3
NAMED_DAYS = 7


def human_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    number = float(value)
    for unit in units:
        if number < 1024 or unit == units[-1]:
            return f"{number:.1f} {unit}"
        number /= 1024
    raise AssertionError("unreachable")


def tree_info(path: Path) -> tuple[bool, int]:
    """Return (contains recursive .keep marker, allocated bytes), no symlink walk."""
    contains_keep = False
    allocated = path.lstat().st_blocks * 512
    for base, directories, files in os.walk(path, followlinks=False):
        if ".keep" in files:
            contains_keep = True
        base_path = Path(base)
        for name in directories + files:
            child = base_path / name
            try:
                allocated += child.lstat().st_blocks * 512
            except FileNotFoundError:
                # Concurrent removal means the estimate can only be lower. Apply
                # mode revalidates every selected top-level directory.
                continue
    return contains_keep, allocated


def top_level_directories(root: Path) -> list[Path]:
    result = []
    for entry in os.scandir(root):
        try:
            mode = entry.stat(follow_symlinks=False).st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISDIR(mode):
            result.append(Path(entry.path))
    return sorted(result, key=lambda item: item.name)


def scan(root: Path, now: float) -> tuple[list[tuple[Path, int]], int, int, int]:
    candidates: list[tuple[Path, int]] = []
    protected = 0
    auto_candidates = 0
    named_candidates = 0
    for path in top_level_directories(root):
        contains_keep, allocated = tree_info(path)
        if contains_keep:
            protected += 1
            continue
        days = AUTO_DAYS if path.name.startswith("auto_") else NAMED_DAYS
        cutoff = now - days * 86400
        try:
            old_enough = path.lstat().st_mtime < cutoff
        except FileNotFoundError:
            continue
        if not old_enough:
            continue
        candidates.append((path, allocated))
        if path.name.startswith("auto_"):
            auto_candidates += 1
        else:
            named_candidates += 1
    return candidates, protected, auto_candidates, named_candidates


def append_log(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, (message.rstrip("\n") + "\n").encode())
    finally:
        os.close(fd)


def still_deletable(path: Path, root: Path, now: float) -> bool:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(mode) or path.parent != root:
        return False
    days = AUTO_DAYS if path.name.startswith("auto_") else NAMED_DAYS
    if path.lstat().st_mtime >= now - days * 86400:
        return False
    contains_keep, _ = tree_info(path)
    return not contains_keep


def tree_owner_deletable(path: Path) -> bool:
    """Return whether every real directory permits owner unlink operations."""

    def fail_on_walk_error(error: OSError) -> None:
        raise error

    try:
        for base, _directories, _files in os.walk(
            path,
            topdown=True,
            onerror=fail_on_walk_error,
            followlinks=False,
        ):
            base_path = Path(base)
            mode = base_path.lstat().st_mode
            if not stat.S_ISDIR(mode):
                return False
            if mode & stat.S_IWUSR == 0 or mode & stat.S_IXUSR == 0:
                return False
    except OSError:
        return False
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prune auto_* backups older than 3 days and named backups older "
            "than 7 days; recursive .keep markers are retained forever."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.home() / "sanada_backup_persistent",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=Path.home() / ".local/log/sanada_backup_retention.log",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--apply", action="store_true", help="delete selected directories")
    parser.add_argument("--now", type=float, default=time.time(), help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    requested_root = args.root.expanduser().absolute()
    if requested_root.is_symlink() or not requested_root.is_dir():
        print(
            f"error: retention root is not a real directory: {requested_root}",
            file=sys.stderr,
        )
        return 2
    root = requested_root.resolve()

    lock_path = root / ".sanada-retention.lock"
    lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        candidates, protected, auto_count, named_count = scan(root, args.now)
        reclaim = sum(size for _, size in candidates)
        mode = "APPLY" if args.apply else "DRY-RUN"
        summary = (
            f"mode={mode} delete_dirs={len(candidates)} "
            f"auto={auto_count} named={named_count} "
            f"reclaim_bytes={reclaim} reclaim={human_bytes(reclaim)} "
            f"keep_excluded={protected}"
        )
        print(summary)
        stamp = dt.datetime.now(dt.timezone.utc).isoformat()
        append_log(args.log.expanduser(), f"{stamp} plan {summary}")
        if not args.apply:
            print("No directories deleted. Re-run with --apply only after review.")
            return 0

        deleted = 0
        deleted_bytes = 0
        skipped = 0
        for path, allocated in candidates:
            if not still_deletable(path, root, args.now):
                append_log(
                    args.log.expanduser(),
                    f"{stamp} abort revalidation_failed={path.name} deleted={deleted}",
                )
                print(
                    f"error: candidate changed during scan; stopped before {path.name}",
                    file=sys.stderr,
                )
                return 3
            if not tree_owner_deletable(path):
                skipped += 1
                append_log(
                    args.log.expanduser(),
                    f"{stamp} skip not_owner_deletable={path.name}",
                )
                print(f"skip: directory tree is not owner-deletable: {path.name}")
                continue
            if not still_deletable(path, root, args.now):
                append_log(
                    args.log.expanduser(),
                    f"{stamp} abort post_permission_revalidation_failed={path.name} "
                    f"deleted={deleted}",
                )
                print(
                    f"error: candidate changed while preparing deletion; "
                    f"stopped before {path.name}",
                    file=sys.stderr,
                )
                return 3
            shutil.rmtree(path)
            deleted += 1
            deleted_bytes += allocated
        final = (
            f"mode=APPLY deleted_dirs={deleted} deleted_bytes={deleted_bytes} "
            f"reclaimed={human_bytes(deleted_bytes)} skipped_dirs={skipped} "
            f"keep_excluded={protected}"
        )
        append_log(args.log.expanduser(), f"{stamp} complete {final}")
        print(final)
        return 4 if skipped else 0
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    raise SystemExit(main())
