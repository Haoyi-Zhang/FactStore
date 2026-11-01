"""Retained paired summaries preserve marginal/paired distinctions."""
import unittest
from experiments.report_native_pairs import summarize,tables

class NativePairSummaryTests(unittest.TestCase):
    def test_native_paired_reversal_and_phase_reconciliation(self):
        result=summarize();row=result['pairs'][0]
        self.assertEqual((row['width'],row['audit_every']),(2,1))
        self.assertEqual((row['frontier_wins'],row['sqlite_r_wins']),(1,2))
        self.assertAlmostEqual(row['median_difference_ms'],34.806935,places=3)
        self.assertEqual(len(result['phases']),12)
        self.assertTrue(all(r['median_residual_ms']>=0 for r in result['phases']))
        self.assertIn('Paired native',tables(result));self.assertEqual(result['measurements_rerun'],0)

if __name__=='__main__':unittest.main()
