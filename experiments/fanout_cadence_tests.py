from pathlib import Path
import tempfile
import unittest

from experiments.history_lifecycle import CANDIDATE,run_case

class FanoutCadenceTests(unittest.TestCase):
    def test_sql_closure_is_not_endpoint_but_enclosing_snapshot_is(self):
        from experiments.closure_scenario import scenario
        rows=scenario()['results']
        self.assertTrue(all(r['closed'] for r in rows))
        self.assertEqual([r['equals_committed_endpoint'] for r in rows],[False,True])

    def test_fanout_and_scheduled_audits_preserve_endpoints_and_leases(self):
        with tempfile.TemporaryDirectory(dir=CANDIDATE) as folder:
            rows=[run_case(Path(folder)/f'w{w}a{a}',16,4,'leases',dependency_width=w,audit_every=a)
                  for w in (2,8) for a in (1,4)]
            self.assertEqual([r['audit_checks'] for r in rows],[4,1,4,1])
            self.assertTrue(all(r['retained_epochs']==[5] and r['endpoint_equal'] and r['held_endpoint_equal'] for r in rows))
            for width in (2,8):
                group=[r for r in rows if r['dependency_width']==width]
                updates=[[x for x in r['records'] if x['event']=='update'] for r in group]
                self.assertEqual([x['current'] for x in updates[0]],[x['current'] for x in updates[1]])
                self.assertTrue(all(x['dependency_edges']==16*6*width for x in updates[0]))

    def test_bad_width_or_cadence_refused_before_creation(self):
        with tempfile.TemporaryDirectory(dir=CANDIDATE) as folder:
            path=Path(folder)/'bad'
            for width,cadence in ((0,1),(5,1),(2,0),(2,5)):
                with self.assertRaises(ValueError): run_case(path,4,4,'leases',dependency_width=width,audit_every=cadence)
                self.assertFalse(path.exists())

if __name__=='__main__': unittest.main()
