#!/usr/bin/env python3
"""sr_depth_gate — structural depth gate for /security-review sessions.

Grounded on the 2026-07-07 audit of 24 back-to-back security-reviews of
docs/tools/* (see docs/design-history/SR_DEPTH_GATE.md). That audit REJECTED the
"reviews got mechanical over time" hypothesis (specificity held ~4/5, corr with
ordinal ~= 0), but surfaced one real, position-independent gap: a no_findings
verdict emitted for a *novel* changed file that was never actually opened —
reasoned from the pasted diff + grep alone (ordinal 3). This gate makes that one
failure mode structurally impossible to ship silently.

Invariant enforced (the only deterministic, false-positive-free one):
    A "no_findings" security-review verdict is INVALID if any changed file was
    not opened with Read. Grep/Bash pattern scans do NOT count — opening the
    file is the point (that is exactly what ordinal 3 skipped).

The richer "per-sink counterfactual trace" contract is behavioral (a CSS-only
diff legitimately has no sink to name, so it cannot be a deterministic gate) and
lives in the review output contract doc, not here.

Two modes:
  CLI:   sr_depth_gate.py <session.jsonl> [<session.jsonl> ...]
         prints a per-session PASS/FAIL report; exit 1 if any FAIL.
  Hook:  sr_depth_gate.py --hook   (reads Stop / SubagentStop hook JSON on
         stdin, uses transcript_path). When the just-finished session was a
         gated review that FAILED, emits {"decision":"block","reason":...} so
         the reviewer cannot stop on a clean verdict without opening the file.
         Fail-open on EVERYTHING else (not a review / passed / parse error /
         malformed transcript): it returns 0 and prints nothing so the session
         is never hard-trapped. The wrapper in .claude/settings.json also
         `|| exit 0`s so an unresolvable path can never turn into a block.
"""
from __future__ import annotations
import json
import os
import re
import sys

# /security-review's opening line. Kept loose (wording may drift) but still
# specific enough not to match ordinary sessions. Format drift beyond this makes
# the gate fail OPEN (inert), never closed — an acknowledged limitation.
MARKER = re.compile(r"review this (?:change|diff|pr|pull request)\b.{0,60}security",
                    re.I | re.S)
# Free-text fallback, used ONLY when the review emitted no StructuredOutput.
# We only ever infer the *clean* verdict from prose (that is the one the gate
# acts on); we never infer "findings" from prose — an ambiguous blob stays
# "unknown", which is fail-open (never blocks).
NO_FIND = re.compile(
    r"no[_ ]findings|no (?:security )?vulnerabilit|no exploitable|"
    r"no source.{0,4}sink|found no (?:security|vulnerab)|0 findings|"
    r"no (?:issues|problems) found",
    re.I,
)


def _norm_findings(inp: dict):
    """Unwrap StructuredOutput input to a findings list (or None if absent).

    The review skill emits {"findings": [...]}, occasionally double-nested as
    {"findings": {"findings": [...]}}. Empty list == a clean (no_findings) verdict.
    """
    if not isinstance(inp, dict):
        return None
    f = inp.get("findings")
    seen = 0
    while isinstance(f, dict) and "findings" in f and seen < 5:
        f = f["findings"]
        seen += 1
    return f if isinstance(f, list) else None


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for it in content:
            if not isinstance(it, dict):
                continue
            if it.get("type") in {"text", "input_text", "output_text"}:
                out.append(it.get("text", ""))
            elif it.get("type") == "tool_use":
                out.append(json.dumps(it.get("input", {}), ensure_ascii=False))
        return "\n".join(out)
    return ""


def _tool_input(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return {"_raw": value}
    return {}


def _read_paths(name: str, inp: dict) -> list[str]:
    if name in {"Read", "read_file", "mcp__filesystem__read_file"}:
        path = inp.get("file_path") or inp.get("path")
        return [path] if isinstance(path, str) and path else []
    if name not in {"Bash", "exec_command", "functions.exec"}:
        return []
    command = next((inp.get(key) for key in ("command", "cmd", "code", "_raw")
                    if isinstance(inp.get(key), str)), "")
    # Codex has no universal Read tool. Accept explicit file-display commands,
    # then use suffix matching below to decide whether a changed path was read.
    if not re.search(r'''(?:^|[;&|\s"'])(?:cat|sed|head|tail|bat|less)\s''', command):
        return []
    return re.findall(
        r"(?:/|\./)?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+|"
        r"\b[A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+\b",
        command,
    )


def _record_parts(record: dict) -> tuple[str | None, str, list[tuple[str, dict]]]:
    """Normalize Claude messages and current Codex rollout records."""
    typ = record.get("type")
    if typ in {"user", "assistant"}:
        msg = record.get("message") if isinstance(record.get("message"), dict) else {}
        content = msg.get("content")
        tools = []
        if isinstance(content, list):
            tools = [(it.get("name", ""), _tool_input(it.get("input")))
                     for it in content if isinstance(it, dict) and it.get("type") == "tool_use"]
        return typ, _text(content), tools

    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    payload_type = payload.get("type")
    if typ == "event_msg" and payload_type == "user_message":
        return "user", str(payload.get("message") or ""), []
    if typ == "event_msg" and payload_type == "agent_message":
        return "assistant", str(payload.get("message") or ""), []
    if typ == "response_item" and payload_type == "message":
        role = payload.get("role")
        return role if role in {"user", "assistant"} else None, _text(payload.get("content")), []
    if typ == "response_item" and payload_type in {"custom_tool_call", "function_call"}:
        return None, "", [(str(payload.get("name") or ""),
                            _tool_input(payload.get("input") or payload.get("arguments")))]
    return None, "", []


def _is_read_of(changed: str, read_paths: list[str]) -> bool:
    """True if a repo-relative changed path was opened. Match by full path
    SUFFIX, not basename: an absolute Read of .../docs/tools/x.html satisfies
    changed 'docs/tools/x.html', but a Read of 'other/x.html' does NOT satisfy
    a different-directory changed file that merely shares a basename."""
    c = changed.strip().lstrip("./").lstrip("/")
    for r in read_paths:
        r = (r or "").strip()
        if r == c or r.endswith("/" + c):
            return True
    return False


def parse_session(path: str) -> dict | None:
    """Return None if this transcript is not a security-review session, or if it
    cannot be parsed at all (fail-open: the caller treats None as 'do nothing')."""
    changed: list[str] = []
    read_paths: list[str] = []       # full file_paths, not basenames
    struct_lists: list[list] = []    # normalized findings lists, in emission order
    verdict_blob: list[str] = []
    is_sr = False
    first_prompt = ""

    try:
        fh = open(path, encoding="utf-8", errors="ignore")
    except OSError:
        return None
    with fh:
        for line in fh:
            try:
                o = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(o, dict):        # valid JSON but not an object
                continue
            typ, text, tools = _record_parts(o)
            if typ == "user":
                if not first_prompt and MARKER.search(text):
                    is_sr = True
                    first_prompt = text
            elif typ == "assistant":
                verdict_blob.append(text)
            for name, inp in tools:
                read_paths.extend(_read_paths(name, inp))
                if name == "StructuredOutput":
                    fl = _norm_findings(inp)
                    if fl is not None:
                        struct_lists.append(fl)
    if not is_sr:
        return None

    # extract "Changed files" bullet list (between the header and the diff)
    m = re.search(r"Changed files.*?:\s*\n(.*?)(?:\n\s*\n|=== DIFF|Unified diff|\Z)",
                  first_prompt, re.S)
    if m:
        for ln in m.group(1).splitlines():
            mm = re.match(r"\s*[-*]\s+(\S+)", ln)
            if mm:
                changed.append(mm.group(1))

    # verdict: the LAST StructuredOutput emission is the real verdict (an earlier
    # draft with items must not veto a clean final). Fall back to prose only for
    # the clean signal; anything ambiguous stays "unknown" (fail-open).
    verdict = "unknown"
    if struct_lists:
        verdict = "findings" if struct_lists[-1] else "no_findings"
    elif NO_FIND.search("\n".join(verdict_blob)):
        verdict = "no_findings"

    unread = [f for f in changed if not _is_read_of(f, read_paths)]
    # gate bites ONLY a clean verdict with an unread changed file. A "findings"
    # verdict already did work; "unknown" and "no changed files parsed" fail open.
    fail = verdict == "no_findings" and bool(changed) and bool(unread)
    return {
        "session": os.path.basename(path),
        "is_security_review": True,
        "verdict": verdict,
        "changed_files": changed,
        "read_files": read_paths,
        "unread_changed": unread,
        "gate": "FAIL" if fail else "PASS",
    }


def _cli(paths: list[str]) -> int:
    any_fail = False
    for p in paths:
        try:
            r = parse_session(p)
        except Exception as e:  # CLI is diagnostic; surface but keep going
            print(f"error parsing {os.path.basename(p)}: {e}")
            continue
        if r is None:
            print(f"skip (not a security-review): {os.path.basename(p)}")
            continue
        tag = "FAIL" if r["gate"] == "FAIL" else "PASS"
        extra = f"  unread={r['unread_changed']}" if r["gate"] == "FAIL" else ""
        if r["gate"] == "FAIL":
            any_fail = True
        print(f"{tag}  {r['session']}  verdict={r['verdict']}  "
              f"changed={len(r['changed_files'])} read={len(r['read_files'])}{extra}")
    return 1 if any_fail else 0


def _hook() -> int:
    """Never returns non-zero and never raises: fail-open is the whole contract."""
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return 0
        # re-entry guard: if we already blocked this stop-cycle, let it through.
        if payload.get("stop_hook_active"):
            return 0
        tp = (payload.get("agent_transcript_path")
              if payload.get("hook_event_name") == "SubagentStop"
              else payload.get("transcript_path")) or ""
        if not tp or not os.path.exists(tp):
            return 0
        r = parse_session(tp)
        if r is None or r["gate"] != "FAIL":
            return 0  # not a gated review, or it passed — stay silent
        reason = (
            "SR-DEPTH-GATE: this security-review returned a no_findings verdict "
            f"WITHOUT opening {r['unread_changed']} with Read (reasoned from the "
            "diff/grep alone). Open each unread changed file, then for every hunk "
            "emit a per-sink line — [sink@file:line] | [interpolated var] | "
            "[provenance to constant/coercion/allowlist] | [counterfactual: what "
            "input would break it and why it cannot reach here] — before concluding."
        )
        print(json.dumps({"decision": "block", "reason": reason}))
    except Exception:
        return 0  # any failure => fail open, session is never trapped
    return 0


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--hook":
        return _hook()
    if not args:
        print(__doc__)
        return 2
    return _cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
