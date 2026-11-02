"""Tiny owned receiver fixtures plus exact calculations on unchanged saved data.

No fixture executes a store, analyzer, workload generator or campaign. The
retained-data integration test only decodes, binds and summarizes saved bytes.
"""
import copy
import csv
import hashlib
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest.mock import patch

from experiments import paper_evidence_admission as admission
from experiments import report_import_lifecycle, report_native_pairs, report_sqlite_fanout

ROOT = Path(__file__).resolve().parents[1]


def api_fixture():
    rows, raw_cases, order = [], {}, []
    for repetition in range(3):
        configs = [(w, a) for w in (2, 8) for a in (1, 4)]
        random.Random(21024 + repetition).shuffle(configs)
        for width, cadence in configs:
            key = (repetition, width, cadence)
            order.append(f'r{repetition}-w{width}-a{cadence}')
            events = [dict(event='bootstrap', input={})]
            for i in range(1, 9):
                events.append(dict(event='update', index=i, dependency_edges=384 * width,
                    fact_replacements=1, invalidated_facts=1, scheduled_audit=i % cadence == 0,
                    historical={} if i <= 4 else None, current={'index': i},
                    collection={'removed': ['owned-a', 'owned-b']}))
                if i == 4:events.append(dict(event='release'))
            events.append(dict(event='closed-reopened', current={'index': 8}))
            stage = {k: 1 for k in admission.API_PHASES};stage['checkpoint'] = 5
            zero_footprint=dict(files={},total_bytes=0,db_wal_shm_bytes=0,archive_bytes=0)
            raw = dict(repetition=repetition,dependency_width=width,audit_every=cadence,
                sources=64,facts=384,updates=8,release_at=4,raw_record_count=11,
                audit_checks=8//cadence,endpoint_equal=True,held_endpoint_equal=True,
                policy='leases',retained_epochs=[9],stage_ns=stage,lifecycle_elapsed_ns=100,
                process_cpu_ns=50,peak_sampled_bytes=1,records=events,
                checkpoints=[dict(elapsed_ns=1) for _ in range(5)],
                final=copy.deepcopy(zero_footprint),before_close=copy.deepcopy(zero_footprint))
            raw_cases[key] = raw
            row = {k: copy.deepcopy(v) for k,v in raw.items() if k not in ('records','checkpoints')}
            row.update(mean_replaced_facts=1,total_archive_files_removed=16)
            rows.append(row)
    aggregates = []
    for w in (2,8):
        for a in (1,4):
            aggregates.append(dict(dependency_width=w,audit_every=a,repetitions=3,
                mean_replaced_facts=1,audit_checks=8//a,median_lifecycle_ms=.0001,
                median_stage_ms={k:(5 if k=='checkpoint' else 1)/1e6 for k in admission.API_PHASES},final_bytes=0))
    return dict(cases=12,current_updates=96,held_reads=48,reopens=12,source_unchanged=True,
                rows=rows,schedule=order,aggregates=aggregates),raw_cases


def native_fixture():
    rows,raw,traces=[],{},{}
    for repetition,width,cadence in sorted(admission.CASES):
        for position,engine in enumerate(admission.ENGINES):
            name=f'r{repetition}-w{width}-a{cadence}-{engine}'
            events=[dict(event='bootstrap',phase_ns={k:1 for k in admission.NATIVE_PHASES})]
            for i in range(1,9):
                events.append(dict(event='update',index=i,scheduled_audit=i%cadence==0,
                    current_after_update={},update_expected={},phase_ns={k:1 for k in admission.NATIVE_PHASES}))
            events.append(dict(event='reopen',current={},expected={},phase_ns={k:1 for k in admission.NATIVE_PHASES}))
            row=dict(repetition=repetition,dependency_width=width,audit_every=cadence,engine=engine,
                position=position,accepted=True,sources=64,updates=8,records=10,
                lifecycle_wall_ns=1000,process_cpu_ns=100,stage_ns={k:10 for k in admission.NATIVE_PHASES})
            raw[name]=copy.deepcopy(row);traces[name]=events;rows.append(dict(row,case=name))
    return dict(status='NATIVE_CASES_COMPLETED',native_three_engine_costs_measured=True,
                source_unchanged=True,rows=rows),raw,traces


def import_fixture():
    changed=['owned.a','owned.b','owned.c','owned.d'];rows=[];raw={};traces={}
    for engine in admission.ENGINES:
        row=dict(engine=engine,updates=4,all_endpoints_equal=True,orderly_reopen_equal=True,
                 incremental_source_reads=4,full_source_reads=4,
                 incremental_source_bytes=4,full_source_bytes=4)
        rows.append(row);raw[engine]=copy.deepcopy(row);events=[]
        for name in changed:
            events.append(dict(module=name,current_equal=True,held_initial_equal=True,
                full_recompute_equal=True,incremental={'source_reads':1,'source_bytes':1,'registry_reads':1},
                full={'source_reads':1,'source_bytes':1,'registry_reads':1},
                incremental_read_events=[dict(id='py.registry',bytes=2),dict(id='owned.source',bytes=1)],
                full_read_events=[dict(id='py.registry',bytes=2),dict(id='owned.source',bytes=1)]))
        traces[engine]=events
    return dict(status='PASS',modules=8,changed_modules=4,engines=3,native_transitions=12,
        released_endpoint_pairs=1,performance_claim=False,natural_intermediate_commit_history=False,
        power_loss_or_cross_process_pins_tested=False,rows=rows),raw,traces,


class ReceiverFixtureTests(unittest.TestCase):
    def test_valid_owned_api_fixture(self):
        admission.validate_api(*api_fixture())

    def test_api_empty_partial_duplicate_and_aggregate_coverage(self):
        for mode in ('empty','partial','duplicate','aggregate'):
            summary,raw=api_fixture()
            if mode=='empty':summary['rows']=[];summary['aggregates']=[]
            if mode=='partial':summary['rows']=summary['rows'][:3];summary['aggregates']=summary['aggregates'][:1]
            if mode=='duplicate':summary['rows'][-1]=copy.deepcopy(summary['rows'][0])
            if mode=='aggregate':summary['aggregates'].pop()
            with self.subTest(mode=mode),self.assertRaises(ValueError):admission.validate_api(summary,raw)

    def test_api_strict_counts_identities_flags_and_phase_costs(self):
        for field,value in [('cases',True),('cases',12.0),('source_unchanged','true')]:
            s,r=api_fixture();s[field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):admission.validate_api(s,r)
        for field,value in [('repetition',0.0),('endpoint_equal','false'),('stage_ns',{}),('lifecycle_elapsed_ns',-1)]:
            s,r=api_fixture();key=next(iter(r));r[key][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):admission.validate_api(s,r)

    def test_api_raw_binding_and_all_aggregate_cells(self):
        for mode in ('raw','median','phase','count'):
            s,r=api_fixture()
            if mode=='raw':next(iter(r.values()))['process_cpu_ns']+=1
            if mode=='median':s['aggregates'][0]['median_lifecycle_ms']+=1
            if mode=='phase':s['aggregates'][0]['median_stage_ms']={}
            if mode=='count':s['current_updates']=1
            with self.subTest(mode=mode),self.assertRaises(ValueError):admission.validate_api(s,r)

    def test_valid_owned_native_fixture(self):
        admission.validate_native(*native_fixture())

    def test_native_accepted_and_typed_identity(self):
        for field,value in [('accepted','false'),('accepted',1),('repetition',0.0),('repetition',False),('lifecycle_wall_ns',True)]:
            s,r,t=native_fixture();s['rows'][0][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):admission.validate_native(s,r,t)

    def test_native_missing_extra_nonfinite_and_negative_phases(self):
        for value in ({},dict(bootstrap=1),{k:float('nan') for k in admission.NATIVE_PHASES},
                      {k:float('inf') for k in admission.NATIVE_PHASES},
                      {k:-1 for k in admission.NATIVE_PHASES},{k:True for k in admission.NATIVE_PHASES},
                      dict({k:1 for k in admission.NATIVE_PHASES},unexpected=0)):
            s,r,t=native_fixture();s['rows'][0]['stage_ns']=value
            with self.subTest(value=value),self.assertRaises(ValueError):admission.validate_native(s,r,t)

    def test_native_matrix_trace_and_raw_binding(self):
        for mode in ('missing','duplicate','raw','trace','sum'):
            s,r,t=native_fixture();name=s['rows'][0]['case']
            if mode=='missing':s['rows'].pop()
            if mode=='duplicate':s['rows'][-1]=copy.deepcopy(s['rows'][0])
            if mode=='raw':r[name]['process_cpu_ns']+=1
            if mode=='trace':t[name].pop()
            if mode=='sum':t[name][0]['phase_ns']['bootstrap']+=1
            with self.subTest(mode=mode),self.assertRaises(ValueError):admission.validate_native(s,r,t)

    def test_valid_owned_import_fixture(self):
        s,r,t=import_fixture();admission.validate_import(s,r,t,dict(modules=8,engines=list(admission.ENGINES),changed_modules=['owned.a','owned.b','owned.c','owned.d']))

    def test_import_coverage_typed_counts_and_flags(self):
        for mode in ('one','duplicate','bool','float','negative','flag','reopen','total'):
            s,r,t=import_fixture();reg=dict(modules=8,engines=list(admission.ENGINES),changed_modules=['owned.a','owned.b','owned.c','owned.d'])
            if mode=='one':s['rows']=s['rows'][:1]
            if mode=='duplicate':s['rows'][-1]=copy.deepcopy(s['rows'][0])
            if mode=='bool':s['rows'][0]['incremental_source_reads']=True
            if mode=='float':s['native_transitions']=12.0
            if mode=='negative':s['rows'][0]['incremental_source_bytes']=-1
            if mode=='flag':s['rows'][0]['all_endpoints_equal']='false'
            if mode=='reopen':s['rows'][0]['orderly_reopen_equal']=1
            if mode=='total':s['native_transitions']=False
            with self.subTest(mode=mode),self.assertRaises(ValueError):admission.validate_import(s,r,t,reg)

    def test_import_archived_summary_and_read_callbacks_binding(self):
        for mode in ('row','trace','count','bytes'):
            s,r,t=import_fixture();reg=dict(modules=8,engines=list(admission.ENGINES),changed_modules=['owned.a','owned.b','owned.c','owned.d'])
            if mode=='row':r['frontier']['full_source_reads']+=1
            if mode=='trace':t['frontier'].pop()
            if mode=='count':t['frontier'][0]['incremental']['source_reads']=True
            if mode=='bytes':t['frontier'][0]['full_read_events'][0]['bytes']=-1
            with self.subTest(mode=mode),self.assertRaises(ValueError):admission.validate_import(s,r,t,reg)

    def test_strict_json_duplicates_and_nonfinite(self):
        for raw in (b'{"x":1,"x":2}',b'{"x":NaN}',b'{"x":Infinity}',b'{"x":1e999}'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):admission.decode(raw)

    def test_source_bindings_do_not_reseal_changed_inputs(self):
        with tempfile.TemporaryDirectory(prefix='factstore-owned-receiver-') as directory:
            root=Path(directory);source=root/'owned.py';source.write_bytes(b'# inert owned fixture\n')
            bindings={'owned.py':hashlib.sha256(source.read_bytes()).hexdigest()}
            admission.source_binding(root,bindings,{'owned.py'},dict(bindings))
            with self.assertRaises(ValueError):admission.source_binding(root,bindings,{'missing.py'})
            with self.assertRaises(ValueError):admission.source_binding(root,bindings,{'owned.py'},{})
            source.write_bytes(b'# changed inert fixture\n')
            with self.assertRaises(ValueError):admission.source_binding(root,bindings,{'owned.py'})

    def test_actual_report_paths_validate_before_output(self):
        # Exercise the real documented CLI receiver with the original F1
        # empty-coverage counterexample, not just its pure validator.
        malformed=dict(cases=12,current_updates=96,held_reads=48,reopens=12,
                       source_unchanged=True,rows=[],aggregates=[])
        with patch.object(admission,'load',return_value=malformed),\
             patch.object(sys,'argv',['receiver','--tex','must-not-exist.tex']),\
             patch.object(Path,'write_text') as write:
            with self.assertRaises(ValueError):report_sqlite_fanout.main()
            write.assert_not_called()
        with patch.object(report_sqlite_fanout,'admit_api',side_effect=ValueError('owned invalid')),\
             patch.object(sys,'argv',['receiver','--tex','must-not-exist.tex']),\
             patch.object(Path,'write_text') as write:
            with self.assertRaises(ValueError):report_sqlite_fanout.main()
            write.assert_not_called()
        with patch.object(report_import_lifecycle,'admit_import',side_effect=ValueError('owned invalid')):
            sink=unittest.mock.Mock()
            with self.assertRaises(ValueError):report_import_lifecycle.render(sink)
            sink.write_text.assert_not_called()
        with patch.object(report_native_pairs,'admit_native',side_effect=ValueError('owned invalid')):
            with self.assertRaises(ValueError):report_native_pairs.summarize()


class RetainedInputTests(unittest.TestCase):
    def test_all_current_inputs_identical_without_native_or_bootstrap(self):
        from experiments.check_paper_inputs import check
        from experiments.check_paper_inputs import native_medians, native_table, import_text
        from experiments import native_fanout_cadence as native
        # These are the four reviewed consumer inputs, not regenerated expected
        # values, with text newlines normalized as in the manuscript preflight.
        # Keep this contract executable in the artifact-only checkout.
        expected = {
            'fanout-cadence.tex': 'c30e72f833b596babe9f02eda9fe7e4f19bd9ba32d916e612ae0ebf7c6d3203f',
            'native-fanout.tex': 'b9a24bf2475835a5a63901fe40b6e293a447112344f88e495b9bea1ab4462e6a',
            'native-paired.tex': 'c17cefeb6ced4c4347ded0e16f3be83e45d4836ad1d8a5a3037858bfae5442da',
            'import-lifecycle-results.tex': 'b24a68cd3eaed63a7f94b01520ce87748221e19b70c2f75a9841fa1d5cb33b56',
        }
        def forbidden(*args,**kwargs):raise AssertionError('native/scientific call forbidden')
        with patch.object(native,'verify',forbidden),patch.object(native,'run',forbidden),\
             patch.object(native,'NativeOwner',forbidden),\
             patch.object(native,'make_synthetic_state',forbidden),patch.object(native,'make_update',forbidden),\
             tempfile.TemporaryDirectory(prefix='factstore-consumer-inputs-') as directory:
            paper = Path(directory)
            contents = {
                'fanout-cadence.tex': report_sqlite_fanout.table(admission.admit_api()),
                'native-fanout.tex': native_table(native_medians(admission.admit_native())),
                'native-paired.tex': report_native_pairs.tables(report_native_pairs.summarize()),
                'import-lifecycle-results.tex': import_text(),
            }
            for name, content in contents.items():
                raw = content.encode('utf-8')
                self.assertEqual(hashlib.sha256(raw).hexdigest(), expected[name], name)
                (paper/name).write_bytes(raw)
            result=check(paper)
        self.assertEqual(result['status'],'PAPER_INPUTS_IDENTICAL')
        self.assertEqual(result['new_campaigns'],0)

    def test_current_ledger_and_build_recipe(self):
        with (ROOT/'claim_evidence_ledger.csv').open(encoding='utf-8',newline='') as f:
            rows={r['claim_id']:r for r in csv.DictReader(f)}
        self.assertIn('65',rows['C32']['manuscript_claim'])
        self.assertNotIn('tos-submission.tex',rows['C32']['implementation_or_test'])
        for key in ('C32','C34','C35','C36','C37'):
            for name in rows[key]['raw_result_path'].split(';'):
                self.assertTrue((ROOT/name).is_file(),name)
    @unittest.skipUnless((ROOT.parent/'paper/README.md').is_file(),
                         'manuscript checkout is not part of the artifact-only repository')
    def test_manuscript_build_recipe_and_installed_inputs(self):
        from experiments.check_paper_inputs import check
        self.assertEqual(check(ROOT.parent/'paper')['status'], 'PAPER_INPUTS_IDENTICAL')
        doc=(ROOT.parent/'paper/README.md').read_text(encoding='utf-8')
        self.assertIn('experiments.check_paper_inputs',doc)
        self.assertNotIn('experiments.fanout_cadence --check',doc)


if __name__=='__main__':unittest.main()
