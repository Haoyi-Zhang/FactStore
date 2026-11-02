"""An undated manual remains documentation, not a falsely dated preprint."""
import unittest
from verify_publication_references import valid_bibliography_year

class ManualYearTests(unittest.TestCase):
    def test_undated_manual_requires_locator_and_access_note(self):
        good=dict(entry_type='manual',year='n.d',note='Accessed November 1, 2026',url='https://glean.software/docs/')
        self.assertTrue(valid_bibliography_year(good))
        for field,value in [('entry_type','misc'),('entry_type','article'),('note',''),('url',''),('year','2026?'),('year','n.d.')]:
            bad={**good,field:value}
            self.assertFalse(valid_bibliography_year(bad))
        self.assertTrue(valid_bibliography_year(dict(entry_type='article',year='2026')))

if __name__=='__main__':unittest.main()
