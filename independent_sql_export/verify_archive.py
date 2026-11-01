"""Read-only deterministic ZIP admission; optional reconstruction in fresh scratch.

Uses only stdlib, never imports or executes any source supplied by the archive.
Full mode rechecks108 exports,12 final DBs and6 final roundtrips, NOT54 DBs.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import zipfile

MAX_MEMBER = 32 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024


def require(value, message):
    if not value:
        raise ValueError(message)


def unique(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, 'duplicate JSON member')
        value[key] = item
    return value


def decode(data):
    def reject(value):
        raise ValueError('nonfinite JSON: '+value)
    return json.loads(data, object_pairs_hook=unique, parse_constant=reject)


def logical(value):
    def fields(row, names):
        require(type(row) is dict and set(row)==set(names), 'record fields')
    def number(n, maximum=(1<<63)-1):
        require(type(n) is int and 0<n<=maximum, 'integer domain')
    def identifier(s):
        require(type(s) is str and re.fullmatch(r'[A-Za-z][A-Za-z0-9_.:-]{0,127}',s), 'identifier')
    fields(value,['epoch','schema','sources','facts','reverse'])
    number(value['epoch'],99999999);number(value['schema'])
    for family in ['sources','facts','reverse']:
        require(type(value[family]) is dict,'map')
    inverse={sid:[] for sid in value['sources']}
    for sid,row in value['sources'].items():
        identifier(sid);fields(row,['id','generation','payload'])
        require(row['id']==sid and type(row['payload']) is str,'source key/text')
        row['payload'].encode('utf-8');number(row['generation'],value['epoch'])
    for fid,row in value['facts'].items():
        identifier(fid);fields(row,['id','payload','schema','dependencies'])
        require(row['id']==fid and type(row['payload']) is str,'fact key/text')
        row['payload'].encode('utf-8');number(row['schema'])
        require(row['schema']==value['schema'],'fact schema')
        require(type(row['dependencies']) is list and row['dependencies'],'dependencies')
        ids=[]
        for edge in row['dependencies']:
            fields(edge,['source','generation']);sid=edge['source'];identifier(sid);number(edge['generation'])
            require(sid in value['sources'] and edge['generation']==value['sources'][sid]['generation'],'dependency closure')
            ids.append(sid);inverse[sid].append(fid)
        require(ids==sorted(set(ids)),'duplicate/unsorted dependencies')
    require(set(value['reverse'])==set(inverse),'inverse domain')
    for sid,members in value['reverse'].items():
        require(type(members) is list and members==sorted(inverse[sid]),'inverse membership')
    return value


def database(path):
    wal=Path(str(path)+'-wal')
    require(not wal.exists() or wal.stat().st_size==0,'immutable reader cannot ignore live WAL')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as db:
        db.execute('PRAGMA trusted_schema=OFF');db.execute('BEGIN')
        meta=list(db.execute('SELECT key,value FROM meta ORDER BY key'))
        require(len(meta)==2 and {r[0] for r in meta}=={'epoch','schema'},'SQL metadata')
        sources={};facts={}
        for sid,g,p in db.execute('SELECT id,generation,payload FROM sources ORDER BY id'):
            require(sid not in sources,'duplicate source');sources[sid]=dict(id=sid,generation=g,payload=p)
        for fid,p,s in db.execute('SELECT id,payload,schema FROM facts ORDER BY id'):
            require(fid not in facts,'duplicate fact');facts[fid]=dict(id=fid,payload=p,schema=s,dependencies=[])
        inverse={sid:[] for sid in sources}
        for fid,sid,g in db.execute('SELECT fact_id,source_id,generation FROM dependencies ORDER BY fact_id,source_id'):
            require(fid in facts and sid in sources,'SQL orphan')
            facts[fid]['dependencies'].append(dict(source=sid,generation=g));inverse[sid].append(fid)
        result=dict(dict(meta),sources=sources,facts=facts,reverse=inverse)
        return logical(result)


def verify(archive, scratch=None, expected_sha256=None):
    archive=Path(archive)
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    if expected_sha256:
        require(digest==expected_sha256,'archive external SHA256 mismatch')
    with zipfile.ZipFile(archive) as z:
        infos=z.infolist();names=[i.filename for i in infos]
        require(names==sorted(names) and len(names)==len(set(names)),'ZIP ordering/duplicate')
        require(sum(i.file_size for i in infos)<=MAX_TOTAL,'expanded archive bound')
        for info in infos:
            p=PurePosixPath(info.filename)
            require(not p.is_absolute() and '..' not in p.parts and '\\' not in info.filename,'confined member')
            require(info.file_size<=MAX_MEMBER and not (info.flag_bits&1),'member bound/encryption')
            require(info.compress_type in {zipfile.ZIP_DEFLATED,zipfile.ZIP_STORED},'compression')
            require(stat.S_IFMT(info.external_attr>>16)==stat.S_IFREG,'no symlink/directory member')
            require(info.date_time==(1980,1,1,0,0,0),'fixed ZIP metadata')
        manifest=decode(z.read('MANIFEST.json'))
        by={r['member']:r for r in manifest['files']}
        require(len(by)==len(manifest['files']) and set(names)==set(by)|{'MANIFEST.json'},'exact manifest coverage')
        if scratch is not None:
            scratch=Path(scratch).resolve();require(not scratch.exists(),'fresh scratch required')
            scratch.mkdir(parents=True)
        for name,row in by.items():
            data=z.read(name)
            require(len(data)==row['bytes'] and hashlib.sha256(data).hexdigest()==row['sha256'],'member identity: '+name)
            if scratch is not None:
                target=(scratch/name).resolve();require(target.is_relative_to(scratch),'extraction confinement')
                target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    result=dict(status='PASS',archive_sha256=digest,manifest_files=len(by),all_member_identities_verified=True,
        full_reconstruction_checked=scratch is not None,archive_code_executed=False,
        path_representation=manifest.get('path_representation'),
        authenticity='Unkeyed integrity only; use externally trusted archive SHA256. Not a signature.')
    if scratch is not None:
        frozen=scratch/'inputs/raw-facts-transactions.json'
        origin=decode((scratch/'inputs/manifest.json').read_bytes())
        require(hashlib.sha256(frozen.read_bytes()).hexdigest()==origin['sha256'],'input freeze')
        inputs=decode(frozen.read_bytes());expected={}
        for h in inputs['histories']:
            expected[h['id'],0]=h['initial']
            for e in h['events']:expected[h['id'],e['step']]=e['expected']
        root=scratch/'run-001/matrix'
        rows=[decode(line) for line in (root/'matrix.jsonl').read_bytes().splitlines()]
        keys=[(r['case'],r['step']) for r in rows]
        require(len(keys)==54 and len(keys)==len(set(keys)) and set(keys)==set(expected),'full fixed matrix')
        for row in rows:
            value=logical(expected[row['case'],row['step']])
            for engine,col in [('independent','independent_export_sha256'),('normalized','normalized_export_sha256')]:
                export=root/row['case']/engine/f"epoch-{row['step']:02d}.json"
                require(hashlib.sha256(export.read_bytes()).hexdigest()==row[col],'trace/export binding')
                require(logical(decode(export.read_bytes()))==value,'full export endpoint')
        for h in inputs['histories']:
            directory=root/h['id'];last=h['events'][-1]['expected']
            require(logical(decode((directory/'held-epoch01.json').read_bytes()))==h['initial'],'held endpoint')
            for engine,name in [('independent','logical.sqlite'),('normalized','normalized.sqlite')]:
                require(database(directory/engine/name)==last,'final DB endpoint')
            require(database(directory/'roundtrip-08.sqlite')==last,'final roundtrip endpoint')
        result.update(complete_exports_rechecked=108,final_databases_rechecked=12,
            final_roundtrip_databases_rechecked=6,held_exports_rechecked=6,
            no_claim_of_54_fresh_database_rereads=True,scientific_matrix_rerun=False)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--scratch',type=Path,help='Absent private directory; omitted means manifest/hash-only, no reconstruction')
    p.add_argument('--expected-sha256')
    a=p.parse_args();print(json.dumps(verify(a.archive,a.scratch,a.expected_sha256),sort_keys=True))


if __name__=='__main__':
    main()
