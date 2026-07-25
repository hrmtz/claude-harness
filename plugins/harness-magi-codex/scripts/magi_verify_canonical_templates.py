#!/usr/bin/env python3
"""Verify the canonical dual-magi reviewer template root by fingerprint.

magi_fanout_codex.sh reads reviewer persona templates from MAGI_CANONICAL_SKILLS_DIR
(default: plugins/harness-magi/skills). Any root that merely mirrors the
<set>/templates/<persona>_prompt.md layout resolves silently -- the harness-magi-codex
pre-flight skill originally shipped its short briefs under exactly that layout, so a
mistaken override swapped canonical review prompts for weak pre-flight ones with nothing
noticing. (The pre-flight briefs now live in skills/magi/preflight/, a separate namespace.)

The canonical root therefore carries a fingerprint marker,
.magi-canonical-templates.json:

    {"schema": "magi-canonical-templates/v1",
     "templates": {"<set>/templates/<persona>_prompt.md": "<sha256>", ...}}

Verification (fan-out pre-flight, before any provider launch):
    magi_verify_canonical_templates.py <canonical-skills-root> <persona-set>
Exit 0 = identity verified · 64 = missing/incompatible marker or template drift.

After intentionally editing a canonical template, regenerate the marker in place:
    magi_verify_canonical_templates.py --write <canonical-skills-root>
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


SCHEMA_ID = "magi-canonical-templates/v1"
MARKER_NAME = ".magi-canonical-templates.json"
PERSONA_SETS = {
    "magi": ("melchior", "balthasar", "caspar"),
    "bug-hunt": ("hornet", "gnat", "wasp"),
}


class Incompatible(Exception):
    """The template root cannot serve as the canonical reviewer templates."""


def template_relpath(persona_set: str, persona: str) -> str:
    return f"{persona_set}/templates/{persona}_prompt.md"


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expected_templates(root: Path) -> dict[str, str]:
    return {
        template_relpath(persona_set, persona): file_sha(root / template_relpath(persona_set, persona))
        for persona_set, personas in sorted(PERSONA_SETS.items())
        for persona in personas
    }


def write_marker(root: Path) -> int:
    marker = root / MARKER_NAME
    payload = {"schema": SCHEMA_ID, "templates": expected_templates(root)}
    marker.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"magi-verify-canonical: wrote {marker} ({len(payload['templates'])} templates)")
    return 0


def verify(root: Path, persona_set: str) -> int:
    marker = root / MARKER_NAME
    if not marker.is_file():
        raise Incompatible(f"canonical fingerprint marker not found: {marker}")
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Incompatible(f"canonical fingerprint marker is unreadable: {marker}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA_ID:
        raise Incompatible(f"canonical fingerprint marker has an unknown schema: {marker}")
    recorded = payload.get("templates")
    if not isinstance(recorded, dict) or not recorded:
        raise Incompatible(f"canonical fingerprint marker lists no templates: {marker}")
    for relpath in recorded:
        path = Path(relpath)
        if path.is_absolute() or ".." in path.parts:
            raise Incompatible(f"canonical fingerprint marker has an unsafe path: {relpath!r}")
    for persona in PERSONA_SETS[persona_set]:
        relpath = template_relpath(persona_set, persona)
        template = root / relpath
        if not template.is_file():
            raise Incompatible(f"canonical template missing: {template}")
        expected = recorded.get(relpath)
        if expected is None:
            raise Incompatible(f"canonical template not covered by the marker: {relpath}")
        if file_sha(template) != expected:
            raise Incompatible(
                f"canonical template digest mismatch: {template} "
                "(template edited without regenerating the marker, or wrong template root)"
            )
    print(f"magi-verify-canonical: {persona_set} templates verified against {marker}")
    return 0


def main(argv: list[str]) -> int:
    args = argv[1:]
    if len(args) == 2 and args[0] == "--write":
        root = Path(args[1]).resolve()
        if not root.is_dir():
            print(f"magi-verify-canonical: not a directory: {root}", file=sys.stderr)
            return 64
        try:
            return write_marker(root)
        except OSError as exc:
            print(f"magi-verify-canonical: cannot write marker: {exc}", file=sys.stderr)
            return 1
    if len(args) == 2 and args[1] in PERSONA_SETS:
        root = Path(args[0]).resolve()
        try:
            return verify(root, args[1])
        except Incompatible as exc:
            print(f"magi-verify-canonical: {exc}", file=sys.stderr)
            return 64
        except OSError as exc:
            print(f"magi-verify-canonical: cannot verify templates: {exc}", file=sys.stderr)
            return 64
    print(
        "usage: magi_verify_canonical_templates.py <canonical-skills-root> "
        f"<{'|'.join(sorted(PERSONA_SETS))}>\n"
        "       magi_verify_canonical_templates.py --write <canonical-skills-root>",
        file=sys.stderr,
    )
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
