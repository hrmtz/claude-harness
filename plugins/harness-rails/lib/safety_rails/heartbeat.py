"""Heartbeat helper for long-running operations.

Spawns a daemon thread that writes ~/.local/run/safety-rails/<project>/<job>.json
every interval seconds. The watcher process reads these files to detect stale
(crashed / hung) jobs and slowdowns vs declared eta.

Usage:
    from safety_rails import heartbeat

    def _sample_pg_progress():
        # return any dict; values can be int/float/str
        with psycopg.connect(POSTGRES_URL) as c, c.cursor() as cur:
            cur.execute("SELECT tuples_done FROM pg_stat_progress_create_index")
            r = cur.fetchone()
            return {"tuples_done": r[0] if r else 0}

    with heartbeat.beat("hnsw_shard_build", project="PRS-LLM",
                       eta_hours=4, sampler=_sample_pg_progress):
        run_long_operation()

    # bash side: source <(safety-rails-beat init --project PRS-LLM \
    #                       --job hnsw_shard_build --eta 4h)
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

_RUN_DIR = Path(os.path.expanduser("~/.local/run/safety-rails"))


def _heartbeat_path(project: str, job: str) -> Path:
    project_dir = _RUN_DIR / project
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir / f"{job}.json"


def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data))
    tmp.rename(path)


@contextlib.contextmanager
def beat(
    job: str,
    project: str,
    eta_hours: float,
    sampler: Optional[Callable[[], dict]] = None,
    interval_sec: int = 30,
):
    """Context manager that runs a heartbeat thread for the duration of a block.

    The heartbeat thread writes a JSON file every interval_sec containing:
        ts          float   unix epoch
        pid         int     parent process pid (the one that called beat)
        started_ts  float   when this beat session started
        eta_hours   float   declared ETA, used by watcher for slowdown detection
        metric      dict    sampler() result, optional
    """
    path = _heartbeat_path(project, job)
    started = time.time()
    stop_event = threading.Event()

    def _loop():
        while not stop_event.wait(interval_sec):
            data: dict[str, Any] = {
                "ts": time.time(),
                "pid": os.getpid(),
                "started_ts": started,
                "eta_hours": eta_hours,
            }
            if sampler is not None:
                try:
                    data["metric"] = sampler()
                except Exception as e:  # never kill parent
                    data["metric"] = {"sampler_error": repr(e)[:200]}
            try:
                _atomic_write(path, data)
            except Exception:
                pass

    # write initial heartbeat synchronously so watcher sees the job immediately
    initial: dict[str, Any] = {
        "ts": started,
        "pid": os.getpid(),
        "started_ts": started,
        "eta_hours": eta_hours,
    }
    if sampler is not None:
        try:
            initial["metric"] = sampler()
        except Exception:
            initial["metric"] = {}
    _atomic_write(path, initial)

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop_event.set()
        thread.join(timeout=2)
        # final marker so watcher knows job completed cleanly (unlink == done)
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
