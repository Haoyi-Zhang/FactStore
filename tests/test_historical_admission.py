"""Fail-closed admission of retained data; no engine or new timing run."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from experiments.summarize_results import summarize
from experiments.verify_checked_results import verify

RESULTS = Path(__file__).resolve().parents[1] / 'results'


class HistoricalAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='factstore-retained-admission-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ('raw', 'summary'):
            shutil.copytree(RESULTS / name, self.root / name)

    def mutate_csv(self, name, field, value):
        path = self.root / 'raw' / (name + '.csv')
        with path.open(newline='', encoding='utf-8') as stream:
            reader = csv.DictReader(stream)
            fields, rows = reader.fieldnames, list(reader)
        rows[0][field] = value
        with path.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def mutate_resource(self, field, value):
        path = self.root / 'raw/resources.json'
        record = json.loads(path.read_text(encoding='utf-8'))
        record[field] = value
        path.write_text(json.dumps(record), encoding='utf-8')

    def test_original_data_and_summaries_still_pass(self):
        self.assertEqual('RETAINED_DATA_CONSISTENT', verify(self.root)['status'])

    def test_nonfinite_measurements_are_rejected_before_aggregation(self):
        original = (self.root / 'raw/scale.csv').read_bytes()
        for field in ('update_ms', 'query_p50_us', 'write_bytes', 'space_amplification'):
            for value in ('nan', 'inf', '-inf'):
                with self.subTest(field=field, value=value):
                    (self.root / 'raw/scale.csv').write_bytes(original)
                    self.mutate_csv('scale', field, value)
                    with self.assertRaisesRegex(AssertionError, 'finite'):
                        summarize(self.root, self.root / 'derived')

    def test_malformed_timeout_cannot_mean_false(self):
        self.mutate_csv('crash', 'timeout', 'not-a-boolean')
        with self.assertRaisesRegex(AssertionError, 'boolean'):
            summarize(self.root, self.root / 'derived')

    def test_verifier_rejects_bad_raw_before_reconstruction(self):
        self.mutate_csv('scale', 'query_p50_us', 'inf')
        with self.assertRaisesRegex(AssertionError, 'finite'):
            verify(self.root)

    def test_negative_measurement_is_rejected(self):
        self.mutate_csv('scale', 'query_p50_us', '-0.5')
        with self.assertRaisesRegex(AssertionError, 'nonnegative'):
            summarize(self.root, self.root / 'derived')

    def test_unknown_mode_cannot_bypass_full_count_validation(self):
        self.mutate_resource('mode', 'unrecognized')
        with self.assertRaisesRegex(AssertionError, 'mode'):
            summarize(self.root, self.root / 'derived')

    def test_invalid_resources_are_not_republished_as_valid_history(self):
        for field, value in (('wall_seconds', -1), ('workers', True),
                             ('peak_resident_mib', float('inf'))):
            with self.subTest(field=field):
                shutil.copy2(RESULTS / 'raw/resources.json', self.root / 'raw/resources.json')
                self.mutate_resource(field, value)
                with self.assertRaisesRegex(AssertionError, 'resource'):
                    summarize(self.root, self.root / 'derived')


if __name__ == '__main__':
    unittest.main()
