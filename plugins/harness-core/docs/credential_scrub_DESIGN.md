# credential_scrub: design (v2, post round-1 revise)

**Status**: skeleton v2 for dual-magi round 2. NOT deployed. NOT wired into settings.json.
**Author**: claude-moss-lantern, 2026-05-24
**Predecessor**: v1 skeleton at `/tmp/cred_hash_skeleton/` REJECTED by all 4 reviewers in round 1.

## Changelog v1 → v2

| round 1 finding | v2 resolution |
|---|---|
| **Codex-1 REJECT** (JSON-escape mismatch in raw-byte replace) | Replaced byte-replace with **JSON-aware traversal**: parse each jsonl line as JSON, walk all string fields recursively, replace decoded literal in decoded string, serialize back via `json.dumps(ensure_ascii=False)`. |
| **Codex-2 REJECT** (ambient env capture in builder) | Build wrapper snapshots `env \| cut -d= -f1` BEFORE `sops exec-env`, passes file via `--baseline-env-file`. Build script rejects with hard error if any sops-declared key was already in baseline. |
| **Codex-3 HIGH** (broad sidecar glob = audit tamper) | Manifests loaded ONLY from `~/.claude/state/credential_scrub/manifest/`. No discovery glob, no `~/projects/**`. Refused if not regular file or wrong uid. |
| **Codex-4 HIGH** (key-name injection via marker / context) | Strict key-name regex at load (`^[A-Z][A-Z0-9_]{0,63}$`). Marker is generic `<REDACTED>` (no key name). additionalContext emits count only. |
| **Codex-5 HIGH** (char vs byte length) | Manifest stores `byte_length = len(value.encode('utf-8'))`. Scanner slices output bytes by byte_length. |
| **Codex-6 HIGH** (P4 violated by scan-time ImportError) | `scan_output` + `redact_jsonl` wrapped in fail-safe try/except. Top-level `BaseException` handler also exits 0. `set -e` removed from bash wrapper. |
| **Codex-10 MED** (additionalContext leaks key + length) | additionalContext emits count only, no key names, no lengths. Detail goes to hook log only. |
| **Codex-11 HIGH** (settings matcher) | DESIGN.md §6 below specifies exact entry: `matcher: "Bash\|Read\|Edit\|Task"`, timeout 30. |
| **Codex-12 LOW** (sed vs python rewrite drift) | DESIGN.md and code both describe Python JSON-aware rewrite; sed not mentioned. |
| **Melchior-1 REJECT** (corpus / mutation target mismatch) | Detection corpus = decoded `parse_tool_output` (= stdout + stderr + output + content concat via lib.sh `jq` script). Mutation target = jsonl record string fields via JSON traversal. Match in decoded form must ALSO produce string-field hit in jsonl; if zero hits despite detection, log + warn (= leaked via boundary not covered, e.g., field outside the four). |
| **Melchior-2 REJECT** (perf) | (a) `MAX_SCAN_BYTES = 256_000` hard cap; oversize → scan SKIPPED + visible warning. (b) Candidate-run pre-filter (= regex `[A-Za-z0-9._\-+/=:@%!#$&*()|~?\[\]]{16,}`) reduces effective scan corpus to base64-ish runs only. Only candidate runs are sliding-hashed. (c) `MAX_MANIFEST_ENTRIES = 500` caps loaded set. Rabin-Karp deferred — Phase A perf measured against (a)+(b). |
| **Melchior-3 REJECT** (lib.sh ignored) | Bash wrapper sources `~/.claude/hooks/lib.sh`, exports `HOOK_INPUT`, calls `active_jsonl` and `parse_tool_output` (= reads all 4 fields), passes via env vars to Python. |
| **Melchior-4 REJECT** (blake3 silent fallback) | Build script `preflight_algorithm()` raises RuntimeError if requested algo unavailable. Manifest declares algorithm truthfully (= what was actually used). Scrub hook compares manifest algorithm to runtime availability; mismatch → fail-safe + hook_log. |
| **Melchior-8 HIGH** (os.replace race) | Tempfile in same dir + `os.replace`. Documented caveat: Claude's append fd may temporarily orphan if open during replace; subsequent append reopens by path. Risk window = milliseconds; alternative (= flock) requires Claude cooperation we don't have. |
| **Melchior-10 HIGH** (hash collision discards keys) | Manifest load uses `dict[hmac] -> list[key_name]`. Same hash across multiple sops files preserves all key names in hook_log (not in user-facing marker). |
| **Balthasar-1 REJECT** (P3 framing wrong) | Property P3 reworded as **damage-narrowing, not exposure-prevention**. New §3 explicit: leak window = tool_response → model context → assistant message → API retention; hook narrows persistence on disk only. Rotation is mandatory on any detection. |
| **Balthasar-2/3 REJECT** (selector misses + sidecar oracle) | (a) Lowered `MIN_BYTE_LENGTH = 12` and `MIN_ENTROPY = 2.5` to admit weak user passwords. (b) Strong override: any name containing `PASSWORD/SECRET/TOKEN/PRIVATE_KEY/ACCESS_KEY/BEARER` bypasses entropy + skip-keyword. (c) URL_PATTERNS expanded to cover libsql, jdbc, redis-empty-user. (d) JSON inner field extraction (`private_key`, `private_key_id`, `client_secret`, `refresh_token`) for any *_JSON-shaped value. (e) PEM body extraction. (f) HMAC-keyed (BLAKE3 keyed mode or HMAC-SHA256) with per-host salt — manifests no longer act as oracle. (g) Explicit include manifest (`<file>.scrub.yaml`) for production-grade coverage; heuristic is fallback. |
| **Balthasar-4 REJECT** (stderr bypass) | `parse_tool_output` (lib.sh) emits all 4 fields. |
| **Balthasar-5 HIGH** (TOCTOU staleness) | Build script stores `source_mtime`. `--check-staleness` CLI mode for audit. `sops_edit_wrapper.sh` makes regeneration atomic with rotation. |
| **Caspar-1/2 REJECT** (blake3 + log path) | Build preflight hard-fails on missing blake3. Hook log path = `~/.claude/state/hook_logs/hooks.log` (canonical). |
| **Caspar-3 REJECT** (rotation friction) | `sops_edit_wrapper.sh` runs `sops edit` then auto-regenerates manifest atomically. Drop-in for `sops edit` muscle memory; recommended `~/.local/bin/sops-rotate` alias. |
| **Caspar-4 REJECT** (multi-host scope) | DESIGN.md §7 explicit: hook + manifests are per-host operational state. Per-host salt, per-host manifest dir. NOT committed to a secrets repo. |
| **Caspar-6/7/8 HIGH** (settings wiring + perf + bootstrap) | §6 explicit settings entry. Perf: 256KB cap + candidate-run filter. Bootstrap: `credential_scrub_build_all.sh` loops 27 files in one invocation. |
| **Caspar-9 HIGH** (gitignore policy) | Manifests live in `~/.claude/state/`, NOT in a secrets repo. No gitignore policy needed; physical separation enforces P2. |
| **Caspar-10 HIGH** (format_version) | Manifest declares `format_version: 1`. Hook refuses mismatched versions. Phase C will bump to version 2. |
| **Caspar-17 LOW** (kill switch) | `touch ~/.claude/hooks/credential_scrub.disabled` → hook exits 0 immediately. |
| **Balthasar-8 HIGH** (split / base64 / assistant / discord-notify) | §10 Limitations section enumerates uncovered vectors explicitly. Phase C considers Stop-event scrubbing + PreToolUse on `discord-notify`. |

## 1. Goal

Replace the v1 split (= L2 hash + L3 pattern) with **one unified PostToolUse scrubber** that:
1. Sources `lib.sh` for canonical hook semantics.
2. Hash-matches credential values literal from a local-only, HMAC-keyed manifest.
3. Mutates the active jsonl via JSON-aware traversal (not raw byte replace).
4. Coexists with existing `credential_value_scrub.sh` during migration; eventually subsumes it (= once HMAC manifest coverage is verified, the pattern hook can be removed).

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│ PreToolUse  (existing): bash_command_guard, credential_file_read_guard   │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │
                          Tool executes
                                 │
┌────────────────────────────────▼─────────────────────────────────────────┐
│ PostToolUse                                                              │
│                                                                          │
│   ┌────────────────────────────────────────────────────────────────┐     │
│   │  credential_scrub.sh  (= NEW unified scrubber, this design)    │     │
│   │     sources lib.sh → resolves transcript_path, all-4-field      │     │
│   │     output. Invokes Python with env. Python:                   │     │
│   │       1. Load HMAC manifests from ~/.claude/state/.../manifest/ │     │
│   │       2. Candidate-run prefilter on output (256KB cap)         │     │
│   │       3. HMAC-window match against manifest                    │     │
│   │       4. JSON-aware redact of transcript_path                  │     │
│   │       5. Generic context out to Claude (count only)            │     │
│   └────────────────────────────────────────────────────────────────┘     │
│                                                                          │
│   ┌────────────────────────────────────────────────────────────────┐     │
│   │  credential_value_scrub.sh  (= EXISTING pattern-based)         │     │
│   │     KEEP during migration: catches base64-encoded prefix forms │     │
│   │     (sk-ant-*, AKIA*) that HMAC literal-match cannot.          │     │
│   │     Will be re-evaluated after Phase A coverage is verified.   │     │
│   └────────────────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│ Build pipeline (= out of band, runs at rotation time)                    │
│                                                                          │
│   sops-rotate <file.enc.yaml>                                            │
│     └─ sops edit <file>          (= user edits)                          │
│     └─ if mtime changed:                                                 │
│          sops exec-env <file>                                            │
│            'python3 credential_scrub_build.py                            │
│                --source-file <file>                                      │
│                --baseline-env-file <pre-exec-env snapshot>'              │
│          → writes ~/.claude/state/credential_scrub/manifest/<basename>.scrub.json
│            atomically, 0o600 perms                                       │
│                                                                          │
│   credential_scrub_build_all.sh   (= bootstrap / refresh all)            │
│     loops every .enc.yaml in secrets-template/ with same env discipline. │
└──────────────────────────────────────────────────────────────────────────┘
```

## 3. Properties (= honest)

- **P1**: Hook process loads only `(key_name, byte_length, hmac_hex)`. Plaintext is never read from manifest, never written to manifest.
- **P2**: Manifest files live in `~/.claude/state/credential_scrub/manifest/` (mode 0600), with HMAC values keyed by a per-host salt in `~/.claude/state/credential_scrub/salt.bin` (mode 0400). Manifests do NOT travel with the secrets repo and are NOT safe to share across hosts. Same-host attacker with read access to salt+manifest can dictionary-attack short low-entropy values; mitigate by avoiding short low-entropy values in sops.
- **P3** (honest reframe): **The hook narrows on-disk persistence of leaked credentials in the active jsonl. It does NOT prevent exposure.** By the time PostToolUse fires:
  - The tool_response has already been written to the jsonl
  - The decoded content is already in Claude's active context window for the current turn
  - If this is an API-mediated session, content has already left the local machine
  - Claude's next assistant turn may reference / quote the value, producing a new leak outside this hook's scope

  → **Rotation is mandatory on any detection**. This hook reduces post-rotation forensic spread, not pre-rotation containment.
- **P4** (fail-safe): Any uncaught exception in load / scan / redact → exit 0 + hook log entry. Hook never blocks tool execution.
- **P5** (clean invocation): Build invocation `sops exec-env <file> 'python3 ... --source-file <file>.enc.yaml --baseline-env-file <path>'` is CLAUDE.md SOPS 2-command compliant. No `sops -d`, no outer pipe of plaintext. Plaintext lives only in `os.environ` within the subprocess.
- **P6** (no key-name disclosure to Claude): Marker is generic `<REDACTED>`. additionalContext emits count only. Key identity goes to hook_log (local file, mode 0600 in hook_logs dir).

## 4. Files

| path | role |
|---|---|
| `~/.claude/hooks/credential_scrub.sh` | bash wrapper, sources lib.sh, sets env, execs Python |
| `~/.claude/hooks/credential_scrub.py` | scan + redact implementation |
| `~/.claude/hooks/credential_scrub_build.py` | manifest generator (invoked under `sops exec-env`) |
| `<secrets-repo>/scripts/credential_scrub_build_all.sh` | bootstrap loop |
| `~/.local/bin/sops-rotate` (symlink) | wrapper around `sops edit` + auto-rebuild |
| `~/.claude/state/credential_scrub/salt.bin` | 32-byte per-host HMAC salt, 0o400 |
| `~/.claude/state/credential_scrub/manifest/<basename>.scrub.json` | per-source manifest, 0o600 |
| `<secrets-repo>/secrets-template/<file>.scrub.yaml` (optional) | explicit include manifest (Phase A production-grade coverage) |

## 5. Manifest schema (= format_version 1)

```json
{
  "format_version": 1,
  "algorithm": "blake3-keyed",
  "generated_at": "2026-05-24T07:30:00Z",
  "source_file_basename": "llm.enc.yaml",
  "source_file_id": "<sha256(absolute_path)[:16]>",
  "source_mtime": "2026-05-24T07:29:55Z",
  "phase": "A",
  "entries": [
    {
      "key": "ANTHROPIC_API_KEY_INGEST",
      "byte_length": 108,
      "hmac": "<hex>",
      "extracted_from": "strong_keyword | name_heuristic | url_password | json_field:<inner> | pem_body | explicit_include"
    }
  ],
  "skipped": [
    {"key": "CLOUDFLARE_ACCOUNT_ID", "reason": "skip_keyword_override"},
    {"key": "OPENALEX_API_KEY", "reason": "placeholder"}
  ]
}
```

## 6. settings.json wiring

Insert AFTER the existing `credential_value_scrub.sh` entry so the new hook runs second. Both receive the same hook payload (independent processes); ordering does not affect detection but does affect log ordering for audit.

```json
{
  "matcher": "Bash|Read|Edit|Task",
  "hooks": [
    {
      "type": "command",
      "command": "bash ~/.claude/hooks/credential_value_scrub.sh",
      "timeout": 10
    },
    {
      "type": "command",
      "command": "bash ~/.claude/hooks/credential_scrub.sh",
      "timeout": 30
    }
  ]
}
```

Timeout 30s budget split: ~50ms python startup + ~5-10ms yaml load × ≤64 manifests + ~5s scan worst-case (256KB cap + candidate-run filter). 30s allows margin for cold-cache filesystem access.

## 7. Multi-host scope (= per-host)

- Hook and manifest state are local to each Claude/Codex host.
- Salt + manifests are per-host operational state, NOT git-tracked.
- A secrets repo can remain the canonical sops storage and sync through its own pipeline, **without** sidecars/manifests riding along.
- Each host needs its own salt + manifest build pass (= run `--init-salt` + `build_all.sh`).
- This boundary keeps the security control simple and avoids sharing HMAC material across hosts.

## 8. Coverage health-check

After bootstrap, expected state:
- ≥ N manifests in `~/.claude/state/credential_scrub/manifest/` where N = `ls "$SECRETS_REPO"/secrets-template/*.enc.yaml | wc -l`
- All manifests have `source_mtime` within 1 second of the corresponding sops file mtime

Audit commands:
```bash
# Current sops file count
find "$SECRETS_REPO"/secrets-template -maxdepth 1 -name '*.enc.yaml' | wc -l

# Current manifest count
ls ~/.claude/state/credential_scrub/manifest/ 2>/dev/null | wc -l

# Staleness drift
python3 ~/.claude/hooks/credential_scrub_build.py --check-staleness
```

Suggested operational check: SessionStart hook can compare counts and surface a warning if drift detected. (Not in skeleton; Phase A+ enhancement.)

## 9. Selection policy (= Phase A)

Two layers:

**Layer 9a — Explicit include manifest** (= production-grade coverage): if `<file>.scrub.yaml` exists alongside the sops file with `include: [...]` / `exclude: [...]`, those rules are honored. Build script `--dry-run` reports candidate keys that match heuristic; user reviews and pins via include list.

**Layer 9b — Name heuristic fallback**:
1. If key is in `exclude:` of include manifest → skip
2. If key is in `include:` of include manifest → hash (bypass heuristic, but apply placeholder filter)
3. **Strong-keyword override**: name contains `PASSWORD/SECRET/TOKEN/PRIVATE_KEY/ACCESS_KEY/BEARER` → include (bypass entropy/skip-keyword)
4. **URL hint**: name contains `URL/DSN/CONN` and value parses as scheme://...:pw@... → extract pw, hash it
5. **Generic cred keyword**: name contains `KEY/PWD/AUTH/CRED/SALT` AND not in skip-keyword list → include (subject to byte_length ≥ 12 + entropy ≥ 2.5)
6. **JSON/PEM inner extraction**: any value that parses as JSON dict → extract `private_key`, `private_key_id`, `client_secret`, `refresh_token`, `access_token` inner fields; PEM bodies extracted whitespace-normalized
7. Otherwise: skip

Skip keywords (= treated as config, not credential): `BUCKET, ENDPOINT, REGION, HOST, PORT, EMAIL, USER, BIND, CMD, PATH, PROPERTY, PUBLIC, SITE_URL, AGENT`.

Placeholder regex (= already-rotated-out values): `(?i)<REDACTED>|changeme|placeholder|example|YOUR_*|test-token|dummy|TODO|FIXME|XXX|REPLACE-ME|FILL-ME|NOT_SET`.

## 10. Limitations (= explicit, not deferred)

This hook does **NOT** cover the following leak vectors. These require complementary mechanisms (= some exist as separate hooks, some are open).

| vector | coverage |
|---|---|
| **Assistant-message text quoting credential** | NOT covered — PostToolUse fires after tool, before next assistant turn. If Claude quotes the value in its response, it is written to the jsonl as `assistant`-type entry, which this hook does not re-scan. Phase C considers a Stop-event scanner. |
| **Out-of-band exfil** (`discord-notify`, curl-to-webhook with literal arg) | NOT covered — leaves the local machine via network before PostToolUse. Phase C considers a PreToolUse Bash matcher that scans command args against the manifest before execution. |
| **Split across tool calls** | NOT covered — credential split into two Bash invocations bypasses literal-match. Phase C considers a rolling buffer of recent tool outputs. |
| **base64 / hex / URL encode / gzip** | Partially covered by existing pattern-based hook (= matches `sk-ant-*` etc. prefix patterns); HMAC literal-match cannot. KEEP `credential_value_scrub.sh` during migration. |
| **Anthropic API retention** | Out of scope — the moment a credential is in tool_response, it has been sent to Anthropic infrastructure for the current turn. Hook cannot retract. Mitigated by rotation policy. |
| **Codex / formation peer panes** | NOT covered — peer agents run their own sessions; their tool outputs do not flow through another agent's Claude Code hook. Each peer needs its own scrubber (Codex side is a separate skill out of scope here). |

The reader is expected to internalize: **this hook is one layer in a defense-in-depth chain, not a complete solution**. Rotation discipline + sops 2-command principle + PreToolUse guards + this hook + L3 pattern + AgentShield nightly scan together form the chain. Removing any one layer leaves a known gap.

## 11. Operational runbook

### 11.1 Initial bootstrap (= one-time)

Default algorithm is **sha256-hmac** (= stdlib, always available). `blake3-keyed`
is optional ~3-5x perf upgrade requiring `pip install blake3` (on Debian 12+ /
PEP 668 hosts: use `--break-system-packages` or a dedicated venv). Phase A may
ship with sha256-hmac and upgrade later.

```bash
# 1. Place hook scripts
mkdir -p ~/.claude/hooks
cp /tmp/cred_hash_skeleton/v2/hooks/credential_scrub.{sh,py} ~/.claude/hooks/
cp /tmp/cred_hash_skeleton/v2/hooks/credential_scrub_build.py ~/.claude/hooks/
chmod +x ~/.claude/hooks/credential_scrub.sh

# 2. Place ops scripts
mkdir -p "$SECRETS_REPO"/scripts
cp /tmp/cred_hash_skeleton/v2/scripts/credential_scrub_build_all.sh \
   "$SECRETS_REPO"/scripts/
cp /tmp/cred_hash_skeleton/v2/scripts/sops_edit_wrapper.sh \
   ~/.claude/hooks/sops_edit_wrapper.sh
chmod +x "$SECRETS_REPO"/scripts/credential_scrub_build_all.sh \
         ~/.claude/hooks/sops_edit_wrapper.sh

# 3. Install sops-rotate symlink so muscle memory uses the wrapper (H4 round 2 fix)
mkdir -p ~/.local/bin
ln -sf ~/.claude/hooks/sops_edit_wrapper.sh ~/.local/bin/sops-rotate

# 4. Initialize per-host salt (one-time)
python3 ~/.claude/hooks/credential_scrub_build.py --init-salt

# 5. Build all manifests
bash "$SECRETS_REPO"/scripts/credential_scrub_build_all.sh

# 6. Wire into settings.json (see §6)

# 7. Verify
tail -n 20 ~/.claude/state/hook_logs/hooks.log | grep credential_scrub
ls -la ~/.claude/state/credential_scrub/manifest/   # should show N manifests, mode 0600
```

#### 11.1.1 Dotfile snippet (= recommended; structural shadow of `sops edit`)

To make `sops edit` automatically route through the wrapper, add to `~/.zshrc`
(or `~/.bashrc`):

```bash
# Route `sops edit` through credential_scrub manifest regen
sops() {
    if [ "$1" = "edit" ] && [ -n "${2:-}" ] && [ -f "$2" ]; then
        shift
        command sops-rotate "$@"
    else
        command sops "$@"
    fi
}
```

After this, `sops edit foo.enc.yaml` automatically regenerates the manifest;
all other `sops` invocations (encrypt, decrypt, exec-env, etc.) pass through
unchanged. **This is the H4 enforcement mechanism** — without it, bare
`sops edit` muscle memory silently drifts the manifest.

Verify by editing a sops file: should print `manifest regenerated for ...`
on save.

### 11.2 After credential rotation

If using `sops-rotate` wrapper (= recommended):
```bash
sops-rotate "$SECRETS_REPO"/secrets-template/llm.enc.yaml
# → opens sops edit; on save, automatically regenerates the manifest
```

If using raw `sops edit`:
```bash
sops edit "$SECRETS_REPO"/secrets-template/llm.enc.yaml
# → manually trigger rebuild:
bash /tmp/cred_hash_skeleton/v2/scripts/credential_scrub_build_all.sh --secrets-dir "$SECRETS_REPO"/secrets-template
```

### 11.3 Disable hook (incident triage)

```bash
touch ~/.claude/hooks/credential_scrub.disabled
# → hook exits 0 immediately on every invocation
# Re-enable with: rm ~/.claude/hooks/credential_scrub.disabled
```

### 11.4 False-positive triage

If hook redacts something that should NOT have been a credential:
1. Check `~/.claude/state/hook_logs/hooks.log` for the `credential_scrub` event — finds key name and source manifest.
2. Decide remedy:
   - Value is genuinely a credential and the FP was hallucinated: nothing to do.
   - Value is config that got included by heuristic: add to `<file>.scrub.yaml` `exclude:` list, rebuild manifest.
   - Value is a placeholder accidentally left in sops: rotate that key (it's leaking as plaintext); after sops edit + manifest rebuild, the placeholder hash drops out.

## 12. Open questions for Round 2 review

1. Performance: candidate-run prefilter relies on credentials being in a base64-ish alphabet. User-typed passwords with arbitrary chars may not match the prefilter regex; selector includes them in manifest but scanner never finds them. Worth measuring real coverage gap.
2. JSON-aware redaction: walks the entire jsonl on every PostToolUse. For sessions with very large jsonls (>50MB), file rewrite may exceed timeout even if scan was fast. Cap on rewrite? Skip rewrite if jsonl too big?
3. The `os.replace` race: window is small but real. Worst case = Claude's next append silently disappears. Acceptable? Or implement reverse approach (= append `<REDACTED>` entries instead of rewriting)?
4. Codex Round 1 suggested "capability gating" as alternative: prevent unsafe sops access at the source, not detect after. Out of scope for Phase A but worth tracking as Phase C+ direction.
5. CF_*_ID family handling: currently default skip via SKIP_KEYWORDS (= ACCOUNT/ZONE/TUNNEL). Adversarial reviewer flagged this as making CF endpoint subdomain leaks invisible. Include vs warn-only?
6. Manifest discovery cap (`MAX_MANIFEST_FILES = 64`): user has ~24 sops files now; 64 leaves headroom. Right number?
7. v2 still does naive sliding-byte-window. Rabin-Karp polynomial rolling hash + Bloom filter prefilter would amortize O(N) per length class. Skeleton flags as deferred until measured perf is unacceptable; Phase A may ship without it. Is that calibration correct?
8. Existing L3 (`credential_value_scrub.sh`) is kept during migration. When can it be retired? Proposed criterion: after 30 days of L2 + L3 dual operation with no L2-missed leaks attributed to forms L3 caught. Round 2 to assess.
