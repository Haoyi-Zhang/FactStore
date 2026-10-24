"""Current-source SQLite-API sensitivity, not a native three-engine comparison."""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import random
import sqlite3
import statistics
import subprocess
import sys
import time

from experiments.history_lifecycle import CANDIDATE, owned, run_case

ROOT=CANDIDATE/'artifact'
SOURCES=('experiments/fanout_cadence.py','experiments/history_lifecycle.py',
    'frontierstore/baselines.py','frontierstore/model.py','frontierstore/workload.py',
    'frontierstore/audit_export.py')

def bindings():
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES}

def environment():
    host=None
    if sys.platform=='win32':
        command=("[pscustomobject]@{cpu=Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors; "
            "os=Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber,OSArchitecture,TotalVisibleMemorySize; "
            "volume=Get-CimInstance Win32_LogicalDisk -Filter \"DeviceID='D:'\" | Select-Object FileSystem,DriveType} | ConvertTo-Json -Depth 5")
        result=subprocess.run(['powershell.exe','-NoProfile','-Command',command],capture_output=True,timeout=20,check=True)
        host=json.loads(result.stdout.decode('utf-8-sig'))
    with sqlite3.connect(':memory:') as db:
        source_id=db.execute('SELECT sqlite_source_id()').fetchone()[0]
        options=[row[0] for row in db.execute('PRAGMA compile_options')]
    return dict(observed_utc=datetime.now(timezone.utc).isoformat(),python=sys.version,
        sqlite=sqlite3.sqlite_version,sqlite_source_id=source_id,sqlite_compile_options=options,
        platform=platform.platform(),host=host,affinity='not pinned',
        resource_allocation='unknown',virtualization='unknown',storage_medium='unknown',
        mount_options='not applicable to Windows API experiment; native POSIX mount unknown',
        clocks={name:vars(time.get_clock_info(name)) for name in ('perf_counter','process_time')},
        device_bytes_measured=False,power_loss_tested=False)

def run(out):
    out=owned(out)
    out.mkdir(parents=True,exist_ok=False)
    before=bindings()
    (out/'source-before.json').write_text(json.dumps(before,indent=2),encoding='utf-8')
    (out/'environment.json').write_text(json.dumps(environment(),indent=2),encoding='utf-8')
    native=dict(available=importlib.util.find_spec('fcntl') is not None,
        platform=platform.platform(),required='native FrontierStore fcntl + unchanged directory fsync',
        substituted=False,native_three_representation_costs_measured=False)
    (out/'native-boundary.json').write_text(json.dumps(native,indent=2),encoding='utf-8')
    started=time.monotonic()
    configs=[(w,a) for w in (2,8) for a in (1,4)]
    rows=[]
    order=[]
    for repetition in range(3):
        schedule=configs[:]
        random.Random(21024+repetition).shuffle(schedule)
        for width,cadence in schedule:
            if time.monotonic()-started>180:
                raise TimeoutError('180-second campaign envelope')
            name=f'r{repetition}-w{width}-a{cadence}'
            order.append(name)
            row=run_case(out/name,64,8,'leases',dependency_width=width,audit_every=cadence)
            row['repetition']=repetition
            with gzip.open(out/(name+'.json.gz'),'wt',encoding='utf-8') as stream:
                json.dump(row,stream,sort_keys=True)
            updates=[r for r in row['records'] if r['event']=='update']
            row['mean_replaced_facts']=statistics.mean(r['fact_replacements'] for r in updates)
            row['total_archive_files_removed']=sum(len(r.get('collection',{}).get('removed',[])) for r in row['records'])
            rows.append({key:value for key,value in row.items() if key not in ('records','checkpoints')})
            print(json.dumps(dict(case=name,replaced=row['mean_replaced_facts'],audits=row['audit_checks'])),flush=True)
    after=bindings()
    (out/'source-after.json').write_text(json.dumps(after,indent=2),encoding='utf-8')
    if before!=after:
        raise ValueError('source changed during campaign')
    aggregates=[]
    for width,cadence in configs:
        group=[row for row in rows if (row['dependency_width'],row['audit_every'])==(width,cadence)]
        stages={key:statistics.median(r['stage_ns'][key]/1e6 for r in group) for key in group[0]['stage_ns']}
        aggregates.append(dict(dependency_width=width,audit_every=cadence,repetitions=3,
            mean_replaced_facts=group[0]['mean_replaced_facts'],audit_checks=group[0]['audit_checks'],
            median_lifecycle_ms=statistics.median(r['lifecycle_elapsed_ns']/1e6 for r in group),
            median_stage_ms=stages,final_bytes=group[0]['final']['total_bytes']))
    result=dict(cases=12,current_updates=96,held_reads=48,reopens=12,
        schedule=order,seed_policy='random.Random(21024+repetition); four configs shuffled per repetition',
        rows=rows,aggregates=aggregates,source_unchanged=True,
        scope='SQLite API only; current durable reads every update; separate pair audit every 1/4 updates; '
              '8 changed sources, 64 sources/384 facts, widths 2/8, bootstrap lease through update 4; '
              'common make_update+transition included in lifecycle and transition stage; '
              'content fsync only for archives, no POSIX directory durability or atomic pair/root publication',
        interpretation='Host-dependent three-repeat sensitivity; no native engine ranking or statistical significance claim')
    (out/'summary.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result

def table(result):
    lines=[r'\begin{table}[t]\centering\small',
        r'\caption{Current-source SQLite-API sensitivity; median of three lifecycles.}',
        r'\label{tab:fanout-cadence}',r'\begin{tabular}{rrrrrr}\toprule',
        r'Width & Audit interval & Replaced/update & Transition (ms) & Audit (ms) & Full (ms)\\\midrule']
    for row in result['aggregates']:
        stage=row['median_stage_ms']
        lines.append(f"{row['dependency_width']} & {row['audit_every']} & {row['mean_replaced_facts']:.1f} & {stage['transition']:.2f} & {stage['audit']:.2f} & {row['median_lifecycle_ms']:.2f}"+r'\\')
    lines.extend([r'\bottomrule\end{tabular}\end{table}'])
    return '\n'.join(lines)+'\n'

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--out',type=Path)
    group.add_argument('--check',type=Path)
    parser.add_argument('--tex',type=Path)
    args=parser.parse_args()
    if args.check:
        result=json.loads((args.check/'summary.json').read_text(encoding='utf-8'))
        if json.loads((args.check/'source-before.json').read_text())!=bindings():
            raise ValueError('current scientific source differs from measured source')
        if json.loads((args.check/'source-after.json').read_text())!=bindings():
            raise ValueError('post-run scientific source mismatch')
        for row in result['rows']:
            if not row['endpoint_equal'] or row['retained_epochs']!=[9] or row['total_archive_files_removed']!=16:
                raise ValueError('lifecycle contract mismatch')
            name=f"r{row['repetition']}-w{row['dependency_width']}-a{row['audit_every']}"
            with gzip.open(args.check/(name+'.json.gz'),'rt',encoding='utf-8') as stream:
                raw=json.load(stream)
            updates=[r for r in raw['records'] if r['event']=='update']
            for key in ('stage_ns','final','lifecycle_elapsed_ns','audit_checks','retained_epochs'):
                if raw[key]!=row[key]: raise ValueError('raw lifecycle summary mismatch: '+key)
            if (sum(r['scheduled_audit'] for r in updates)!=row['audit_checks']
                    or statistics.mean(r['fact_replacements'] for r in updates)!=row['mean_replaced_facts']):
                raise ValueError('raw work-count mismatch')
        for aggregate in result['aggregates']:
            group=[r for r in result['rows'] if (r['dependency_width'],r['audit_every'])==
                (aggregate['dependency_width'],aggregate['audit_every'])]
            if len(group)!=3: raise ValueError('incomplete sensitivity grid')
            if statistics.median(r['lifecycle_elapsed_ns']/1e6 for r in group)!=aggregate['median_lifecycle_ms']:
                raise ValueError('lifecycle median mismatch')
            for key,value in aggregate['median_stage_ms'].items():
                if statistics.median(r['stage_ns'][key]/1e6 for r in group)!=value:
                    raise ValueError('phase median mismatch')
        print(json.dumps(dict(cases=result['cases'],current_updates=result['current_updates'],source_unchanged=True)))
    else:
        result=run(args.out)
    if args.tex:
        args.tex.write_text(table(result),encoding='utf-8')
