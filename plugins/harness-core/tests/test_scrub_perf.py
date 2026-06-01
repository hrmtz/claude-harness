#!/usr/bin/env python3
"""
Regression tests for credential_scrub.py scan_output: the MAX_CANDIDATE_RUN
per-run cap that lets the hash sensor be widened onto large MCP outputs without
fail-open (SIGKILL on timeout). scan_output is a pure function, so these run with
a hand-built by_length map — no salt/manifest state on disk required.

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


print(f"== MAX_CANDIDATE_RUN = {cs.MAX_CANDIDATE_RUN} ==")

# T1: a standalone known secret is found (baseline coverage).
secret = b"Sb9XqTokenValue001Z"          # 19 chars, in CANDIDATE_RUN alphabet, >=16
by_length = manifest_for(secret)
hits = cs.scan_output(b"psql failed for " + secret + b" here", by_length, SALT, ALGO)
if any(w == secret for w, _ in hits):
    ok("T1 standalone known secret found")
else:
    bad("T1 standalone known secret MISSED")

# T2: a known secret embedded in a MODERATE run (<= cap) is still found via sliding.
mid_run = b"AAAA" + secret + b"BBBB"      # one ~27-char run, under the cap
hits = cs.scan_output(mid_run, by_length, SALT, ALGO)
if any(w == secret for w, _ in hits):
    ok("T2 secret inside sub-cap run found (sliding works)")
else:
    bad("T2 secret inside sub-cap run MISSED")

# T3: a large STANDALONE secret up to 4096B (e.g. a PEM body) is still found.
big_secret = b"K" * 3000                  # 3000 <= 4096 -> must be covered
by_big = manifest_for(big_secret, "BIG_KEY")
hits = cs.scan_output(b"-----BEGIN-----\n" + big_secret + b"\n-----END-----", by_big, SALT, ALGO)
if any(w == big_secret for w, _ in hits):
    ok("T3 large standalone secret (3000B) found")
else:
    bad("T3 large standalone secret MISSED (cap too low?)")

# T4 (the headline perf regression): a single ~300KB contiguous base64-ish run.
# Without the cap this is millions of sliding HMACs (tens of seconds -> SIGKILL ->
# fail-open). With the cap the run is skipped; assert it returns FAST.
huge_run = b"a" * 300_000                 # one contiguous in-class run, >> cap
t0 = time.perf_counter()
hits = cs.scan_output(huge_run, by_length, SALT, ALGO)
dt = time.perf_counter() - t0
if dt < 2.0:
    ok(f"T4 300KB single run returns fast ({dt*1000:.0f}ms, cap skipped it)")
else:
    bad(f"T4 300KB single run SLOW ({dt:.1f}s) — cap not bounding the scan")

# T5: oversized run skip is a DOCUMENTED tradeoff (secret embedded in a >cap blob
# is not found). This asserts the intended behavior so it can't regress silently.
embedded = b"a" * 200_000 + secret + b"b" * 100_000   # secret inside a >cap run
hits = cs.scan_output(embedded, by_length, SALT, ALGO)
if not any(w == secret for w, _ in hits):
    ok("T5 secret inside >cap blob is skipped (documented tradeoff)")
else:
    bad("T5 unexpected: secret inside >cap blob was found")

print()
print(f"RESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
