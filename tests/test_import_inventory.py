import json
import unittest
from pathlib import Path
import tempfile

from frontierstore.import_inventory import REGISTRY, bootstrap, refresh


class ImportInventoryTests(unittest.TestCase):
    def assert_full_recompute(self, state, modules):
        expected = bootstrap(modules)
        self.assertEqual(set(state.facts), set(expected.facts))
        for key in state.facts:
            self.assertEqual(state.facts[key].payload, expected.facts[key].payload)
            self.assertEqual([i for i, _ in state.facts[key].dependencies],
                             [i for i, _ in expected.facts[key].dependencies])

    def test_transitive_edit_and_unrelated_retention(self):
        modules = {"app": "import lib", "lib": "import leaf", "leaf": "def before(): pass",
                   "unused": "class Kept: pass"}
        old = bootstrap(modules)
        modules["leaf"] = "def after(): pass"
        state, delta, _ = refresh(old, modules)
        self.assertEqual(set(delta.fact_put), {"py.fact.app", "py.fact.lib", "py.fact.leaf"})
        self.assertEqual(state.facts["py.fact.unused"], old.facts["py.fact.unused"])
        self.assert_full_recompute(state, modules)

    def test_negative_lookup_insertion_deletion_and_retarget(self):
        modules = {"app": "import absent", "other": "import app"}
        state = bootstrap(modules)
        self.assertIn(REGISTRY, dict(state.facts["py.fact.app"].dependencies))
        self.assertEqual(json.loads(state.facts["py.fact.app"].payload)["unresolved"], ["absent"])
        for replacement in ({**modules, "absent": "import app"},
                            {**modules, "app": "import other"},
                            {"app": "import other"}):
            state, _, _ = refresh(state, replacement)
            self.assert_full_recompute(state, replacement)

    def test_parse_only_and_explicit_scope(self):
        state = bootstrap({"app": "raise RuntimeError('must never execute')\nimport missing"})
        self.assertIn("missing", state.facts["py.fact.app"].payload)
        with self.assertRaises(ValueError):
            bootstrap({"app": "from . import relative"})
        with self.assertRaises(SyntaxError):
            bootstrap({"app": "def broken("})

    def test_native_representations_consume_captured_dependencies(self):
        from frontierstore.baselines import SQLiteNormalized, SQLiteRootedAudit
        from frontierstore.store import FrontierStore
        modules = {"app": "import lib", "lib": "def f(): pass", "unused": "pass"}
        initial = bootstrap(modules)
        with tempfile.TemporaryDirectory() as directory:
            for kind in (FrontierStore, SQLiteNormalized, SQLiteRootedAudit):
                with self.subTest(engine=kind.__name__):
                    path = Path(directory) / kind.__name__
                    owner = kind.create(path, initial)
                    state = initial
                    try:
                        for updated in ({**modules, "lib": "def g(): pass"},
                                        {**modules, "app": "import new", "new": "import lib"},
                                        {"app": "import lib"}):
                            state, delta, _ = refresh(state, updated)
                            if kind is FrontierStore:
                                owner.publish_precomputed(state, delta)
                                self.assertEqual(FrontierStore(path).state, state)
                            else:
                                owner.persist_precomputed(state, delta)
                                self.assertEqual(owner.durable_state(), state)
                            self.assert_full_recompute(state, updated)
                    finally:
                        if kind is not FrontierStore:
                            owner.close()
