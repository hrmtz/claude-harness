# DESIGN — Credential Leak GAP #3: Multi-step / Transformation Taint Correlator

*Status: **RESOLVED — DON'T-BUILD (accepted limitation).** Superseded by the verdict below; the body is retained as the review record. Decision logged in [ROADMAP §5](../ROADMAP.md#credential-transform-exfil-gap-3--taint-correlator-rejected-below-waterline).*

---

## VERDICT (dual-magi round 1, 2026-05-31)

**4 reviewers across 2 model families — unanimous REJECT / DON'T-BUILD.** (MELCHIOR, BALTHASAR, CASPAR + cross-family codex-exec.) GAP #3 is accepted as a **known, documented limitation**; no taint-correlator code ships. The §6.3 fork resolved to "ship nothing, document the gap."

Three load-bearing reasons (cross-family confirmed):

1. **§6.2 "opaque egress" is non-identifiable, not just hard to calibrate.** A transformed secret (`base64`/`gzip`/`openssl enc`) and routine high-entropy dev output — git SHAs, JWTs, base64 data-URIs, terraform state, container IDs, gzip dumps — occupy the *same* byte distribution. No entropy/length threshold separates them. "Calibrate harder" is a non-fix.
2. **§4.3 "scan harder during the taint window" is a structural no-op *and* net-negative.** The existing sensor only HMAC-matches *known raw* values; a transformed value is never in the table, so no escalation helps — and raising the scan caps regresses the working sensor toward its `SIGKILL → fail-open` boundary. A warn-only signal with non-zero FP trains the single operator to dismiss alerts and dilutes the FP-safe hash-match channel (negative value).
3. **Below-waterline on a single-uid self-owned box.** A transform-then-exfil adversary has user-level code-exec and trivially defeats every source / window / channel (source evasion, window-split, post-hoc network timing, file-laundering — several conceded in §5/§6.5). This is the same class as crypto-identity, which was also declined.

**Grounding drifts the review caught in this very doc** (why self-review would have missed it): the headline source "Read of a SOPS file" is *not* detected by `credential_file_read_guard.sh` (it matches plain `.env`/`.pem`, never `.enc.yaml`); bare `sops exec-env` is undetected *and* is the box's normal operating mode (taint would be ~always armed); "all egress channels" overclaims (`WebSearch` is uncovered).

**Cross-family's added catch (the Claude family's shared blind spot):** even the §6.5 "pre-egress network warn" the three same-family reviewers converged on as "the buildable win" is weak — normal secret use is `sops exec-env file cmd` with the value env-injected/piped, so the network command rarely carries a literal secret-path in argv. The only FP-safe residue is a handful of *deterministic same-command* Bash patterns, most already blocked by `bash_command_guard.sh`.

**Real follow-ups surfaced (tracked separately, not part of this decision):** `WebSearch` is an uncovered egress channel for the value sensor (a GAP #2 coverage hole).

---

### (Original pre-flight framing — retained as the review record)

*Status was: PRE-FLIGHT REVIEW (dual-magi round 1). The §6 open decisions below were the input to the verdict above.*

## 0. Reviewer orientation

This doc is the design artifact for the **third and only genuinely new layer** in the harness-core credential-leak defense. GAP #2 (egress-channel coverage of the existing known-secret HMAC sensor) is already addressed by PR #11 and is **not** in scope here. Reviewers should pressure-test §4 (proposed design), §5 (what it deliberately won't do), and especially §6 (open decisions) — and should treat §6.3 ("ship nothing, document the gap") as a live, acceptable outcome of this review, not a failure.

## 1. The gap

The existing PostToolUse sensor (`credential_scrub.py`) matches **raw known-secret values** (HMAC of SOPS-manifest credentials) against tool output, and redacts the transcript on a hit. It is FP-safe and now covers all egress channels (PR #11). Its structural limit:

> **Value-fingerprinting is defeated by transformation.** If an agent (compromised by prompt injection, or simply misbehaving) does `base64`/`gzip`/`xor`/`openssl enc` on a secret *before* it crosses an egress boundary, the raw literal never appears in the output, so no fingerprint matches. The transformed blob exfiltrates cleanly.

This is the multi-step exfil shape: each individual step is benign (read a config; encode a string; make a network call), and the value sensor — which only ever sees one tool output at a time — has nothing to match.

## 2. The approach we REJECT (and why reviewers should hold us to it)

All three independent design passes initially proposed **transform-reversal**: on each tool output, try a fixed list of decoders (base64, base64url, gzip, hex, rot13), and re-run the HMAC scan on each decoded blob.

**Rejected by all three adversarial critiques, consensus:**
- **Perf bomb.** Each output is re-scanned ×N transforms; the decoded blobs are themselves large; this multiplies the already-quadratic sliding-HMAC cost.
- **Security theater.** The transform set is infinite. `openssl enc -aes-256` with any key, a custom base32 alphabet, byte-reversal, or splitting the secret across two tool calls each defeats a fixed decoder list. Adding decoders is **whack-a-mole on the infinite *transform* set** — the exact failure mode this whole effort exists to escape.

**Conclusion:** you cannot redact what you cannot recognize. Stop trying to recover the secret's *value* through transformation. The only thing that survives transformation is **flow**: the knowledge that a secret SOURCE was touched, correlated with a subsequent EGRESS.

## 3. Reframe: the three finite sets

The harness's guiding principle is "enumerate the finite, not the infinite." Attack *methods* and *transforms* are infinite. These three sets are finite and enumerable:

1. **SOURCES** — where a secret enters agent-reachable context: a `Read` of a SOPS file; the `HRMTZ_ACK_CRED_READ` bypass firing; a Bash command string containing `sops exec-env` / `sops edit`.
2. **CHANNELS** — egress points that land in the transcript or leave the box: each tool's PostToolUse output (covered by the value sensor); outbound-network Bash commands (`curl`/`wget`/`nc`/external `ssh`/`git push`); file writes outside the repo.
3. **KNOWN-VALUES** — the HMAC manifest (the value sensor's domain).

The value sensor is the sensor on set 3. **This design proposes a sensor on the SOURCE→CHANNEL *arc*** — taint flow — to cover what value-matching structurally cannot.

## 4. Proposed design (taint-flow, survives transformation)

### 4.1 Source sensor (reuse, do not duplicate)
Extend the **existing** `credential_file_read_guard.sh` (already wired `PreToolUse:Read`) and the Bash guard to *also* set a session taint flag when a SOURCE is touched. **Do not add a second overlapping Read hook** (structural-before-behavioral; avoid hook multiplicity).

### 4.2 Taint state
Per-session file: `~/.claude/state/credential_scrub/session_<id>.taint.json` =
`{ "armed_until": <ts>, "source_kind": "...", "egress_seen": <n> }`.

**Honest limitation (do not paper over):** on `sops exec-env`, the hook sees only the command *string*, never which keys the subprocess consumed. Taint is therefore **coarse — "a credential source was touched," not "key K is live."** Coarse-but-true beats precise-but-fabricated.

### 4.3 Egress watch + action
While taint is armed (sliding window, refreshed on each source touch), the existing PostToolUse sensor runs as normal **plus** a structural escalation: taint acts as a *knob on the existing sensor* (scan harder / lower the skip threshold during the window), not a standalone advisory stream.

- **Action = WARN, never BLOCK.** Hooks are fail-safe; blocking a tool on a heuristic violates the never-break-the-session invariant.
- The warning is **structural and rare**: it fires only when (taint armed) **AND** (an egress channel emitted output the value sensor could not clear as benign — i.e. an opaque/high-entropy blob left the box during a live credential window). We cannot prove the blob *is* the secret, but "opaque egress during a credential-source window" is precisely what a Purple Team operator wants surfaced.

### 4.4 FP controls (make-or-break)
Every prior critique killed taint designs on alert-fatigue. Therefore:
1. **No per-tool advisory** — at most one warning per taint window, deduped.
2. **No transform-guessing** (see §2).
3. Taint **auto-disarms** on window expiry.
4. **Silent-log-only by default; no gh issue** for taint events (only *hash-match* leaks file issues). Promote to a visible warning only on the opaque-egress-during-window correlation.

## 5. Deliberately NOT in scope
- Transform-reversal correlator (§2).
- Per-key taint granularity from `sops exec-env` (§4.2).
- Chunked / cross-session split-exfil detection (requires a cross-call state machine; low threat on a single-uid box, trivial bypass).
- Cross-host / crypto-identity / multi-tenant threats (out of scope: single self-owned host, single uid).
- Any blocking action (fail-safe is inviolable; warn + redact only).

## 6. OPEN DECISIONS the review must settle (do not pre-decide)

### 6.1 Window length
60s vs 120s vs activity-based. Too long = noise; too short = misses multi-tool workflows. Needs empirical session-trace calibration — propose a method.

### 6.2 "Opaque egress" definition
What entropy/length threshold counts as a blob worth warning on, **without** firing on every legitimate base64 (git objects, image data-URIs, JWTs, terraform state)? This is the FP cliff. The layer is net-negative unless this is defined precisely. This is the single highest-risk decision.

### 6.3 BUILD vs DON'T-BUILD (the real fork)
Is coarse taint high-enough signal to justify the layer at all — or is the honest move to **document GAP #3 as a known, accepted limitation and ship nothing**? On a single-uid self-owned box where the dominant real threat is the agent *accidentally* printing a DSN (already caught by the value sensor), a transformation-exfil adversary is low-probability and can trivially out-maneuver a coarse heuristic. "Drop the heuristic, document the gap honestly" is a legitimate — possibly the correct — outcome. The review must adjudicate this explicitly.

### 6.4 Source-detection completeness
Is the existing read-guard's path-matching sufficient to catch non-standard SOPS paths, or does it need a configurable source list?

### 6.5 Network-egress reality
`PostToolUse` is **post-hoc** — `curl -d @secret` has already left the box before the hook runs. Transcript redaction ≠ exfil prevention for outbound network. Should outbound-network Bash commands get a `PreToolUse` *warn* (the only point before egress), and does that change the §6.3 calculus?

## 7. Acceptance criteria for "round 1 complete"
A decision on §6.3 (build / don't-build / build-reduced-scope), and — if build — concrete settled values for §6.1 and §6.2, plus a stance on §6.5. Anything less, round 1 is not done.
