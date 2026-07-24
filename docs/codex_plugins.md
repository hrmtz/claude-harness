# Codex native plugins

The repository marketplace is `.agents/plugins/marketplace.json`. It exposes:

- `harness-core`: lifecycle safety hooks, including Codex self-naming
- `harness-rails`: operational hooks and the versioning skill
- `harness-formation`: Formation suggestion hook and worker skill
- `harness-magi-codex`: one-shot Magi plus bounded dual-Magi and ultramagi skills

## Install

From a clone of this repository:

```bash
codex plugin marketplace add /absolute/path/to/claude-harness
codex plugin add harness-core@claude-harness
codex plugin add harness-rails@claude-harness
codex plugin add harness-formation@claude-harness
codex plugin add harness-magi-codex@claude-harness
```

Codex discovers `hooks/hooks.json` and `skills/*/SKILL.md` from each enabled
plugin. Hook commands receive `PLUGIN_ROOT` plus the compatibility
`CLAUDE_PLUGIN_ROOT`, so the same hook files work in both CLIs.

Formation's CLI still needs a PATH entry:

```bash
ln -s /absolute/path/to/claude-harness/plugins/harness-formation/bin/formation ~/.local/bin/formation
```

Restart Codex, open `/hooks`, review the plugin sources, and trust the hook
definitions. The app can enable or disable each installed plugin individually.

## Migrate from the legacy installers

After installing the native plugins, remove only the old marker-bounded inline
hook block to prevent double execution:

```bash
bash /absolute/path/to/claude-harness/uninstall-codex-hooks.sh
bash /absolute/path/to/claude-harness/plugins/harness-magi-codex/uninstall-codex-skills.sh
```

The hook uninstaller takes a Sanada backup and preserves third-party hooks,
`[hooks.state]`, profiles, and all non-harness config. It refuses ambiguous or
duplicate managed markers. The Magi command removes only legacy skill links or
copies carrying its ownership marker; native plugin skills remain in the plugin
cache. The legacy Magi installer also refuses to overwrite a foreign directory
or symlink at a managed skill path. Resolve that ownership conflict explicitly
instead of deleting the foreign entry through the harness installer.

## Update

Pull the repository and reinstall each affected plugin so Codex refreshes its
cached copy:

```bash
git pull --ff-only
codex plugin add harness-core@claude-harness
```

The install instructions above register a **local** marketplace, which reads
the checked-out marketplace directly; `codex plugin marketplace upgrade` is
for Git-backed marketplaces and rejects local sources. Use the same `plugin
add` command for each affected component, then start a new Codex thread.
Re-review hooks whose definition hash changed.

## Disable or uninstall

Use the Codex app plugin toggle for a temporary disable. To uninstall from the
CLI:

```bash
codex plugin remove harness-core@claude-harness
codex plugin remove harness-rails@claude-harness
codex plugin remove harness-formation@claude-harness
codex plugin remove harness-magi-codex@claude-harness
codex plugin marketplace remove claude-harness
```

Removing a plugin does not delete Formation mailbox state or project review
artifacts.
