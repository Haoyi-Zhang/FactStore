"""Portable native-data admission tests; no native persistence is emulated."""
from contextlib import contextmanager
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from experiments import fresh_run
from experiments import native_fanout_results as results

ROOT = results.native.ROOT
DATA = ROOT / 'results/native-fanout'


@contextmanager
def dataset_copy():
    with tempfile.TemporaryDirectory(prefix='p021-native-data-test-') as temporary:
        target = Path(temporary) / 'results/native-fanout'
        shutil.copytree(DATA, target)
        yield target


def update_import_binding(directory, relative):
    path = directory / relative
    origin = json.loads((directory / 'origin.json').read_text())
    entry = next(x for x in origin['imported_members'] if x['path'] == relative)
    entry.update(bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    (directory / 'origin.json').write_text(json.dumps(origin), encoding='utf-8')


class NativeFanoutResultsTests(unittest.TestCase):
    def test_exact_native_cohort_replays_and_drives_table(self):
        result, admitted, origin = results.verify_dataset(DATA)
        self.assertEqual((36, 288, 180, 144, 36), (result['cases'], result['updates'],
            result['scheduled_audits'], result['held_endpoint_reads'], result['orderly_reopens']))
        self.assertEqual(36, len(admitted))
        self.assertEqual(40066415944, origin['run_id'])
        table = results.render_table(result)
        self.assertIn('1128.09 & 72.35 & 189.33', table)
        self.assertIn('2036.46 & 204.64 & 523.44', table)
        self.assertEqual(12, sum(' & \\system &' in x or ' & \\sqlnorm &' in x or
                                 ' & \\sqlroot &' in x for x in table.splitlines()))

    def test_raw_and_preimage_tampering_are_rejected(self):
        with dataset_copy() as directory:
            trace = next(directory.glob('cases/*/trace.jsonl.gz'))
            old = trace.read_bytes()
            trace.write_bytes(old + b'changed')
            with self.assertRaisesRegex(ValueError, 'member bytes differ'):
                results.bind_origin(directory, ROOT)
            trace.write_bytes(old)
            preimage = directory / 'source-preimage/experiments/native_fanout_cadence.py.txt'
            preimage.write_bytes(preimage.read_bytes() + b'\n')
            with self.assertRaisesRegex(ValueError, 'measured/preimage source differs'):
                results.bind_origin(directory, ROOT)

    def test_changed_ordered_input_rejects_even_with_consistent_import_hash(self):
        with dataset_copy() as directory:
            relative = 'cases/r0-w8-a1-frontier/trace.jsonl.gz'
            path = directory / relative
            records = results.trace_records(path)
            dependencies = records[1]['transaction']['fact_replacements'][0]['sources']
            self.assertGreater(len(dependencies), 1)
            dependencies[0], dependencies[1] = dependencies[1], dependencies[0]
            with gzip.open(path, 'wt', encoding='utf-8') as stream:
                stream.write('\n'.join(json.dumps(x) for x in records) + '\n')
            update_import_binding(directory, relative)
            with self.assertRaisesRegex(ValueError, 'unequal input transition'):
                results.verify_dataset(directory)

    def test_unknown_compressed_neighbor_and_changed_summary_reject(self):
        with dataset_copy() as directory:
            extra = directory / 'cases/r0-w8-a1-frontier/extra.jsonl.gz'
            extra.write_bytes(gzip.compress(b'{}'))
            with self.assertRaisesRegex(ValueError, 'unconsumed/missing native dataset'):
                results.verify_dataset(directory)
            extra.unlink()
            path = directory / 'verified-summary.json'
            value = json.loads(path.read_text())
            value['medians'][0]['median_wall_ms'] += 1
            path.write_text(json.dumps(value), encoding='utf-8')
            update_import_binding(directory, 'verified-summary.json')
            with self.assertRaisesRegex(ValueError, 'uploaded summary differs'):
                results.verify_dataset(directory)

    def test_strict_bounded_jsonl_rejects_duplicate_nonfinite_and_truncation(self):
        with tempfile.TemporaryDirectory(prefix='p021-native-decode-') as temporary:
            path = Path(temporary) / 'trace.jsonl.gz'
            for raw in (b'{"event":1,"event":2}\n', b'{"phase_ns":NaN}\n', b'{"phase_ns":1e999}\n'):
                path.write_bytes(gzip.compress(raw))
                with self.assertRaises(ValueError):
                    results.trace_records(path)
            path.write_bytes(gzip.compress(b'x' * 100))
            with self.assertRaisesRegex(ValueError, 'decoded-byte limit'):
                results.trace_records(path, limit=50)
            path.write_bytes(gzip.compress(b'{}')[:-6])
            with self.assertRaises((EOFError, OSError, ValueError)):
                results.trace_records(path)

    def test_current_suite_executes_extensions_without_relabeling_frozen_baseline(self):
        inventory = fresh_run.source_inventory(ROOT)
        extensions = [x for x in inventory if x.startswith('tests.test_native_fanout_results.')]
        self.assertEqual(7, len(extensions))
        self.assertEqual(132, len(fresh_run.baseline_inventory(inventory)))
        suite = unittest.TestLoader().loadTestsFromName('tests.test_native_fanout_results')
        self.assertEqual(len(extensions), suite.countTestCases())
        self.assertFalse(set(extensions) & set(fresh_run.baseline_inventory(inventory)))

    def test_measured_bytes_survive_git_checkout_with_autocrlf_enabled(self):
        with tempfile.TemporaryDirectory(prefix='p021-native-checkout-') as temporary:
            root = Path(temporary)
            expected = {}
            for name in results.native.SOURCES:
                expected[name] = (ROOT / name).read_bytes()
            expected['results/native-fanout/source-preimage/driver.txt'] = b'line one\nline two\n'
            expected['results/native-fanout/cases/owned/store/segments/segment-00000011.jsonl'] = b'{"owned":true}\n'
            expected['results/native-fanout/cases/owned/trace.jsonl.gz'] = gzip.compress(b'{"owned":true}\n')
            shutil.copyfile(ROOT / '.gitattributes', root / '.gitattributes')
            for name, data in expected.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            def git(*args):
                subprocess.run(['git', '-c', 'core.autocrlf=true', '-c', 'core.safecrlf=false',
                                '-c', 'user.name=Owned fixture', '-c', 'user.email=fixture@invalid', *args],
                               cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=20)
            git('init', '--quiet')
            git('add', '.')
            git('commit', '--quiet', '-m', 'Owned byte preservation fixture')
            for name in expected:
                (root / name).unlink()
            git('checkout-index', '--all', '--force')
            self.assertEqual(expected, {name: (root / name).read_bytes() for name in expected})


if __name__ == '__main__':
    unittest.main()
