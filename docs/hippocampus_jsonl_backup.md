# hippocampus jsonl backup (encrypted, R2)

Encrypted off-site backup of `~/.claude/projects/*.jsonl` — the hippocampus memory SSOT.

## Model
- **Local stays plaintext** → owner reads past sessions trivially ("show me yesterday's jsonl"). No local encryption, no `rm`.
- **R2 copy is age-encrypted** → no plaintext secret leaves the machine. Bucket `r2:hippocampus/jsonl/<relpath>.age`.
- **Incremental** via a local mtime manifest (`~/.local/state/hippocampus_backup/manifest.tsv`) — no remote ListObjects (the Object-RW R2 token can't list at account level).
- age recipient (public): `age1vrg258ktp7gnz6vwyeh28ss0vx5s37h45xlrjnzd5h0h3kp8lcgs4k33xf`. Decrypt key: `~/.config/sops/age/keys.txt` (SOPS-managed, the canonical key — keep it backed up; losing it makes the R2 copies unrecoverable).

## Run
```bash
bash scripts/backup_jsonl_to_r2.sh          # incremental (cron uses this)
bash scripts/backup_jsonl_to_r2.sh --full   # re-encrypt+upload everything
```
Cron (chichibu, daily 04:30 JST): see `scripts/hippocampus_backup.crontab`.

## Restore a session
```bash
# one session
rclone copyto "r2:hippocampus/jsonl/<relpath>.jsonl.age" /tmp/s.age
age -d -i ~/.config/sops/age/keys.txt -o /tmp/s.jsonl /tmp/s.age

# bulk (whole tree) — download then decrypt each
rclone copy r2:hippocampus/jsonl/ /tmp/restore/        # .age files
find /tmp/restore -name '*.age' -exec sh -c \
  'age -d -i ~/.config/sops/age/keys.txt -o "${1%.age}" "$1"' _ {} \;
```

## Notes
- `r2:hippocampus/jsonl/_manifest.json` carries run counts (no secrets) per canonical-artifact convention.
- The R2 token is Object Read & Write (all buckets); it cannot `rclone lsd r2:` (ListBuckets 403) — that's expected, not an error.
- rclone.conf rollback (if a creds update goes wrong): `~/.config/rclone/rclone.conf.bak_r2set`.
