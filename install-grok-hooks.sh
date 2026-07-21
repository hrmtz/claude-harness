#!/bin/bash
# install-grok-hooks.sh — wire harness hooks into ~/.grok/hooks/harness.json
#
# Grok has a NATIVE hook API (Kimi >= 0.28 got one too — see install-kimi-hooks.sh),
# so this is a close cousin of
# install-codex-hooks.sh: the hook SET comes from plugins/cross_cli_hooks.json
# (gh #55, grok section); the event/matcher/timeout for each hook is looked up
# from the owning plugin's hooks/hooks.json (the same SSOT that drives Claude via
# sync_hooks_to_live.py). Adding/removing a Grok hook = edit cross_cli_hooks.json,
# re-run this script. Global hooks in ~/.grok/hooks/ are ALWAYS trusted (no
# per-project trust needed).
#
# Idempotent. Script CONTENT changes need no re-run (harness.json references repo
# paths directly); only hook SET changes do.
#
# Double-fire note: Grok also scans ~/.claude/settings.json by default
# ([compat.claude] hooks = true). If those Claude entries include the same harness
# hooks, a single Bash call runs each guard TWICE. After installing, set
#   [compat.claude]
#   hooks = false
# in ~/.grok/config.toml (see docs/grok_hooks.md) so only this native set fires.

set -euo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GROK_HOOKS="$HOME/.grok/hooks/harness.json"
OVERLAY="$HARNESS_DIR/plugins/cross_cli_hooks.json"

if [[ ! -f "$OVERLAY" ]]; then
    echo "error: $OVERLAY not found." >&2
    exit 1
fi

# ---- prerequisites ----------------------------------------------------------
if ! command -v jq >/dev/null 2>&1; then
    echo "error: jq not found." >&2
    exit 1
fi
if ! command -v grok >/dev/null 2>&1; then
    echo "warning: grok CLI not found on PATH — writing harness.json anyway." >&2
fi

mkdir -p "$(dirname "$GROK_HOOKS")"

# ---- backup any existing harness.json ---------------------------------------
if [[ -f "$GROK_HOOKS" ]]; then
    BK="$HOME/sanada_backup_persistent/grok_hooks_install_$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$BK"
    cp "$GROK_HOOKS" "$BK/harness.json"
fi

# ---- generate harness.json to a temp file first (atomic) --------------------
# Any generator failure aborts with the existing harness.json untouched.
OUT_TMP="$(mktemp)"
trap 'rm -f "$OUT_TMP"' EXIT

python3 - "$OVERLAY" "$HARNESS_DIR" > "$OUT_TMP" <<'PYEOF'
import json, sys, collections

overlay_path, harness_dir = sys.argv[1], sys.argv[2]
plugins_dir = f"{harness_dir}/plugins"
overlay = json.load(open(overlay_path))["grok"]

# Grok lifecycle events REJECT a matcher (docs/user-guide/10-hooks.md); only the
# tool/notification events carry one. Drop the matcher for lifecycle events so the
# hook loads instead of being rejected.
LIFECYCLE = {"SessionStart", "SessionEnd", "Stop", "StopFailure",
             "UserPromptSubmit", "PreCompact", "PostCompact",
             "SubagentStart", "SubagentStop", "SessionEnd"}

# Look up (event, matcher, timeout) for each selected hook from the owning
# plugin's hooks.json — the same SSOT that drives Claude Code and Codex.
lookup = {}
for plugin in sorted({h.split("/")[0] for h in overlay["hooks"]}):
    hooks_json = json.load(open(f"{plugins_dir}/{plugin}/hooks/hooks.json"))
    for event, blocks in hooks_json.get("hooks", {}).items():
        for blk in blocks:
            matcher = blk.get("matcher")
            for h in blk.get("hooks", []):
                name = h["command"].split("/hooks/")[-1]
                lookup[f"{plugin}/hooks/{name}"] = (event, matcher, h.get("timeout", 5))

# Group by (event, matcher) preserving overlay order.
groups = collections.OrderedDict()
for hook in overlay["hooks"]:
    if hook not in lookup:
        print(f"error: {hook} not registered in its plugin hooks.json", file=sys.stderr)
        sys.exit(1)
    event, matcher, timeout = lookup[hook]
    if event in LIFECYCLE:
        matcher = None
    groups.setdefault((event, matcher), []).append(
        (f"bash {plugins_dir}/{hook}", timeout))
sys.path.insert(0, f"{harness_dir}/scripts/lib")
from cross_cli_externals import resolve  # noqa: E402
for ext in resolve(overlay_path, "grok", harness_dir):
    event = ext["event"]
    matcher = None if event in LIFECYCLE else ext["matcher"]
    groups.setdefault((event, matcher), []).append(
        (ext["command"], ext["timeout"]))

# Assemble the Grok hooks.json structure.
events = collections.OrderedDict()
for (event, matcher), entries in groups.items():
    block = {}
    if matcher:
        block["matcher"] = matcher
    block["hooks"] = [
        {"type": "command", "command": cmd, "timeout": timeout}
        for cmd, timeout in entries
    ]
    events.setdefault(event, []).append(block)

doc = {
    "_generated_by": "install-grok-hooks.sh from plugins/cross_cli_hooks.json "
                     "(hook set) + plugins/*/hooks/hooks.json (SSOT). "
                     "Do not edit by hand; edit the overlay and re-run.",
    "hooks": events,
}
print(json.dumps(doc, indent=2, ensure_ascii=False))
PYEOF

# ---- validate + install atomically ------------------------------------------
jq empty "$OUT_TMP" >/dev/null    # abort if the generated JSON is malformed
mv "$OUT_TMP" "$GROK_HOOKS"
trap - EXIT

echo "wrote $GROK_HOOKS (set: $(jq -r '.grok.hooks | length' "$OVERLAY") hooks)"

# ---- instructions -----------------------------------------------------------
cat <<MSG

Install complete. Global hooks are always trusted — no per-project trust needed.

Next:
  1. Run: grok /hooks     (confirm harness.json loaded under Global)
  2. Avoid double-fire: add to ~/.grok/config.toml
        [compat.claude]
        hooks = false
     (else Grok also runs the same guards from ~/.claude/settings.json)
  3. Verify a deny fires (expect {"decision":"deny",...} on stdout):
        printf '%s' '{"toolName":"run_terminal_command","toolInput":{"command":"sops -d x.enc.yaml"}}' \\
          | GROK_SESSION_ID=test bash $HARNESS_DIR/plugins/harness-core/hooks/bash_command_guard.sh

Hook set: $OVERLAY (grok section)
Drift check: bash scripts/check_cross_cli_hooks.sh --live
MSG
