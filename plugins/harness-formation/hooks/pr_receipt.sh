#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# FORMATION_SESSION_ID is a claim (#335): take it from the registry row of the
# worker whose pane this hook really runs in, or record nothing.
# shellcheck source=/dev/null
source "$SCRIPT_DIR/../lib/mailbox_delivery.sh"
mailbox_require_identity 2>/dev/null || exit 0
exec python3 "$SCRIPT_DIR/pr_receipt.py"
