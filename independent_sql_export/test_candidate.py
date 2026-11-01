"""Owned functional/oracle tests, no timing or external program execution."""
import ast
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from export_consumer import load, parse_bytes, require_endpoint
from frozen_inputs import history
from sql_export import LogicalSQL, canonical, export_database, read_database, read_transaction


class ExportTests(unittest.TestCase):
    def test_unsupported_directory_sync_refused_before_publication(self):
        from sql_export import publish_export
        with patch('sql_export.os.name', 'nt'):
            with self.assertRaisesRegex(ValueError, 'no output was published'):
                publish_export(self.root / 'must-not-exist.json', self.initial)
        self.assertFalse((self.root / 'must-not-exist.json').exists())
        self.assertFalse((self.root / '.must-not-exist.json.pending').exists())

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='owned-test-', dir=TEST_ROOT))
        self.case = history(16, 2)
        self.initial = self.case['initial']

    def tearDown(self):
        pass  # Keep malformed inputs and any unexpected failure specimens.

    def store(self, name='state.sqlite'):
        return LogicalSQL.create(self.root / name, self.initial)

    def test_complete_roundtrip_and_uninterpreted_payloads(self):
        store = self.store()
        try:
            export_database(store.path, self.root / 'export.json')
            decoded = load(self.root / 'export.json')
            require_endpoint(decoded, self.initial)
            restored = LogicalSQL.create(self.root / 'restored.sqlite', decoded)
            restored.close()
            require_endpoint(read_database(self.root / 'restored.sqlite'), self.initial)
        finally:
            store.close()

    def test_sql_transition_uses_indexed_invalidation(self):
        store = self.store()
        try:
            for event in self.case['events']:
                require_endpoint(store.apply(event['transaction']), event['expected'])
                require_endpoint(read_database(store.path), event['expected'])
        finally:
            store.close()

    def test_held_transaction_is_not_a_closed_mixed_endpoint(self):
        store = self.store()
        try:
            event = self.case['events'][0]
            old = read_database(store.path, after_metadata=lambda: store.apply(event['transaction']))
            require_endpoint(old, self.initial)
            require_endpoint(read_database(store.path), event['expected'])
            self.assertNotEqual(old, event['expected'])
        finally:
            store.close()

    def test_autocommit_materialization_refused(self):
        store = self.store()
        try:
            with self.assertRaisesRegex(ValueError, 'explicit transaction'):
                read_transaction(store.db)
        finally:
            store.close()

    def test_rollback_after_invalid_replacement_leaves_endpoint(self):
        store = self.store()
        tx = copy.deepcopy(self.case['events'][0]['transaction'])
        tx['fact_replacements'].append(dict(id='badFact', payload='x', sources=['missing']))
        try:
            with self.assertRaisesRegex(ValueError, 'absent source'):
                store.apply(tx)
            require_endpoint(read_database(store.path), self.initial)
        finally:
            store.close()

    def test_existing_target_never_replaced(self):
        store = self.store()
        store.close()
        before = (self.root / 'state.sqlite').read_bytes()
        with self.assertRaisesRegex(ValueError, 'fresh database'):
            LogicalSQL.create(self.root / 'state.sqlite', self.initial)
        self.assertEqual(before, (self.root / 'state.sqlite').read_bytes())

    def test_sql_domain_faults_not_filtered_by_inner_join(self):
        mutations = [
            "INSERT INTO dependencies VALUES('missingFact','s0000',1)",
            "UPDATE dependencies SET source_id='missingSource' WHERE fact_id='f00000' AND source_id='s0000'",
            "UPDATE dependencies SET generation=999 WHERE fact_id='f00000' AND source_id='s0000'",
            "DELETE FROM dependencies WHERE fact_id='f00000'",
            "UPDATE facts SET schema=2 WHERE id='f00000'",
            "INSERT INTO meta VALUES('extra',1)",
            "UPDATE sources SET generation=2 WHERE id='s0000'",
            "UPDATE facts SET payload=NULL WHERE id='f00000'",
        ]
        for index, sql in enumerate(mutations):
            store = self.store(f'mutation{index}.sqlite')
            try:
                try:
                    store.db.execute(sql); store.db.commit()
                except sqlite3.IntegrityError:
                    store.db.rollback()
                    continue  # Native NOT NULL rejection is an actual refusal.
                with self.assertRaises(ValueError, msg=sql):
                    read_database(store.path)
            finally:
                store.close()

    def test_duplicate_dependency_in_malformed_sql_schema(self):
        store = self.store()
        try:
            store.db.executescript('ALTER TABLE dependencies RENAME TO olddeps; CREATE TABLE dependencies(fact_id TEXT,source_id TEXT,generation INTEGER); INSERT INTO dependencies SELECT * FROM olddeps; INSERT INTO dependencies SELECT * FROM olddeps LIMIT 1; DROP TABLE olddeps;')
            with self.assertRaisesRegex(ValueError, 'duplicate dependency'):
                read_database(store.path)
        finally:
            store.close()

    def test_export_consumer_rejects_actual_structure_faults(self):
        values = []
        for mutate in [lambda x: x.update(epoch=True),
                       lambda x: x.update(epoch=100_000_000),
                       lambda x: x['reverse']['s0000'].append('fFake'),
                       lambda x: x['sources']['s0000'].update(generation=99),
                       lambda x: x['facts']['f00000'].update(schema=True),
                       lambda x: x['facts']['f00000'].update(dependencies=[]),
                       lambda x: x['facts']['f00000']['dependencies'].reverse(),
                       lambda x: x['facts']['f00000']['dependencies'].append(x['facts']['f00000']['dependencies'][0])]:
            value = copy.deepcopy(self.initial); mutate(value); values.append(value)
        for value in values:
            with self.assertRaises((ValueError, UnicodeError)):
                parse_bytes(canonical(value))
        for data in [b'{"epoch":1,"epoch":2}', b'{"epoch":NaN}', b'\xff']:
            with self.assertRaises((ValueError, UnicodeError)):
                parse_bytes(data)

    def test_closed_wrong_payload_requires_endpoint_oracle(self):
        value = copy.deepcopy(self.initial)
        value['facts']['f00000']['payload'] = 'wrong but still closed'
        decoded = parse_bytes(canonical(value))
        with self.assertRaisesRegex(ValueError, 'endpoint mismatch'):
            require_endpoint(decoded, self.initial)

    def test_bad_reverse_import_not_silently_repaired(self):
        value = copy.deepcopy(self.initial)
        value['reverse']['s0000'] = []
        with self.assertRaisesRegex(ValueError, 'input differs'):
            LogicalSQL.create(self.root / 'bad.sqlite', value)

    def test_independence_import_boundary(self):
        root = Path(__file__).parent
        for filename in ['sql_export.py', 'export_consumer.py', 'frozen_inputs.py']:
            tree = ast.parse((root / filename).read_text())
            names = [node.module or '' for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
            names += [a.name for node in ast.walk(tree) if isinstance(node, ast.Import) for a in node.names]
            self.assertFalse(any(n.startswith('frontierstore') for n in names))
            if filename == 'export_consumer.py':
                self.assertNotIn('sqlite3', names)
                self.assertNotIn('sql_export', names)


TEST_ROOT = None  # Set to the private run directory by the guarded runner.
