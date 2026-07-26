# Credential eradication

Deleting a leaked credential is not sufficient when the Sanada hook snapshots
the deletion target. Eradication means that a shape-based scan completes and
reports zero remaining plaintext copies.

## Procedure

1. Rotate or revoke the exposed credential when its provider supports it.
2. Redact the value in place. Never put the real value in a shell command,
   search pattern, replacement expression, or test fixture. Build any test from
   synthetic text with the same character classes.
3. Search broadly and decide at replacement time. Do not narrow the search so
   aggressively that a real value is excluded together with false positives.
4. Run `scripts/verify_credential_eradication.sh`. It scans the default session,
   persistent-backup, legacy-agent, temporary-session, and project roots using
   the transcript scrubber's shared shape catalog. It prints candidate paths
   only, never file contents; credential-shaped substrings in a path are
   redacted before display.
5. Repeat redaction and verification until the verifier exits 0 with `CLEAN`.
   Exit 1 means matches remain. Exit 2 means the scan was incomplete and cannot
   establish eradication.
6. Re-run verification after every remediation or diagnostic step. A command
   created to test the fix is itself a possible new transcript leak.

The Sanada hook now consults the same catalog before every file or directory
snapshot. A non-placeholder match, or a scan error/timeout, suppresses the
backup and logs only the target path after shape-based redaction. This makes
both deletion and in-place redaction stop creating fresh persistent plaintext
copies.

## Targeted verification

Pass explicit roots to replace the defaults:

```bash
scripts/verify_credential_eradication.sh /path/to/session-root /path/to/backups
```

The verifier is detective, not destructive. It never rewrites or deletes a
file. Inspect each reported path and redact in place with a shape-based rule;
do not copy the source into another backup while doing so.
