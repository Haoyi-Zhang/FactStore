"""Native stores consume a captured analysis of exact released source modules.

The natural old/new endpoints are fixed licensed package releases. Intermediate
single-module updates are an authored interleaving, not upstream commit history.
Only source ASTs are parsed. No external analyzer/input program is executed.
This is a semantic lifecycle and read-work study, not a new timing campaign.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys
import zipfile
from frontierstore.import_inventory import bootstrap,refresh,REGISTRY
from frontierstore.model import Transaction,apply_transaction
from experiments.native_fanout_cadence import NativeOwner,transaction_dict
ROOT=Path(__file__).resolve().parents[1]
def save(p,v):p.write_text(json.dumps(v,sort_keys=True,indent=2)+'\n',encoding='utf-8')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load_sources():
    manifest=json.loads((ROOT/'data/import-releases/sources.json').read_text())
    bundles={}
    with zipfile.ZipFile(ROOT/'data/import-releases/sources.zip') as z:
        expected=set()
        for release in manifest['releases']:
            version=release['release'];modules={}
            for path,identity in release['modules'].items():
                key=version+'/'+path;expected.add(key);raw=z.read(key)
                if len(raw)!=identity['bytes'] or hashlib.sha256(raw).hexdigest()!=identity['sha256']:raise ValueError('source byte identity')
                name=path[:-3].replace('/','.')
                if name.endswith('.__init__'):name=name[:-len('.__init__')]
                if name in modules:raise ValueError('duplicate module mapping')
                modules[name]=raw.decode('utf-8')
            bundles[version]=modules;expected.add(version+'/LICENSE.txt')
            if b'Permission is hereby granted' not in z.read(version+'/LICENSE.txt'):raise ValueError('license')
        if set(z.namelist())!=expected or len(z.namelist())!=len(expected):raise ValueError('archive member set')
    return bundles
def logical_projection(state):
    return {key:dict(payload=fact.payload,sources=[key for key,generation in fact.dependencies]) for key,fact in state.facts.items()}
def measure():
    events=[]
    def observe(identifier,size):events.append(dict(id=identifier,bytes=size))
    return events,observe
def counts(events):return dict(source_reads=sum(e['id']!=REGISTRY for e in events),
    source_bytes=sum(e['bytes'] for e in events if e['id']!=REGISTRY),registry_reads=sum(e['id']==REGISTRY for e in events))
def run(out):
    if out.exists() or out.resolve().is_relative_to(ROOT.resolve()):raise ValueError('fresh external output required')
    if not sys.platform.startswith('linux'):raise ValueError('native Linux persistence required')
    bundles=load_sources();old,new=bundles['2.1.1'],bundles['2.2.0']
    if old.keys()!=new.keys():raise ValueError('this fixed cohort requires unchanged registry membership')
    changed=sorted(k for k in old if old[k]!=new[k])
    if not 1<=len(changed)<=16:raise ValueError('bounded changed-module count')
    out.mkdir(parents=True)
    bindings={p:sha(ROOT/p) for p in ('experiments/import_lifecycle.py','frontierstore/import_inventory.py',
      'frontierstore/model.py','frontierstore/store.py','frontierstore/baselines.py','frontierstore/checker.py',
      'frontierstore/audit_export.py','experiments/native_fanout_cadence.py','data/import-releases/sources.zip','data/import-releases/sources.json')}
    save(out/'registration.json',dict(releases=['2.1.1','2.2.0'],modules=len(old),changed_modules=changed,
        intermediate_schedule='lexically ordered one-module replacements, authored interleaving of real released endpoint bytes',
        engines=['frontier','sqlite_n','sqlite_r'],held_endpoint='complete bootstrap analysis',
        maintenance='after middle update and after release; common empty epoch and native maintenance',
        observation='exact persisted current, historical leased endpoint, full recomputation, actual read callbacks and reclaim/reopen',
        performance_claim=False,wall_campaign_cap_seconds=120,source_bindings=bindings))
    initial=bootstrap(old);rows=[]
    for engine in ('frontier','sqlite_n','sqlite_r'):
        location=out/engine;location.mkdir();owner=NativeOwner(engine,location/'store',initial)
        current=initial;modules=dict(old);events=[]
        save(location/'initial-state.json',initial.as_dict())
        try:
            for i,name in enumerate(changed,1):
                modules[name]=new[name]
                incremental,observe=measure();updated,delta,tx=refresh(current,modules,observe=observe)
                full_reads,observe_full=measure();fresh=bootstrap(modules,observe=observe_full)
                if logical_projection(updated)!=logical_projection(fresh):raise ValueError('incremental/full analysis differs')
                owner.persist(updated,delta)
                if owner.current()!=updated.as_dict() or owner.held_value()!=initial.as_dict() or owner.audit()['endpoint']!=updated.as_dict():raise ValueError('native current/held/audit differs')
                event=dict(module=name,transaction=transaction_dict(tx),incremental=counts(incremental),
                    full=counts(full_reads),incremental_read_events=incremental,full_read_events=full_reads,
                    fact_replacements=sorted(delta.fact_put),facts_total=len(updated.facts),
                    current_equal=True,held_initial_equal=True,full_recompute_equal=True,
                    current_state=updated.as_dict())
                current=updated
                if i==max(1,len(changed)//2):
                    current,empty=apply_transaction(current,Transaction())
                    event['maintenance']=owner.maintain(current,empty,True)
                    event['collection']=owner.collect()
                    if owner.held_value()!=initial.as_dict() or owner.current()!=current.as_dict():raise ValueError('held analysis lost at maintenance')
                events.append(event);save(location/'trace.json',events)
            released=owner.release()
            current,empty=apply_transaction(current,Transaction());maintenance=owner.maintain(current,empty,False)
            collected=owner.collect();reopened=owner.reopen()
            if reopened!=current.as_dict() or logical_projection(current)!=logical_projection(bootstrap(new)):raise ValueError('final released endpoint differs')
            row=dict(engine=engine,updates=len(events),all_endpoints_equal=True,orderly_reopen_equal=True,
                incremental_source_reads=sum(e['incremental']['source_reads'] for e in events),
                full_source_reads=sum(e['full']['source_reads'] for e in events),
                incremental_source_bytes=sum(e['incremental']['source_bytes'] for e in events),
                full_source_bytes=sum(e['full']['source_bytes'] for e in events),
                released=released,maintenance=maintenance,collection=collected)
            rows.append(row);save(location/'final-state.json',current.as_dict());save(location/'summary.json',row)
        finally:owner.close()
    if len({(r['incremental_source_reads'],r['full_source_reads'],r['incremental_source_bytes'],r['full_source_bytes']) for r in rows})!=1:
        raise ValueError('engines did not consume the same analysis work')
    summary=dict(status='PASS',released_endpoint_pairs=1,modules=len(old),changed_modules=len(changed),
        engines=len(rows),native_transitions=sum(r['updates'] for r in rows),rows=rows,
        performance_claim=False,natural_intermediate_commit_history=False,power_loss_or_cross_process_pins_tested=False)
    save(out/'summary.json',summary);save(out/'environment.json',dict(python=platform.python_version(),platform=platform.platform()))
    print(json.dumps(summary));return summary
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);run(p.parse_args().output)
