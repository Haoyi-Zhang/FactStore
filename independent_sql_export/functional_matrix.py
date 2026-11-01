"""Same frozen raw facts through independent SQL and unchanged normalized writer.

Producer data classes are used ONLY by the compatibility adapter. Neither the
SQL exporter, separate JSON consumer, nor frozen input oracle imports them.
No operation/lifecycle time is taken; sizes are actual file lengths.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

from export_consumer import load, require_endpoint
from sql_export import LogicalSQL, canonical, export_database, read_database, read_transaction


def footprint(root):
    files = {p.relative_to(root).as_posix(): p.stat().st_size for p in root.rglob('*') if p.is_file()}
    return dict(files=files, total_bytes=sum(files.values()))


def producer_types(raw):
    from frontierstore.model import Source, Fact, State
    return State(raw['epoch'], raw['schema'],
        {sid: Source(sid, r['generation'], r['payload']) for sid, r in raw['sources'].items()},
        {fid: Fact(fid, r['payload'], r['schema'], tuple((e['source'], e['generation']) for e in r['dependencies'])) for fid, r in raw['facts'].items()},
        {sid: tuple(members) for sid, members in raw['reverse'].items()})


def producer_delta(old, new):
    from frontierstore.model import Delta
    a, b = producer_types(old), producer_types(new)
    return Delta({k: b.sources[k] for k in b.sources if old['sources'].get(k) != new['sources'][k]},
        tuple(sorted(set(a.sources) - set(b.sources))),
        tuple(sorted(set(a.facts) - set(b.facts))),
        {k: b.facts[k] for k in b.facts if old['facts'].get(k) != new['facts'][k]},
        {k: b.reverse[k] for k in b.reverse if old['reverse'].get(k) != new['reverse'][k]},
        tuple(sorted(set(a.reverse) - set(b.reverse))))


def run(inputs, output, baseline):
    sys.path.insert(0, str(Path(baseline) / 'artifact'))
    from frontierstore.baselines import SQLiteNormalized
    records = []
    output = Path(output)
    with (output / 'matrix.jsonl').open('x', encoding='utf-8') as log:
        for case in inputs['histories']:
            directory = output / case['id']; directory.mkdir()
            sql_dir = directory / 'independent'; sql_dir.mkdir()
            producer_dir = directory / 'normalized'
            if producer_dir.exists():
                raise ValueError('refuse to let canonical create replace an existing directory')
            independent = LogicalSQL.create(sql_dir / 'logical.sqlite', case['initial'])
            native = SQLiteNormalized.create(producer_dir, producer_types(case['initial']))
            native.connection.execute('PRAGMA wal_autocheckpoint=0')
            native.connection.execute('PRAGMA busy_timeout=0')
            held = sqlite3.connect(independent.path.resolve().as_uri() + '?mode=ro', uri=True)
            held.execute('BEGIN')
            require_endpoint(read_transaction(held), case['initial'])
            previous = case['initial']
            try:
                for step, expected in [(0, case['initial'])] + [(e['step'], e['expected']) for e in case['events']]:
                    if step:
                        event = case['events'][step-1]
                        require_endpoint(independent.apply(event['transaction']), expected)
                        native.persist_precomputed(producer_types(expected), producer_delta(previous, expected))
                    a_path = sql_dir / f'epoch-{step:02d}.json'
                    b_path = producer_dir / f'epoch-{step:02d}.json'
                    a = export_database(independent.path, a_path)
                    b = export_database(native.db_path, b_path)
                    require_endpoint(a, expected); require_endpoint(b, expected)
                    require_endpoint(load(a_path), expected); require_endpoint(load(b_path), expected)
                    restored_path = directory / f'roundtrip-{step:02d}.sqlite'
                    restored = LogicalSQL.create(restored_path, load(a_path)); restored.close()
                    require_endpoint(read_database(restored_path), expected)
                    held_equal = None
                    if held is not None:
                        held_value = read_transaction(held)
                        require_endpoint(held_value, case['initial'])
                        held_equal = True
                    if step == 4:
                        # Real held-read export and actual release, not only an
                        # in-memory historical dictionary comparison.
                        from sql_export import publish_export
                        publish_export(directory / 'held-epoch01.json', read_transaction(held))
                        require_endpoint(load(directory / 'held-epoch01.json'), case['initial'])
                        held.rollback(); held.close(); held = None
                        checkpoint = list(independent.db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone())
                        native_checkpoint = list(native.connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone())
                    else:
                        checkpoint = native_checkpoint = None
                    row = dict(case=case['id'], step=step, epoch=expected['epoch'], equal=True,
                        expected_sha256=hashlib.sha256(canonical(expected)).hexdigest(),
                        independent_export_sha256=hashlib.sha256(a_path.read_bytes()).hexdigest(),
                        normalized_export_sha256=hashlib.sha256(b_path.read_bytes()).hexdigest(),
                        sql_snapshot_held_equal=held_equal, checkpoint=checkpoint,
                        normalized_checkpoint=native_checkpoint,
                        independent_files=footprint(sql_dir), normalized_files=footprint(producer_dir),
                        roundtrip_database_bytes=restored_path.stat().st_size)
                    records.append(row); log.write(json.dumps(row, sort_keys=True) + '\n'); log.flush()
                    previous = expected
                independent.close(); native.close()
                reopened = LogicalSQL(independent.path)
                try:
                    require_endpoint(read_database(reopened.path), previous)
                finally:
                    reopened.close()
                reopened_native = SQLiteNormalized(producer_dir)
                try:
                    require_endpoint(read_database(reopened_native.db_path), previous)
                finally:
                    reopened_native.close()
            finally:
                if held is not None:
                    held.close()
                try:
                    independent.close()
                except sqlite3.ProgrammingError:
                    pass
                try:
                    native.close()
                except sqlite3.ProgrammingError:
                    pass
    return dict(status='PASS', histories=len(inputs['histories']), endpoint_pairs=len(records),
        independently_decoded_exports=2*len(records), roundtrip_databases=len(records),
        held_snapshot_checks=sum(r['sql_snapshot_held_equal'] is True for r in records),
        physical_held_exports=len(inputs['histories']), orderly_reopens=2*len(inputs['histories']),
        all_equal=all(r['equal'] for r in records), final_files=footprint(output),
        scientific_timings_collected=False, independent_program_population=False,
        comparison='functional same frozen endpoints, not equal-work performance: SQL computes transitions; normalized adapter receives precomputed endpoints')
