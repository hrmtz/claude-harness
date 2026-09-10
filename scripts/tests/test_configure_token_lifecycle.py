"""Native settings policy remains reversible and preserves unrelated settings."""
import importlib.util
import json
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "configure_token_lifecycle", Path(__file__).parents[1] / "configure_token_lifecycle.py")
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


def test_preview_apply_backup_and_idempotence(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = tmp_path / ".claude/settings.json"
    settings.parent.mkdir()
    original = {"hooks": {"Stop": []}, "permissions": {"deny": ["Bash(rm *)"]},
                "model": "test-model", "autoCompactWindow": 500000}
    settings.write_text(json.dumps(original))
    before = settings.read_bytes()
    assert policy.configure(settings, 100000).startswith("would set")
    assert settings.read_bytes() == before
    assert policy.configure(settings, 100000, True).startswith("set ")
    assert json.loads(settings.read_text()) == dict(original, autoCompactWindow=100000)
    backups = list((tmp_path / "sanada_backup_persistent").glob("*/settings.json"))
    assert len(backups) == 1 and backups[0].read_bytes() == before
    assert policy.configure(settings, 100000, True) == "unchanged"
    assert len(list((tmp_path / "sanada_backup_persistent").glob("*"))) == 1

    # The public CLI default must agree with Formation's quality-first policy.
    monkeypatch.setattr("sys.argv", ["configure_token_lifecycle.py", "--apply"])
    policy.main()
    assert json.loads(settings.read_text()) == dict(original, autoCompactWindow=1000000)


def test_spawn_uses_native_default_without_replacing_explicit_override():
    # This checks the actual generated launch command, without starting tmux or
    # a paid model. Native behavior is covered by test_native_autocompact.py.
    import os
    import subprocess
    source = (Path(__file__).parents[2] / "plugins/harness-formation/bin/formation").read_text()
    begin = source.index('    local compact_window=')
    end = source.index('\n\n', begin)
    fragment = source[begin:end].replace("local compact_window=", "compact_window=")
    prefix = '''
tmux() { printf '%s\\n' "$@"; }
pane_id=%test
guard_command=guard
session_name=test
claude_perm_flag=--permission-mode\\ default
claude_model_flag=''
settings_json='{}'
goal_prompt='test goal'
'''
    for explicit, expected in [(None, "1000000"), ("200000", "200000")]:
        environment = dict(os.environ)
        environment.pop("CLAUDE_CODE_AUTO_COMPACT_WINDOW", None)
        if explicit:
            environment["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = explicit
        result = subprocess.run(["bash", "-c", prefix + fragment], env=environment,
                                text=True, capture_output=True, check=True)
        assert f"env CLAUDE_CODE_AUTO_COMPACT_WINDOW={expected} guard claude" in result.stdout
        assert "--permission-mode default" in result.stdout
