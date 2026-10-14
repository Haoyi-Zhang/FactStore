"""Owned small JSON controls for data compression, not arbitrary archives."""
import gzip
import json
from pathlib import Path
import tempfile
import unittest

from experiments.compressed_records import bind_record, decode_record


class CompressedRecordTests(unittest.TestCase):
    def setUp(self):
        self.row = {'sources': 8, 'facts': 48, 'updates': 12, 'policy': 'all',
                    'raw_record_count': 15, 'endpoint_equal': True, 'held_endpoint_equal': True,
                    'records': [{'event': 'bootstrap'}] +
                               [{'event': 'update', 'index': i} for i in range(1, 13)] +
                               [{'event': 'release'}, {'event': 'close'}]}
        self.summary = {k: v for k, v in self.row.items() if k != 'records'}

    def decode(self, data, limit=8192):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'owned-record.json.gz'
            path.write_bytes(gzip.compress(data))
            return decode_record(path, limit=limit)

    def test_valid_bounded_record_binds(self):
        value = self.decode(json.dumps(self.row).encode())
        bind_record(value, self.summary, 8, 'all')

    def test_decode_bound_is_enforced(self):
        with self.assertRaisesRegex(ValueError, 'decoded-byte limit'):
            self.decode(b' ' * 100, limit=50)

    def test_duplicate_and_nonfinite_json_reject(self):
        for data in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b'[]'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.decode(data)

    def test_truncated_gzip_rejects(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'owned-record.json.gz'
            path.write_bytes(gzip.compress(b'{"value":1}')[:-2])
            with self.assertRaises((EOFError, OSError)):
                decode_record(path)

    def test_unknown_case_and_missing_update_reject(self):
        with self.assertRaisesRegex(ValueError, 'unknown lifecycle case'):
            bind_record(self.row, self.summary, 9, 'all')
        self.row['records'][1]['index'] = 2
        with self.assertRaisesRegex(ValueError, 'update sequence'):
            bind_record(self.row, self.summary, 8, 'all')

    def test_typed_counts_and_full_summary_binding(self):
        self.row['updates'] = True
        with self.assertRaisesRegex(ValueError, 'count mismatch'):
            bind_record(self.row, self.summary, 8, 'all')
        self.row['updates'] = 12
        self.row['records'][1]['index'] = True
        with self.assertRaisesRegex(ValueError, 'update sequence'):
            bind_record(self.row, self.summary, 8, 'all')
        self.row['records'][1]['index'] = 1
        self.summary['endpoint_equal'] = 1
        with self.assertRaisesRegex(ValueError, 'differs from summary'):
            bind_record(self.row, self.summary, 8, 'all')


if __name__ == '__main__':
    unittest.main()
