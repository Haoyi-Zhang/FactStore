"""Owned small JSON controls for data compression, not arbitrary archives."""
import gzip
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from experiments.compressed_records import bind_record, decode_record
from experiments.sqlite_fanout_admission import CONSUMED_SOURCES, verify_sqlite_fanout

ROOT=Path(__file__).resolve().parents[1]


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


class SQLiteFanoutAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='factstore-fanout-admission-')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.directory=self.root/'results/sqlite-fanout'
        self.directory.mkdir(parents=True)
        source=ROOT/'results/sqlite-fanout'
        for path in source.iterdir():
            if path.is_file():
                shutil.copy2(path,self.directory/path.name)
        for name in CONSUMED_SOURCES:
            target=self.root/name
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(ROOT/name,target)

    def test_exact_twelve_source_bound_scientific_records_are_admitted(self):
        admitted=verify_sqlite_fanout(self.root)
        expected={f'results/sqlite-fanout/r{r}-w{w}-a{a}.json.gz'
                  for r in range(3) for w in (2,8) for a in (1,4)}
        self.assertEqual(admitted,expected)
        self.assertNotIn('results/sqlite-fanout/arbitrary.zip',admitted)

    def test_missing_or_unrecognized_compressed_case_rejects(self):
        path=self.directory/'r0-w2-a1.json.gz'
        path.rename(self.directory/'r0-w2-a1-copy.json.gz')
        with self.assertRaisesRegex(ValueError,'filename coverage'):
            verify_sqlite_fanout(self.root)

    def test_measured_source_cannot_be_rebound(self):
        path=self.root/'experiments/fanout_cadence.py'
        path.write_bytes(path.read_bytes()+b'\n# owned source mutation\n')
        with self.assertRaisesRegex(ValueError,'source binding'):
            verify_sqlite_fanout(self.root)

    def test_altered_raw_transaction_rejects_even_with_unchanged_counts(self):
        path=self.directory/'r0-w2-a1.json.gz'
        raw=decode_record(path)
        raw['records'][1]['transaction']['fact_replacements'][0]['payload']='owned changed payload'
        path.write_bytes(gzip.compress(json.dumps(raw).encode('utf-8')))
        with self.assertRaisesRegex(ValueError,'input transition'):
            verify_sqlite_fanout(self.root)

    def test_duplicate_summary_identity_and_changed_median_reject(self):
        path=self.directory/'summary.json'
        original=path.read_bytes()
        value=json.loads(original)
        value['rows'][1]=value['rows'][0]
        path.write_text(json.dumps(value),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'case coverage'):
            verify_sqlite_fanout(self.root)
        value=json.loads(original)
        value['aggregates'][0]['median_lifecycle_ms']+=1
        path.write_text(json.dumps(value),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'aggregate medians'):
            verify_sqlite_fanout(self.root)

    def test_real_package_gate_still_rejects_unadmitted_archives(self):
        from experiments import verify_artifact
        copy=self.root/'complete-artifact'
        shutil.copytree(ROOT,copy)
        # No archive is unpacked or executed; this benign owned marker tests the
        # actual package gate after both scientific consumers have succeeded.
        for name in ('unconsumed.zip','unconsumed.tar','unconsumed.7z','unconsumed.gz'):
            path=copy/'results/sqlite-fanout'/name
            path.write_bytes(b'owned non-executable rejected archive marker')
            with self.subTest(name=name),patch.object(verify_artifact,'__file__',str(copy/'experiments/verify_artifact.py')):
                with self.assertRaisesRegex((AssertionError,ValueError),'nested archives|filename coverage'):
                    verify_artifact.main()
            path.unlink()


if __name__ == '__main__':
    unittest.main()
