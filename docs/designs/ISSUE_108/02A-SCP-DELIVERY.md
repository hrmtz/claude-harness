# Issue #108 Slice 2A — Minimal SCP delivery contract

Status: human-approved and implemented

## Authorization

Only this complete prompt mints a receipt:

```text
AUTHORIZE_SECRET_DELIVERY source=/absolute/path destination=scp://[user@]host[:port]/absolute/path representation=file
```

The source must be a regular 1..1048576-byte file classified by
`credential_file_read_guard.sh --classify-path`. Conservative path, host, user, port, and session
grammars reject shell/SCP metacharacters. A Read acknowledgement, partial match, prose, prior
destination, or unclassified file does not authorize delivery.

The mode-0600 receipt records only source identity metadata, destination metadata, session,
creation time, and the fixed 120-second expiry. It contains no file bytes.

## Delivery

`harness-secret-deliver` atomically claims the receipt before validation or network activity.
Every claimed receipt is one-use, including failed attempts. It rechecks session, expiry, source
device/inode/size/mtime, and credential classification, then runs `/usr/bin/scp` with fixed safety
options:

```text
scp -B -q \
  -oBatchMode=yes \
  -oStrictHostKeyChecking=yes \
  -oClearAllForwardings=yes \
  -oForwardAgent=no \
  -oPermitLocalCommand=no \
  -oPasswordAuthentication=no \
  -oKbdInteractiveAuthentication=no \
  [ -P explicit-port ] -- source user@host:/absolute/path
```

If the prompt omits a port, existing SSH configuration selects it. Child stdin, stdout, and stderr
are disconnected from the agent. The wrapper emits only a stable success or value-free failure
code and kills the child process group after 60 seconds.

## Security boundary

This prevents the normal agent/tool path from copying credential bytes into chat or logs. It does
not claim isolation from malicious same-UID processes or protect against operator-controlled
changes to local SSH configuration.

## Acceptance

`plugins/harness-core/tests/test_secret_delivery.py` verifies:

- unrelated and unclassified prompts do not mint receipts;
- receipt and wrapper output contain no synthetic file bytes;
- exact host/path and explicit port reach fixed SCP argv;
- a receipt launches at most once;
- session mismatch and source mutation prevent transfer;
- Claude-shaped and camel-case prompt payloads are accepted.

Slice 1 denial tests and cross-CLI manifest drift checks must remain green.
