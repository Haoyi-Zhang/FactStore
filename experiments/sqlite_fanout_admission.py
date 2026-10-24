"""Data-only scientific admission of the twelve scoped SQLite fanout records.

Only successfully decoded, source-bound and replayed case paths are returned to
package hygiene. No archive extension/directory exception or engine run occurs.
The consumed measurement sources and all historical outcomes remain unchanged.
"""
import hashlib
import json
from pathlib import Path
import random
import statistics

from experiments.compressed_records import (decode_record,unique_object,
    reject_constant,finite_float)
from frontierstore.model import apply_transaction
from frontierstore.workload import make_synthetic_state,make_update

CASES={(r,w,a) for r in range(3) for w in (2,8) for a in (1,4)}
CONSUMED_SOURCES={'experiments/fanout_cadence.py','experiments/history_lifecycle.py',
    'frontierstore/baselines.py','frontierstore/model.py','frontierstore/workload.py',
    'frontierstore/audit_export.py'}
STAGES={'transition','persistence','reads','archive','audit','reclamation','checkpoint'}


def require(value,message):
    if not value:
        raise ValueError('sqlite-fanout: '+message)


def encoded(value):
    # Preserve Boolean/integer distinctions; normalize only JSON's container
    # representation of ordered tuple/list sequences, not their content/order.
    return json.dumps(value,sort_keys=True,allow_nan=False,separators=(',',':'))


def read_json(path):
    raw=path.read_bytes()
    require(len(raw)<=8*1024*1024,'metadata exceeds byte limit')
    value=json.loads(raw.decode('utf-8'),object_pairs_hook=unique_object,
        parse_constant=reject_constant,parse_float=finite_float)
    require(type(value) is dict,'metadata must be an object')
    return value


def identity(row):
    keys=('repetition','dependency_width','audit_every')
    require(type(row) is dict and all(type(row.get(k)) is int for k in keys),'typed case identity required')
    key=tuple(row[k] for k in keys)
    require(key in CASES,'unknown case')
    return key


def verify_sqlite_fanout(root):
    root=Path(root).resolve()
    directory=root/'results/sqlite-fanout'
    require(directory.resolve().is_relative_to(root),'dataset leaves owned artifact')
    before=read_json(directory/'source-before.json')
    require(set(before)==CONSUMED_SOURCES,'source identity set differs')
    require(before==read_json(directory/'source-after.json'),'source changed during measurement')
    require(all((root/name).resolve().is_relative_to(root)
        and hashlib.sha256((root/name).read_bytes()).hexdigest()==digest
        for name,digest in before.items()),'measured source binding mismatch')
    boundary=read_json(directory/'native-boundary.json')
    require(boundary.get('substituted') is False
        and boundary.get('native_three_representation_costs_measured') is False,
        'API dataset must not be relabelled as native three-engine evidence')
    summary=read_json(directory/'summary.json')
    for key,value in (('cases',12),('current_updates',96),('held_reads',48),('reopens',12)):
        require(type(summary.get(key)) is int and summary[key]==value,'summary count differs: '+key)
    require(summary.get('source_unchanged') is True,'source stability not established')
    rows=summary.get('rows')
    require(type(rows) is list and len(rows)==12,'twelve summary rows required')
    index={identity(row):row for row in rows}
    require(len(index)==len(rows) and set(index)==CASES,'missing/duplicate case coverage')
    names={key:f'r{key[0]}-w{key[1]}-a{key[2]}' for key in CASES}
    expected={name+'.json.gz' for name in names.values()}
    require({p.name for p in directory.glob('*.gz')}==expected,'compressed case filename coverage differs')
    order=[]
    for repetition in range(3):
        configs=[(w,a) for w in (2,8) for a in (1,4)]
        random.Random(21024+repetition).shuffle(configs)
        order.extend(names[repetition,w,a] for w,a in configs)
    require(summary.get('schedule')==order,'declared shuffled schedule differs')
    common={}
    for key in sorted(CASES):
        repetition,width,cadence=key
        raw=decode_record(directory/(names[key]+'.json.gz'))
        require(identity(raw)==key,'raw case identity differs')
        for field,value in (('sources',64),('facts',384),('updates',8),('release_at',4),('raw_record_count',11)):
            require(type(raw.get(field)) is int and raw[field]==value,'raw count differs: '+field)
        require(raw.get('policy')=='leases' and raw.get('endpoint_equal') is True
            and raw.get('held_endpoint_equal') is True and raw.get('retained_epochs')==[9],
            'lifecycle endpoint/retention contract differs')
        require(raw['settings']['journal_mode']=='wal' and raw['settings']['synchronous']==2
            and raw['settings']['wal_autocheckpoint']==0,'native SQLite API settings differ')
        records=raw.get('records')
        require(type(records) is list and len(records)==11 and all(type(x) is dict for x in records),
                'raw event coverage differs')
        require([x.get('event') for x in records]==['bootstrap']+['update']*4+['release']+['update']*4+['closed-reopened'],
                'lease event ordering differs')
        state=make_synthetic_state(64,dependency_width=width)
        bootstrap=state.as_dict()
        require(encoded(records[0]['input'])==encoded(bootstrap),'generated input differs')
        updates=[x for x in records if x['event']=='update']
        for number,event in enumerate(updates,1):
            require(type(event.get('index')) is int and event['index']==number,'update sequence differs')
            tx=make_update(state,batch=8,seed=100+number)
            projection=dict(source_changes=dict(tx.source_changes),fact_replacements=[dict(x) for x in tx.fact_replacements],
                fact_retractions=list(tx.fact_retractions),new_schema=tx.new_schema)
            require(encoded(event['transaction'])==encoded(projection),'input transition differs')
            state,delta=apply_transaction(state,tx)
            require(encoded(event['current'])==encoded(state.as_dict()),'complete current endpoint differs')
            require(encoded(event['historical'])==encoded(bootstrap if number<=4 else None),'leased endpoint differs')
            require(event['scheduled_audit'] is (number%cadence==0 or number==8),'scheduled audit differs')
            require(type(event['fact_replacements']) is int and event['fact_replacements']==len(delta.fact_put)
                and type(event['invalidated_facts']) is int and event['invalidated_facts']==len(delta.fact_del),
                'affected-set work counts differ')
            require(type(event['dependency_edges']) is int and event['dependency_edges']==384*width,
                'dependency fanout differs')
            obsolete=[] if number==1 else [number]
            expected_removed=sorted(name for epoch in obsolete for name in
                (f'image-{epoch:08d}.sqlite',f'audit-{epoch:08d}.json'))
            require(sorted(event['collection']['removed'])==expected_removed,'obsolete archive removal differs')
            require(event['collection']['remaining_epochs']==([1,number+1] if number<=4 else [number+1]),
                'leased archive retention differs')
        release=records[5]
        require(type(release.get('index')) is int and release['index']==4
            and sorted(release['collection']['removed'])==['audit-00000001.json','image-00000001.sqlite']
            and release['collection']['remaining_epochs']==[5],'lease release/reclamation differs')
        require(encoded(records[-1]['current'])==encoded(state.as_dict()),'orderly reopen endpoint differs')
        if width in common:
            require(encoded([x['current'] for x in updates])==common[width],'cadences/repetitions received unequal work')
        common[width]=encoded([x['current'] for x in updates])
        require(type(raw['audit_checks']) is int and raw['audit_checks']==sum(x['scheduled_audit'] for x in updates),
                'audit count differs')
        checkpoints=raw['checkpoints']
        require([(x['event'],x['mode']) for x in checkpoints]==[('bootstrap','TRUNCATE'),('held-reader','PASSIVE'),
            ('held-reader','TRUNCATE'),('reader-released','TRUNCATE'),('final','TRUNCATE')],'checkpoint coverage differs')
        require(checkpoints[1]['result'][0]==0 and checkpoints[2]['result'][0]==1
            and all(not checkpoints[i]['all_frames_checkpointed'] and not checkpoints[i]['wal_truncated'] for i in (1,2))
            and all(checkpoints[i]['result']==[0,0,0] and checkpoints[i]['wal_truncated'] for i in (0,3,4)),
            'checkpoint busy/completion evidence differs')
        require(set(raw['stage_ns'])==STAGES and all(type(x) is int and x>=0 for x in raw['stage_ns'].values()),
                'phase cost domain differs')
        require(raw['stage_ns']['checkpoint']==sum(x['elapsed_ns'] for x in checkpoints),'checkpoint phase costs differ')
        reduced={k:v for k,v in raw.items() if k not in ('records','checkpoints')}
        reduced['mean_replaced_facts']=statistics.mean(x['fact_replacements'] for x in updates)
        reduced['total_archive_files_removed']=sum(len(x.get('collection',{}).get('removed',[])) for x in records)
        require(reduced['total_archive_files_removed']==16,'total reclamation differs')
        require(encoded(reduced)==encoded(index[key]),'raw full summary binding differs')
    aggregates=[]
    for width in (2,8):
        for cadence in (1,4):
            group=[index[r,width,cadence] for r in range(3)]
            aggregates.append(dict(dependency_width=width,audit_every=cadence,repetitions=3,
                mean_replaced_facts=group[0]['mean_replaced_facts'],audit_checks=group[0]['audit_checks'],
                median_lifecycle_ms=statistics.median(r['lifecycle_elapsed_ns']/1e6 for r in group),
                median_stage_ms={k:statistics.median(r['stage_ns'][k]/1e6 for r in group) for k in STAGES},
                final_bytes=group[0]['final']['total_bytes']))
    require(encoded(aggregates)==encoded(summary['aggregates']),'aggregate medians differ from raw observations')
    return {'results/sqlite-fanout/'+name for name in expected}
