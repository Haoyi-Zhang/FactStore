"""Native integer encoding is validated before any persistent mutation."""
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from frontierstore import baselines
from frontierstore.model import State, TransitionError


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


if __name__ == '__main__':
    unittest.main()
