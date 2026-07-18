#!/usr/bin/env python3
import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).parents[2]
MARKET = ROOT / ".agents/plugins/marketplace.json"
GUIDE = ROOT / "docs/codex_plugins.md"
EXPECTED = ["harness-core", "harness-rails", "harness-formation", "harness-magi-codex"]


class CodexPluginManifestTest(unittest.TestCase):
    def test_marketplace_contract(self):
        data = json.loads(MARKET.read_text())
        self.assertEqual(data["name"], "claude-harness")
        self.assertEqual([p["name"] for p in data["plugins"]], EXPECTED)
        for entry in data["plugins"]:
            self.assertEqual(entry["source"], {
                "source": "local", "path": f"./plugins/{entry['name']}"})
            self.assertIn(entry["policy"]["installation"], {"AVAILABLE", "INSTALLED_BY_DEFAULT"})
            self.assertIn(entry["policy"]["authentication"], {"ON_INSTALL", "ON_USE"})
            self.assertTrue(entry["category"])

    def test_local_marketplace_update_commands(self):
        guide = GUIDE.read_text()
        self.assertNotIn("codex plugin marketplace upgrade claude-harness", guide)
        for name in EXPECTED:
            if name == "harness-core":
                continue
            self.assertIn(f"codex plugin add {name}@claude-harness", guide)

    def test_manifests_match_directories_and_components_exist(self):
        for name in EXPECTED:
            root = ROOT / "plugins" / name
            manifest = json.loads((root / ".codex-plugin/plugin.json").read_text())
            self.assertEqual(manifest["name"], name)
            self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+$")
            self.assertNotIn("[TODO:", json.dumps(manifest))
            if "skills" in manifest:
                self.assertTrue((root / manifest["skills"]).is_dir())
            if name in {"harness-core", "harness-rails", "harness-formation"}:
                self.assertTrue((root / "hooks/hooks.json").is_file())

    def test_codex_magi_routes_to_bundled_root(self):
        for skill in ("dual-magi-review", "ultramagi"):
            text = (ROOT / "plugins/harness-magi-codex/skills" / skill / "SKILL.md").read_text()
            self.assertIn("`harness-magi-codex` plugin root", text)

    def test_native_core_dispatches_codex_identity(self):
        text = (ROOT / "plugins/harness-core/hooks/tmux_self_name.sh").read_text()
        self.assertIn('${PLUGIN_ROOT:-}', text)
        self.assertIn("codex_tmux_self_name.sh", text)

    def test_native_core_preserves_optional_codex_companion(self):
        hooks = json.loads((ROOT / "plugins/harness-core/hooks/hooks.json").read_text())
        commands = [
            hook["command"]
            for group in hooks["hooks"]["SessionStart"]
            for hook in group["hooks"]
        ]
        self.assertTrue(any("codex_hippocampus_session_start.sh" in command
                            for command in commands))
        adapter = (ROOT / "plugins/harness-core/hooks/"
                   "codex_hippocampus_session_start.sh").read_text()
        self.assertIn('[ -n "${PLUGIN_ROOT:-}" ] || exit 0', adapter)
        self.assertIn("scripts/hooks/codex_session_start.sh", adapter)
        self.assertIn('[ -f "$SCRIPT" ] || exit 0', adapter)


if __name__ == "__main__":
    unittest.main()
