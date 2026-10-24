"""Read-only admission and manuscript table for the reviewed native cohort.

This is a data consumer, not a replacement timing driver. The nine inputs bound
by the accepted driver remain unchanged. No native writer or benchmark is run.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath

from experiments import native_fanout_cadence as native
from experiments.compressed_records import (
    MAX_DECODED_BYTES, finite_float, reject_constant, unique_object,
)

HEAD = 'f12a27c375ede02ebd3a12443485ebea815df837'
ARCHIVE_SHA256 = '54a6411b32062ab724723bc0191496f782f58dc7d87ce4eda70a68cc062e9ff1'
PREFIX = 'p021-native-fanout-40066415944-1/native-fanout/'
LOG_PREFIX = 'p021-native-fanout-logs-40066415944-1/'


def require(value, message):
    if not value:
        raise ValueError(message)


def decode(text):
    return json.loads(text, object_pairs_hook=unique_object,
                      parse_constant=reject_constant, parse_float=finite_float)


def load(path):
    require(path.stat().st_size <= MAX_DECODED_BYTES, 'oversized JSON metadata')
    return decode(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def trace_records(path, *, limit=MAX_DECODED_BYTES):
    require(type(limit) is int and limit > 0, 'positive decoded-byte limit required')
    with gzip.open(path, 'rb') as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, 'native trace exceeds decoded-byte limit')
    records = [decode(line) for line in raw.decode('utf-8').splitlines()]
    require(len(records) == 10 and all(type(x) is dict for x in records), 'native trace coverage')
    for event in records:
        phases = event.get('phase_ns')
        require(type(phases) is dict and set(phases) == set(native.PHASES), 'native phase keys')
        require(all(type(v) is int and v >= 0 for v in phases.values()), 'invalid native phase cost')
    return records


def bind_origin(directory, root):
    origin = load(directory / 'origin.json')
    expected = dict(schema='reviewed-native-artifact-v1', dataset='native-fanout',
                    repository='https://github.com/Haoyi-Zhang/FactStore', run_id=40066415944,
                    run_attempt=1, job_id=120790520723, head=HEAD, artifact_id=12549104460,
                    artifact_sha256=ARCHIVE_SHA256, source_preimage_suffix='.txt')
    require(all(origin.get(k) == v for k, v in expected.items()), 'accepted run identity mismatch')
    members = origin.get('imported_members')
    require(type(members) is list and len(members) == 169, 'accepted artifact member coverage')
    paths, archives = set(), set()
    for entry in members:
        require(type(entry) is dict and set(entry) == {'archive_member', 'path', 'bytes', 'sha256'},
                'invalid imported member binding')
        name, rel = entry['archive_member'], entry['path']
        require(type(name) is str and type(rel) is str, 'invalid imported member name')
        parts = PurePosixPath(rel)
        require(not parts.is_absolute() and '..' not in parts.parts and '\\' not in rel and ':' not in rel
                and rel not in paths and name not in archives, 'unsafe/duplicate imported member')
        mapped = (name[len(PREFIX):] if name.startswith(PREFIX) else
                  'execution/' + name[len(LOG_PREFIX):] if name.startswith(LOG_PREFIX) else None)
        require(mapped == rel, 'archive-to-dataset path differs')
        path = directory / rel
        require(path.resolve().is_relative_to(directory.resolve()) and not path.is_symlink(), 'escaped dataset')
        require(type(entry['bytes']) is int and 0 <= entry['bytes'] <= MAX_DECODED_BYTES,
                'invalid imported member length')
        require(path.is_file() and path.stat().st_size == entry['bytes'] and sha(path) == entry['sha256'],
                'accepted member bytes differ: ' + rel)
        paths.add(rel)
        archives.add(name)
    require((directory / 'execution/commit.txt').read_text().strip() == HEAD, 'execution commit differs')
    sources = load(directory / 'source-before.json')
    require(set(sources) == set(native.SOURCES), 'nine measured sources required')
    for name, digest in sources.items():
        snapshot = 'source-preimage/' + name + '.txt'
        require(sha(directory / snapshot) == sha(root / name) == digest, 'measured/preimage source differs: ' + name)
        paths.add(snapshot)
    paths |= {'origin.json', 'README.md'}
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file()}
    require(actual == paths, 'unconsumed/missing native dataset members: ' + str(sorted(actual ^ paths)))
    return origin


def verify_dataset(directory, root=None):
    directory = Path(directory).resolve()
    root = (Path(root) if root is not None else native.ROOT).resolve()
    origin = bind_origin(directory, root)
    names = {f"r{r['repetition']}-w{r['dependency_width']}-a{r['audit_every']}-{r['engine']}"
             for r in native.schedule()}
    expected = {f'cases/{name}/trace.jsonl.gz' for name in names}
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob('*.gz')}
    require(actual == expected, 'native compressed case coverage differs')
    for rel in sorted(expected):
        trace_records(directory / rel)
    summary = load(directory / 'summary.json')
    for row in summary['rows']:
        for key in ('lifecycle_wall_ns', 'process_cpu_ns', 'records'):
            require(type(row[key]) is int and row[key] > 0, 'invalid native case timing/count')
        # The original verifier compares these fields with raw case.json.
        load(directory / 'cases' / row['case'] / 'case.json')
    # Independent replay derives the same ordered transactions from each prior
    # endpoint, checks every current/leased observation, maintenance and audit,
    # and decodes final saved segment/SQLite/pair bytes. It does not run writers.
    result = native.verify(directory)
    require(result == load(directory / 'verified-summary.json'), 'uploaded summary differs from raw replay')
    require((result['cases'], result['updates'], result['scheduled_audits'], result['held_endpoint_reads'],
             result['orderly_reopens'], result['maintenance_epochs'], result['mismatches']) ==
            (36, 288, 180, 144, 36, 72, 0), 'native evidence counts differ')
    require(load(directory / 'reservation.json')['historical_ledger_reset'] is False, 'historical budget reset')
    return result, {str((directory / rel).relative_to(root).as_posix()) for rel in expected}, origin


def verify_native_fanout(root):
    """Exact scientific admission only; no general gzip/archive allowance."""
    return verify_dataset(Path(root) / 'results/native-fanout', root)[1]


def render_table(result):
    lines = [r'\begin{table*}[t]', r'\centering', r'\normalsize', r'\setlength{\tabcolsep}{3pt}',
             r'\caption{Native matched lifecycle at 64 sources/384 facts. Each cell is the median of three eight-update lifecycle totals in milliseconds. Wall includes bootstrap and raw-trace instrumentation; phase medians are aggregated separately and do not sum to wall.}',
             r'\label{tab:native-fanout}',
             r'\begin{tabular}{@{}rrlrrrrrrr@{}}', r'\toprule',
             r'Width & Audit every & Engine & Wall & Transition & Persist. & Current & Held & Audit & Maint. \\',
             r'\midrule']
    labels = {'frontier': r'\system', 'sqlite_n': r'\sqlnorm', 'sqlite_r': r'\sqlroot'}
    for index, row in enumerate(result['medians']):
        if index and index % 3 == 0:
            lines.append(r'\addlinespace')
        phases = row['median_phase_ms']
        values = [row['median_wall_ms']] + [phases[p] for p in
                  ('transition', 'persistence', 'current_read', 'held_read', 'audit', 'maintenance')]
        lines.append(f"{row['width']} & {row['audit_interval']} & {labels[row['engine']]} & " +
                     ' & '.join(f'{value:.2f}' for value in values) + r' \\')
    lines += [r'\bottomrule', r'\end{tabular}',
              r'\par\smallskip\begin{minipage}{0.98\linewidth}\small',
              r'Current reads follow updates and maintenance; held reads decode the bootstrap through update four. Audit totals cover the scheduled interval-one/four checks and final acceptance; bootstrap validation is charged to bootstrap. Maintenance covers two matched logical epochs; collection, release and reopen are also included in wall and retained separately in raw phase totals. SQLite-N is the logical SQL null, not an independently exported interface. Ordinary host page cache is retained.',
              r'\end{minipage}', r'\end{table*}']
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', type=Path, default=native.ROOT / 'results/native-fanout')
    parser.add_argument('--tex', type=Path)
    parser.add_argument('--check-tex', type=Path)
    args = parser.parse_args()
    result, admitted, origin = verify_dataset(args.check)
    table = render_table(result)
    if args.check_tex:
        require(args.check_tex.read_text(encoding='utf-8') == table, 'native paper table differs from raw evidence')
    if args.tex:
        args.tex.write_text(table, encoding='utf-8', newline='\n')
    print(json.dumps(dict(status='REVIEWED_NATIVE_DATA_CONSISTENT', run_id=origin['run_id'],
                         cases=result['cases'], updates=result['updates'],
                         scheduled_audits=result['scheduled_audits'], held_reads=result['held_endpoint_reads'],
                         reopens=result['orderly_reopens'], mismatches=result['mismatches'],
                         admitted_traces=len(admitted), measured_sources_unchanged=True,
                         data_only=True), separators=(',', ':')))


if __name__ == '__main__':
    main()
