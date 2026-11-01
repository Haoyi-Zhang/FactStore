"""Recheck source-driven transitions and retained final native store bytes."""
import hashlib
import json
from pathlib import Path,PurePosixPath
import tempfile
import unittest
import zipfile
from experiments.import_lifecycle import load_sources,logical_projection,measure,counts
from experiments.native_fanout_cadence import transaction_dict
from frontierstore.import_inventory import bootstrap,refresh
from frontierstore.model import Transaction,apply_transaction

ROOT=Path(__file__).resolve().parents[1]
class ImportLifecycleTests(unittest.TestCase):
    def test_complete_captured_native_lifecycle(self):
        data=ROOT/'results/import-lifecycle';registration=json.loads((data/'registration.json').read_text())
        for rel,digest in registration['source_bindings'].items():
            self.assertEqual(hashlib.sha256((ROOT/rel).read_bytes()).hexdigest(),digest)
        manifest=json.loads((data/'native-evidence.json').read_text())
        bundles=load_sources();old,new=bundles['2.1.1'],bundles['2.2.0']
        with tempfile.TemporaryDirectory() as directory,zipfile.ZipFile(data/'native-evidence.zip') as z:
            self.assertEqual(set(z.namelist()),set(manifest));self.assertEqual(len(z.namelist()),len(manifest))
            self.assertLess(sum(r['bytes'] for r in manifest.values()),128*1024*1024)
            root=Path(directory)
            for rel,identity in manifest.items():
                self.assertFalse(PurePosixPath(rel).is_absolute());self.assertNotIn('..',PurePosixPath(rel).parts)
                raw=z.read(rel);self.assertEqual(len(raw),identity['bytes']);self.assertEqual(hashlib.sha256(raw).hexdigest(),identity['sha256'])
                target=root/rel;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
            for engine in ('frontier','sqlite_n','sqlite_r'):
                current=bootstrap(old);modules=dict(old);trace=json.loads((root/engine/'trace.json').read_text())
                self.assertEqual([e['module'] for e in trace],registration['changed_modules'])
                for event in trace:
                    modules[event['module']]=new[event['module']]
                    events,observe=measure();updated,delta,tx=refresh(current,modules,observe=observe)
                    full,observe_full=measure();expected=bootstrap(modules,observe=observe_full)
                    self.assertEqual(event['transaction'],transaction_dict(tx));self.assertEqual(event['current_state'],updated.as_dict())
                    self.assertEqual(event['incremental_read_events'],events);self.assertEqual(event['full_read_events'],full)
                    self.assertEqual(event['incremental'],counts(events));self.assertEqual(event['full'],counts(full))
                    self.assertEqual(logical_projection(updated),logical_projection(expected))
                    current=updated
                    if 'maintenance' in event:current,_=apply_transaction(current,Transaction())
                current,_=apply_transaction(current,Transaction())
                self.assertEqual(json.loads((root/engine/'final-state.json').read_text()),current.as_dict())
                path=root/engine/'store'
                if engine=='frontier':
                    from frontierstore.store import FrontierStore
                    actual=FrontierStore(path).state.as_dict()
                else:
                    from frontierstore.baselines import SQLiteNormalized,SQLiteRootedAudit
                    owner=(SQLiteNormalized if engine=='sqlite_n' else SQLiteRootedAudit)(path)
                    try:actual=owner.durable_state().as_dict()
                    finally:owner.close()
                self.assertEqual(actual,current.as_dict())
if __name__=='__main__':unittest.main()
