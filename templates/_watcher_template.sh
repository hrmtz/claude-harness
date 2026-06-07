#!/usr/bin/env bash
# _watcher_template.sh — copy this for any vastai/long-running watcher
#
# IMPORTANT: do NOT use 'status' as a variable name in zsh.
#            'status' is read-only in zsh → assignment silently crashes the script.
#            Use: node_status, http_status, api_status, task_status, etc.
#
# IMPORTANT: SECONDS= resets the zsh elapsed-time timer — use separate vars for
#            timeouts (e.g. ELAPSED_START=$SECONDS; ... $((SECONDS - ELAPSED_START))).
#
# IMPORTANT: PPID, LINENO, HISTCMD are also read-only in zsh. Avoid assigning them.
#
# heartbeat: safety-rails-beat emits a Discord alert at T+5 and T+10 min of silence.
#            Call it inside every polling iteration so silence == real stall.

LABEL="${1:-watcher}"
TOTAL=60
set -uo pipefail

for i in $(seq 1 "$TOTAL"); do
    sleep 20

    # --- do your work here ---
    node_status=$(echo "RUNNING")  # ← NOT 'status=' (read-only in zsh → silent crash)
    # -------------------------

    # heartbeat: T+5/T+10 Discord alert if this line stops appearing in logs
    safety-rails-beat "$LABEL" "$i" "$TOTAL" 2>/dev/null || true

    echo "[$(date +%H:%M)] iter=$i node_status=$node_status"
    [[ "$node_status" == "DONE" ]] && break
done
echo "watcher $LABEL complete"
