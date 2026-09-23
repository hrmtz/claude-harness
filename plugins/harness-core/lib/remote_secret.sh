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
    remote_program_safe=0
    cleanup() {
        [ "$remote_program_safe" -ne 1 ] || command timeout 10 ssh "$destination" \
            "rm -f -- '$remote_program'" </dev/null >/dev/null 2>&1 || true
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
    remote_program_safe=1

    # remote_program passed validation above: absolute path, ASCII path chars only.
    # OpenSSH concatenates command arguments rather than preserving positional args,
    # so embed this validated value in the one remote command string.
    command ssh "$destination" \
        "cat > '$remote_program' && chmod 600 '$remote_program'" \
        < "$program" || return
    command ssh "$destination" "python3 '$remote_program'"
)
