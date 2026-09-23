#!/bin/bash
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

mkdir "$WORK/bin"
FAKE_SSH="$WORK/bin/ssh"
cat > "$FAKE_SSH" <<'SH'
#!/bin/sh
destination="$1"
shift
printf '%s %s\n' "$destination" "$*" >> "$SSH_ARGV_LOG"
if [ -n "${SSH_MKTEMP_OUTPUT:-}" ]; then
    case "$*" in
        *mktemp*) printf '%s\n' "$SSH_MKTEMP_OUTPUT"; exit 0 ;;
    esac
fi
cd "$SSH_REMOTE_CWD"
TMPDIR="$SSH_REMOTE_CWD" sh -c "$*"
SH
chmod 700 "$FAKE_SSH"

PROGRAM="$WORK/program.py"
printf '%s\n' \
    'import os, pathlib, sys' \
    'pathlib.Path(os.environ["SSH_PROGRAM_COPY"]).write_bytes(pathlib.Path(__file__).read_bytes())' \
    'pathlib.Path(os.environ["SSH_SECRET_STDIN"]).write_text(sys.stdin.read())' \
    > "$PROGRAM"
SYNTHETIC_SECRET='synthetic-secret-data-only'

mkdir "$WORK/remote"
printf 'must survive cleanup\n' > "$WORK/remote/sh"
export PATH="$WORK/bin:/usr/bin:/bin"
export SSH_ARGV_LOG="$WORK/argv.log"
export SSH_REMOTE_CWD="$WORK/remote"
export SSH_PROGRAM_COPY="$WORK/program.copy"
export SSH_SECRET_STDIN="$WORK/secret.stdin"

# shellcheck source=../lib/remote_secret.sh
source "$HERE/../lib/remote_secret.sh"
printf '%s\n' "$SYNTHETIC_SECRET" \
    | harness_remote_python_with_secret host.example "$PROGRAM"

cmp "$PROGRAM" "$SSH_PROGRAM_COPY"
[ "$(cat "$SSH_SECRET_STDIN")" = "$SYNTHETIC_SECRET" ]
! grep -Fq "$SYNTHETIC_SECRET" "$SSH_ARGV_LOG"
[ "$(cat "$WORK/remote/sh")" = "must survive cleanup" ]
! find "$WORK/remote" -maxdepth 1 -name 'harness-secret-program.*' | grep -q .

export SSH_MKTEMP_OUTPUT="/tmp/rejected'; touch '$WORK/injected'; echo 'path"
if printf '%s\n' "$SYNTHETIC_SECRET" \
    | harness_remote_python_with_secret host.example "$PROGRAM" 2>/dev/null; then
    echo "malicious temporary path was accepted" >&2
    exit 1
fi
[ ! -e "$WORK/injected" ]
printf 'remote secret stdin separation: PASS\n'
