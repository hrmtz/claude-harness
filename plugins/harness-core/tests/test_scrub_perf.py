#!/usr/bin/env python3
"""
Regression tests for credential_scrub.py scan_output: the bounded, no-fail-open
scanner (#39). scan_output now (a) returns (hits, scan_complete), (b) scans large
outputs and long runs instead of hard-skipping them, and (c) stays fast via the
candidate-run prefilter + a global HMAC budget (MAX_SCAN_WINDOWS). scan_output is a
pure function, so these run with a hand-built by_length map — no salt/manifest state
on disk required.

Run: python3 plugins/harness-core/tests/test_scrub_perf.py
"""
import sys, time
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
sys.path.insert(0, str(HOOKS))
import credential_scrub as cs  # noqa: E402

SALT = bytes(32)            # all-zero salt is fine for a deterministic test
ALGO = "sha256-hmac"
PASS = 0
FAIL = 0


def ok(m):
    global PASS
    PASS += 1
    print(f"  PASS {m}")


def bad(m):
    global FAIL
    FAIL += 1
    print(f"  FAIL {m}")


def manifest_for(secret: bytes, key="TEST_KEY"):
    h = cs.compute_hmac(secret, SALT, ALGO)
    return {len(secret): {h: [key]}}


print(f"== MAX_CANDIDATE_RUN = {cs.MAX_CANDIDATE_RUN}  MAX_SCAN_WINDOWS = {cs.MAX_SCAN_WINDOWS} ==")

# T1: a standalone known secret is found (baseline coverage).
secret = b"Sb9XqTokenValue001Z"          # 19 chars, in CANDIDATE_RUN alphabet, >=16
by_length = manifest_for(secret)
hits, complete = cs.scan_output(b"psql failed for " + secret + b" here", by_length, SALT, ALGO)
if any(w == secret for w, _ in hits) and complete:
    ok("T1 standalone known secret found (complete)")
else:
    bad("T1 standalone known secret MISSED")

# T2: a known secret embedded in a MODERATE run (<= cap) is still found via sliding.
mid_run = b"AAAA" + secret + b"BBBB"      # one ~27-char run, under the cap
hits, complete = cs.scan_output(mid_run, by_length, SALT, ALGO)
if any(w == secret for w, _ in hits):
    ok("T2 secret inside sub-cap run found (sliding works)")
else:
    bad("T2 secret inside sub-cap run MISSED")

# T3: a large STANDALONE secret up to 4096B (e.g. a PEM body) is still found.
big_secret = b"K" * 3000                  # 3000 <= 4096 -> must be covered
by_big = manifest_for(big_secret, "BIG_KEY")
hits, complete = cs.scan_output(b"-----BEGIN-----\n" + big_secret + b"\n-----END-----", by_big, SALT, ALGO)
if any(w == big_secret for w, _ in hits):
    ok("T3 large standalone secret (3000B) found")
else:
    bad("T3 large standalone secret MISSED (cap too low?)")

# T4 (the headline perf bound): a single ~300KB contiguous base64-ish run with no
# matching secret. The run is now SCANNED (not hard-skipped), but the global budget +
# single short manifest length keep it FAST. Assert it returns well under timeout.
huge_run = b"a" * 300_000                 # one contiguous in-class run, >> cap
t0 = time.perf_counter()
hits, complete = cs.scan_output(huge_run, by_length, SALT, ALGO)
dt = time.perf_counter() - t0
if dt < 2.0:
    ok(f"T4 300KB single run returns fast ({dt*1000:.0f}ms, budget-bounded)")
else:
    bad(f"T4 300KB single run SLOW ({dt:.1f}s) — budget not bounding the scan")

# T5 (#39 fix): a secret embedded in a >cap delimiter-free blob is now FOUND — the
# old code hard-skipped the whole run and fail-OPENED. The scan still completes within
# budget for a single short manifest length.
embedded = b"a" * 200_000 + secret + b"b" * 100_000   # secret inside a >cap run
hits, complete = cs.scan_output(embedded, by_length, SALT, ALGO)
if any(w == secret for w, _ in hits):
    ok("T5 secret inside >cap blob is now FOUND (no fail-open)")
else:
    bad("T5 secret inside >cap blob MISSED — fail-open regression")

# T6 (no fail-open on huge total output): the old MAX_SCAN_BYTES hard skip meant any
# output over 256KB was not scanned at all. Now a secret in a delimited field of a
# ~600KB output (well over the old cap) must still be found and the scan complete.
big_output = (b'{"log":"' + b"x" * 600_000 + b'","key":"' + secret + b'"}')
t0 = time.perf_counter()
hits, complete = cs.scan_output(big_output, by_length, SALT, ALGO)
dt = time.perf_counter() - t0
if any(w == secret for w, _ in hits):
    ok(f"T6 secret in 600KB+ output FOUND (old size-skip removed, {dt*1000:.0f}ms)")
else:
    bad("T6 secret in oversized output MISSED — size hard-skip not removed")

# T7 (budget honesty): when many distinct lengths blow the budget on a giant blob,
# scan_output must report scan_complete=False rather than silently fail-open.
saved = cs.MAX_SCAN_WINDOWS
cs.MAX_SCAN_WINDOWS = 5000                  # tiny budget to force exhaustion
many = {L: {cs.compute_hmac(b"z" * L, SALT, ALGO): ["K%d" % L]} for L in range(12, 60)}
hits, complete = cs.scan_output(b"q" * 200_000, many, SALT, ALGO)
cs.MAX_SCAN_WINDOWS = saved
if complete is False:
    ok("T7 budget-exhausted scan reports INCOMPLETE (honest, no silent fail-open)")
else:
    bad("T7 budget-exhausted scan claimed complete — silent fail-open risk")

# T8 (HIGH boundary fix): a MAX-length secret straddling the head/tail split of an
# oversized delimiter-free blob must still be found. We force the pass-2 head+tail
# path (budget < full-run windows but >= head+tail) and place a 4096B secret so its
# last byte lands just INSIDE the tail region, i.e. its start is BEFORE
# run_len - MAX_CANDIDATE_RUN. The old tail_start = run_len - MAX_CANDIDATE_RUN never
# hashed that window; the backed-off tail_start does.
ML = cs.MAX_CANDIDATE_RUN                    # 4096 = longest allowed secret
boundary_secret = b"K" * ML                  # exactly max length, single in-class run
by_bnd = manifest_for(boundary_secret, "BND_KEY")
run_len = 300_000
start = run_len - ML - (ML - 1) + 10         # start just AFTER backed-off tail_start
assert 0 < start < run_len - ML
blob = b"a" * start + boundary_secret + b"a" * (run_len - start - ML)
assert len(blob) == run_len
saved_w = cs.MAX_SCAN_WINDOWS
cs.MAX_SCAN_WINDOWS = 100_000               # < full-run windows, >> head+tail (~8192)
hits, complete = cs.scan_output(blob, by_bnd, SALT, ALGO)
cs.MAX_SCAN_WINDOWS = saved_w
if any(w == boundary_secret for w, _ in hits):
    ok("T8 max-length secret straddling tail boundary FOUND (boundary fix)")
else:
    bad("T8 boundary secret MISSED — tail_start not backed off (HIGH regression)")
if complete is False:
    ok("T8b pass-2 best-effort scan reports INCOMPLETE")
else:
    bad("T8b pass-2 head+tail scan claimed complete")

# T9 (wall-clock soft-deadline): a past deadline must stop the scan and report
# INCOMPLETE rather than running the full budget. Independent of MAX_SCAN_WINDOWS.
saved_s = cs.MAX_SCAN_SECONDS
cs.MAX_SCAN_SECONDS = -1.0                   # already expired -> trip on first sample
t0 = time.perf_counter()
hits, complete = cs.scan_output(b"q" * 200_000, by_length, SALT, ALGO)
dt = time.perf_counter() - t0
cs.MAX_SCAN_SECONDS = saved_s
if complete is False and dt < 1.0:
    ok(f"T9 wall-clock deadline trips scan INCOMPLETE fast ({dt*1000:.0f}ms)")
else:
    bad(f"T9 deadline guard ineffective (complete={complete}, {dt:.2f}s)")

# T10 (incomplete-messaging honesty, MED): a redacted-but-incomplete scan must NOT use
# the all-clear "No manual steps needed" wording; a complete scan must.
msg_complete = cs.resume_context(2, scan_complete=True)
msg_partial = cs.resume_context(2, scan_complete=False)
if "No manual steps needed" in msg_complete:
    ok("T10a complete scan keeps all-clear wording")
else:
    bad("T10a complete-scan wording regressed")
if "No manual steps needed" not in msg_partial and "INCOMPLETE" in msg_partial \
        and "Manual review" in msg_partial:
    ok("T10b incomplete-but-redacted scan drops all-clear, asks for manual review")
else:
    bad("T10b incomplete scan still implies fully auto-handled (MED regression)")

# T11 (codex re-review MED: dedup bypass): a large run of REPEATED already-matched
# windows must NOT bypass the budget/deadline. With an expired wall-clock deadline the
# scan must report INCOMPLETE (the duplicate-hit `continue` no longer skips accounting)
# and return promptly instead of slicing the whole run unbounded.
rep_secret = b"RepEatEdSecret01"            # 16 chars, in CANDIDATE_RUN alphabet
by_rep = manifest_for(rep_secret, "REP_KEY")
repeated = rep_secret * 130_000             # ~2MB of back-to-back identical matches
saved_s = cs.MAX_SCAN_SECONDS
cs.MAX_SCAN_SECONDS = -1.0                   # already expired
t0 = time.perf_counter()
hits, complete = cs.scan_output(repeated, by_rep, SALT, ALGO)
dt = time.perf_counter() - t0
cs.MAX_SCAN_SECONDS = saved_s
if complete is False and dt < 1.0:
    ok(f"T11 repeated-match run honors deadline -> INCOMPLETE, no unbounded loop ({dt*1000:.0f}ms)")
else:
    bad(f"T11 dedup bypass: deadline ignored (complete={complete}, {dt:.2f}s)")

print()
print(f"RESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
