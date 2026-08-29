import json
import shutil
import subprocess
import sys
from pathlib import Path


PLUGIN = Path(__file__).resolve().parents[1]
CHECKER = PLUGIN / "scripts" / "magi_codex_schema_preflight.py"
PREFLIGHT_SCHEMA = PLUGIN / "schemas" / "preflight-review.schema.json"
PREFLIGHT_RUNNER = PLUGIN / "scripts" / "magi_preflight_codex.sh"


def run_checker(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), str(path)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_preflight_review_schema_is_provider_compatible() -> None:
    result = run_checker(PREFLIGHT_SCHEMA)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


def test_const_without_explicit_type_is_rejected(tmp_path: Path) -> None:
    schema = {
        "type": "object",
        "properties": {"schema": {"const": "example/v1"}},
        "required": ["schema"],
        "additionalProperties": False,
    }
    path = tmp_path / "const-without-type.json"
    path.write_text(json.dumps(schema))

    result = run_checker(path)

    assert result.returncode == 64
    assert "type required for const" in result.stderr


def test_deeply_nested_const_without_type_is_rejected(tmp_path: Path) -> None:
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"kind": {"const": "example"}},
                    "required": ["kind"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }
    path = tmp_path / "nested-const-without-type.json"
    path.write_text(json.dumps(schema))

    result = run_checker(path)

    assert result.returncode == 64
    assert "properties['items'].items.properties['kind'].type required for const" in result.stderr


def test_unique_items_is_rejected_before_provider_launch(tmp_path: Path) -> None:
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "uniqueItems": True,
                "items": {"type": "string"},
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }
    path = tmp_path / "unique-items.json"
    path.write_text(json.dumps(schema))

    result = run_checker(path)

    assert result.returncode == 64
    assert "properties['items'].uniqueItems" in result.stderr


def test_one_shot_runner_preflights_schema_before_output_mutation(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    scripts = plugin / "scripts"
    schemas = plugin / "schemas"
    scripts.mkdir(parents=True)
    schemas.mkdir()
    shutil.copy2(PREFLIGHT_RUNNER, scripts / PREFLIGHT_RUNNER.name)
    shutil.copy2(CHECKER, scripts / CHECKER.name)
    shutil.copy2(
        PLUGIN / "scripts" / "magi_target_root.sh",
        scripts / "magi_target_root.sh",
    )
    (schemas / PREFLIGHT_SCHEMA.name).write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {"schema": {"const": "broken/v1"}},
                "required": ["schema"],
                "additionalProperties": False,
            }
        )
    )
    brief = tmp_path / "brief.md"
    brief.write_text("# bounded brief\n")
    output = tmp_path / "must-not-exist"

    result = subprocess.run(
        ["bash", str(scripts / PREFLIGHT_RUNNER.name), str(brief), str(output)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 64
    assert "type required for const" in result.stderr
    assert not output.exists()
