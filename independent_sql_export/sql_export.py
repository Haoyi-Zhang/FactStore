"""Ordinary SQL writer and logical exporter: no producer/model imports.

The exporter derives its value from database rows, not a cached proposed state.
JSON publication is durable on the actual POSIX surface but is NOT atomic with
the database commit. SQLite's own interpreter, not a physical-page parser, reads
the database. The separate JSON consumer does not import this module.
"""
import json
import os
from pathlib import Path
import re
import sqlite3

ID = re.compile(r'[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z')
MAX_INT = (1 << 63) - 1
MAX_EPOCH = 99_999_999
TABLES = {'meta': ('key', 'value'), 'sources': ('id', 'generation', 'payload'),
          'facts': ('id', 'payload', 'schema'),
          'dependencies': ('fact_id', 'source_id', 'generation')}
SCHEMA = '''
CREATE TABLE meta(key TEXT PRIMARY KEY,value INTEGER NOT NULL);
CREATE TABLE sources(id TEXT PRIMARY KEY,generation INTEGER NOT NULL,payload TEXT NOT NULL);
CREATE TABLE facts(id TEXT PRIMARY KEY,payload TEXT NOT NULL,schema INTEGER NOT NULL);
CREATE TABLE dependencies(fact_id TEXT NOT NULL,source_id TEXT NOT NULL,generation INTEGER NOT NULL,PRIMARY KEY(fact_id,source_id));
CREATE INDEX dependencies_by_source ON dependencies(source_id,fact_id);
'''


def require(value, reason):
    if not value:
        raise ValueError(reason)


def identifier(value):
    require(type(value) is str and ID.fullmatch(value), 'identifier')
    return value


def integer(value, upper=MAX_INT):
    require(type(value) is int and 0 < value <= upper, 'integer domain')
    return value


def text(value):
    require(type(value) is str, 'text type')
    value.encode('utf-8', errors='strict')
    return value


def read_transaction(db, after_metadata=None):
    """One already-open snapshot (or writer transaction); never autocommit reads."""
    require(db.in_transaction, 'explicit transaction required')
    for name, columns in TABLES.items():
        info = list(db.execute('PRAGMA table_info(' + name + ')'))
        require(tuple(row[1] for row in info) == columns, 'SQL schema: ' + name)
    meta_rows = list(db.execute('SELECT key,value FROM meta ORDER BY key'))
    require(len(meta_rows) == 2 and {r[0] for r in meta_rows} == {'epoch', 'schema'}, 'metadata set')
    meta = dict(meta_rows)
    epoch, schema = integer(meta['epoch'], MAX_EPOCH), integer(meta['schema'])
    if after_metadata:
        after_metadata()
    sources = {}
    for sid, generation, payload in db.execute('SELECT id,generation,payload FROM sources ORDER BY id'):
        sid = identifier(sid)
        require(sid not in sources, 'duplicate source')
        sources[sid] = dict(id=sid, generation=integer(generation, epoch), payload=text(payload))
    facts = {}
    for fid, payload, stamp in db.execute('SELECT id,payload,schema FROM facts ORDER BY id'):
        fid = identifier(fid)
        require(fid not in facts and integer(stamp) == schema, 'fact identity/schema')
        facts[fid] = dict(id=fid, payload=text(payload), schema=stamp, dependencies=[])
    edges = set()
    # LEFT JOIN makes orphan dependency groups visible instead of hiding them.
    for fid, sid, generation, live_fid, live_sid, live_gen in db.execute('''
            SELECT d.fact_id,d.source_id,d.generation,f.id,s.id,s.generation
            FROM dependencies d LEFT JOIN facts f ON f.id=d.fact_id
            LEFT JOIN sources s ON s.id=d.source_id ORDER BY d.fact_id,d.source_id'''):
        identifier(fid); identifier(sid); integer(generation)
        require(live_fid is not None and live_sid is not None and generation == live_gen,
                'orphan/stale dependency')
        require((fid, sid) not in edges, 'duplicate dependency')
        edges.add((fid, sid))
        facts[fid]['dependencies'].append(dict(source=sid, generation=generation))
    require(all(row['dependencies'] for row in facts.values()), 'empty dependency group')
    reverse = {sid: [] for sid in sources}
    for sid, fid in db.execute('''SELECT s.id,d.fact_id FROM sources s
            LEFT JOIN dependencies d ON d.source_id=s.id ORDER BY s.id,d.fact_id'''):
        if fid is not None:
            reverse[sid].append(fid)
    return dict(epoch=epoch, schema=schema, sources=sources, facts=facts, reverse=reverse)


def read_database(path, after_metadata=None):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=0)
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA trusted_schema=OFF')
        db.execute('BEGIN')
        return read_transaction(db, after_metadata)
    finally:
        db.rollback()
        db.close()


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def publish_export(path, value):
    """Synchronize one logical file, not a DB/export pair or authentication root."""
    require(os.name == 'posix' and hasattr(os, 'O_DIRECTORY'),
            'POSIX directory synchronization required; no output was published')
    path = Path(path)
    pending = path.with_name('.' + path.name + '.pending')
    with pending.open('xb') as stream:
        data = canonical(value)
        require(stream.write(data) == len(data), 'incomplete export write')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(pending, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return len(data)


def export_database(database, export):
    value = read_database(database)
    publish_export(export, value)
    return value


class LogicalSQL:
    """Independent transition lowering; invalidation is an ordinary SQL query."""
    def __init__(self, path):
        self.path = Path(path)
        self.db = sqlite3.connect(self.path, timeout=0)
        require(self.db.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal', 'WAL unavailable')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA wal_autocheckpoint=0')
        self.db.execute('PRAGMA busy_timeout=0')

    @classmethod
    def create(cls, path, value):
        path = Path(path)
        require(not path.exists(), 'fresh database required; never replace an existing target')
        captured = json.loads(canonical(value))
        require(set(captured) == {'epoch', 'schema', 'sources', 'facts', 'reverse'}, 'state shape')
        instance = cls(path)
        try:
            instance.db.executescript(SCHEMA)
            instance.db.execute('BEGIN IMMEDIATE')
            instance.db.executemany('INSERT INTO meta VALUES(?,?)', [('epoch', integer(captured['epoch'], MAX_EPOCH)), ('schema', integer(captured['schema']))])
            for sid, row in captured['sources'].items():
                require(set(row) == {'id', 'generation', 'payload'} and row['id'] == sid, 'source shape/key')
                instance.db.execute('INSERT INTO sources VALUES(?,?,?)', (identifier(sid), integer(row['generation']), text(row['payload'])))
            for fid, row in captured['facts'].items():
                require(set(row) == {'id', 'payload', 'schema', 'dependencies'} and row['id'] == fid, 'fact shape/key')
                instance.db.execute('INSERT INTO facts VALUES(?,?,?)', (identifier(fid), text(row['payload']), integer(row['schema'])))
                require(type(row['dependencies']) is list, 'dependency list')
                for edge in row['dependencies']:
                    require(set(edge) == {'source', 'generation'}, 'edge shape')
                    instance.db.execute('INSERT INTO dependencies VALUES(?,?,?)', (fid, identifier(edge['source']), integer(edge['generation'])))
            # Never silently repair a wrong serialized reverse or dependency order.
            require(read_transaction(instance.db) == captured, 'input differs from complete SQL decode')
            instance.db.commit()
            return instance
        except BaseException:
            instance.db.rollback()
            instance.close()
            raise

    def apply(self, transaction):
        tx = json.loads(canonical(transaction))
        require(set(tx) == {'source_changes', 'fact_replacements', 'fact_retractions', 'new_schema'}, 'transaction shape')
        require(type(tx['source_changes']) is dict and type(tx['fact_replacements']) is list and type(tx['fact_retractions']) is list, 'transaction containers')
        changed = {identifier(k): None if v is None else text(v) for k, v in tx['source_changes'].items()}
        retracted = [identifier(k) for k in tx['fact_retractions']]
        require(len(retracted) == len(set(retracted)), 'duplicate retraction')
        replacements = {}
        for row in tx['fact_replacements']:
            require(set(row) == {'id', 'payload', 'sources'}, 'replacement shape')
            fid = identifier(row['id'])
            require(fid not in replacements and type(row['sources']) is list and row['sources'], 'replacement identity/dependencies')
            ids = [identifier(k) for k in row['sources']]
            require(len(ids) == len(set(ids)), 'duplicate replacement dependency')
            replacements[fid] = (text(row['payload']), sorted(ids))
        new_schema = tx['new_schema']
        if new_schema is not None:
            integer(new_schema)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            old = dict(self.db.execute('SELECT key,value FROM meta'))
            epoch = integer(old['epoch'] + 1, MAX_EPOCH)
            schema = old['schema'] if new_schema is None else new_schema
            invalid = set(retracted) | set(replacements)
            if schema != old['schema']:
                invalid.update(r[0] for r in self.db.execute('SELECT id FROM facts'))
            for sid in changed:
                invalid.update(r[0] for r in self.db.execute('SELECT fact_id FROM dependencies WHERE source_id=?', (sid,)))
            for fid in sorted(invalid):
                self.db.execute('DELETE FROM dependencies WHERE fact_id=?', (fid,))
                self.db.execute('DELETE FROM facts WHERE id=?', (fid,))
            for sid, payload in changed.items():
                if payload is None:
                    self.db.execute('DELETE FROM sources WHERE id=?', (sid,))
                else:
                    self.db.execute('INSERT INTO sources VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET generation=excluded.generation,payload=excluded.payload', (sid, epoch, payload))
            for fid, (payload, source_ids) in sorted(replacements.items()):
                self.db.execute('INSERT INTO facts VALUES(?,?,?)', (fid, payload, schema))
                for sid in source_ids:
                    row = self.db.execute('SELECT generation FROM sources WHERE id=?', (sid,)).fetchone()
                    require(row is not None, 'replacement references absent source')
                    self.db.execute('INSERT INTO dependencies VALUES(?,?,?)', (fid, sid, row[0]))
            self.db.execute('UPDATE meta SET value=? WHERE key=?', (epoch, 'epoch'))
            self.db.execute('UPDATE meta SET value=? WHERE key=?', (schema, 'schema'))
            result = read_transaction(self.db)
            self.db.commit()
            return result
        except BaseException:
            self.db.rollback()
            raise

    def close(self):
        self.db.close()
