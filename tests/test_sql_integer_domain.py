"""Native integer encoding is validated before any persistent mutation."""
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from frontierstore import baselines
from frontierstore.model import State, TransitionError, bootstrap_state, apply_transaction, Transaction


def state(schema=1, epoch=1):
    return State(epoch, schema, {}, {}, {})


class SQLiteIntegerDomain(unittest.TestCase):
    def test_supported_schema_boundaries(self):
        with sqlite3.connect(':memory:') as db:
            for schema in (1, (1 << 63) - 1):
                captured = baselines._capture_sql_state(state(schema))
                self.assertEqual(db.execute('SELECT ?', (captured.schema,)).fetchone()[0], schema)

    def test_replacing_create_rejects_before_filesystem_access(self):
        for adapter in (baselines.SQLiteNormalized, baselines.SQLiteRebuild,
                        baselines.SQLiteRootedAudit):
            for proposed in (state(1 << 63), state(epoch=1 << 63)):
                with self.subTest(adapter=adapter.__name__, state=proposed):
                    with patch.object(Path, 'exists', side_effect=AssertionError('filesystem touched')):
                        with self.assertRaises(TransitionError):
                            adapter.create('unused-preflight-target', proposed)

    def test_update_rejects_before_delta_or_mutation(self):
        for adapter in (baselines.SQLiteNormalized, baselines.SQLiteRebuild,
                        baselines.SQLiteRootedAudit):
            instance = adapter.__new__(adapter)
            instance._failed = False
            instance._state = state()
            with patch.object(baselines, 'capture_and_validate_delta',
                              side_effect=AssertionError('delta reached')):
                with self.assertRaises(TransitionError):
                    instance.persist_precomputed(state(1 << 63), None)
            self.assertEqual(instance._state, state())
            self.assertFalse(instance._failed)

    def test_low_level_image_preflight_preserves_existing_path(self):
        with patch.object(Path, 'exists', side_effect=AssertionError('path touched')):
            with self.assertRaises(TransitionError):
                baselines.SQLiteRebuild._write_image(state(1 << 63), Path('unused-image'))

    def test_unencodable_text_create_rejects_before_filesystem_access(self):
        for family in ("source", "fact"):
            proposed = bootstrap_state(
                {"a": chr(0xD800) if family == "source" else "source"},
                [{"id": "p", "payload": chr(0xD800) if family == "fact" else "fact", "sources": ["a"]}],
            )
            for adapter in (baselines.SQLiteNormalized, baselines.SQLiteRebuild, baselines.SQLiteRootedAudit):
                with self.subTest(family=family, adapter=adapter.__name__):
                    with patch.object(Path, 'exists', side_effect=AssertionError('filesystem touched')):
                        with self.assertRaisesRegex(TransitionError, "SQLITE_TEXT_ENCODING"):
                            adapter.create('unused-preflight-target', proposed)

    def test_text_update_preflight_and_supported_unicode_round_trip(self):
        old = bootstrap_state({"a": "source"}, [{"id": "p", "payload": "fact", "sources": ["a"]}])
        proposed, delta = apply_transaction(old, Transaction(
            fact_replacements=[{"id": "p", "payload": chr(0xDC00), "sources": ["a"]}],
        ))
        for adapter in (baselines.SQLiteNormalized, baselines.SQLiteRebuild, baselines.SQLiteRootedAudit):
            instance = adapter.__new__(adapter)
            instance._failed = False
            instance._state = old
            with self.subTest(adapter=adapter.__name__):
                with patch.object(baselines, 'capture_and_validate_delta', side_effect=AssertionError('delta reached')):
                    with self.assertRaisesRegex(TransitionError, "SQLITE_TEXT_ENCODING"):
                        instance.persist_precomputed(proposed, delta)
                self.assertEqual(instance._state, old)
                self.assertFalse(instance._failed)
        for payload in ("", "\u0000", "source \u4e2d\u6587 \U0001f30d"):
            captured = baselines._capture_sql_state(bootstrap_state({"a": payload}, []))
            with sqlite3.connect(':memory:') as db:
                self.assertEqual(payload, db.execute('SELECT ?', (captured.sources["a"].payload,)).fetchone()[0])


if __name__ == '__main__':
    unittest.main()
