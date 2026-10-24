"""Fresh bounded Linux lifecycle comparison, never a Windows persistence surrogate.

The common observation contract is quiescent post-return current/leased endpoint
equality under native persistence and matched logical maintenance epochs. It does
not give SQLite-N an independent export or the two rooted engines' crash-atomic
cross-interface selector. Historical cohorts and resource ledgers are read only.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import itertools
import json
import os
from pathlib import Path
import platform
import random
import re
import sqlite3
import statistics
import subprocess
import sys
import time

from frontierstore import baselines
from frontierstore.audit_export import parse_audit_export
from frontierstore.checker import check_selected
from frontierstore.model import Transaction, apply_transaction
from frontierstore.workload import make_synthetic_state, make_update
from experiments.history_lifecycle import read_transaction

ROOT=Path(__file__).resolve().parents[1]
ENGINES=('frontier','sqlite_n','sqlite_r')
SOURCES=('experiments/native_fanout_cadence.py','experiments/history_lifecycle.py',
    'frontierstore/store.py','frontierstore/baselines.py','frontierstore/model.py',
    'frontierstore/checker.py','frontierstore/audit_export.py','frontierstore/workload.py',
    'resource-accounting.json')
PHASES=('bootstrap','transition','persistence','current_read','held_read','audit',
        'maintenance','collection','release','reopen')


def require(value,message):
    if not value:
        raise ValueError(message)


def bindings():
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES}


def write_json(path,value):
    path.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n',encoding='utf-8')


def reserve(path):
    path=Path(path).resolve()
    require(not path.is_relative_to(ROOT.parent),'fresh native output must be outside delivered project')
    path.mkdir(parents=True,exist_ok=False)
    return path


def environment(out):
    def text_file(name):
        try:
            return Path(name).read_text(encoding='utf-8').strip()
        except OSError:
            return None
    cpu=text_file('/proc/cpuinfo')
    cpu_model=next((line.split(':',1)[1].strip() for line in (cpu or '').splitlines()
                    if line.startswith('model name')),None)
    with closing(sqlite3.connect(':memory:')) as db:
        source_id=db.execute('SELECT sqlite_source_id()').fetchone()[0]
        options=[row[0] for row in db.execute('PRAGMA compile_options')]
    filesystem=None
    if sys.platform.startswith('linux'):
        try:
            result=subprocess.run(['findmnt','-n','-o','FSTYPE,TARGET','--target',str(out)],
                capture_output=True,text=True,timeout=5,check=True)
            filesystem=result.stdout.strip()
        except (OSError,subprocess.SubprocessError):
            pass
    limits=None
    if importlib.util.find_spec('resource') is not None:
        import resource
        limits={name:list(resource.getrlimit(getattr(resource,name))) for name in ('RLIMIT_AS','RLIMIT_CPU')}
    return dict(observed_utc=datetime.now(timezone.utc).isoformat(),python=sys.version,
        python_executable=sys.executable,platform=platform.platform(),cpu_model=cpu_model,
        affinity=sorted(os.sched_getaffinity(0)) if hasattr(os,'sched_getaffinity') else None,
        sqlite=sqlite3.sqlite_version,sqlite_source_id=source_id,sqlite_compile_options=options,
        os_release=text_file('/etc/os-release'),memory=text_file('/proc/meminfo'),
        cgroup={name:text_file('/sys/fs/cgroup/'+name) for name in
                ('cpu.max','memory.max','cpuset.cpus.effective')},limits=limits,filesystem=filesystem,
        storage_medium='unknown; filesystem identity is not device-traffic measurement',
        filesystem_mount_options='not captured; unknown',virtualization='unknown',cpu_count=os.cpu_count(),
        clocks={name:vars(time.get_clock_info(name)) for name in ('perf_counter','process_time')},
        linux_native_available=sys.platform.startswith('linux') and os.name=='posix'
            and importlib.util.find_spec('fcntl') is not None,
        substitution_used=False,power_loss_tested=False)


def native_probe(out):
    import fcntl
    directory=out/'native-probe'
    directory.mkdir()
    temporary=directory/'pending'
    with temporary.open('wb') as stream:
        stream.write(b'owned native persistence boundary\n')
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary,directory/'committed')
    baselines._fsync_directory(directory)
    with (directory/'LOCK').open('a+b') as stream:
        fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
        fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
    return dict(file_fsync=True,replace=True,directory_fsync=True,flock=True,
                substitution_used=False,power_loss_test=False)


def schedule(engines=ENGINES):
    require(len(set(engines))==len(engines) and set(engines)<=set(ENGINES) and bool(engines),'invalid engines')
    rows=[]
    for repetition in range(3):
        configs=[(w,a) for w in (2,8) for a in (1,4)]
        random.Random(21024+repetition).shuffle(configs)
        for width,cadence in configs:
            base=ENGINES if cadence==1 else (ENGINES[0],ENGINES[2],ENGINES[1])
            order=base[repetition:]+base[:repetition]
            for position,engine in enumerate(order):
                if engine in engines:
                    rows.append(dict(repetition=repetition,dependency_width=width,
                        audit_every=cadence,engine=engine,position=position))
    return rows


def transaction_dict(value):
    # Transaction captures dependency sequences as tuples. Project that exact
    # ordered content to JSON arrays before comparing it with a decoded trace.
    # Do not drop fields, sort dependencies or relax the same-input predicate.
    return dict(source_changes=dict(value.source_changes),
        fact_replacements=[dict(item,sources=list(item['sources'])) for item in value.fact_replacements],
        fact_retractions=list(value.fact_retractions),new_schema=value.new_schema)


def replay_transaction(state,event,index):
    tx=make_update(state,batch=min(8,len(state.sources)),seed=100+index)
    require(transaction_dict(tx)==event['transaction'],'unequal input transition')
    return tx


def footprint(path):
    files={p.relative_to(path).as_posix():p.stat().st_size for p in sorted(path.rglob('*')) if p.is_file()}
    return dict(files=files,total_bytes=sum(files.values()))


class NativeOwner:
    """Minimal observation/lease/maintenance adapter around unchanged writers."""
    def __init__(self,name,path,initial):
        require(sys.platform.startswith('linux') and os.name=='posix','native Linux required; no surrogate')
        self.name,self.path=name,path
        self.reader=self.held=self.held_root=None
        if name=='frontier':
            from frontierstore.store import FrontierStore
            self.store=FrontierStore.create(path,initial)
            self.held=self.store.snapshot()
            self.held_root=(path/'ROOT').read_bytes()
            self.settings=dict(writer='unchanged FrontierStore',file_and_directory_fsync=True,flock=True)
        elif name=='sqlite_n':
            self.store=baselines.SQLiteNormalized.create(path,initial)
            db=self.store.connection
            db.execute('PRAGMA wal_autocheckpoint=0')
            db.execute('PRAGMA busy_timeout=0')
            self.settings={key:db.execute('PRAGMA '+key).fetchone()[0] for key in
                           ('journal_mode','synchronous','wal_autocheckpoint','busy_timeout','page_size')}
            require(self.settings['synchronous']==2 and self.settings['journal_mode']=='wal','native FULL/WAL required')
            self.reader=sqlite3.connect(self.store.db_path.resolve().as_uri()+'?mode=ro',uri=True,timeout=0)
            self.reader.execute('PRAGMA query_only=ON'); self.reader.execute('BEGIN')
            require(read_transaction(self.reader).as_dict()==initial.as_dict(),'initial SQL lease mismatch')
        elif name=='sqlite_r':
            self.store=baselines.SQLiteRootedAudit.create(path,initial)
            self.held_root=self.store._read_root()
            self.settings=dict(writer='unchanged SQLiteRootedAudit',
                image='native OFF/OFF construction followed by file fsync; not an unsynchronized publication',
                file_and_directory_fsync=True,atomic_pair_selector=True)
        else:
            raise ValueError(name)

    def persist(self,state,delta):
        if self.name=='frontier':
            self.store.publish_precomputed(state,delta)
        else:
            self.store.persist_precomputed(state,delta)

    def current(self):
        # Read selected durable bytes, not the writer's cached state. Reuse the
        # one live FrontierStore instance, preserving its in-process pin domain.
        if self.name=='frontier':
            return self.store._load_selected()[0].as_dict()
        return self.store.durable_state().as_dict()

    def held_value(self):
        if self.name=='frontier' and self.held is not None:
            memory=self.held.materialize()
            physical=check_selected(self.path,self.held_root)['endpoint']
            require(memory==physical,'pinned segment bytes differ from held value')
            return physical
        if self.name=='sqlite_n' and self.reader is not None:
            return read_transaction(self.reader).as_dict()
        if self.name=='sqlite_r' and self.held_root is not None:
            root=self.held_root
            state=baselines._read_sqlite_state(self.store.images/root['database']).as_dict()
            require(parse_audit_export(self.store.exports/root['export'])==state,'leased pair differs')
            return state
        return None

    def audit(self):
        if self.name=='frontier':
            value=check_selected(self.path,(self.path/'ROOT').read_bytes())['endpoint']
            kind='independent selected-segment parser'
        else:
            value=self.store.durable_state().as_dict()
            kind=('SQLite explicit read transaction; no independent export' if self.name=='sqlite_n'
                  else 'one native selected-pair read: SQLite decode + independent export parse + equality')
        return dict(status='ACCEPT',kind=kind,endpoint=value)

    def selectors(self):
        if self.name=='frontier':
            held=json.loads(self.held_root) if self.held_root is not None else None
            return dict(current=json.loads((self.path/'ROOT').read_bytes()),held=held,
                protected_files=[] if held is None else ['segments/'+s for s in held['segments']])
        if self.name=='sqlite_r':
            held=self.held_root
            return dict(current=self.store._read_root(),held=held,protected_files=[] if held is None else
                ['images/'+held['database'],'exports/'+held['export']])
        return dict(current_database='normalized.sqlite',
            held_read_transaction=self.reader is not None and self.reader.in_transaction,
            protected_files=[])  # WAL lifetime belongs to SQLite, not a manual file lease.

    def checkpoint(self,held=False):
        result=list(self.store.connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone())
        wal=self.path/'normalized.sqlite-wal'
        size=wal.stat().st_size if wal.exists() else 0
        if held:
            require(result[0]==1 and result[1]>result[2] and size>0,'held WAL lease was not preserved')
        else:
            require(result==[0,0,0] and size==0,'released WAL was not truncated')
        return dict(mode='TRUNCATE',result=result,wal_bytes=size,held=held)

    def maintain(self,expected,delta,held):
        if self.name=='frontier':
            require(self.store.compact().as_dict()==expected.as_dict(),'native compaction endpoint differs')
            return dict(method='native base compaction',selected=list(self.store.selected_segments))
        self.persist(expected,delta)  # Same empty logical epoch in both SQLite arms.
        if self.name=='sqlite_n':
            return dict(method='empty logical commit plus native WAL checkpoint',checkpoint=self.checkpoint(held))
        return dict(method='empty logical commit publishes native full image/export pair')

    def collect(self):
        if self.name=='frontier':
            return dict(method='native pin-aware collector',removed=self.store.collect())
        if self.name=='sqlite_n':
            return dict(method='SQLite owns WAL lifetime; reclaimed by maintenance/release checkpoint',removed=[])
        current=self.store._read_root()
        keep={current['database'],current['export']}
        if self.held_root is not None:
            keep|={self.held_root['database'],self.held_root['export']}
        removed=[]
        for directory,pattern in ((self.store.images,r'image-[0-9]{8}\.sqlite'),
                                  (self.store.exports,r'audit-[0-9]{8}\.json')):
            changed=False
            for file in sorted(directory.iterdir()):
                if file.is_file() and re.fullmatch(pattern,file.name) and file.name not in keep:
                    file.unlink(); removed.append(file.relative_to(self.path).as_posix()); changed=True
            if changed:
                baselines._fsync_directory(directory)
        return dict(method='single-owner harness lease protects captured native pair; confined unlink + directory fsync',removed=removed)

    def release(self):
        if self.held is not None:
            self.held.close(); self.held=None
        if self.reader is not None:
            self.reader.close(); self.reader=None
        self.held_root=None
        return self.checkpoint(False) if self.name=='sqlite_n' else dict(released=True)

    def reopen(self):
        require(self.held is None and self.reader is None and self.held_root is None,'release before reopen')
        if self.name=='frontier':
            from frontierstore.store import FrontierStore
            # No pins/writer survive this quiescent abandonment and reopen.
            self.store=None
            self.store=FrontierStore(self.path)
        else:
            kind=type(self.store); self.store.close(); self.store=kind(self.path)
        return self.current()

    def close(self):
        if self.held is not None:
            self.held.close(); self.held=None
        if self.reader is not None:
            self.reader.close(); self.reader=None
        if self.name!='frontier':
            self.store.close()


def run_case(path,config,*,sources=64,updates=8):
    require(4<=sources<=64 and updates in (4,8),'bounded native case required')
    width,cadence=config['dependency_width'],config['audit_every']
    require(1<=width<=min(8,sources) and cadence in (1,2,4),'bounded width/cadence required')
    path.mkdir(parents=True,exist_ok=False)
    owner=None; rows=[]; stage={key:0 for key in PHASES}; accepted=False
    started=time.perf_counter_ns(); cpu=time.process_time_ns()
    def timed(phase,fn,cost):
        begin=time.perf_counter_ns(); value=fn(); elapsed=time.perf_counter_ns()-begin
        cost[phase]+=elapsed; stage[phase]+=elapsed
        return value
    try:
        with gzip.open(path/'trace.jsonl.gz','wt',encoding='utf-8') as trace:
            def emit(row):
                rows.append(row)
                trace.write(json.dumps(row,sort_keys=True)+'\n'); trace.flush()
            begin=time.perf_counter_ns()
            state=make_synthetic_state(sources,dependency_width=width)
            bootstrap=state.as_dict()
            owner=NativeOwner(config['engine'],path/'store',state)
            require(owner.current()==bootstrap and owner.held_value()==bootstrap,'bootstrap mismatch')
            require(owner.audit()['endpoint']==bootstrap,'bootstrap audit mismatch')
            cost={key:0 for key in PHASES}
            cost['bootstrap']=time.perf_counter_ns()-begin; stage['bootstrap']+=cost['bootstrap']
            emit(dict(event='bootstrap',input=bootstrap,settings=owner.settings,
                current=bootstrap,historical=bootstrap,selectors=owner.selectors(),
                footprint=footprint(owner.path),phase_ns=cost))
            release_at=updates//2
            for index in range(1,updates+1):
                cost={key:0 for key in PHASES}
                def advance():
                    tx=make_update(state,batch=min(8,sources),seed=100+index)
                    value,delta=apply_transaction(state,tx)
                    return tx,value,delta
                tx,state,delta=timed('transition',advance,cost)
                update_expected=state.as_dict()
                timed('persistence',lambda:owner.persist(state,delta),cost)
                current=timed('current_read',owner.current,cost)
                require(current==update_expected,'durable update endpoint mismatch')
                audited=index%cadence==0 or index==updates
                audit=timed('audit',owner.audit,cost) if audited else None
                require(audit is None or audit['endpoint']==update_expected,'scheduled audit endpoint mismatch')
                maintenance=None
                if index in (release_at,updates):
                    # Charge construction of the common empty transition too.
                    state,empty=timed('transition',lambda:apply_transaction(state,Transaction()),cost)
                    maintenance=timed('maintenance',lambda:owner.maintain(state,empty,index<=release_at),cost)
                after=timed('current_read',owner.current,cost) if maintenance is not None else current
                require(after==state.as_dict(),'post-maintenance endpoint mismatch')
                collection=timed('collection',owner.collect,cost)
                held=timed('held_read',owner.held_value,cost)
                require(held==(bootstrap if index<=release_at else None),'leased physical endpoint mismatch after collection')
                selectors=owner.selectors(); before_release=footprint(owner.path)
                require(set(selectors['protected_files'])<=set(before_release['files']),
                        'collector removed physically leased objects')
                release=None
                if index==release_at:
                    release=timed('release',owner.release,cost)
                    release['collection']=timed('collection',owner.collect,cost)
                    require(owner.held_value() is None and owner.current()==state.as_dict(),'lease release changed current state')
                emit(dict(event='update',index=index,transaction=transaction_dict(tx),
                    fact_replacements=len(delta.fact_put),invalidated_facts=len(delta.fact_del),
                    dependency_edges=sum(len(f.dependencies) for f in state.facts.values()),
                    update_expected=update_expected,current_after_update=current,current=after,
                    historical=held,scheduled_audit=audited,audit=audit,maintenance=maintenance,
                    collection=collection,release=release,selectors=selectors,before_release=before_release,
                    phase_ns=cost,footprint=footprint(owner.path)))
                require(time.perf_counter_ns()-started<60_000_000_000,'60-second native case limit')
            cost={key:0 for key in PHASES}
            reopened=timed('reopen',owner.reopen,cost)
            audit=timed('audit',owner.audit,cost)
            require(reopened==state.as_dict()==audit['endpoint'],'orderly reopen endpoint mismatch')
            timed('release',owner.close,cost); owner=None
            emit(dict(event='reopen',expected=state.as_dict(),current=reopened,audit=audit,
                phase_ns=cost,footprint=footprint(path/'store')))
        accepted=True
    finally:
        if owner is not None:
            owner.close()
        result=dict(**config,accepted=accepted,updates=updates,sources=sources,
            stage_ns=stage,lifecycle_wall_ns=time.perf_counter_ns()-started,
            process_cpu_ns=time.process_time_ns()-cpu,records=len(rows),
            scope='native bootstrap, workload construction, transitions, persistence, current/held reads, scheduled audits, maintenance, collection, release, orderly reopen; wall includes raw trace instrumentation; phase totals exclude trace writes')
        write_json(path/'case.json',result)
    return result


def run(out,engines):
    out=reserve(out)
    write_json(out/'reservation.json',dict(authorized_new_bounded_study=True,
        nonrefundable_seconds=300,case_limit_seconds=60,cases=len(schedule(engines)),
        sources=64,facts=384,updates=8,widths=[2,8],audit_intervals=[1,4],
        repetitions=3,engines=list(engines),historical_ledger_reset=False))
    before=bindings(); write_json(out/'source-before.json',before)
    env=environment(out); write_json(out/'environment.json',env)
    if not env['linux_native_available']:
        write_json(out/'unavailable.json',dict(native_three_engine_costs_measured=False,
            reason='Linux, fcntl and unchanged native file/directory synchronization required',substitution_used=False))
        raise RuntimeError('native Linux lifecycle unavailable; no Windows surrogate allowed')
    write_json(out/'native-probe.json',native_probe(out))
    rows=[]; started=time.monotonic()
    try:
        for config in schedule(engines):
            require(time.monotonic()-started<300,'300-second native campaign envelope')
            name=f"r{config['repetition']}-w{config['dependency_width']}-a{config['audit_every']}-{config['engine']}"
            row=run_case(out/'cases'/name,config)
            row['case']=name; rows.append(row)
            print(json.dumps(dict(case=name,accepted=row['accepted'],wall_ms=round(row['lifecycle_wall_ns']/1e6,3))),flush=True)
    except BaseException as error:
        write_json(out/'failure.json',dict(accepted=False,completed_cases=len(rows),
            error_type=type(error).__name__,error=str(error)))
        raise
    after=bindings(); write_json(out/'source-after.json',after)
    require(before==after,'scientific source changed during native campaign')
    result=dict(status='NATIVE_CASES_COMPLETED',rows=rows,source_unchanged=True,
        native_three_engine_costs_measured=set(engines)==set(ENGINES),
        scope=__doc__,order_policy='all six permutations twice across 12 config/repetition blocks; each arm occupies each slot once per width/cadence',
        audit_policy='mandatory current read each update; full delivered audit at interval 1/4, plus bootstrap and final acceptance; SQLite-R uses one selected-pair pass per current/audit call, not a relabeling of old timings',
        history_policy='bootstrap endpoint leased through first maintenance/collection, then released; current endpoint always retained; no concurrent writers, raw-device or independent-process crash injection')
    write_json(out/'summary.json',result)
    return result


def verify(out):
    summary=json.loads((out/'summary.json').read_text())
    require(summary.get('status')=='NATIVE_CASES_COMPLETED' and summary.get('native_three_engine_costs_measured'),
            'complete native three-engine cohort required')
    before=json.loads((out/'source-before.json').read_text())
    require(before==json.loads((out/'source-after.json').read_text())==bindings(),'source binding mismatch')
    env=json.loads((out/'environment.json').read_text())
    require(env['linux_native_available'] and not env['substitution_used'],'not a native Linux run')
    probe=json.loads((out/'native-probe.json').read_text())
    require(all(probe[k] is True for k in ('file_fsync','replace','directory_fsync','flock'))
            and not probe['substitution_used'],'native persistence probe failed')
    require(not (out/'failure.json').exists(),'failed cohort is not accepted')
    result=replay_cohort_contents(out,summary)
    return dict(result,status='VERIFIED_FRESH_NATIVE_COHORT',source_binding_verified=True,source_unchanged=True)


def replay_cohort_contents(out,summary=None):
    """Replay saved data without accepting its source/environment provenance.

    This separately named read-only diagnostic also permits forensic inspection
    of an old failed run. Only verify() can report a source-bound fresh cohort;
    it must pass all identity, native-environment and failure gates first.
    """
    if summary is None:
        summary=json.loads((out/'summary.json').read_text())
    require(summary.get('status')=='NATIVE_CASES_COMPLETED' and summary.get('native_three_engine_costs_measured'),
            'complete native three-engine cohort required')
    expected_schedule=schedule()
    require(len(summary['rows'])==len(expected_schedule)==36,'incomplete native case matrix')
    common={}; audits=held_reads=updates=maintenance=0; medians=[]
    for row,config in zip(summary['rows'],expected_schedule):
        require(all(row[key]==value for key,value in config.items()),'engine/config order mismatch')
        name=f"r{config['repetition']}-w{config['dependency_width']}-a{config['audit_every']}-{config['engine']}"
        require(row['case']==name and row['accepted'],'failed/incorrectly named case')
        require(row['sources']==64 and row['updates']==8,'case work bounds differ')
        path=out/'cases'/name
        meta=json.loads((path/'case.json').read_text())
        require(all(meta[k]==v for k,v in row.items() if k!='case'),'raw case/summary mismatch')
        with gzip.open(path/'trace.jsonl.gz','rt',encoding='utf-8') as stream:
            trace=[json.loads(line) for line in stream]
        require(len(trace)==row['records']==10 and trace[0]['event']=='bootstrap' and trace[-1]['event']=='reopen','incomplete per-transition trace')
        stage={key:sum(r['phase_ns'][key] for r in trace) for key in PHASES}
        require(stage==row['stage_ns'] and all(v>=0 for v in stage.values()),'phase timing mismatch')
        require(row['lifecycle_wall_ns']>=sum(stage.values())>0,'lifecycle/phase timing accounting')
        state=baselines._state_from_dict(trace[0]['input']); bootstrap=state.as_dict()
        require(bootstrap==make_synthetic_state(64,dependency_width=config['dependency_width']).as_dict(),
                'declared generated input differs')
        require(trace[0]['current']==trace[0]['historical']==bootstrap,'bootstrap mismatch')
        identities=[]
        for index,event in enumerate(trace[1:-1],1):
            require(event['event']=='update' and event['index']==index,'update sequence mismatch')
            tx=replay_transaction(state,event,index)
            state,delta=apply_transaction(state,tx)
            endpoint=state.as_dict()
            require(event['current_after_update']==event['update_expected']==endpoint,'post-update durable state mismatch')
            require((event['fact_replacements'],event['invalidated_facts'])==(len(delta.fact_put),len(delta.fact_del)),'transition work-count mismatch')
            require(event['dependency_edges']==sum(len(f.dependencies) for f in state.facts.values()),'dependency work-count mismatch')
            scheduled=index%config['audit_every']==0 or index==8
            require(event['scheduled_audit']==scheduled,'audit cadence mismatch')
            if scheduled:
                require(event['audit']['status']=='ACCEPT' and event['audit']['endpoint']==endpoint,'scheduled audit mismatch')
                audits+=1
            else:
                require(event['audit'] is None and event['phase_ns']['audit']==0,'unscheduled audit work')
            if index in (4,8):
                require(event['maintenance'] is not None,'missing matched maintenance epoch')
                state,_=apply_transaction(state,Transaction()); maintenance+=1
            else:
                require(event['maintenance'] is None,'extra maintenance epoch')
            require(event['current']==state.as_dict(),'post-maintenance state mismatch')
            require(event['historical']==(bootstrap if index<=4 else None),'historical lease mismatch')
            require((event['release'] is not None)==(index==4),'release schedule mismatch')
            selectors=event['selectors']
            require(set(selectors['protected_files'])<=set(event['before_release']['files']),
                    'protected physical objects absent during lease')
            if config['engine']=='sqlite_n':
                require(selectors['held_read_transaction']==(index<=4),'SQL read lease missing')
                if index in (4,8):
                    checkpoint=event['maintenance']['checkpoint']
                    require(checkpoint['held']==(index==4) and checkpoint['result'][0]==(1 if index==4 else 0),
                            'checkpoint/lease schedule mismatch')
                if index==4:
                    require(event['release']['result']==[0,0,0] and event['release']['wal_bytes']==0,'released WAL not collected')
            else:
                require(selectors['current']['epoch']==state.epoch,'current selector/endpoint epoch mismatch')
                require((selectors['held'] is not None)==(index<=4),'captured historical selector missing')
                if index<=4:
                    require(selectors['held']['epoch']==1,'historical selector changed')
            identities.append(dict(transaction=event['transaction'],current=event['current']))
            held_reads+=int(index<=4); updates+=1
        cohort=config['dependency_width']
        if cohort in common:
            require(common[cohort]==identities,'engines/cadences/repetitions received unequal work or endpoints')
        common[cohort]=identities
        final=state.as_dict()
        require(state.epoch==11 and trace[-1]['current']==trace[-1]['expected']==trace[-1]['audit']['endpoint']==final,
                'orderly reopen or final audit mismatch')
        store=path/'store'
        if config['engine']=='frontier':
            require(check_selected(store,(store/'ROOT').read_bytes())['endpoint']==final,'saved segment endpoint mismatch')
            selected=set(json.loads((store/'ROOT').read_text())['segments'])
            require({p.name for p in (store/'segments').iterdir()}==selected and len(selected)==1,'uncollected obsolete segments')
        elif config['engine']=='sqlite_n':
            wal=store/'normalized.sqlite-wal'
            require(not wal.exists() or wal.stat().st_size==0,'final WAL not reclaimed')
            with closing(sqlite3.connect((store/'normalized.sqlite').resolve().as_uri()+'?immutable=1',uri=True)) as db:
                db.execute('BEGIN'); require(read_transaction(db).as_dict()==final,'saved normalized endpoint mismatch')
        else:
            engine=baselines.SQLiteRootedAudit(store)
            require(engine.durable_state().as_dict()==final,'saved rooted pair mismatch')
            root=engine._read_root()
            require({p.name for p in engine.images.iterdir()}=={root['database']}
                and {p.name for p in engine.exports.iterdir()}=={root['export']},'uncollected obsolete pairs')
    for width,cadence,engine in itertools.product((2,8),(1,4),ENGINES):
        group=[r for r in summary['rows'] if (r['dependency_width'],r['audit_every'],r['engine'])==(width,cadence,engine)]
        require(len(group)==3,'incomplete repetition block')
        medians.append(dict(width=width,audit_interval=cadence,engine=engine,repetitions=3,
            median_wall_ms=round(statistics.median(r['lifecycle_wall_ns']/1e6 for r in group),3),
            median_phase_ms={key:round(statistics.median(r['stage_ns'][key]/1e6 for r in group),3) for key in PHASES}))
    return dict(status='REPLAYED_NATIVE_CONTENTS_ONLY',cases=36,paired_blocks=12,unique_logical_streams=2,
        updates=updates,maintenance_epochs=maintenance,scheduled_audits=audits,
        held_endpoint_reads=held_reads,orderly_reopens=36,source_binding_verified=False,mismatches=0,
        medians=medians,boundary='quiescent native post-return equality/leases; SQLite-N remains a logical SQL null without an independent export/atomic cross-interface selector; no power loss or historical timing rewrite')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--out',type=Path); group.add_argument('--check',type=Path)
    parser.add_argument('--authorize-new-study',action='store_true')
    parser.add_argument('--engines',nargs='+',choices=ENGINES,default=list(ENGINES))
    parser.add_argument('--json-out',type=Path)
    args=parser.parse_args()
    if args.check:
        result=verify(args.check)
    else:
        require(args.authorize_new_study,'explicit new bounded study authorization required')
        run(args.out,args.engines)
        # A named subset can aid native diagnosis, but never masquerades as the
        # advertised complete three-engine cohort or pass its verifier.
        result=(verify(args.out) if set(args.engines)==set(ENGINES) else
            dict(status='NATIVE_SUBSET_COMPLETED',cases=len(schedule(args.engines)),
                 engines=args.engines,native_three_engine_costs_measured=False))
    if args.json_out:
        write_json(args.json_out,result)
    print(json.dumps(result,separators=(',',':')))


if __name__=='__main__':
    main()
