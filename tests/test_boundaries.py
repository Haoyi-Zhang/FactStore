"""Additional boundary regressions; no retained execution result is claimed."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from frontierstore.checker import run as independent_check
from frontierstore.model import (
    Delta, Fact, Source, State, Transaction, TransitionError,
    apply_transaction, bootstrap_state, check_closure,
)
from frontierstore.store import FrontierStore, StoreFormatError


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'store'
        self.initial = bootstrap_state(
            {'a': 'old', 'b': 'stable'},
            [{'id': 'p', 'payload': 'old fact', 'sources': ['a', 'b']}],
        )
        self.store = FrontierStore.create(self.path, self.initial)

    def tearDown(self):
        self.tmp.cleanup()

    def updated(self):
        return apply_transaction(self.store.state, Transaction(
            source_changes={'a': 'new'},
            fact_replacements=[{'id': 'p', 'payload': 'new fact', 'sources': ['a', 'b']}],
        ))

    def assert_rejected_unchanged(self, code, state, delta):
        root = (self.path / 'ROOT').read_bytes()
        selected = self.store.selected_segments
        with self.assertRaises(TransitionError) as caught:
            self.store.publish_precomputed(state, delta)
        self.assertEqual(code, caught.exception.code)
        self.assertEqual(root, (self.path / 'ROOT').read_bytes())
        self.assertEqual(selected, self.store.selected_segments)

    def test_future_source_generation_is_rejected(self):
        future = State(1, 1, {'a': Source('a', 2, 'x')},
                       {'p': Fact('p', 'x', 1, (('a', 2),))}, {'a': ('p',)})
        self.assertIn('SOURCE_GENERATION_HORIZON', {x['code'] for x in check_closure(future)})
        with self.assertRaises(TransitionError):
            FrontierStore.create(Path(self.tmp.name) / 'future', future)

    def test_future_generation_bytes_are_rejected_independently(self):
        segment = self.path / 'segments' / self.store.selected_segments[0]
        values = [json.loads(line) for line in segment.read_text().splitlines()]
        for row in values:
            if row['kind'] == 'source_put':
                row['generation'] = 2
            if row['kind'] == 'fact_put':
                for edge in row['dependencies']:
                    edge['generation'] = 2
        segment.write_text(''.join(json.dumps(x) + '\n' for x in values))
        self.assertEqual('SOURCE_GENERATION_HORIZON', independent_check(self.path)['witness'])
        with self.assertRaises(StoreFormatError):
            FrontierStore(self.path)

    def test_closed_target_does_not_justify_empty_delta(self):
        new, _ = self.updated()
        self.assert_rejected_unchanged('DELTA_STATE_MISMATCH', new, Delta({}, (), (), {}, {}, ()))

    def test_changed_source_requires_next_epoch_stamp(self):
        old = self.store.state
        source = Source('a', old.sources['a'].generation, 'different payload')
        sources = {**old.sources, 'a': source}
        new = State(old.epoch + 1, old.schema, sources, old.facts, old.reverse)
        delta = Delta({'a': source}, (), (), {}, {}, ())
        self.assert_rejected_unchanged('SOURCE_STAMP', new, delta)

    def test_precomputed_capture_does_not_alias_caller_maps(self):
        new, delta = self.updated()
        expected = new.as_dict()
        self.store.publish_precomputed(new, delta)
        with self.store.snapshot() as pinned:
            new.sources.clear()
            new.facts.clear()
            new.reverse.clear()
            delta.source_put.clear()
            delta.fact_put.clear()
            delta.reverse_put.clear()
            self.assertEqual(expected, pinned.materialize())
            self.assertEqual(expected, self.store.state.as_dict())
        self.assertEqual(expected, FrontierStore(self.path).state.as_dict())

    def test_dependency_list_is_captured_as_tuple(self):
        new, delta = self.updated()
        original = new.facts['p']
        edges = list(original.dependencies)
        fact = Fact(original.identifier, original.payload, original.schema, edges)
        new = replace(new, facts={'p': fact})
        delta = replace(delta, fact_put={'p': fact})
        expected = new.as_dict()
        self.store.publish_precomputed(new, delta)
        edges.clear()
        self.assertEqual(expected, self.store.state.as_dict())
        self.assertEqual('ACCEPT', independent_check(self.path)['status'])

    def test_duplicate_deletions_are_rejected_before_publication(self):
        new, delta = self.updated()
        self.assert_rejected_unchanged('DUPLICATE_DELTA_DELETE', new,
                                       replace(delta, fact_del=('p', 'p')))

    def test_nontext_deletion_is_not_hidden_by_map_equality(self):
        new, delta = self.updated()
        self.assert_rejected_unchanged('DELTA_IDENTIFIER', new,
                                       replace(delta, reverse_del=(7,)))

    def test_boolean_dependency_generation_is_rejected(self):
        fact = Fact('p', 'payload', 1, (('a', True), ('b', 1)))
        bad = replace(self.initial, facts={'p': fact})
        self.assertIn('DEPENDENCY_GENERATION', {x['code'] for x in check_closure(bad)})

    def test_epoch_name_limit_is_rejected_before_writing(self):
        initial = bootstrap_state({'a': 'x'}, [], epoch=99_999_999)
        path = Path(self.tmp.name) / 'limit'
        store = FrontierStore.create(path, initial)
        before = (path / 'ROOT').read_bytes()
        with self.assertRaises(TransitionError) as caught:
            store.commit(Transaction(source_changes={'a': 'y'}))
        self.assertEqual('EPOCH_FORMAT_LIMIT', caught.exception.code)
        self.assertEqual(before, (path / 'ROOT').read_bytes())

    def test_closure_alone_allows_nonatomic_intermediate(self):
        old = self.initial
        new, _ = self.updated()
        retracted_first = State(old.epoch, old.schema, old.sources, {}, {'a': (), 'b': ()})
        self.assertEqual([], check_closure(retracted_first))
        self.assertNotEqual(old.observational_dict(), retracted_first.observational_dict())
        self.assertNotEqual(new.observational_dict(), retracted_first.observational_dict())

    def test_current_and_pinned_snapshots_have_different_freshness(self):
        with self.store.snapshot() as pinned:
            old = pinned.materialize()
            protected = set(self.store.selected_segments)
            new, delta = self.updated()
            self.store.publish_precomputed(new, delta)
            with self.store.snapshot() as current:
                self.assertEqual(new.as_dict(), current.materialize())
            self.store.compact()
            self.store.collect()
            self.assertEqual(old, pinned.materialize())
            present = {x.name for x in (self.path / 'segments').glob('segment-*.jsonl')}
            self.assertTrue(protected <= present)
        self.store.collect()
        present = {x.name for x in (self.path / 'segments').glob('segment-*.jsonl')}
        self.assertTrue(protected.isdisjoint(present))


if __name__ == '__main__':
    unittest.main()
