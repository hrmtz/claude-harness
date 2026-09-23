#!/bin/bash
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

mkdir "$WORK/bin"
FAKE_SSH="$WORK/bin/ssh"
cat > "$FAKE_SSH" <<'SH'
#!/bin/sh
printf '%s\n' "$*" >> "$SSH_ARGV_LOG"
case "$*" in
    *mktemp*) printf '/tmp/harness-secret-program.synthetic\n' ;;
    *'cat >'*) cat > "$SSH_PROGRAM_STDIN" ;;
    *python3*) cat > "$SSH_SECRET_STDIN" ;;
    *'rm -f'*) : > "$SSH_CLEANED" ;;
    *) exit 1 ;;
esac
SH
chmod 700 "$FAKE_SSH"

PROGRAM="$WORK/program.py"
printf 'import sys\nvalue = sys.stdin.readline()\n' > "$PROGRAM"
SYNTHETIC_SECRET='synthetic-secret-data-only'

export PATH="$WORK/bin:/usr/bin:/bin"
export SSH_ARGV_LOG="$WORK/argv.log"
export SSH_PROGRAM_STDIN="$WORK/program.stdin"
export SSH_SECRET_STDIN="$WORK/secret.stdin"
export SSH_CLEANED="$WORK/cleaned"

# shellcheck source=../lib/remote_secret.sh
source "$HERE/../lib/remote_secret.sh"
printf '%s\n' "$SYNTHETIC_SECRET" \
    | harness_remote_python_with_secret host.example "$PROGRAM"

cmp "$PROGRAM" "$SSH_PROGRAM_STDIN"
[ "$(cat "$SSH_SECRET_STDIN")" = "$SYNTHETIC_SECRET" ]
! grep -Fq "$SYNTHETIC_SECRET" "$SSH_ARGV_LOG"
[ -f "$SSH_CLEANED" ]
printf 'remote secret stdin separation: PASS\n'
