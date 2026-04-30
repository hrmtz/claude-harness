"""safety-rails: pre-flight + in-flight + post-execution rails for long-running ops.

Built after a 23h sunk-cost incident where philosophy-level "early bleeding
detection" failed because it lived only in CLAUDE.md memory, not in
structural rails. See: https://github.com/hrmtz/PRS-LLM/issues/59

Components:
  preflight   — algorithm fitness check (working set vs RAM, alternatives)
  heartbeat   — daemon thread writing periodic status to ~/.local/run/safety-rails/
  watcher     — cron-driven scanner, detects stale + slowdown, notifies Discord+gh
"""
from . import preflight, heartbeat

__version__ = "0.1.0"
__all__ = ["preflight", "heartbeat"]
