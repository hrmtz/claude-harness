#!/usr/bin/env python3
"""Closed, cross-plugin protocol identity for dual-magi campaigns.

The runtime is checkout-linked for Kimi, so a campaign must refuse to mix
provider phases produced by different checkout generations.  Keep this list
closed: adding a script, schema, canonical template, or orchestration contract
requires adding it here and updating the protocol tests.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from pathlib import PurePosixPath


RUNTIME_FILES = (
    "schemas/finding.codex.schema.json",
    "schemas/finding.schema.json",
    "schemas/implementation-convergence.schema.json",
    "schemas/protocol.external.json",
    "scripts/magi_autorun.py",
    "scripts/magi_campaign_guard.py",
    "scripts/magi_classify_failure.py",
    "scripts/magi_codex_schema_preflight.py",
    "scripts/magi_convergence_gate.py",
    "scripts/magi_convergence_kernel.py",
    "scripts/magi_design_convergence_gate.py",
    "scripts/magi_fanout_codex.sh",
    "scripts/magi_git.py",
    "scripts/magi_lock.sh",
    "scripts/magi_plateau_gate.sh",
    "scripts/magi_protocol.py",
    "scripts/magi_review_packet.py",
    "scripts/magi_rename_noreplace.py",
    "scripts/magi_scrub.py",
    "scripts/magi_synthesize.py",
    "scripts/magi_validate_findings.py",
    "scripts/magi_verify_canonical_templates.py",
    "scripts/magi_verify_round.py",
    "scripts/magi_verify_xfamily_artifacts.py",
    "scripts/magi_xfamily.sh",
    "scripts/magi_xfamily_claude.sh",
    "skills/magi/SKILL.md",
    "skills/dual-magi-review/SKILL.md",
    "skills/ultramagi/SKILL.md",
)

REPOSITORY_FILES = (
    "plugins/harness-magi/skills/.magi-canonical-templates.json",
    "plugins/harness-magi/skills/magi/templates/melchior_prompt.md",
    "plugins/harness-magi/skills/magi/templates/balthasar_prompt.md",
    "plugins/harness-magi/skills/magi/templates/caspar_prompt.md",
    "plugins/harness-magi/skills/bug-hunt/templates/hornet_prompt.md",
    "plugins/harness-magi/skills/bug-hunt/templates/gnat_prompt.md",
    "plugins/harness-magi/skills/bug-hunt/templates/wasp_prompt.md",
    "plugins/harness-magi/skills/magi/SKILL.md",
    "plugins/harness-magi/skills/bug-hunt/SKILL.md",
    "plugins/harness-magi/skills/dual-magi-review/SKILL.md",
    "plugins/harness-magi/skills/ultramagi/SKILL.md",
    "plugins/harness-kimi/skills/magi/SKILL.md",
    "plugins/harness-kimi/skills/bug-hunt/SKILL.md",
    "plugins/harness-kimi/skills/dual-magi-review/SKILL.md",
    "plugins/harness-kimi/skills/ultramagi/SKILL.md",
)


class ProtocolError(RuntimeError):
    """The checkout does not contain the closed protocol manifest."""


def reject_duplicate_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON member: {key}")
        result[key] = value
    return result


def strict_json_loads(raw: bytes | str) -> object:
    return json.loads(raw, object_pairs_hook=reject_duplicate_members)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_root() -> Path:
    return Path(__file__).resolve().parent.parent


def repository_root() -> Path | None:
    root = runtime_root()
    try:
        repo = root.parents[1]
    except IndexError:
        return None
    if (repo / "plugins" / "harness-magi-codex").resolve() == root:
        return repo
    return None


def external_contracts() -> tuple[list[dict[str, str]], dict[str, bytes]]:
    runtime = runtime_root()
    snapshot_path = runtime / "schemas" / "protocol.external.json"
    try:
        payload = strict_json_loads(snapshot_path.read_bytes())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"external contract snapshot is unreadable: {exc}") from exc
    entries = payload.get("files") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "magi-external-contracts/v2"
        or not isinstance(entries, list)
    ):
        raise ProtocolError("external contract snapshot has an unsupported schema")
    if any(
        not isinstance(item, dict)
        or set(item) != {"path", "sha256", "content_base64"}
        or not isinstance(item["path"], str)
        or not isinstance(item["sha256"], str)
        or not isinstance(item["content_base64"], str)
        or len(item["sha256"]) != 64
        or any(char not in "0123456789abcdef" for char in item["sha256"])
        for item in entries
    ):
        raise ProtocolError("external contract snapshot contains a malformed entry")
    names = [item["path"] for item in entries]
    if tuple(names) != REPOSITORY_FILES:
        raise ProtocolError("external contract snapshot does not match the closed path list")
    if len(names) != len(set(names)):
        raise ProtocolError("external contract snapshot contains duplicate paths")
    payloads: dict[str, bytes] = {}
    for item in entries:
        try:
            data = base64.b64decode(item["content_base64"], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ProtocolError(
                f"external contract snapshot payload is malformed: {item['path']}"
            ) from exc
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ProtocolError(
                f"external contract snapshot payload digest differs: {item['path']}"
            )
        payloads[item["path"]] = data

    repo = repository_root()
    if repo is not None:
        for item in entries:
            path = repo / item["path"]
            if not path.is_file():
                raise ProtocolError(f"closed protocol manifest is missing: {item['path']}")
            actual = path.read_bytes()
            if actual != payloads[item["path"]]:
                raise ProtocolError(
                    f"external contract snapshot is stale for {item['path']}; regenerate it"
                )
    public_entries = [
        {"path": item["path"], "sha256": item["sha256"]} for item in entries
    ]
    return public_entries, payloads


def write_external_contracts() -> Path:
    repo = repository_root()
    if repo is None:
        raise ProtocolError("external contracts can only be regenerated from a source checkout")
    entries = []
    for name in REPOSITORY_FILES:
        path = repo / name
        if not path.is_file():
            raise ProtocolError(f"closed protocol manifest is missing: {name}")
        data = path.read_bytes()
        entries.append(
            {
                "path": name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "content_base64": base64.b64encode(data).decode("ascii"),
            }
        )
    destination = runtime_root() / "schemas" / "protocol.external.json"
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"schema": "magi-external-contracts/v2", "files": entries}, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def git_generation() -> tuple[list[dict[str, str]], dict[str, bytes]] | None:
    repo = repository_root()
    if repo is None:
        return None
    names = tuple(
        f"plugins/harness-magi-codex/{name}" for name in RUNTIME_FILES
    ) + REPOSITORY_FILES
    git_env = os.environ.copy()
    git_env.pop("GIT_DIR", None)
    git_env.pop("GIT_WORK_TREE", None)
    try:
        index_raw = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "--stage", "-z", "--", *names],
            check=True,
            capture_output=True,
            env=git_env,
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise ProtocolError(
            f"cannot read closed protocol Git index: {exc.stderr.decode(errors='replace').strip()}"
        ) from exc
    indexed: dict[str, str] = {}
    for record in index_raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_name = record.split(b"\t", 1)
            _mode, object_id, stage = metadata.decode("ascii").split()
            name = raw_name.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProtocolError("closed protocol Git index record is malformed") from exc
        if stage != "0" or name in indexed:
            raise ProtocolError(f"closed protocol Git index has conflict/duplicate: {name}")
        indexed[name] = object_id
    if set(indexed) != set(names) or len(indexed) != len(names):
        missing = sorted(set(names) - set(indexed))
        extra = sorted(set(indexed) - set(names))
        raise ProtocolError(
            f"closed protocol Git index path set differs: missing={missing}, extra={extra}"
        )
    generation = hashlib.sha256(index_raw).hexdigest()
    payloads: dict[str, bytes] = {}
    for name in names:
        try:
            payload = subprocess.run(
                ["git", "-C", str(repo), "cat-file", "blob", indexed[name]],
                check=True,
                capture_output=True,
                env=git_env,
            ).stdout
        except subprocess.CalledProcessError as exc:
            raise ProtocolError(
                f"closed protocol blob is absent from Git index generation {generation}: {name}"
            ) from exc
        working = repo / name
        if not working.is_file() or working.read_bytes() != payload:
            raise ProtocolError(
                f"protocol input differs from pinned Git index generation {generation}; "
                f"stage or revert: {name}"
            )
        payloads[name] = payload

    snapshot_name = "plugins/harness-magi-codex/schemas/protocol.external.json"
    try:
        snapshot = strict_json_loads(payloads[snapshot_name])
    except (ValueError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"pinned external contract snapshot is unreadable: {exc}") from exc
    if not isinstance(snapshot, dict) or snapshot.get("schema") != "magi-external-contracts/v2":
        raise ProtocolError("pinned external contract snapshot is malformed")
    expected = snapshot.get("files")
    if not isinstance(expected, list) or [item.get("path") for item in expected
        if isinstance(item, dict)] != list(REPOSITORY_FILES):
        raise ProtocolError("pinned external contract path list is malformed")
    for item in expected:
        name = item["path"]
        if set(item) != {"path", "sha256", "content_base64"}:
            raise ProtocolError(f"pinned external contract entry is malformed: {name}")
        try:
            embedded = base64.b64decode(item["content_base64"], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ProtocolError(
                f"pinned external contract payload is malformed: {name}"
            ) from exc
        if (
            item.get("sha256") != hashlib.sha256(payloads[name]).hexdigest()
            or embedded != payloads[name]
        ):
            raise ProtocolError(f"pinned external contract snapshot is stale for {name}")
    entries = [
        {"path": name, "sha256": hashlib.sha256(payload).hexdigest()}
        for name, payload in payloads.items()
    ]
    return sorted(entries, key=lambda item: item["path"]), payloads


def snapshot_generation() -> tuple[list[dict[str, str]], dict[str, bytes]] | None:
    root = runtime_root().parents[1]
    marker = root / "manifest.json"
    if not marker.is_file():
        return None
    try:
        payload = strict_json_loads(marker.read_bytes())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"protocol snapshot manifest is unreadable: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != "magi-protocol-snapshot/v1":
        raise ProtocolError("protocol snapshot manifest schema is invalid")
    entries = payload.get("files")
    if not isinstance(entries, list):
        raise ProtocolError("protocol snapshot file list is invalid")
    expected_names = sorted(
        tuple(f"plugins/harness-magi-codex/{name}" for name in RUNTIME_FILES)
        + REPOSITORY_FILES
    )
    if len(entries) != len(expected_names):
        raise ProtocolError("protocol snapshot file list is not the closed path set")
    names: list[str] = []
    for item in entries:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ProtocolError("protocol snapshot entry is invalid")
        name = item.get("path")
        digest = item.get("sha256")
        if not isinstance(name, str) or not isinstance(digest, str):
            raise ProtocolError("protocol snapshot entry types are invalid")
        parsed = PurePosixPath(name)
        if parsed.is_absolute() or ".." in parsed.parts or str(parsed) != name:
            raise ProtocolError(f"protocol snapshot path is unsafe: {name!r}")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ProtocolError(f"protocol snapshot digest is invalid: {name}")
        names.append(name)
    if names != expected_names or len(names) != len(set(names)):
        raise ProtocolError("protocol snapshot path list is not exact, unique, and sorted")
    payloads: dict[str, bytes] = {}
    for item in entries:
        name = item["path"]
        path = root / name
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ProtocolError(f"protocol snapshot content changed: {name}")
        payloads[name] = data
    if manifest_sha(entries) != payload.get("protocol_sha"):
        raise ProtocolError("protocol snapshot digest is invalid")
    return entries, payloads


def manifest_generation() -> tuple[list[dict[str, str]], dict[str, bytes]]:
    snapshotted = snapshot_generation()
    if snapshotted is not None:
        return snapshotted
    pinned = git_generation()
    if pinned is not None:
        return pinned
    runtime = runtime_root()
    local = [
        {
            "path": f"plugins/harness-magi-codex/{name}",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for name in RUNTIME_FILES
        for path in (runtime / name,)
        if path.is_file()
    ]
    if len(local) != len(RUNTIME_FILES):
        found = {item["path"].removeprefix("plugins/harness-magi-codex/") for item in local}
        missing = sorted(set(RUNTIME_FILES) - found)
        raise ProtocolError(f"closed protocol manifest is missing local files: {missing}")
    external_entries, external_payloads = external_contracts()
    entries = local + external_entries
    names = [item["path"] for item in entries]
    if len(names) != len(set(names)):
        raise ProtocolError("closed protocol manifest contains duplicate paths")
    entries = sorted(entries, key=lambda item: item["path"])
    payloads = {
        item["path"]: (
            runtime / item["path"].removeprefix("plugins/harness-magi-codex/")
        ).read_bytes()
        for item in entries
        if item["path"].startswith("plugins/harness-magi-codex/")
    }
    payloads.update(external_payloads)
    return entries, payloads


def manifest() -> list[dict[str, str]]:
    return manifest_generation()[0]


def manifest_sha(entries: list[dict[str, str]]) -> str:
    digest = hashlib.sha256()
    for item in entries:
        digest.update(item["path"].encode())
        digest.update(b"\0")
        digest.update(item["sha256"].encode())
        digest.update(b"\0")
    return digest.hexdigest()


def protocol_sha() -> str:
    return manifest_sha(manifest())


def write_snapshot(destination: Path, expected_sha: str | None = None) -> Path:
    """Materialize one verified protocol generation for a charged provider attempt."""
    if destination.exists():
        raise ProtocolError(f"snapshot destination already exists: {destination}")
    entries, payloads = manifest_generation()
    captured_sha = manifest_sha(entries)
    if expected_sha is not None and captured_sha != expected_sha:
        raise ProtocolError(
            f"captured protocol generation {captured_sha} does not match claim {expected_sha}"
        )
    destination.mkdir(parents=True, mode=0o700)
    try:
        for item in entries:
            name = item["path"]
            payload = payloads.get(name)
            if payload is None:
                raise ProtocolError(
                    f"repository protocol input cannot be snapshotted outside a checkout: {name}"
                )
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        snapshot_manifest = {
            "schema": "magi-protocol-snapshot/v1",
            "protocol_sha": captured_sha,
            "files": entries,
        }
        (destination / "manifest.json").write_text(
            json.dumps(snapshot_manifest, indent=2) + "\n", encoding="utf-8"
        )
    except BaseException as original:
        try:
            shutil.rmtree(destination)
        except BaseException as cleanup:
            raise ProtocolError(
                f"protocol snapshot failed ({type(original).__name__}: {original}); "
                f"cleanup also failed for residual destination {destination} "
                f"({type(cleanup).__name__}: {cleanup})"
            ) from cleanup
        raise
    return destination


def main(argv: list[str]) -> int:
    if argv == ["write-external"]:
        print(write_external_contracts())
        return 0
    if argv == ["sha"]:
        print(protocol_sha())
        return 0
    if argv == ["manifest"]:
        print(
            json.dumps(
                {"schema": "magi-protocol/v1", "protocol_sha": protocol_sha(),
                 "files": manifest()},
                indent=2,
            )
        )
        return 0
    if len(argv) in (2, 3) and argv[0] == "snapshot":
        print(write_snapshot(Path(argv[1]), argv[2] if len(argv) == 3 else None))
        return 0
    print(
        "usage: magi_protocol.py sha|manifest|write-external|snapshot <dir> [expected-sha]",
        file=sys.stderr,
    )
    return 64


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (OSError, ProtocolError) as exc:
        print(f"magi-protocol: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
