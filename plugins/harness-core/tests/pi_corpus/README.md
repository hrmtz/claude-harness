# pi_corpus — standing prompt-injection regression battery

A red-team pass finds a pile of holes at once, and then the findings decay
because nothing re-checks them. This freezes each finding as a corpus row so
every commit re-runs it — the same shape `fp_corpus` uses for the credential
guards.

It answers one narrow question: **does untrusted text stay where the prompt
builder put it?** It does not, and cannot, tell you whether a model is persuaded
by content sitting inside a correctly formed fence. A green run means the
delimiters held and single-line fields stayed on one line. Nothing more.

## Layout

| file | role |
|---|---|
| `pi_adapter.py` | the contract. Sink/Sabotage dataclasses, invariants, sentinels, config errors |
| `build_corpus.py` | generates `corpus.jsonl`. `--check` verifies it is in sync (CI) |
| `corpus.jsonl` | 35 cases. Generated — never hand-edit |
| `run_pi_eval.py` | the runner. Loads a project adapter, executes, gates |
| `test_runner_gates.py` | negative controls for the runner itself |

`corpus.jsonl` deliberately carries raw control characters, lone surrogates and
bidi overrides. Hand-editing those in a `.jsonl` is a trap — a stray literal
makes the whole line unparseable — so the corpus is generated with
`ensure_ascii=True` and the generator is the source of truth.

## Writing an adapter

One file per project, exposing `SINKS` (and optionally `NOTATION`,
`STRUCTURAL_TOKENS`, `SABOTAGE`). Every sink calls the **real** prompt builder;
re-implementing the builder in the adapter measures the adapter.

```python
from pi_adapter import Sink, Sabotage, MISSING

SINKS = [
    Sink("chunk_body", render_chunk, "fenced",
         delims=extract_delims, nonce=extract_nonce, accepts_nonce=True),
    Sink("source_line", render_source, "single_line", delims=extract_delims),
]
```

`kind` is a generic shape, never a project field name, so corpus rows stay
portable:

- `fenced` — the text is wrapped in delimiters and may contain newlines. The
  question is whether it can break **out**.
- `single_line` — the text is interpolated into a line expected to stay one line
  (`    Source: {book} / {chap}`). A newline here grows new prompt structure
  outside any fence.

`render` must be side-effect free — no DB, no network, no model call. It receives
`None` for the explicit-null case and the `MISSING` sentinel for key-absent,
because `.get(k)` and `.get(k, default)` are different code paths.

## Invariants

| | question |
|---|---|
| **I1** | containment — untrusted text cannot escape its delimiters |
| **I1b** | neutralisation — structural tokens are defanged even *inside* the fence |
| **I2** | no new structure — a single-line sink stays one line |
| **I3** | domain notation survives verbatim |
| **I4** | the nonce is per-response |
| **I5** | malformed input does not crash the renderer |

I1 is split on purpose. A containment-only checker reports PASS for a raw
`[INST]` sitting quietly inside a well-formed fence, which is not the same claim.

I3 is the counterweight: a sanitiser that mangles `≥10⁶ CFU/mL` into
`≥106CFU/mL` has traded the product's value for safety theatre. NFKC
normalisation is the usual culprit.

## Reading the output

`PASS` / `FAIL` are verdicts. `ERROR`, `SKIPPED` and `UNMEASURED` are not — they
mean nothing was learned, and the runner prints them in their own section so
they can never be read as green. A vanished canary marker is an ERROR, not a
FAIL: "the renderer truncated my markers" and "the fence was breached" are
different events.

The coverage manifest lists every registered sink. **An unregistered field is
invisible** — that is the battery's real limit, and the manifest is how you see
what you chose to look at.

## The gate

Exit codes: `0` clean, `1` new failures, `2` config error, `3` the battery could
not prove it still detects.

**`ERROR` and `UNMEASURED` fail the gate alongside `FAIL`.** A run where some
cases blew up while the rest passed is the same silent pass in a smaller costume,
so a case nobody measured is never quietly green.

`--write-baseline` freezes the current failures *and unmeasurable cases* as
accepted, tracked gaps. Anything new fails the gate; a fixed one is reported but
never offsets a new one, and a baseline entry that merely stopped being measured
is listed as not re-measured rather than fixed. The baseline is the *only* amnesty mechanism —
`Sink(covered=False)` marks a known-undefended field in the manifest but forgives
nothing, because two amnesty mechanisms let one silently void the other.

Config mistakes are rejected rather than absorbed, since each otherwise degrades
into a silent pass — or worse: an empty adapter, zero executed cases, a duplicate
sink name, `accepts_nonce=True` with no extractor, a `Sabotage` with nothing to
match, and a `delims` hook returning an empty, identical, or non-string pair. A
`delims` hook may return `None` ("this sink has no fence"), but a pair it does
return has to be usable: an empty delimiter matches everywhere and nowhere, and
one token for both ends leaves containment with no direction to check.

## Negative controls

`SABOTAGE` is not optional decoration. Without it a green run only proves the
battery ran, not that it can still see:

```python
SABOTAGE = [
    Sabotage("source_line", build_unfolded_sink,
             expected=[("i2-heading", "I2"), ("i2-lone-cr", "I2")]),
]
```

`build` returns a replacement sink with one defence removed; `expected` names the
exact `(case, invariant)` pairs that must go red against it. Matching is
per-pair, never by count, so an unrelated crash cannot be mistaken for a
successful detection — an exception is recorded as ERROR and satisfies nothing.

## Checking the checker

`test_runner_gates.py` holds the runner to its own standard. Each case is a way
the runner could report green while measuring nothing, built as a small adapter
and asserted to produce a non-zero exit:

- every render raises, so no corpus case is actually judged
- only *some* cases unmeasurable while the rest pass — the partial version of the
  same hole, which a whole-run guard alone walks straight past
- no `SABOTAGE` declared, so nothing shows the battery still detects
- a sink that accepts a nonce and then discards it — every pinned I1 case would
  be attacking a delimiter the fence never used
- notation destroyed in the field while the surrounding template mentions it
- notation kept in the first copy of the field and destroyed in the second
- the first copy fenced and the second copy left bare
- a second occurrence neutralised in one copy and not the other
- a canary marker eaten on one occurrence, which must be ERROR and never PASS

All nine were live defects found by cross-family review on 2026-09-05 and are
frozen here so an edit cannot quietly reintroduce them. Four of them survived the
first round of fixes: the review's second pass showed that guarding the whole run
and joining the spans still let a *partially* broken render through.

## Running

```bash
python3 test_runner_gates.py                       # does the runner still see?
python3 build_corpus.py --check                    # corpus in sync?
python3 run_pi_eval.py --adapter path/to/adapter.py
python3 run_pi_eval.py --adapter ... --json        # machine-readable
python3 run_pi_eval.py --adapter ... --sink chunk_body   # narrow to one sink
```

## Provenance

Built 2026-09-05 after PRS-LLM epic #455. Four PRs had shipped and a hand-written
52-case battery reported 44/44 green, but a later review found live newline
injection in nine single-line metadata fields — every one of them *outside* the
fence the battery had spent all its cases attacking. The `I2` family exists
because of that blind spot; `i2-lone-cr`, `i2-line-sep`, `i2-para-sep` and
`i2-nel` exist because a fix that folds only `\n` walks past them.

First run against PRS-LLM confirmed all 96 of those and turned up a hole nobody
had looked for: `<<SYS>>` / `<</SYS>>`, the delimiter Llama-2 actually uses,
passed through a sanitiser that defangs the bracket form `[SYS]` (PRS-LLM #475).
