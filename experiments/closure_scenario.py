"""Owned SQL snapshot contrast motivated by a documented analyzer version mismatch.

This does not run clangd or reproduce issue #1088. The issue supplies the
version-selection use case; this program executes the paper's distinct SQL
closure counterexample on benign source/fact payloads.
"""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile

from frontierstore.baselines import _sqlite_schema,_insert_full_state
from frontierstore.model import (State,Transaction,apply_transaction,bootstrap_state,
    check_closure,states_equal,Source)
from experiments.history_lifecycle import read_transaction

def scenario():
    old=bootstrap_state({'header':'header-v1','client':'client-v1'},
        [{'id':'diagnostic','payload':'diagnostic-v1','sources':['header']}])
    new,_=apply_transaction(old,Transaction(source_changes={'client':'client-v2'},
        fact_replacements=[{'id':'diagnostic','payload':'diagnostic-v2','sources':['header']}]))
    results=[]
    for held in (False,True):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'state.sqlite'
            writer=sqlite3.connect(path)
            reader=sqlite3.connect(path)
            try:
                writer.execute('PRAGMA journal_mode=WAL')
                writer.execute('PRAGMA synchronous=FULL')
                _sqlite_schema(writer)
                _insert_full_state(writer,old); writer.commit()
                if held: reader.execute('BEGIN')
                meta=dict(reader.execute('SELECT key,value FROM meta'))
                sources={i:Source(i,g,p) for i,g,p in reader.execute('SELECT id,generation,payload FROM sources')}
                # One atomic writer commit between the reader's SELECT groups.
                writer.execute('BEGIN IMMEDIATE')
                _insert_full_state(writer,new); writer.commit()
                if not held: reader.execute('BEGIN')
                tail=read_transaction(reader)
                mixed=State(meta['epoch'],meta['schema'],sources,tail.facts,tail.reverse)
                closed=not check_closure(mixed)
                endpoint=states_equal(mixed,old) or states_equal(mixed,new)
                if not closed or endpoint!=held:
                    raise ValueError('SQL closure/snapshot contrast failed')
                results.append(dict(one_enclosing_read_transaction=held,closed=closed,
                    equals_committed_endpoint=endpoint,observation=mixed.as_dict()))
            finally:
                reader.close(); writer.close()
    return dict(provenance='owned SQL schedule; not an analyzer reproduction',old=old.as_dict(),
        new=new.as_dict(),results=results,
        motivating_source='https://github.com/clangd/clangd/issues/1088',
        source_scope='reported editor/disk and multi-file event-order mismatch; no storage defect or exact closure schedule inferred')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    result=scenario()
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result['results']))
