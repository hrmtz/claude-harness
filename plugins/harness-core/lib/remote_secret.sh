#!/bin/bash
# Run a remote Python file with the caller's stdin reserved for data, never code.

harness_remote_python_with_secret() (
    if [ "$#" -ne 2 ]; then
        echo "usage: harness_remote_python_with_secret <ssh-destination> <program.py>" >&2
        return 2
    fi

    destination="$1"
    program="$2"
    if ! printf '%s' "$destination" \
        | grep -qE '^([A-Za-z_][A-Za-z0-9._-]*@)?[A-Za-z0-9][A-Za-z0-9._-]*$'; then
        echo "harness_remote_python_with_secret: invalid ssh destination" >&2
        return 2
    fi
    if [ ! -f "$program" ] || [ ! -r "$program" ]; then
        echo "harness_remote_python_with_secret: program is not a readable file" >&2
        return 2
    fi

    remote_program=""
    cleanup() {
        [ -z "$remote_program" ] || command ssh "$destination" \
            'rm -f -- "$1"' sh "$remote_program" </dev/null >/dev/null 2>&1 || true
    }
    trap cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' HUP TERM

    remote_program=$(command ssh "$destination" \
        'umask 077; mktemp "${TMPDIR:-/tmp}/harness-secret-program.XXXXXXXX"' \
        </dev/null) || return
    if [[ "$remote_program" == *[$'\r\n\t ']* ]] \
        || ! printf '%s' "$remote_program" | grep -qE '^/[A-Za-z0-9._/-]+$'; then
        echo "harness_remote_python_with_secret: invalid remote temporary path" >&2
        return 1
    fi

    command ssh "$destination" 'cat > "$1" && chmod 600 "$1"' \
        sh "$remote_program" < "$program" || return
    command ssh "$destination" 'python3 "$1"' sh "$remote_program"
)
