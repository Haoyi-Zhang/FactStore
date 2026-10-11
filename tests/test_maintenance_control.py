import unittest
from experiments.maintenance_control import run


class MaintenanceTests(unittest.TestCase):
    def test_checkpoint_and_history_keep_exact_endpoint(self):
        row = run(4, 3)
        self.assertTrue(row["endpoint_equal"])
        self.assertEqual(row["checkpoint_result"][0], 0)
        self.assertEqual(row["retained_endpoints_all"], 4)
        self.assertGreater(row["full_image_export_all_endpoints_bytes"],
                           row["full_image_export_current_endpoint_bytes"])

    def test_reject_invalid_size(self):
        with self.assertRaises(ValueError):
            run(1, 2)
