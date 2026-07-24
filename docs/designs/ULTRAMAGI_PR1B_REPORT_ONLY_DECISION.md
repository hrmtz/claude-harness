# PR 1b report-only convergence decision

Date: 2026-07-24
Issue: `hrmtz/claude-harness#107`
Decision: adopt a read-only convergence kernel; do not implement PASS enforcement in PR 1b.

## Inputs and authority

This decision follows:

- the 16/16 implementation-convergence design campaign, whose exact revision
  `f44642fe7f055a1fefc2f48a67a676a8309dc3642b7023ad63e66ef3337e03f5`
  ended with Round 8 Grok `REVISE` and a denied mechanical plateau;
- the independent Kimi scope judgment in
  `ULTRAMAGI_PR1B_KIMI_SCOPE_JUDGMENT.md`;
- Issue #107 comment `5067518046`, proposing an exact-SHA packet, tool-free heterogeneous
  reviewers, stable finding deltas, bounded cycles, and deterministic terminal states.

The Kimi artifact is not a review round and is not part of campaign accounting.

## Adopt in PR 1b

PR 1b contains only a report-only evaluator over existing, already-charged campaign artifacts:

1. an exact target tree/full-diff packet embedded in the reviewed manifest, plus complete
   ledger-to-artifact and content-addressed historical-manifest validation;
2. stable cross-revision finding identity and deterministic finding deltas;
3. deterministic `CONTINUE`, `FINAL_REVIEW_REQUIRED`, `BLOCKED`, or `REDESIGN`;
4. an explicit maximum of two logical full-review cycles, where a cycle is the existing
   `fanout(3) -> xfamily(1)` transition pair;
5. budget and next-transition affordability checks using the campaign guard's existing
   accounting helpers;
6. read-only extraction of G1–G6/G9 verification shared with the existing plateau gate.

PR 1b does not emit `PASS` or `PASS_WITH_RESIDUALS`, launch a model, write campaign state, or
write a plateau marker. Existing G1–G9 exact-revision plateau remains the sole mechanical PASS
authority.

The external proposal's exact-SHA packet principle is adopted as a trusted local packet builder.
The existing adapters can review those packet bytes without new launch phases. Capability-proven
tool-free transport remains PR 3.

## Defer

PR 2 owns the prerequisites for any future enforcement:

- requirement-revision cancellation, child ownership, locks, and bounded cleanup;
- shared claim publication reconciliation;
- a durable provider-started nonce that prevents uncharged replay;
- tool-inaccessible provider authentication and credential-sentinel probes;
- audited provider confinement and detached-snapshot execution;
- trusted test-result production and evidence receipts.

PR 3 owns review-transport and cost optimization:

- provider-facing bounded exact-SHA packets;
- capability-proven tool-free adapters;
- heterogeneous panel composition;
- diff-scoped and weight-1 incremental review;
- calibrated corroboration and adaptive stopping.

## Conditional adoption of voting rules

The proposed `2/3` rule is suitable for corroborating ordinary findings but is not PASS
authority. A singleton normal finding may become a non-authorizing `QUESTION`; a singleton
evidence-backed CRITICAL, security, or data-loss finding is a veto. Any unresolved HIGH+ or
unresolved `QUESTION` still prevents a PASS-family result.

This policy is deferred to PR 3 because current launch accounting hard-codes a homogeneous
three-member fanout, while the proposed Codex/Grok/Kimi panel requires new adapters and capability
proof. It must not add an uncharged judge or an extra final launch.

## Why tool-free does not erase the Round 8 blockers yet

Claude and Grok expose explicit tool-selection controls. The installed Codex and Kimi CLIs do not
currently expose an equivalent, mechanically checkable "no tools at all" contract in their
headless help surfaces. Prompting a reviewer not to use tools is not capability removal.

Therefore PR 1b cannot delete sandbox/auth protections on the assumption that all reviewers are
tool-free. The report-only cut removes provider execution entirely, making the Round 6–8
confinement, credential, claim-replay, and publication-reconciliation roots unreachable in PR 1b
without pretending they are resolved.

## Required PR 1b properties

- the evaluator is byte-for-byte read-only with respect to the repository, ledger, and
  `.dual-magi/`;
- malformed, missing, stale, or identity-mismatched evidence returns `BLOCKED`;
- a repeated blocking root or qualifying regression returns `REDESIGN`;
- two completed logical cycles cannot lead to another full cycle;
- budget exhaustion never authorizes shipping;
- no PR 1b result bypasses the existing exact-revision cross-family plateau gate.
