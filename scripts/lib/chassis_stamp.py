"""Attach a chassis declaration to a hook command, for the whole command.

Why this is a shared function and not three inline f-strings.

The first attempt at #177 stamped commands as ``HARNESS_CHASSIS=codex <cmd>``.
That is an assignment prefix, and POSIX scopes it to the single simple command
that follows. Every hook command this repo generates is compound — the
dispatcher probe is ``test -x ... && grep ... || exec bash ...; exec ...`` — so
the value reached ``test`` and nothing else. The adapter at the end of the chain
saw no chassis at all, which routed Codex hooks to the Claude adapter and made
the Codex companion hook silently no-op.

Measured, on this host:

    $ bash -lc 'unset X; X=1 test -x /bin/true && true; printf "%s\\n" "${X:-UNSET}"'
    UNSET

Three installers made the same mistake in the same way because each wrote its
own prefix. One function, exercised by one test that actually runs the result,
is the rail: a future installer stamps by calling this, and the test that proves
the scoping covers it for free.

Codex 0.145.0 runs hook command strings through ``$SHELL -lc`` (verified against
``codex-rs/hooks/src/engine/command_runner.rs``), and the Grok and Kimi hook
runners take shell strings too, so a leading ``export`` is evaluated by the same
shell that runs the rest of the command.
"""

_VALID = ("claude", "codex", "kimi", "grok")


def stamp(command: str, chassis: str) -> str:
    """Return ``command`` with ``chassis`` exported for its entire duration.

    ``export`` rather than an assignment prefix: the latter applies only to the
    next simple command, and every command here is compound.
    """
    if chassis not in _VALID:
        raise ValueError(f"unknown chassis: {chassis!r} (expected one of {_VALID})")
    if not command or not command.strip():
        raise ValueError("refusing to stamp an empty command")
    return f"export HARNESS_CHASSIS={chassis}; {command}"


def unstamp(command: str) -> str:
    """Inverse of :func:`stamp`, for comparing live config against the overlay.

    The drift checker compares generated command strings to the overlay they
    came from. Once the installers stamp, every live command carries a prefix
    the overlay does not, and a whole managed block reads as missing-and-extra
    at once. A checker that is always red teaches people to ignore it, which
    costs more than the drift it was watching for.

    The older assignment-prefix form is accepted too, so a config written by the
    installer between #179 and #181 normalises rather than looking like drift.
    """
    for chassis in _VALID:
        for prefix in (f"export HARNESS_CHASSIS={chassis}; ", f"HARNESS_CHASSIS={chassis} "):
            if command.startswith(prefix):
                return command[len(prefix):]
    return command


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--unstamp":
        for line in sys.stdin:
            sys.stdout.write(unstamp(line.rstrip("\n")) + "\n")
    else:
        sys.stderr.write("usage: chassis_stamp.py --unstamp  (filters stdin)\n")
        sys.exit(64)
