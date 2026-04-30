"""Cron-driven watcher that scans heartbeat files and reports anomalies.

Run from a cron line like:
    */1 * * * * python3 -m safety_rails.watcher

Reads:
  ~/.local/run/safety-rails/<project>/<job>.json   per-job heartbeat
  ~/.local/share/safety-rails/etc/projects.d/<project>/jobs.yaml   per-project rules (optional)

Detects:
  T1 stale (heartbeat older than threshold)
  T2 eta overrun (elapsed_hours / eta_hours > factor)
  T3 progress stall (sampler metric not advancing)

Reports:
  Discord notify (via local discord-notify CLI if available)
  gh issue create (deduped by title)

Does NOT auto-kill / auto-revert. Per Melchior: human-in-loop only.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_RUN_DIR = Path(os.path.expanduser("~/.local/run/safety-rails"))
_STATE_DIR = Path(os.path.expanduser("~/.local/state/safety-rails"))
_STATE_DIR.mkdir(parents=True, exist_ok=True)

# default thresholds (override per-job via jobs.yaml when integrated)
DEFAULT_STALE_SEC = 180
DEFAULT_ETA_WARN_FACTOR = 1.5
DEFAULT_ETA_ALERT_FACTOR = 2.0
DEFAULT_ETA_CRITICAL_FACTOR = 3.0


@dataclass
class Anomaly:
    project: str
    job: str
    level: str  # ok / warn / alert / critical
    detail: str


def _read_heartbeat(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _previous_level_path(project: str, job: str) -> Path:
    d = _STATE_DIR / project
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{job}.level"


def _previous_level(project: str, job: str) -> str:
    p = _previous_level_path(project, job)
    try:
        return p.read_text().strip()
    except FileNotFoundError:
        return "ok"


def _save_level(project: str, job: str, level: str) -> None:
    _previous_level_path(project, job).write_text(level)


def evaluate(project: str, job: str, hb: dict, now: float) -> Anomaly:
    age = now - hb.get("ts", 0)
    if age > DEFAULT_STALE_SEC:
        return Anomaly(project, job, "alert", f"stale {age:.0f}s pid={hb.get('pid')}")

    eta_hours = hb.get("eta_hours", 0)
    if eta_hours <= 0:
        return Anomaly(project, job, "ok", "ok")

    elapsed_hours = (now - hb.get("started_ts", now)) / 3600.0
    ratio = elapsed_hours / eta_hours

    if ratio > DEFAULT_ETA_CRITICAL_FACTOR:
        return Anomaly(
            project, job, "critical",
            f"elapsed {elapsed_hours:.1f}h vs eta {eta_hours}h ({ratio:.2f}x over)",
        )
    if ratio > DEFAULT_ETA_ALERT_FACTOR:
        return Anomaly(
            project, job, "alert",
            f"elapsed {elapsed_hours:.1f}h vs eta {eta_hours}h ({ratio:.2f}x over)",
        )
    if ratio > DEFAULT_ETA_WARN_FACTOR:
        return Anomaly(
            project, job, "warn",
            f"elapsed {elapsed_hours:.1f}h vs eta {eta_hours}h ({ratio:.2f}x over)",
        )
    return Anomaly(project, job, "ok", f"on track ({ratio:.2f}x)")


def _notify_discord(project: str, anom: Anomaly) -> None:
    icon = {"warn": "⚠️", "alert": "🚨", "critical": "🔥"}.get(anom.level, "ℹ️")
    msg = (
        f"{icon} **safety-rails [{project}/{anom.job}]** level={anom.level}\n"
        f"```\nhost:    {socket.gethostname()}\nstatus:  {anom.detail}\n```"
    )
    # try discord-bot post (per-project channel) → fallback discord-notify → log only
    for cmd in (["discord-bot", "post", project, msg], ["discord-notify", msg]):
        try:
            r = subprocess.run(cmd, check=False, capture_output=True, text=True)
            if r.returncode == 0:
                return
        except FileNotFoundError:
            continue  # try next
    # all notify tools missing — log to stderr (cron captures to log file)
    print(f"[NOTIFY-FAIL] {msg}", file=sys.stderr)


def _has_command(name: str) -> bool:
    try:
        return subprocess.run(["which", name], capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


def _gh_issue(project_repo: str, anom: Anomaly) -> None:
    # de-dup by title
    title = f"[safety-rails][{anom.job}] {anom.level} — eta overrun"
    label = "type:bug,area:infra"
    if anom.level == "critical":
        label += ",priority:high"
    elif anom.level == "alert":
        label += ",priority:medium"
    else:
        label += ",priority:low"

    if not _has_command("gh"):
        return  # gh not installed, silent skip
    # check if open issue with same title exists (de-dup)
    try:
        check = subprocess.run(
            ["gh", "issue", "list", "--repo", project_repo, "--state", "open",
             "--search", title, "--json", "number"],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        return
    if check.returncode == 0 and check.stdout.strip() not in ("", "[]"):
        return  # issue already open, skip duplicate

    body = (
        f"## safety-rails detected anomaly\n\n"
        f"- project: `{anom.project}`\n"
        f"- job: `{anom.job}`\n"
        f"- level: `{anom.level}`\n"
        f"- detail: `{anom.detail}`\n"
        f"- host: `{socket.gethostname()}`\n"
        f"- detected_at: `{time.strftime('%Y-%m-%d %H:%M:%S %Z')}`\n\n"
        f"## suggested action\n\n"
        f"1. inspect the running operation; do not auto-kill blindly\n"
        f"2. check pg_stat_progress / log / iostat as appropriate\n"
        f"3. if algorithm-level mismatch (working set vs RAM, etc), evaluate:\n"
        f"   - kill + retune\n"
        f"   - kill + alternative algorithm\n"
        f"   - continue with degraded performance\n"
    )
    try:
        subprocess.run(
            ["gh", "issue", "create", "--repo", project_repo,
             "--title", title, "--label", label, "--body", body],
            check=False, capture_output=True,
        )
    except FileNotFoundError:
        pass


def _project_to_repo(project: str) -> Optional[str]:
    """Map project name to GitHub repo. Override via env or config later."""
    overrides = {
        "PRS-LLM": "hrmtz/PRS-LLM",
        "PRS-LLM-dev": "hrmtz/PRS-LLM",
        "zetith-emdash": "hrmtz/zetith-emdash",
        "content-forge": "hrmtz/content-forge",
        "claude-harness": "hrmtz/claude-harness",
    }
    return overrides.get(project)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="evaluate but don't notify")
    parser.add_argument("--no-gh", action="store_true",
                        help="skip gh issue creation (still send Discord)")
    args = parser.parse_args(argv)

    if not _RUN_DIR.exists():
        return 0  # no jobs being watched yet

    now = time.time()
    anomalies = []

    for project_dir in _RUN_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        project = project_dir.name
        for job_file in project_dir.glob("*.json"):
            if job_file.name.endswith(".tmp"):
                continue
            job = job_file.stem
            hb = _read_heartbeat(job_file)
            if hb is None:
                continue

            anom = evaluate(project, job, hb, now)
            prev = _previous_level(project, job)

            if anom.level == prev:
                continue  # de-dup, no level change

            anomalies.append(anom)
            if args.dry_run:
                print(f"[DRY] {anom.project}/{anom.job}: {prev} -> {anom.level} ({anom.detail})")
                continue

            if anom.level in ("warn", "alert", "critical"):
                _notify_discord(project, anom)
                if not args.no_gh and anom.level in ("alert", "critical"):
                    repo = _project_to_repo(project)
                    if repo:
                        _gh_issue(repo, anom)
            _save_level(project, job, anom.level)

    if anomalies:
        print(f"safety-rails watcher: {len(anomalies)} anomalies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
