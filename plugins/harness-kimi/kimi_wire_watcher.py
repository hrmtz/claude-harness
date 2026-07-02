#!/usr/bin/env python3
"""
kimi_wire_watcher.py — detective 2nd-wall for the Kimi bash guard (gh #53).

The BASH_ENV / PATH-shim guard (gh #52) is the *preventive* 1st wall, but it can
be dropped (`unset BASH_ENV`, env scrub) or is not sourced by some invocations
(`bash --posix -c`, `bash -i -c`, absolute-path `sh -c` — see docs/kimi_hooks.md).
This watcher is the *detective* 2nd wall (AgentShield-style santa-method): it
tails every Kimi wire.jsonl, re-runs the harness bash gate over each executed
Bash command, and alerts when a command that the gate WOULD have denied ran
without the guard blocking it — i.e. the 1st wall had a gap.

Reactive by design: detection + alert, not prevention (a race window exists).

Gap definition (low false-positive):
  a Bash tool.call whose command the gate denies, AND whose paired tool.result
  does NOT contain the guard's block marker → the guard did not fire → GAP.
  If the result carries the marker, the guard blocked it correctly → no alert.

Fail-safe: any error exits 0 (a watcher must never wedge cron).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
KIMI_SESSIONS = Path.home() / ".kimi-code" / "sessions"
KILL_SWITCH = Path.home() / ".kimi-code" / "harness-watch.disabled"
GUARD_DIR = Path.home() / ".kimi-code" / "harness-guard"
STATE_FILE = GUARD_DIR / "watcher_state.json"
LOG_FILE = GUARD_DIR / "watcher.log"

# The security-relevant gate to re-run. Only bash_command_guard is a pure
# "is this command dangerous / credential-exposing" check; the other gates
# (pipeline_preflight, phase_review, branch_policy) depend on session/workflow
# state the watcher does not have, so re-running them post-hoc would be noise.
PLUGINS_DIR = Path(
    os.environ.get("HARNESS_PLUGINS", str(Path.home() / "projects" / "claude-harness" / "plugins"))
)
GATE_HOOK = PLUGINS_DIR / "harness-core" / "hooks" / "bash_command_guard.sh"

# Marker guard-check.sh / guarded-bash.sh print to stderr on a block; its
# presence in a tool.result means the 1st wall fired for that command.
GUARD_BLOCK_MARKER = "harness guard"

DISCORD_REPO = "claude-harness"
REAL_BASH = os.environ.get("HARNESS_KIMI_REAL_BASH", "/bin/bash")


def log(msg: str) -> None:
    try:
        GUARD_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%F_%T')}] {msg}\n")
    except OSError:
        pass


# ----------------------------------------------------------------------------
# State (per-session set of already-evaluated toolCallIds, so we alert once)
# ----------------------------------------------------------------------------
def load_state() -> dict[str, list[str]]:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict[str, list[str]]) -> None:
    try:
        GUARD_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, STATE_FILE)
    except OSError as exc:
        log(f"state save error: {type(exc).__name__}")


# ----------------------------------------------------------------------------
# wire.jsonl parsing (real Kimi schema; see kimi_session_scrub.py)
# ----------------------------------------------------------------------------
def parse_wire(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Return (bash_calls, results): toolCallId -> command / result-text."""
    bash_calls: dict[str, str] = {}
    results: dict[str, str] = {}
    try:
        with path.open("r", encoding="utf-8", errors="surrogateescape") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict) or rec.get("type") != "context.append_loop_event":
                    continue
                event = rec.get("event")
                if not isinstance(event, dict):
                    continue
                etype = event.get("type")
                tcid = event.get("toolCallId")
                if not isinstance(tcid, str):
                    continue
                if etype == "tool.call" and event.get("name") == "Bash":
                    args = event.get("args")
                    if isinstance(args, dict) and isinstance(args.get("command"), str):
                        bash_calls[tcid] = args["command"]
                elif etype == "tool.result":
                    results[tcid] = json.dumps(event.get("result"), ensure_ascii=False)
    except OSError as exc:
        log(f"read error {path}: {type(exc).__name__}")
    return bash_calls, results


# ----------------------------------------------------------------------------
# Gate re-check
# ----------------------------------------------------------------------------
def gate_denies(command: str, cwd: str) -> bool:
    """Run bash_command_guard over the command; True if it denies."""
    if not GATE_HOOK.is_file():
        return False
    payload = json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd}
    )
    try:
        proc = subprocess.run(
            [REAL_BASH, str(GATE_HOOK)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "HARNESS_KIMI_GUARD_ACTIVE": "1"},
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log(f"gate run error: {type(exc).__name__}")
        return False
    try:
        out = json.loads(proc.stdout)
        return out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    except (json.JSONDecodeError, AttributeError):
        return False


# ----------------------------------------------------------------------------
# Alert
# ----------------------------------------------------------------------------
def alert(session_key: str, tcid: str, command: str) -> None:
    short = command if len(command) <= 200 else command[:200] + "…"
    msg = (
        "**🛑 harness-kimi guard GAP detected**\n"
        "```\n"
        f"session: {session_key}\n"
        f"toolCallId: {tcid}\n"
        f"command matched a DENY pattern but ran WITHOUT the guard blocking it\n"
        f"→ the BASH_ENV/PATH 1st wall was bypassed or down\n"
        f"cmd: {short}\n"
        "```\n"
        "Check: is `HARNESS_KIMI_BASH_GUARD=1` set? was `BASH_ENV` unset? "
        "was this an absolute-path `--posix`/`-i`/`sh -c` bypass (docs/kimi_hooks.md)?"
    )
    log(f"GAP session={session_key} tcid={tcid} cmd={command[:120]}")
    try:
        subprocess.run(
            ["discord-bot", "post", DISCORD_REPO, msg],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"discord alert error (logged locally): {type(exc).__name__}")


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def session_key(path: Path) -> str:
    # .../sessions/<sid>/<sub>/agents/main/wire.jsonl → <sid>/<sub>
    parts = path.parts
    try:
        i = parts.index("sessions")
        return "/".join(parts[i + 1 : i + 3])
    except ValueError:
        return str(path)


def main() -> int:
    if KILL_SWITCH.exists():
        return 0
    if not KIMI_SESSIONS.is_dir():
        return 0

    # First run (no state yet) establishes a BASELINE: historical sessions
    # contain commands from before the guard existed, which are not "gaps" — mark
    # them settled without alerting, so we only alert on gaps that appear AFTER
    # install. Prevents flooding the alert channel on first install.
    baseline = not STATE_FILE.exists()

    state = load_state()
    gaps = 0
    baselined = 0
    deferred = 0
    files = 0

    for wire in sorted(KIMI_SESSIONS.glob("*/*/agents/main/wire.jsonl")):
        files += 1
        key = session_key(wire)
        seen = set(state.get(key, []))
        cwd = str(wire.parent)

        bash_calls, results = parse_wire(wire)
        for tcid, command in bash_calls.items():
            if tcid in seen:
                continue

            # Baseline fast-path: on first install we only need to record history
            # as settled, never alert — so skip the (expensive) per-command gate
            # subprocess entirely. A command with a result is settled; one without
            # is deferred to a later (post-baseline) tick.
            if baseline:
                if tcid in results:
                    baselined += 1
                    seen.add(tcid)
                else:
                    deferred += 1
                continue

            if not gate_denies(command, cwd):
                seen.add(tcid)  # allowed command: settled, never revisit
                continue
            # Command matches a deny pattern. Decide ran-vs-blocked from result.
            result_text = results.get(tcid)
            if result_text is None:
                deferred += 1  # no result yet (still running?) — decide next tick
                continue
            if GUARD_BLOCK_MARKER not in result_text:
                alert(key, tcid, command)
                gaps += 1
            seen.add(tcid)  # settled (alerted or correctly-blocked)

        if seen:
            state[key] = sorted(seen)

    save_state(state)
    if baseline:
        log(f"watch baseline established files={files} baselined={baselined} deferred={deferred}")
    else:
        log(f"watch done files={files} gaps={gaps} deferred={deferred}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException:
        log("top_level_exception suppressed")
        sys.exit(0)
