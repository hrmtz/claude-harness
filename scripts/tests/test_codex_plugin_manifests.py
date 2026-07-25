#!/usr/bin/env python3
import json
import pathlib
import re
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
            self.assertRegex(
                manifest["version"],
                r"^\d+\.\d+\.\d+(?:\+codex\.[a-z0-9-]+)?$",
            )
            self.assertNotIn("[TODO:", json.dumps(manifest))
            if "skills" in manifest:
                self.assertTrue((root / manifest["skills"]).is_dir())
            if name in {"harness-core", "harness-rails", "harness-formation"}:
                self.assertTrue((root / "hooks/hooks.json").is_file())

    def test_codex_magi_routes_to_bundled_root(self):
        for skill in ("magi", "dual-magi-review", "ultramagi"):
            text = (ROOT / "plugins/harness-magi-codex/skills" / skill / "SKILL.md").read_text()
            self.assertIn("`harness-magi-codex` plugin root", text)

    def test_native_core_dispatches_codex_identity(self):
        text = (ROOT / "plugins/harness-core/hooks/tmux_self_name.sh").read_text()
        self.assertIn('${PLUGIN_ROOT:-}', text)
        self.assertIn("codex_tmux_self_name.sh", text)

    def test_native_core_preserves_optional_codex_companion(self):
        hooks = json.loads((ROOT / "plugins/harness-core/hooks/hooks.json").read_text())
        companion_hooks = [
            hook
            for group in hooks["hooks"]["SessionStart"]
            for hook in group["hooks"]
            if "codex_hippocampus_session_start.sh" in hook["command"]
        ]
        self.assertEqual(len(companion_hooks), 1)
        self.assertEqual(companion_hooks[0]["timeout"], 20)
        adapter = (ROOT / "plugins/harness-core/hooks/"
                   "codex_hippocampus_session_start.sh").read_text()
        self.assertIn('[ -n "${PLUGIN_ROOT:-}" ] || exit 0', adapter)
        self.assertIn("scripts/hooks/codex_session_start.sh", adapter)
        self.assertIn('[ -f "$SCRIPT" ] || exit 0', adapter)

    def test_hooks_use_cache_independent_dispatcher(self):
        for plugin in ("harness-core", "harness-rails",
                       "harness-formation", "harness-magi-codex"):
            hooks_path = ROOT / "plugins" / plugin / "hooks/hooks.json"
            hooks = json.loads(hooks_path.read_text())
            commands = [
                hook["command"]
                for groups in hooks["hooks"].values()
                for group in groups
                for hook in group["hooks"]
            ]
            self.assertTrue(commands, plugin)
            for command in commands:
                if "install-cache-safe-entrypoints" in command:
                    self.assertEqual(plugin, "harness-core")
                    continue
                self.assertIn(
                    f'"${{HOME}}/.local/bin/harness-hook" {plugin} hooks/',
                    command,
                    command,
                )
                self.assertIn("${CLAUDE_PLUGIN_ROOT}/hooks/", command, command)
                self.assertIn('test -x "${HOME}/.local/bin/harness-hook"', command)
                self.assertIn(
                    "# HARNESS_HOOK_DISPATCHER_ID=claude-harness/v1",
                    command,
                    command,
                )

        dispatcher = ROOT / "plugins/harness-core/bin/harness-hook"
        launcher = ROOT / "plugins/harness-core/bin/codex-cache-safe"
        bootstrap = (
            ROOT / "plugins/harness-core/bin/install-cache-safe-entrypoints"
        )
        self.assertTrue(dispatcher.is_file())
        self.assertTrue(launcher.is_file())
        self.assertTrue(bootstrap.is_file())

    def test_legacy_codex_installer_resolves_dispatcher_commands(self):
        overlay = json.loads(
            (ROOT / "plugins/cross_cli_hooks.json").read_text()
        )["codex"]
        specs = [
            {"path": item} if isinstance(item, str) else item
            for item in overlay["hooks"]
        ]
        lookup = {}
        for plugin in sorted({spec["path"].split("/")[0] for spec in specs}):
            hooks = json.loads(
                (ROOT / "plugins" / plugin / "hooks/hooks.json").read_text()
            )
            for event, blocks in hooks.get("hooks", {}).items():
                for block in blocks:
                    matcher = block.get("matcher")
                    for hook in block.get("hooks", []):
                        tail = hook["command"].rsplit(";", 1)[-1]
                        match = re.search(
                            r"(?:^|[ /])hooks/(.+)$", tail
                        )
                        if not match:
                            self.assertIn(
                                "install-cache-safe-entrypoints",
                                hook["command"],
                            )
                            continue
                        key = f"{plugin}/hooks/{match.group(1)}"
                        lookup.setdefault(key, []).append((event, matcher))

        for spec in specs:
            candidates = lookup.get(spec["path"], [])
            if "event" in spec:
                candidates = [
                    item for item in candidates if item[0] == spec["event"]
                ]
            if "matcher" in spec:
                candidates = [
                    item for item in candidates if item[1] == spec["matcher"]
                ]
            self.assertEqual(len(candidates), 1, spec["path"])

        installer = (ROOT / "install-codex-hooks.sh").read_text()
        self.assertIn("harness-cross-cli", installer)
        self.assertIn("STANDALONE_CURRENT", installer)
        self.assertLess(
            installer.index('INVENTORY_SNAPSHOT='),
            installer.index('install_stable_link "$SAFE_LAUNCHER"'),
        )


if __name__ == "__main__":
    unittest.main()
