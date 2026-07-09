"""safety-rails: pre-flight + in-flight + post-execution rails for long-running ops.

Built after a long-running indexing incident where philosophy-level "early
bleeding detection" failed because it lived only in agent memory, not in
structural rails.

Components:
  preflight   — algorithm fitness check (working set vs RAM, alternatives)
  heartbeat   — daemon thread writing periodic status to ~/.local/run/safety-rails/
  watcher     — cron-driven scanner, detects stale + slowdown, notifies Discord+gh
"""
from . import preflight, heartbeat

__version__ = "0.1.0"
__all__ = ["preflight", "heartbeat"]
