"""Portable wiring gates plus an explicit Linux-only actual-writer smoke test."""
from collections import Counter
import copy
import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from experiments import native_fanout_cadence as native


class NativeFanoutTests(unittest.TestCase):
    def test_engine_order_is_counterbalanced_within_each_configuration(self):
        rows=native.schedule()
        self.assertEqual(len(rows),36)
        orders=[]
        for repetition in range(3):
            for width in (2,8):
                for cadence in (1,4):
                    group=[r for r in rows if (r['repetition'],r['dependency_width'],r['audit_every'])==
                           (repetition,width,cadence)]
                    orders.append(tuple(r['engine'] for r in group))
        self.assertEqual(sorted(Counter(orders).values()),[2]*6)
        for width in (2,8):
            for cadence in (1,4):
                for engine in native.ENGINES:
                    self.assertEqual(sorted(r['position'] for r in rows if
                        (r['dependency_width'],r['audit_every'],r['engine'])==(width,cadence,engine)),[0,1,2])

    def test_fresh_output_cannot_replace_delivery_or_existing_evidence(self):
        with self.assertRaises(ValueError):
            native.reserve(native.ROOT/'results/native-fanout')
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileExistsError):
                native.reserve(Path(directory))

    def test_existing_workflow_checks_are_preserved_and_native_job_is_bounded(self):
        text=(native.ROOT/'.github/workflows/scientific-checks.yml').read_text(encoding='utf-8')
        job=text.split('  native-fanout:\n',1)[1]
        self.assertIn('runs-on: ubuntu-24.04',job)
        self.assertIn('timeout-minutes: 10',job)
        self.assertIn('$RUNNER_TEMP/p021-native-fanout-',job)
        self.assertIn('[[ -e "$out" || -e "$logs" ]]',job)
        self.assertIn('--engines frontier sqlite_n sqlite_r',job)
        self.assertIn('timeout 360s',job)
        self.assertIn('if: always()',job)
        self.assertIn('${{ env.NATIVE_FANOUT_OUT }}/',job)
        self.assertNotIn('pip install',job)
        for command in ('experiments.fresh_run','experiments.maintenance_control','tests.test_historical_admission'):
            self.assertIn(command,text.split('  native-fanout:\n',1)[0])

    def test_sqlite_only_summary_cannot_be_accepted_as_three_engine_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            (path/'summary.json').write_text(json.dumps(dict(status='NATIVE_CASES_COMPLETED',
                native_three_engine_costs_measured=False)),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'three-engine'):
                native.verify(path)

    def test_retained_windows_api_cohort_is_not_relabelled_native(self):
        with self.assertRaisesRegex(ValueError,'three-engine'):
            native.verify(native.ROOT/'results/sqlite-fanout')

    def test_json_transaction_roundtrip_is_exact_and_mutations_still_reject(self):
        state=native.make_synthetic_state(64,dependency_width=8)
        tx=native.make_update(state,batch=8,seed=101)
        projected=native.transaction_dict(tx)
        saved=json.loads(json.dumps(projected))
        self.assertEqual(projected,saved)
        self.assertIsInstance(saved['fact_replacements'][0]['sources'],list)
        self.assertEqual(native.transaction_dict(native.replay_transaction(state,{'transaction':saved},1)),saved)
        for field in ('payload','sources'):
            changed=copy.deepcopy(saved)
            changed['fact_replacements'][0][field]=('changed payload' if field=='payload' else ['s999999'])
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'unequal input transition'):
                native.replay_transaction(state,{'transaction':changed},1)

    def test_all_declared_streams_replay_after_json_and_maintenance_epochs(self):
        # Pure codec/model regression, not a replacement for a native run.
        for width in (2,8):
            state=native.make_synthetic_state(64,dependency_width=width)
            for index in range(1,9):
                tx=native.make_update(state,batch=8,seed=100+index)
                event={'transaction':json.loads(json.dumps(native.transaction_dict(tx)))}
                replayed=native.replay_transaction(state,event,index)
                state,_=native.apply_transaction(state,replayed)
                if index in (4,8):
                    state,_=native.apply_transaction(state,native.Transaction())
            self.assertEqual(state.epoch,11)

    def test_source_gate_precedes_contents_only_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)
            native.write_json(out/'summary.json',dict(status='NATIVE_CASES_COMPLETED',
                native_three_engine_costs_measured=True))
            wrong=native.bindings()
            wrong['experiments/native_fanout_cadence.py']='0'*64
            native.write_json(out/'source-before.json',wrong)
            native.write_json(out/'source-after.json',wrong)
            with patch.object(native,'replay_cohort_contents') as contents:
                with self.assertRaisesRegex(ValueError,'source binding mismatch'):
                    native.verify(out)
                contents.assert_not_called()

    @unittest.skipUnless(sys.platform.startswith('linux'),'requires actual native Linux writers; no Windows substitution')
    def test_actual_native_writers_match_updates_maintenance_leases_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            cohorts=[]
            for engine in native.ENGINES:
                path=Path(directory)/engine
                result=native.run_case(path,dict(engine=engine,repetition=0,dependency_width=2,
                    audit_every=2,position=0),sources=8,updates=4)
                self.assertTrue(result['accepted'])
                with gzip.open(path/'trace.jsonl.gz','rt',encoding='utf-8') as stream:
                    rows=[json.loads(line) for line in stream]
                self.assertEqual(rows[-1]['current']['epoch'],7)
                self.assertTrue(all(row['historical']==rows[0]['input'] for row in rows[1:3]))
                self.assertTrue(all(row['historical'] is None for row in rows[3:-1]))
                self.assertTrue(all(row['maintenance'] is not None for row in (rows[2],rows[4])))
                state=native.baselines._state_from_dict(rows[0]['input'])
                for index,row in enumerate(rows[1:-1],1):
                    tx=native.replay_transaction(state,row,index)
                    state,_=native.apply_transaction(state,tx)
                    self.assertEqual(row['current_after_update'],state.as_dict())
                    if row['maintenance'] is not None:
                        state,_=native.apply_transaction(state,native.Transaction())
                    self.assertEqual(row['current'],state.as_dict())
                cohorts.append([row['current'] for row in rows])
            self.assertEqual(cohorts[0],cohorts[1]); self.assertEqual(cohorts[1],cohorts[2])


if __name__=='__main__':
    unittest.main()
