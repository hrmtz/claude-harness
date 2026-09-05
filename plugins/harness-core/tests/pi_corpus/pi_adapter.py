"""Adapter contract for the prompt-injection regression corpus.

A project under test supplies one `adapter.py` exposing `SINKS` (and optionally
`NOTATION` / `SABOTAGE`). The runner never imports project internals directly —
everything it needs is declared here.

Design notes (cross-family review, 2026-09-05):
  - A bare ``callable(payload) -> str`` is NOT enough. The runner cannot see where
    the legal fence boundary is, so containment (I1) and nonce (I4) would collapse
    into substring matching — the exact bug the first hand-written battery hit.
    Hence a descriptor with ``kind`` plus optional extraction hooks.
  - ``kind`` is a GENERIC shape (single_line / fenced), never a project-specific
    field name. Corpus rows key off the kind so the corpus stays portable.
  - Unregistered fields cannot be measured. The runner emits a coverage manifest
    and refuses to report success on an empty adapter / empty sink list / zero
    executed cases.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence


class _Missing:
    """Sentinel: the field key was absent entirely (distinct from ``None``)."""

    _instance: Optional["_Missing"] = None

    def __new__(cls) -> "_Missing":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "MISSING"


#: Passed as ``payload`` when the corpus case means "key not present at all".
MISSING = _Missing()

#: Notation that must survive verbatim through a sink. Projects may replace the
#: list wholesale; anything they add is concatenated onto this default.
DEFAULT_NOTATION: tuple[str, ...] = (
    "10⁶", "H₂O", "CO₂", "µg", "≥", "≤", "±", "℃", "α", "β", "γ", "½", "Ω",
)

#: Fixed, benign markers that BRACKET the untrusted text. A raw payload substring
#: cannot be located once the project has escaped or rewritten it, so every
#: positional check rides on these instead.
#:
#: Two markers, not one: a lone marker only answers "is it at a line start?",
#: which a payload with a prefix (``X<canary>\n## heading``) walks straight past.
#: Bracketing lets the runner ask the precise question for a single-line sink —
#: *did a new line break appear BETWEEN the markers* — while still tolerating the
#: newlines the surrounding template legitimately owns.
#:
#: **Bracketing applies to I1 and I2 only** — the invariants that ask *where* the
#: text landed. I5 (robustness) renders the raw input untouched: wrapping ``""``
#: or ``"   "`` in markers makes it non-empty, and ``None`` / :data:`MISSING`
#: cannot be concatenated at all, so canaries would silently convert every
#: robustness case into a different case.
#:
#: If either marker VANISHES the case is an **observability ERROR, not a FAIL**.
#: "the renderer truncated my markers" and "the fence was breached" are different
#: events; scoring the first as a vulnerability manufactures false reports, and
#: scoring it as a pass hides that nothing was measured.
CANARY_OPEN = "ZQCANARYA7"
CANARY_CLOSE = "ZQCANARYZ7"

#: The invariants. I1 is deliberately SPLIT: "did the text stay inside the
#: fence" and "was the dangerous token defanged" are different questions, and a
#: containment-only checker reports PASS for a raw ``[INST]`` sitting quietly
#: inside the fence. A corpus case whose invariant has no predicate the sink can
#: answer is reported UNMEASURED — never PASS.
INVARIANTS = {
    "I1": "containment — untrusted text cannot escape its delimiters",
    "I1b": "neutralisation — structural tokens are defanged even INSIDE the fence",
    "I2": "no new structure — a single-line sink stays one line",
    "I3": "notation survives verbatim",
    "I4": "nonce is per-response",
    "I5": "malformed input does not crash the renderer",
}

#: Sink shapes the runner knows how to reason about.
#:   single_line — untrusted text is interpolated into a line that is expected to
#:                 stay ONE line (``    Source: {book} / {chap}``). Newlines here
#:                 grow new prompt structure outside any fence. This is the shape
#:                 the 2026-09-05 battery missed entirely.
#:   fenced      — untrusted text is wrapped in delimiters and is allowed to
#:                 contain newlines. The question is whether it can break OUT.
KINDS = ("single_line", "fenced")


@dataclass(frozen=True)
class Sink:
    """One place a project interpolates untrusted text into a prompt.

    render:
        ``(payload: str | None | MISSING, *, nonce: str | None = None) -> str``
        — build the prompt fragment with *payload* placed in this sink. Must be
        side-effect free: no DB, no network, no LLM.

        ``payload`` is ``None`` for the explicit-null case and the
        :data:`MISSING` sentinel for the key-absent case. ``None`` alone cannot
        express "the key was not supplied at all", and those two paths often
        differ (``.get(k)`` vs ``.get(k, default)``), so both are exercised.

        ``nonce`` is passed only when :attr:`accepts_nonce` is True.
    kind:
        One of :data:`KINDS`.
    delims:
        ``(fragment: str) -> tuple[str, str] | None`` — for ``fenced`` sinks,
        return the (open, close) delimiter actually used in *fragment*. Without
        this the runner cannot tell a legal boundary from a forged one, so a
        fenced sink that omits it is reported as PARTIAL rather than PASS.
    nonce:
        ``(fragment: str) -> str | None`` — extract the per-response nonce.
        Absent ⇒ I4 is reported as SKIPPED (never as PASS).
    accepts_nonce:
        True when ``render`` honours a caller-supplied nonce, enabling the
        "same nonce is reused / a fresh one differs" checks.
    covered:
        False means "this sink exists but the project does not claim to defend
        it yet" — it is listed in the coverage manifest as UNCOVERED so the gap
        is visible. It grants **no** failure amnesty: forgiveness comes solely
        from an exact ``(case_id, sink, invariant)`` entry in the baseline.
        (Two amnesty mechanisms would let one silently void the other — a
        ``required=False`` sink would absolve future NEW regressions too.)
    """

    name: str
    render: Callable[..., str]  # see docstring for the full signature
    kind: str
    delims: Optional[Callable[[str], Optional[tuple[str, str]]]] = None
    nonce: Optional[Callable[[str], Optional[str]]] = None
    accepts_nonce: bool = False
    covered: bool = True
    note: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"{self.name}: kind must be one of {KINDS}, got {self.kind!r}")


#: Configuration mistakes the runner must reject outright rather than absorb.
#: Each of these otherwise degrades into a silent pass:
#:   - ``accepts_nonce=True`` with no ``nonce`` hook (I4 unverifiable but claimed)
#:   - empty or duplicated sink names (coverage manifest becomes meaningless)
#:   - a :class:`Sabotage` with an empty ``expected`` (nothing to match ⇒ always "ok")
#:   - a ``Sabotage.expected`` naming a case/sink/invariant that does not exist
#:   - an empty adapter, an empty sink list, or zero executed cases
CONFIG_ERRORS = (
    "accepts_nonce_without_hook",
    "empty_sink_name",
    "duplicate_sink_name",
    "sabotage_without_expected",
    "sabotage_unknown_target",
    "unresolvable_placeholder",   # e.g. {OPEN} on a sink with no delims hook
    "empty_adapter",
    "zero_cases_executed",
)

#: Which invariants get canary bracketing. Anything absent is rendered raw.
#: The markers bracket the WHOLE field, so I2 can count line breaks across the
#: entire span — checking only "after the canary" misses extra lines grown by the
#: front half of the payload (``X\n## h<canary>``).
CANARY_INVARIANTS = ("I1", "I1b", "I2")

#: Tokens that must not survive verbatim inside a fence (I1b). Projects may
#: extend this; the runner reports UNMEASURED if a sink declares no way to tell.
DEFAULT_STRUCTURAL_TOKENS: tuple[str, ...] = (
    "<|im_start|>", "<|im_end|>", "<|system|>", "<|user|>", "<|assistant|>",
    "[INST]", "[/INST]", "<<SYS>>", "<</SYS>>", "<|endoftext|>",
)


@dataclass(frozen=True)
class Sabotage:
    """A deliberately broken build used to prove the battery can still detect.

    ``build`` returns a replacement :class:`Sink` with a defence removed.
    ``expected`` lists ``(case_id, invariant)`` pairs that MUST fail against it.
    It is **required**: an empty ``expected`` has nothing to match and would make
    the sabotage check vacuously succeed, so the runner rejects it as a config
    error rather than defaulting to "fine".

    Matching is per (case_id, invariant) — never a total count — so an unrelated
    crash cannot be mistaken for a successful detection. An exception raised by
    the sabotaged sink is recorded as an ERROR and does NOT satisfy an expectation.
    """

    sink: str
    build: Callable[[], Sink]
    expected: Sequence[tuple[str, str]] = field(default_factory=tuple)
    note: str = ""


__all__ = ["Sink", "Sabotage", "KINDS", "INVARIANTS", "DEFAULT_NOTATION",
           "DEFAULT_STRUCTURAL_TOKENS", "MISSING", "CANARY_OPEN", "CANARY_CLOSE",
           "CANARY_INVARIANTS", "CONFIG_ERRORS"]
