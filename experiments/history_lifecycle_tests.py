"""Separately invoked SQLite-API controls; not additions to the historical suite."""
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from experiments.history_lifecycle import CANDIDATE, owned, read_transaction, run_case


class HistoryLifecycleTests(unittest.TestCase):
    def test_reader_lease_checkpoint_and_actual_archive_collection(self):
        with tempfile.TemporaryDirectory(dir=CANDIDATE) as temporary:
            row=run_case(Path(temporary)/'lease',4,4,'leases')
            held=[check for check in row['checkpoints'] if check['event']=='held-reader']
            self.assertEqual([check['result'][0] for check in held],[0,1])
            self.assertTrue(all(not check['all_frames_checkpointed'] for check in held))
            released=[check for check in row['checkpoints'] if check['event']=='reader-released'][0]
            self.assertTrue(released['wal_truncated'])
            self.assertEqual(row['retained_epochs'],[5])
            self.assertEqual(sum(len(record.get('collection',{}).get('removed',[])) for record in row['records']),8)
            self.assertEqual(sorted(name for name in row['final']['files'] if name.startswith('history/')),
                ['history/audit-00000005.json','history/image-00000005.sqlite'])
            self.assertTrue(row['endpoint_equal'] and row['held_endpoint_equal'])
            for record in row['records']:
                if record['event']=='update' and record['historical'] is not None:
                    self.assertEqual(record['historical']['epoch'],1)
                    self.assertEqual(record['current']['epoch'],record['index']+1)

    def test_policies_share_exact_updates_and_required_lease(self):
        with tempfile.TemporaryDirectory(dir=CANDIDATE) as temporary:
            all_row=run_case(Path(temporary)/'all',4,4,'all')
            lease_row=run_case(Path(temporary)/'lease',4,4,'leases')
            self.assertEqual(all_row['retained_epochs'],[1,2,3,4,5])
            self.assertGreater(all_row['final']['archive_bytes'],lease_row['final']['archive_bytes'])
            for left,right in zip(all_row['records'],lease_row['records']):
                self.assertEqual(left['event'],right['event'])
                if left['event']=='update':
                    for key in ('transaction','current','historical'):
                        self.assertEqual(left[key],right[key])
            self.assertEqual(all_row['settings'],lease_row['settings'])

    def test_reader_requires_one_explicit_transaction(self):
        with sqlite3.connect(':memory:') as db:
            with self.assertRaisesRegex(RuntimeError,'explicit read transaction'):
                read_transaction(db)

    def test_input_and_output_bounds_reject_before_creation(self):
        with tempfile.TemporaryDirectory(dir=CANDIDATE) as temporary:
            root=Path(temporary)
            for sources,updates,policy in ((True,4,'all'),(501,4,'all'),(4,13,'leases'),(4,4,'unknown')):
                with self.assertRaises(ValueError):
                    run_case(root/'invalid',sources,updates,policy)
                self.assertFalse((root/'invalid').exists())
        with self.assertRaises(ValueError):
            owned('D:/not-owned-p021-control')

    def test_imported_case_refuses_optimized_execution_before_creation(self):
        with tempfile.TemporaryDirectory(dir=CANDIDATE) as temporary:
            output=Path(temporary)/'optimized-case'
            code=('from pathlib import Path; '
                  'from experiments.history_lifecycle import run_case; '
                  f'run_case(Path({str(output)!r}),4,4,"leases")')
            result=subprocess.run([sys.executable,'-O','-B','-c',code],
                cwd=CANDIDATE/'artifact',capture_output=True,text=True,timeout=30)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('assertions required',result.stderr)
            self.assertFalse(output.exists())

    def test_command_refuses_optimized_execution_before_creation(self):
        with tempfile.TemporaryDirectory(dir=CANDIDATE) as temporary:
            output=Path(temporary)/'optimized-command'
            result=subprocess.run([sys.executable,'-O','-B','-m',
                'experiments.history_lifecycle','--authorize-new-study',
                '--out',str(output)],cwd=CANDIDATE/'artifact',
                capture_output=True,text=True,timeout=30)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('assertions required',result.stderr)
            self.assertFalse(output.exists())


if __name__=='__main__':
    unittest.main()
