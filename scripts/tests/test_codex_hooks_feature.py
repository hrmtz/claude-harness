#!/usr/bin/env python3
import importlib.util
import pathlib
import unittest


PATH = pathlib.Path(__file__).parents[1] / "lib" / "codex_hooks_feature.py"
SPEC = importlib.util.spec_from_file_location("codex_hooks_feature", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FeatureListTest(unittest.TestCase):
    def test_stable_enabled(self):
        self.assertEqual(MODULE.classify("hooks stable true\nplugin_hooks removed false\n"), "enabled")

    def test_stable_disabled(self):
        self.assertEqual(MODULE.classify("hooks stable false\n"), "disabled")

    def test_removed(self):
        self.assertEqual(MODULE.classify("hooks removed false\n"), "removed")

    def test_unknown(self):
        self.assertEqual(MODULE.classify("memories under-development false\n"), "unknown")


if __name__ == "__main__":
    unittest.main()
