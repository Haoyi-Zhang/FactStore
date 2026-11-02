"""Strict, unmeasured receivers for retained paper inputs.

Only saved JSON, gzip/ZIP members, source hashes and arithmetic are consumed.
No store is opened, workload bootstrapped, source AST analyzed, or study run.
Byte binding is not a new execution or an authenticity/signature claim.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import random
import statistics
import zipfile

from experiments.compressed_records import finite_float, reject_constant, unique_object

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 32 * 1024 * 1024
ENGINES = ('frontier', 'sqlite_n', 'sqlite_r')
CASES = {(r, w, a) for r in range(3) for w in (2, 8) for a in (1, 4)}
API_PHASES = {'transition', 'persistence', 'reads', 'archive', 'audit', 'reclamation', 'checkpoint'}
NATIVE_PHASES = ('bootstrap', 'transition', 'persistence', 'current_read', 'held_read',
                 'audit', 'maintenance', 'collection', 'release', 'reopen')
API_SOURCES = {'experiments/fanout_cadence.py', 'experiments/history_lifecycle.py',
               'frontierstore/baselines.py', 'frontierstore/model.py',
               'frontierstore/workload.py', 'frontierstore/audit_export.py'}
IMPORT_SOURCES = {'experiments/import_lifecycle.py', 'frontierstore/import_inventory.py',
                  'frontierstore/model.py', 'frontierstore/store.py',
                  'frontierstore/baselines.py', 'frontierstore/checker.py',
                  'frontierstore/audit_export.py', 'experiments/native_fanout_cadence.py',
                  'data/import-releases/sources.zip', 'data/import-releases/sources.json'}
IMPORT_ARCHIVE_SHA256 = 'b20c7894dcd6570bd673e477cd5484af40ae145e2ed54d32173c52cd7cfa3725'


def require(condition, message):
    if not condition:
        raise ValueError('paper evidence: ' + message)


def decode(raw):
    require(len(raw) <= LIMIT, 'JSON exceeds byte bound')
    return json.loads(raw, object_pairs_hook=unique_object,
                      parse_constant=reject_constant, parse_float=finite_float)


def load(path):
    require(path.stat().st_size <= LIMIT, 'JSON exceeds byte bound')
    value = decode(path.read_bytes())
    require(type(value) is dict, 'JSON object required')
    return value


def encoded(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':'))


def equal(left, right, message):
    require(encoded(left) == encoded(right), message)


def integer(value, label, minimum=0):
    require(type(value) is int and minimum <= value <= (1 << 63) - 1,
            'strict integer domain: ' + label)


def counts(value, expected):
    for key, number in expected.items():
        integer(value.get(key), key)
        require(value[key] == number, 'count differs: ' + key)


def phases(value, names):
    require(type(value) is dict and set(value) == set(names), 'exact phase keys required')
    for key, cost in value.items():
        integer(cost, key)


def footprint(value):
    require(type(value) is dict and type(value.get('files')) is dict, 'saved footprint object required')
    for name, size in value['files'].items():
        require(type(name) is str, 'footprint filename type')
        integer(size, 'file length')
    for key in ('total_bytes', 'db_wal_shm_bytes', 'archive_bytes'):
        integer(value.get(key), key)
    archive = sum(v for k, v in value['files'].items() if k.startswith('history/'))
    require(value['total_bytes'] == sum(value['files'].values()) and
            value['archive_bytes'] == archive and
            value['db_wal_shm_bytes'] + archive == value['total_bytes'], 'footprint sums differ')


def source_binding(root, before, expected, after=None):
    require(type(before) is dict and set(before) == set(expected), 'source identity set differs')
    if after is not None:
        equal(before, after, 'before/after source identities differ')
    for name, digest in before.items():
        require(type(digest) is str and len(digest) == 64, 'invalid source digest')
        path = (root / name).resolve()
        require(path.is_relative_to(root.resolve()) and path.is_file(), 'source leaves artifact')
        require(hashlib.sha256(path.read_bytes()).hexdigest() == digest,
                'frozen source binding differs: ' + name)


def compressed(path, jsonl=False):
    with gzip.open(path, 'rb') as stream:
        raw = stream.read(LIMIT + 1)
    require(len(raw) <= LIMIT, 'decoded gzip exceeds byte bound')
    return [decode(line) for line in raw.splitlines()] if jsonl else decode(raw)


def api_identity(row):
    require(type(row) is dict, 'case object required')
    keys = ('repetition', 'dependency_width', 'audit_every')
    for key in keys:
        integer(row.get(key), key)
    identity = tuple(row[key] for key in keys)
    require(identity in CASES, 'unknown case identity')
    return identity


def validate_api(summary, raw_cases):
    """Validate complete saved API evidence without generating any workload."""
    counts(summary, dict(cases=12, current_updates=96, held_reads=48, reopens=12))
    require(summary.get('source_unchanged') is True, 'source stability flag')
    rows = summary.get('rows')
    require(type(rows) is list and len(rows) == 12, 'twelve API rows required')
    index = {api_identity(row): row for row in rows}
    require(len(index) == len(rows) and set(index) == CASES, 'duplicate/missing API cases')
    require(set(raw_cases) == CASES, 'raw API case coverage differs')
    schedule = []
    for repetition in range(3):
        configs = [(w, a) for w in (2, 8) for a in (1, 4)]
        random.Random(21024 + repetition).shuffle(configs)
        schedule.extend(f'r{repetition}-w{w}-a{a}' for w, a in configs)
    equal(summary.get('schedule'), schedule, 'API schedule differs')
    for identity in sorted(CASES):
        raw = raw_cases[identity]
        require(api_identity(raw) == identity, 'raw identity differs')
        _, width, cadence = identity
        counts(raw, dict(sources=64, facts=384, updates=8, release_at=4,
                         raw_record_count=11, audit_checks=8 // cadence))
        require(raw.get('endpoint_equal') is True and raw.get('held_endpoint_equal') is True,
                'exact API endpoint flags required')
        require(raw.get('policy') == 'leases', 'API lease policy differs')
        equal(raw.get('retained_epochs'), [9], 'API retention differs')
        phases(raw.get('stage_ns'), API_PHASES)
        for key in ('lifecycle_elapsed_ns', 'process_cpu_ns', 'peak_sampled_bytes'):
            integer(raw.get(key), key, 1)
        footprint(raw.get('before_close'))
        footprint(raw.get('final'))
        events = raw.get('records')
        require(type(events) is list and len(events) == 11 and
                all(type(e) is dict for e in events), 'API event coverage differs')
        equal([e.get('event') for e in events], ['bootstrap'] + ['update'] * 4 +
              ['release'] + ['update'] * 4 + ['closed-reopened'], 'API event order differs')
        updates = [e for e in events if e['event'] == 'update']
        for number, event in enumerate(updates, 1):
            counts(event, dict(index=number, dependency_edges=384 * width))
            for key in ('fact_replacements', 'invalidated_facts'):
                integer(event.get(key), key)
            require(event.get('scheduled_audit') is (number % cadence == 0), 'API audit schedule differs')
            equal(event.get('historical'), events[0]['input'] if number <= 4 else None,
                  'API held endpoint differs')
        equal(events[-1].get('current'), updates[-1].get('current'), 'API reopen endpoint differs')
        checkpoints = raw.get('checkpoints')
        require(type(checkpoints) is list and len(checkpoints) == 5, 'checkpoint coverage differs')
        for entry in checkpoints:
            integer(entry.get('elapsed_ns'), 'checkpoint elapsed_ns')
        require(raw['stage_ns']['checkpoint'] == sum(e['elapsed_ns'] for e in checkpoints),
                'checkpoint phase sum differs')
        reduced = {k: v for k, v in raw.items() if k not in ('records', 'checkpoints')}
        reduced['mean_replaced_facts'] = statistics.mean(e['fact_replacements'] for e in updates)
        reduced['total_archive_files_removed'] = sum(len(e.get('collection', {}).get('removed', []))
                                                    for e in events)
        require(reduced['total_archive_files_removed'] == 16, 'API reclamation count differs')
        equal(reduced, index[identity], 'raw API row/summary differs')
    aggregates = []
    for width in (2, 8):
        for cadence in (1, 4):
            group = [index[r, width, cadence] for r in range(3)]
            aggregates.append(dict(dependency_width=width, audit_every=cadence, repetitions=3,
                mean_replaced_facts=group[0]['mean_replaced_facts'], audit_checks=group[0]['audit_checks'],
                median_lifecycle_ms=statistics.median(r['lifecycle_elapsed_ns'] / 1e6 for r in group),
                median_stage_ms={k: statistics.median(r['stage_ns'][k] / 1e6 for r in group)
                                 for k in API_PHASES}, final_bytes=group[0]['final']['total_bytes']))
    equal(summary.get('aggregates'), aggregates, 'complete raw-derived API aggregates differ')
    return summary


def admit_api(directory=None, root=ROOT):
    directory = Path(directory) if directory is not None else root / 'results/sqlite-fanout'
    summary = load(directory / 'summary.json')
    # Reject incomplete summary coverage before opening raw files.
    counts(summary, dict(cases=12, current_updates=96, held_reads=48, reopens=12))
    require(type(summary.get('rows')) is list and len(summary['rows']) == 12, 'twelve API rows required')
    source_binding(root, load(directory / 'source-before.json'), API_SOURCES,
                   load(directory / 'source-after.json'))
    boundary = load(directory / 'native-boundary.json')
    require(boundary.get('substituted') is False and
            boundary.get('native_three_representation_costs_measured') is False, 'API/native boundary differs')
    names = {key: f'r{key[0]}-w{key[1]}-a{key[2]}.json.gz' for key in CASES}
    require({p.name for p in directory.glob('*.gz')} == set(names.values()), 'raw gzip coverage differs')
    return validate_api(summary, {key: compressed(directory / name) for key, name in names.items()})


def validate_native(summary, raw_cases, traces):
    require(summary.get('status') == 'NATIVE_CASES_COMPLETED' and
            summary.get('native_three_engine_costs_measured') is True and
            summary.get('source_unchanged') is True, 'native completion flags differ')
    expected = {(r, w, a, e) for r, w, a in CASES for e in ENGINES}
    rows = summary.get('rows')
    require(type(rows) is list and len(rows) == 36, '36 native rows required')
    index = {}
    for row in rows:
        key = (*api_identity(row), row.get('engine'))
        require(type(row.get('engine')) is str and key in expected and key not in index,
                'duplicate/unknown native case')
        counts(row, dict(sources=64, updates=8, records=10))
        integer(row.get('position'), 'position')
        require(row['position'] < 3 and row.get('accepted') is True, 'native position/accepted differs')
        phases(row.get('stage_ns'), NATIVE_PHASES)
        for field in ('lifecycle_wall_ns', 'process_cpu_ns'):
            integer(row.get(field), field, 1)
        require(row['lifecycle_wall_ns'] >= sum(row['stage_ns'].values()) > 0, 'native phase/wall accounting')
        name = f'r{key[0]}-w{key[1]}-a{key[2]}-{key[3]}'
        require(row.get('case') == name, 'native case filename differs')
        require(name in raw_cases and name in traces, 'missing raw native case')
        equal({k: v for k, v in row.items() if k != 'case'}, raw_cases[name], 'raw native row differs')
        events = traces[name]
        require(type(events) is list and len(events) == 10, 'native trace coverage differs')
        equal([e.get('event') for e in events], ['bootstrap'] + ['update'] * 8 + ['reopen'],
              'native event order differs')
        for event in events:
            phases(event.get('phase_ns'), NATIVE_PHASES)
        equal({phase: sum(e['phase_ns'][phase] for e in events) for phase in NATIVE_PHASES},
              row['stage_ns'], 'native raw phase sums differ')
        for number, event in enumerate(events[1:-1], 1):
            counts(event, dict(index=number))
            require(event.get('scheduled_audit') is (number % key[2] == 0), 'native audit schedule differs')
            equal(event['current_after_update'], event['update_expected'], 'native update endpoint differs')
        equal(events[-1]['current'], events[-1]['expected'], 'native reopen endpoint differs')
        index[key] = row
    require(set(index) == expected and set(raw_cases) == set(traces) == {r['case'] for r in rows},
            'native full matrix/raw coverage differs')
    return summary


def admit_native(directory=None, root=ROOT):
    directory = Path(directory) if directory is not None else root / 'results/native-fanout'
    summary = load(directory / 'summary.json')
    rows = summary.get('rows')
    require(type(rows) is list and len(rows) == 36, '36 native rows required')
    # Existing unmeasured binder checks original 169 members, nine measured
    # current/preimage sources, provenance and exact dataset membership. It only
    # hashes/reads bytes: do not call verify_dataset(), native.verify() or writers.
    from experiments.native_fanout_results import bind_origin
    bind_origin(directory, root)
    names = {f'r{r}-w{w}-a{a}-{e}' for r, w, a in CASES for e in ENGINES}
    raw = {name: load(directory / 'cases' / name / 'case.json') for name in names}
    traces = {name: compressed(directory / 'cases' / name / 'trace.jsonl.gz', jsonl=True) for name in names}
    validate_native(summary, raw, traces)
    equal(load(directory / 'source-before.json'), load(directory / 'source-after.json'),
          'native before/after source identities differ')
    return summary


def validate_import(summary, engine_rows, traces, registration):
    require(summary.get('status') == 'PASS', 'import completion status differs')
    counts(summary, dict(modules=8, changed_modules=4, engines=3, native_transitions=12,
                         released_endpoint_pairs=1))
    for key in ('performance_claim', 'natural_intermediate_commit_history',
                'power_loss_or_cross_process_pins_tested'):
        require(summary.get(key) is False, 'import inference boundary differs: ' + key)
    require(registration.get('modules') == 8 and type(registration['modules']) is int,
            'registered module count differs')
    equal(registration.get('engines'), list(ENGINES), 'registered engines differ')
    changed = registration.get('changed_modules')
    require(type(changed) is list and len(changed) == 4 and changed == sorted(set(changed)) and
            all(type(name) is str for name in changed), 'registered module-change coverage differs')
    rows = summary.get('rows')
    require(type(rows) is list and len(rows) == 3, 'three import engine rows required')
    require([r.get('engine') for r in rows] == list(ENGINES) and
            set(engine_rows) == set(traces) == set(ENGINES), 'import engine coverage differs')
    totals = []
    for row in rows:
        engine = row['engine']
        counts(row, dict(updates=4))
        require(row.get('all_endpoints_equal') is True and row.get('orderly_reopen_equal') is True,
                'exact import success flags required')
        equal(row, engine_rows[engine], 'archived import engine summary differs')
        events = traces[engine]
        require(type(events) is list and len(events) == 4, 'four import trace updates required')
        equal([e.get('module') for e in events], changed, 'import module-update sequence differs')
        result = {key: 0 for key in ('incremental_source_reads', 'full_source_reads',
                                    'incremental_source_bytes', 'full_source_bytes')}
        for event in events:
            require(all(event.get(k) is True for k in
                        ('current_equal', 'held_initial_equal', 'full_recompute_equal')),
                    'import trace endpoint flags differ')
            for kind in ('incremental', 'full'):
                observed = event[kind + '_read_events']
                require(type(observed) is list, 'import read-event list required')
                for read in observed:
                    require(type(read) is dict and type(read.get('id')) is str, 'read-event identity')
                    integer(read.get('bytes'), 'read bytes')
                # Fixed identifier documented by the byte-bound producer. Do
                # not import or run the source-analysis adapter to recount it.
                registry = 'py.registry'
                aggregate = dict(source_reads=sum(x['id'] != registry for x in observed),
                                 source_bytes=sum(x['bytes'] for x in observed if x['id'] != registry),
                                 registry_reads=sum(x['id'] == registry for x in observed))
                equal(event[kind], aggregate, 'captured import read counts differ')
                result[kind + '_source_reads'] += aggregate['source_reads']
                result[kind + '_source_bytes'] += aggregate['source_bytes']
        for key, value in result.items():
            integer(row.get(key), key)
            require(row[key] == value, 'import trace-derived work differs: ' + key)
        totals.append(result)
    require(len({encoded(row) for row in totals}) == 1, 'unequal import work across engines')
    return summary


def admit_import(directory=None, root=ROOT):
    directory = Path(directory) if directory is not None else root / 'results/import-lifecycle'
    summary = load(directory / 'summary.json')
    registration = load(directory / 'registration.json')
    source_binding(root, registration.get('source_bindings'), IMPORT_SOURCES)
    archive = directory / 'native-evidence.zip'
    require(hashlib.sha256(archive.read_bytes()).hexdigest() == IMPORT_ARCHIVE_SHA256,
            'original import evidence archive differs')
    manifest = load(directory / 'native-evidence.json')
    with zipfile.ZipFile(archive) as zipped:
        names = zipped.namelist()
        require(len(names) == len(set(names)) == len(manifest) and set(names) == set(manifest),
                'import archive manifest coverage differs')
        require(sum(item.file_size for item in zipped.infolist()) <= 128 * 1024 * 1024,
                'import expanded archive bound')
        for name, identity in manifest.items():
            integer(identity.get('bytes'), 'archive member bytes')
            require(identity['bytes'] <= LIMIT and zipped.getinfo(name).file_size == identity['bytes'],
                    'import member length differs')
            raw = zipped.read(name)
            require(hashlib.sha256(raw).hexdigest() == identity['sha256'], 'import member hash differs')
        engine_rows = {e: decode(zipped.read(e + '/summary.json')) for e in ENGINES}
        traces = {e: decode(zipped.read(e + '/trace.json')) for e in ENGINES}
    return validate_import(summary, engine_rows, traces, registration)
