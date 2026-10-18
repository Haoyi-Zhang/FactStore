"""Owned SQLite-API read-lease, archive reclamation and lifecycle control.

This is not FrontierStore/SQLite-R publication validation. It uses the unchanged
normalized delta writer after a SQLite-API bootstrap, never stubs POSIX locking
or directory synchronization, and never removes a live database's WAL/SHM files.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import platform
import sqlite3
import sys
import time

from frontierstore.audit_export import parse_audit_export
from frontierstore.baselines import SQLiteNormalized, _insert_full_state, _read_sqlite_state, _sqlite_schema
from frontierstore.model import Fact, Source, State, build_reverse, canonical_state_bytes, require_closed, states_equal
from frontierstore.model import apply_transaction
from frontierstore.workload import make_synthetic_state, make_update

CANDIDATE=Path(__file__).resolve().parents[2]
PRIVATE=CANDIDATE/'artifact'/'results'/'lifecycle-replay'


def owned(path):
    path=Path(path).resolve()
    if not any(path!=root.resolve() and path.is_relative_to(root.resolve())
               for root in (CANDIDATE,PRIVATE)):
        raise ValueError('fresh output must be under owned P021 roots')
    return path


def read_transaction(db):
    """Materialize four relations in a transaction held by this owned reader."""
    if not db.in_transaction:
        raise RuntimeError('explicit read transaction required')
    meta=dict(db.execute('SELECT key,value FROM meta ORDER BY key').fetchall())
    sources={i:Source(i,g,p) for i,g,p in db.execute('SELECT id,generation,payload FROM sources ORDER BY id').fetchall()}
    dependencies={}
    for f,i,g in db.execute('SELECT fact_id,source_id,generation FROM dependencies ORDER BY fact_id,source_id').fetchall():
        dependencies.setdefault(f,[]).append((i,g))
    facts={f:Fact(f,p,s,tuple(dependencies.get(f,[]))) for f,p,s in
           db.execute('SELECT id,payload,schema FROM facts ORDER BY id').fetchall()}
    value=State(meta['epoch'],meta['schema'],sources,facts,build_reverse(sources,facts))
    require_closed(value)
    return value


def footprint(root):
    files={file.relative_to(root).as_posix():file.stat().st_size
           for file in root.rglob('*') if file.is_file()}
    database=sum(size for name,size in files.items() if not name.startswith('history/'))
    archive=sum(size for name,size in files.items() if name.startswith('history/'))
    return dict(files=files,db_wal_shm_bytes=database,archive_bytes=archive,total_bytes=sum(files.values()))


def checkpoint(db,root,mode,event):
    if mode not in {'PASSIVE','TRUNCATE'}:
        raise ValueError(mode)
    before=footprint(root)
    start=time.perf_counter_ns()
    result=list(db.execute(f'PRAGMA wal_checkpoint({mode})').fetchone())
    elapsed=time.perf_counter_ns()-start
    wal=root/'normalized.sqlite-wal'
    complete=(result[0]==0 and result[1]==result[2] and result[1]>=0)
    truncated=(mode=='TRUNCATE' and result==[0,0,0] and (not wal.exists() or wal.stat().st_size==0))
    return dict(event=event,mode=mode,result=result,elapsed_ns=elapsed,
                all_frames_checkpointed=complete,wal_truncated=truncated,
                before=before,after=footprint(root))


def archive_endpoint(db,directory,state):
    image=directory/f'image-{state.epoch:08d}.sqlite'
    export=directory/f'audit-{state.epoch:08d}.json'
    with closing(sqlite3.connect(image)) as destination:
        db.backup(destination)
        if destination.execute('PRAGMA journal_mode=DELETE').fetchone()[0]!='delete':
            raise RuntimeError('archive requires DELETE journal mode')
    # Content synchronization uses owned writable handles on this host. This is
    # not directory-entry synchronization or atomic pair/root publication.
    with image.open('r+b') as handle:
        os.fsync(handle.fileno())
    with export.open('wb') as handle:
        handle.write(canonical_state_bytes(state))
        handle.flush()
        os.fsync(handle.fileno())
    decoded=_read_sqlite_state(image)
    parsed=parse_audit_export(export)
    if not states_equal(decoded,state) or parsed!=state.as_dict():
        raise AssertionError('archived database/export endpoint mismatch')
    return (image,export)


def reclaim(directory,pairs,keep):
    start=time.perf_counter_ns()
    removed=[]
    for epoch in sorted(set(pairs)-set(keep)):
        for path in pairs.pop(epoch):
            if path.resolve().parent!=directory.resolve():
                raise RuntimeError('archive path outside owned history directory')
            path.unlink()
            removed.append(path.name)
    return dict(removed=removed,remaining_epochs=sorted(pairs),elapsed_ns=time.perf_counter_ns()-start)


def run_case(path,sources,updates,policy):
    if not __debug__:
        raise RuntimeError('assertions required')
    if (type(sources)!=int or not 2<=sources<=500 or
            type(updates)!=int or not 4<=updates<=12 or policy not in {'all','leases'}):
        raise ValueError('bounded control requires 2..500 sources, 4..12 updates, all/leases policy')
    path=owned(path)
    path.mkdir(parents=True,exist_ok=False)
    history=path/'history'
    history.mkdir()
    records=[]
    checkpoints=[]
    stage_ns=dict(transition=0,persistence=0,reads=0,archive=0,reclamation=0,checkpoint=0)
    started=time.perf_counter_ns()
    cpu_start=time.process_time_ns()
    state=make_synthetic_state(sources)
    bootstrap=state.clone()
    db=sqlite3.connect(path/'normalized.sqlite',timeout=0)
    reader=None
    try:
        if db.execute('PRAGMA journal_mode=WAL').fetchone()[0]!='wal':
            raise RuntimeError('active database requires WAL journal mode')
        db.execute('PRAGMA synchronous=FULL')
        db.execute('PRAGMA wal_autocheckpoint=0')
        db.execute('PRAGMA busy_timeout=0')
        _sqlite_schema(db)
        db.execute('BEGIN IMMEDIATE')
        _insert_full_state(db,state)
        db.commit()
        checkpoints.append(checkpoint(db,path,'TRUNCATE','bootstrap'))
        assert checkpoints[-1]['wal_truncated']
        store=SQLiteNormalized.__new__(SQLiteNormalized)
        store.path,store.db_path,store.connection,store._state=path,path/'normalized.sqlite',db,state
        settings={key:db.execute('PRAGMA '+key).fetchone()[0] for key in
                  ('journal_mode','synchronous','wal_autocheckpoint','busy_timeout','page_size','foreign_keys')}
        settings['archive_journal_mode']='DELETE'
        assert settings['synchronous']==2 and settings['wal_autocheckpoint']==0
        reader=sqlite3.connect(store.db_path.resolve().as_uri()+'?mode=ro',uri=True,timeout=0)
        reader.execute('PRAGMA query_only=ON')
        reader.execute('BEGIN')
        if not states_equal(read_transaction(reader),bootstrap):
            raise AssertionError('initial reader endpoint mismatch')
        start=time.perf_counter_ns()
        pairs={state.epoch:archive_endpoint(db,history,state)}
        stage_ns['archive']+=time.perf_counter_ns()-start
        peaks=[footprint(path)['total_bytes']]
        records.append(dict(event='bootstrap',input=bootstrap.as_dict(),settings=settings,footprint=footprint(path)))
        release_at=updates//2
        for index in range(1,updates+1):
            transaction=make_update(state,batch=min(8,sources),seed=100+index)
            start=time.perf_counter_ns()
            state,delta=apply_transaction(state,transaction)
            stage_ns['transition']+=time.perf_counter_ns()-start
            start=time.perf_counter_ns()
            store.persist_precomputed(state,delta)
            stage_ns['persistence']+=time.perf_counter_ns()-start
            start=time.perf_counter_ns()
            current=store.durable_state()
            historical=read_transaction(reader) if reader is not None else None
            assert states_equal(current,state)
            assert historical is None or states_equal(historical,bootstrap)
            stage_ns['reads']+=time.perf_counter_ns()-start
            start=time.perf_counter_ns()
            pairs[state.epoch]=archive_endpoint(db,history,state)
            stage_ns['archive']+=time.perf_counter_ns()-start
            peaks.append(footprint(path)['total_bytes'])
            keep=set(pairs) if policy=='all' else {state.epoch}|({bootstrap.epoch} if reader is not None else set())
            collection=reclaim(history,pairs,keep)
            stage_ns['reclamation']+=collection['elapsed_ns']
            assert state.epoch in pairs and (reader is None or bootstrap.epoch in pairs)
            records.append(dict(event='update',index=index,
                transaction=dict(source_changes=dict(transaction.source_changes),
                    fact_replacements=[dict(spec) for spec in transaction.fact_replacements],
                    fact_retractions=list(transaction.fact_retractions),new_schema=transaction.new_schema),
                current=current.as_dict(),historical=None if historical is None else historical.as_dict(),
                collection=collection,footprint=footprint(path)))
            if index==release_at:
                for mode in ('PASSIVE','TRUNCATE'):
                    check=checkpoint(db,path,mode,'held-reader')
                    checkpoints.append(check)
                    assert not check['all_frames_checkpointed'] and not check['wal_truncated']
                    if mode=='PASSIVE':
                        assert check['result'][0]==0  # not a completion indicator
                    else:
                        assert check['result'][0]==1
                assert states_equal(read_transaction(reader),bootstrap)
                reader.close()
                reader=None
                collection=reclaim(history,pairs,set(pairs) if policy=='all' else {state.epoch})
                stage_ns['reclamation']+=collection['elapsed_ns']
                check=checkpoint(db,path,'TRUNCATE','reader-released')
                checkpoints.append(check)
                assert check['wal_truncated']
                assert states_equal(store.durable_state(),state)
                records.append(dict(event='release',index=index,collection=collection,footprint=footprint(path)))
            elif release_at<index<updates and index%4==0:
                check=checkpoint(db,path,'TRUNCATE','reader-gap')
                checkpoints.append(check)
                assert check['wal_truncated']
        checkpoints.append(checkpoint(db,path,'TRUNCATE','final'))
        assert checkpoints[-1]['wal_truncated']
        stage_ns['checkpoint']=sum(row['elapsed_ns'] for row in checkpoints)
        before_close=footprint(path)
        store.close()
        db=None
        reopened=_read_sqlite_state(path/'normalized.sqlite')
        assert states_equal(reopened,state)
        final=footprint(path)
        assert set(pairs)==(set(range(1,updates+2)) if policy=='all' else {state.epoch})
        records.append(dict(event='closed-reopened',current=reopened.as_dict(),footprint=final))
        elapsed=time.perf_counter_ns()-started
        cpu=time.process_time_ns()-cpu_start
        result=dict(sources=sources,facts=len(state.facts),updates=updates,policy=policy,
                    release_at=release_at,settings=settings,endpoint_equal=True,
                    held_endpoint_equal=True,retained_epochs=sorted(pairs),checkpoints=checkpoints,
                    lifecycle_elapsed_ns=elapsed,process_cpu_ns=cpu,stage_ns=stage_ns,
                    peak_sampled_bytes=max(peaks+[row['before']['total_bytes'] for row in checkpoints]),
                    before_close=before_close,final=final,records=records,raw_record_count=len(records),
                    scope='SQLite API including bootstrap, transitions, commit, reads, content-flushed history, actual archive collection, checkpoints, close/reopen. Excludes raw-log serialization, POSIX directory sync, atomic export/root publication, device traffic and other engines.')
        return result
    finally:
        if reader is not None:
            reader.close()
        if db is not None:
            db.close()


def main():
    if not __debug__:
        raise RuntimeError('assertions required')
    parser=argparse.ArgumentParser()
    parser.add_argument('--authorize-new-study',action='store_true')
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    if not args.authorize_new_study:
        raise ValueError('new bounded study must be explicitly authorized')
    out=owned(args.out)
    out.mkdir(parents=True,exist_ok=False)
    reservation=dict(observed_utc=datetime.now(timezone.utc).isoformat(),
        cases=6,source_counts=[8,64,500],updates=12,policies=['all','leases'],
        wall_envelope_seconds=180,refund_on_failure=False,
        scope='separate new finite SQLite study; no historical resource-ledger reset; envelope is checked between cases, not a hard Windows OS CPU/memory limit')
    (out/'reservation.json').write_text(json.dumps(reservation,indent=2)+'\n',encoding='utf-8')
    environment=dict(python=sys.version,sqlite=sqlite3.sqlite_version,platform=platform.platform(),
        sqlite_source_id=sqlite3.connect(':memory:').execute('SELECT sqlite_source_id()').fetchone()[0],
        clocks={name:vars(time.get_clock_info(name)) for name in ('time','monotonic','perf_counter','process_time')})
    started=time.perf_counter()
    summaries=[]
    for sources in (8,64,500):
        for policy in ('all','leases'):
            if time.perf_counter()-started>180:
                raise TimeoutError('study envelope exhausted; reservation retained')
            name=f'sources-{sources}-{policy}'
            row=run_case(out/name,sources,12,policy)
            with gzip.open(out/(name+'.json.gz'),'wt',encoding='utf-8') as stream:
                json.dump(row,stream,sort_keys=True)
            summaries.append({key:value for key,value in row.items() if key!='records'})
            print(json.dumps(dict(case=name,retained=len(row['retained_epochs']),
                final_bytes=row['final']['total_bytes'],lifecycle_ns=row['lifecycle_elapsed_ns']),sort_keys=True),flush=True)
    result=dict(environment=environment,rows=summaries,cases=6,updates=72,
        raw_record_count=sum(row['raw_record_count'] for row in summaries),fixed_policy_order=True,
        interpretation='one repetition per size/policy; descriptive API lifecycle, not a common-native-persistence three-engine ranking or novelty claim')
    (out/'summary.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',encoding='utf-8')


if __name__=='__main__':
    main()
