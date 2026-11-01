"""Active bibliographic inventory is not the old rendering cohort."""
import unittest
from experiments.verify_artifact import publication_inventory_counts


class PublicationInventoryScopeTests(unittest.TestCase):
    def test_active_count_does_not_inherit_old_render_count(self):
        rendering = {"reference_count": 62, "review_pages": 26, "tos_layout_pages": 27}
        counts = publication_inventory_counts([{} for _ in range(65)], rendering)
        self.assertEqual(counts, {
            "reference_audit_rows": 65,
            "retained_rendering_reference_rows": 62,
            "retained_rendering_review_pages": 26,
            "retained_rendering_tos_layout_pages": 27,
        })
        self.assertEqual(rendering["reference_count"], 62)

    def test_active_inventory_changes_without_rewriting_rendering(self):
        rendering = {"reference_count": 62, "review_pages": 26, "tos_layout_pages": 27}
        before = publication_inventory_counts([{}], rendering)
        after = publication_inventory_counts([{}, {}], rendering)
        self.assertEqual(after["reference_audit_rows"], before["reference_audit_rows"] + 1)
        for key in before.keys() - {"reference_audit_rows"}:
            self.assertEqual(before[key], after[key])
