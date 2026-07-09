# hippocampus jsonl backup (encrypted, rclone)

Encrypted off-site backup of `~/.claude/projects/*.jsonl` — the hippocampus memory SSOT.

## Model
- **Local stays plaintext** → owner reads past sessions trivially ("show me yesterday's jsonl"). No local encryption, no `rm`.
- **Remote copy is age-encrypted** → no plaintext secret leaves the machine. Destination example: `rclone-remote:path/jsonl/<relpath>.age`.
- **Incremental** via a local mtime manifest (`~/.local/state/hippocampus_backup/manifest.tsv`) — no remote ListObjects (the Object-RW R2 token can't list at account level).
- Set `HARNESS_JSONL_BACKUP_DEST` to the rclone destination and `HARNESS_JSONL_BACKUP_RECIPIENT` to your public age recipient. Keep the matching age private key backed up; losing it makes remote copies unrecoverable.

## Run
```bash
bash scripts/backup_jsonl_to_r2.sh          # incremental (cron uses this)
bash scripts/backup_jsonl_to_r2.sh --full   # re-encrypt+upload everything
```
Cron example: see `scripts/hippocampus_backup.crontab`.

## Restore a session
```bash
# one session
rclone copyto "$HARNESS_JSONL_BACKUP_DEST/<relpath>.jsonl.age" /tmp/s.age
age -d -i ~/.config/sops/age/keys.txt -o /tmp/s.jsonl /tmp/s.age

# bulk (whole tree) — download then decrypt each
rclone copy "$HARNESS_JSONL_BACKUP_DEST/" /tmp/restore/        # .age files
find /tmp/restore -name '*.age' -exec sh -c \
  'age -d -i ~/.config/sops/age/keys.txt -o "${1%.age}" "$1"' _ {} \;
```

## Notes
- `<dest>/_manifest.json` carries run counts (no secrets) per canonical-artifact convention.
- Some object-store tokens cannot list buckets at account level; `rclone lsd <remote>:` may fail even when `copyto` works.
