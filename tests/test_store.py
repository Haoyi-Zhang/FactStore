from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from frontierstore.baselines import DBMDumbRebuild, SQLiteNormalized, SQLiteRebuild
from frontierstore.checker import run as independent_check
from frontierstore.model import Transaction, TransitionError, apply_transaction, check_closure, states_equal
from frontierstore.store import FrontierStore, StoreFormatError
from frontierstore.workload import make_synthetic_state, make_update


class FrontierStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.initial = make_synthetic_state(12, facts_per_source=3, dependency_width=2)
        self.store = FrontierStore.create(self.root / "store", self.initial)

    def tearDown(self) -> None:
        try:
            self.temporary.cleanup()
        except PermissionError:
            pass

    def assert_closed(self, state) -> None:
        self.assertEqual([], check_closure(state))

    def read_root(self, path: Path | None = None):
        store_path = path or (self.root / "store")
        return json.loads((store_path / "ROOT").read_text(encoding="ascii"))

    def write_root(self, value, path: Path | None = None):
        store_path = path or (self.root / "store")
        (store_path / "ROOT").write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="ascii")

    def test_01_bootstrap_is_closed_and_checker_accepts(self):
        self.assert_closed(self.store.state)
        self.assertEqual("ACCEPT", independent_check(self.root / "store")["status"])

    def test_02_source_replace_invalidates_and_replaces(self):
        transaction = make_update(self.store.state, batch=2, seed=7)
        old = self.store.state
        changed = set(transaction.source_changes)
        affected = set().union(*(set(old.reverse[source]) for source in changed))
        new = self.store.commit(transaction)
        self.assert_closed(new)
        self.assertTrue(affected <= set(new.facts))
        for fact_id in affected:
            self.assertNotEqual(old.facts[fact_id].payload, new.facts[fact_id].payload)

    def test_03_omitted_invalidated_fact_remains_absent(self):
        state = self.store.state
        source = sorted(state.sources)[0]
        victim = state.reverse[source][0]
        transaction = Transaction(source_changes={source: "changed"}, fact_replacements=[])
        new = self.store.commit(transaction)
        self.assertNotIn(victim, new.facts)
        self.assert_closed(new)

    def test_04_explicit_fact_retraction_updates_inverse(self):
        state = self.store.state
        victim = sorted(state.facts)[0]
        dependencies = [source for source, _ in state.facts[victim].dependencies]
        new = self.store.commit(Transaction(fact_retractions=[victim]))
        self.assertNotIn(victim, new.facts)
        for source in dependencies:
            self.assertNotIn(victim, new.reverse[source])
        self.assert_closed(new)

    def test_05_source_retraction_removes_all_dependents(self):
        state = self.store.state
        source = sorted(state.sources)[0]
        dependents = set(state.reverse[source])
        new = self.store.commit(Transaction(source_changes={source: None}))
        self.assertNotIn(source, new.sources)
        self.assertNotIn(source, new.reverse)
        self.assertTrue(dependents.isdisjoint(new.facts))
        self.assert_closed(new)

    def test_06_source_reintroduction_uses_fresh_generation(self):
        source = sorted(self.store.state.sources)[0]
        first_generation = self.store.state.sources[source].generation
        self.store.commit(Transaction(source_changes={source: None}))
        reintroduced = self.store.commit(Transaction(source_changes={source: "returned"}))
        self.assertGreater(reintroduced.sources[source].generation, first_generation)
        self.assertEqual(reintroduced.epoch, reintroduced.sources[source].generation)

    def test_07_schema_cutover_removes_old_schema_facts(self):
        state = self.store.state
        fact = state.facts[sorted(state.facts)[0]]
        replacement = {"id": fact.identifier, "payload": "new schema", "sources": [source for source, _ in fact.dependencies]}
        new = self.store.commit(Transaction(new_schema=2, fact_replacements=[replacement]))
        self.assertEqual(2, new.schema)
        self.assertEqual({2}, {value.schema for value in new.facts.values()})
        self.assertEqual({fact.identifier}, set(new.facts))
        self.assert_closed(new)

    def test_08_missing_dependency_rejection_is_atomic(self):
        before = self.store.state.as_dict()
        with self.assertRaises(TransitionError) as raised:
            self.store.commit(Transaction(fact_replacements=[{"id": "fresh", "payload": "x", "sources": ["missing"]}]))
        self.assertEqual("MISSING_SOURCE", raised.exception.code)
        self.assertEqual(before, self.store.state.as_dict())

    def test_09_duplicate_dependency_rejection_is_atomic(self):
        source = sorted(self.store.state.sources)[0]
        before = self.store.state.as_dict()
        with self.assertRaises(TransitionError) as raised:
            self.store.commit(Transaction(fact_replacements=[{"id": "fresh", "payload": "x", "sources": [source, source]}]))
        self.assertEqual("DUPLICATE_DEPENDENCY", raised.exception.code)
        self.assertEqual(before, self.store.state.as_dict())

    def test_10_bad_identifier_rejection_is_atomic(self):
        before = self.store.state.as_dict()
        with self.assertRaises(TransitionError):
            self.store.commit(Transaction(source_changes={"../escape": "x"}))
        self.assertEqual(before, self.store.state.as_dict())

    def test_11_preview_is_a_defensive_state_copy(self):
        preview = self.store.preview(make_update(self.store.state, batch=1, seed=2))
        preview.sources.clear()
        self.assertEqual(len(self.initial.sources), len(self.store.state.sources))

    def test_12_snapshot_source_accessor_is_defensive(self):
        source_id = sorted(self.store.state.sources)[0]
        with self.store.snapshot() as snapshot:
            value = snapshot.get_source(source_id)
            value["payload"] = "mutated"
            self.assertNotEqual("mutated", snapshot.get_source(source_id)["payload"])

    def test_13_snapshot_fact_accessor_is_defensive(self):
        fact_id = sorted(self.store.state.facts)[0]
        with self.store.snapshot() as snapshot:
            value = snapshot.get_fact(fact_id)
            value["dependencies"].clear()
            self.assertTrue(snapshot.get_fact(fact_id)["dependencies"])

    def test_14_snapshot_remains_epoch_stable(self):
        snapshot = self.store.snapshot()
        old = snapshot.materialize()
        self.store.commit(make_update(self.store.state, batch=2, seed=4))
        self.assertEqual(old, snapshot.materialize())
        self.assertLess(snapshot.epoch, self.store.state.epoch)
        snapshot.close()

    def test_15_pin_prevents_collection_until_release(self):
        snapshot = self.store.snapshot()
        pinned = set(self.store.selected_segments)
        self.store.commit(make_update(self.store.state, batch=1, seed=5))
        self.store.compact()
        self.store.collect()
        remaining = {path.name for path in (self.root / "store" / "segments").glob("segment-*.jsonl")}
        self.assertTrue(pinned <= remaining)
        snapshot.close()
        self.store.collect()
        remaining_after = {path.name for path in (self.root / "store" / "segments").glob("segment-*.jsonl")}
        self.assertTrue(pinned.isdisjoint(remaining_after))

    def test_16_compaction_is_observationally_equivalent(self):
        self.store.commit(make_update(self.store.state, batch=2, seed=6))
        before = self.store.state
        after = self.store.compact()
        self.assertTrue(states_equal(before, after, include_epoch=False))
        self.assertEqual(1, len(self.store.selected_segments))
        self.assertEqual("ACCEPT", independent_check(self.root / "store")["status"])

    def test_17_orphan_segment_is_not_visible(self):
        selected_before = self.store.selected_segments
        visible_before = self.store.state.as_dict()
        orphan = self.root / "store" / "segments" / "segment-99999999.jsonl"
        shutil.copy2(self.root / "store" / "segments" / selected_before[0], orphan)
        reopened = FrontierStore(self.root / "store")
        self.assertEqual(visible_before, reopened.state.as_dict())
        self.assertNotIn(orphan.name, reopened.selected_segments)

    def test_18_path_escape_is_rejected_by_both_parsers(self):
        root = self.read_root()
        root["segments"] = ["../segment-00000001.jsonl"]
        self.write_root(root)
        with self.assertRaises(StoreFormatError):
            FrontierStore(self.root / "store")
        self.assertEqual("PATH_ESCAPE", independent_check(self.root / "store")["witness"])

    def test_19_first_selected_segment_must_be_base(self):
        root = self.read_root()
        segment = self.root / "store" / "segments" / root["segments"][0]
        lines = segment.read_text(encoding="ascii").splitlines()
        header = json.loads(lines[0])
        header["mode"] = "delta"
        header["parent"] = 0
        lines[0] = json.dumps(header, sort_keys=True, separators=(",", ":"))
        segment.write_text("\n".join(lines) + "\n", encoding="ascii")
        with self.assertRaises(StoreFormatError):
            FrontierStore(self.root / "store")
        self.assertEqual("FIRST_NOT_BASE", independent_check(self.root / "store")["witness"])

    def test_20_root_count_tampering_is_detected(self):
        root = self.read_root()
        root["counts"]["facts"] += 1
        self.write_root(root)
        with self.assertRaises(StoreFormatError):
            FrontierStore(self.root / "store")
        self.assertEqual("ROOT_COUNT", independent_check(self.root / "store")["witness"])

    def test_21_missing_selected_segment_is_detected(self):
        root = self.read_root()
        (self.root / "store" / "segments" / root["segments"][0]).unlink()
        with self.assertRaises(StoreFormatError):
            FrontierStore(self.root / "store")
        self.assertEqual("MISSING_SEGMENT", independent_check(self.root / "store")["witness"])

    def test_22_reopen_reconstructs_exact_state(self):
        self.store.commit(make_update(self.store.state, batch=3, seed=11))
        expected = self.store.state
        reopened = FrontierStore(self.root / "store")
        self.assertTrue(states_equal(expected, reopened.state))
        self.assertEqual("ACCEPT", independent_check(self.root / "store")["status"])

    def test_23_all_durable_baselines_match_expected_state(self):
        transaction = make_update(self.initial, batch=2, seed=19)
        expected, delta = apply_transaction(self.initial, transaction)
        classes = [SQLiteNormalized, SQLiteRebuild, DBMDumbRebuild]
        for baseline_class in classes:
            with self.subTest(baseline=baseline_class.name):
                path = self.root / baseline_class.name
                baseline = baseline_class.create(path, self.initial)
                try:
                    baseline.persist_precomputed(expected, delta)
                    self.assertTrue(states_equal(expected, baseline.durable_state()))
                finally:
                    baseline.close()


if __name__ == "__main__":
    unittest.main()
