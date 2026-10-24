"""Narrow admission/coverage regressions; no storage synchronization is mocked."""
import json
from pathlib import Path
import tempfile
import unittest

from experiments import fresh_run


class FreshRunTests(unittest.TestCase):
    def test_existing_132_methods_are_statically_inventoried(self):
        names = fresh_run.source_inventory(fresh_run.ROOT)
        existing = fresh_run.baseline_inventory(names)
        self.assertEqual(132, len(existing))
        wrappers = [n for n in names if n.startswith("tests.test_primary_wrapper.")]
        self.assertEqual(5, len(wrappers))
        maintenance = [n for n in names if n.startswith("tests.test_maintenance_control.")]
        self.assertEqual(2, len(maintenance))
        compressed = {n for n in names if n.startswith("tests.test_compressed_records.")}
        # The original six bounded-decoder checks remain; the scientific
        # SQLite cohort adds six admission checks, not a new historical run.
        expected_compressed = {
            "tests.test_compressed_records.CompressedRecordTests." + method
            for method in (
                "test_valid_bounded_record_binds", "test_decode_bound_is_enforced",
                "test_duplicate_and_nonfinite_json_reject", "test_truncated_gzip_rejects",
                "test_unknown_case_and_missing_update_reject",
                "test_typed_counts_and_full_summary_binding")
        } | {
            "tests.test_compressed_records.SQLiteFanoutAdmissionTests." + method
            for method in (
                "test_exact_twelve_source_bound_scientific_records_are_admitted",
                "test_missing_or_unrecognized_compressed_case_rejects",
                "test_measured_source_cannot_be_rebound",
                "test_altered_raw_transaction_rejects_even_with_unchanged_counts",
                "test_duplicate_summary_identity_and_changed_median_reject",
                "test_real_package_gate_still_rejects_unadmitted_archives")
        }
        self.assertEqual(expected_compressed, compressed)
        self.assertEqual(len(names), len(set(names)))

    def test_baseline_split_retains_extensions_in_current_inventory(self):
        names = fresh_run.source_inventory(fresh_run.ROOT)
        added = [name for name in names if name.startswith('tests.test_historical_admission.')]
        self.assertEqual(7, len(added))
        baseline = fresh_run.baseline_inventory(names)
        self.assertTrue(set(added) <= set(names))
        self.assertFalse(set(added) & set(baseline))
        for name in added:
            self.assertEqual(baseline, fresh_run.baseline_inventory([n for n in names if n != name]))

    def test_exhausted_old_ledger_is_read_only(self):
        path = fresh_run.ROOT / "resource-accounting.json"
        before = path.read_bytes()
        ledger = fresh_run.old_ledger(fresh_run.ROOT)
        self.assertEqual(0, ledger["remaining_experiment_allowance_seconds"])
        self.assertEqual(before, path.read_bytes())

    def test_reset_ledger_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "resource-accounting.json").write_text(json.dumps({
                "campaign_cpu_upper_bound_seconds": 0,
                "remaining_experiment_allowance_seconds": 28800,
                "remaining_repair_reproduction_allowance_seconds": 7200}), encoding="utf-8")
            with self.assertRaises(ValueError):
                fresh_run.old_ledger(root)

    def test_raw_output_cannot_be_in_deliverable(self):
        with self.assertRaises(ValueError):
            fresh_run.new_output(fresh_run.ROOT / "must-not-be-created")
        self.assertFalse((fresh_run.ROOT / "must-not-be-created").exists())

    def test_existing_attempt_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "attempt"
            fresh_run.new_output(target)
            with self.assertRaises(FileExistsError):
                fresh_run.new_output(target)

    def test_source_digest_changes_on_byte_or_membership_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "a.py").write_text("x = 1\n", encoding="utf-8")
            first = fresh_run.source_hashes(root)
            (root / "a.py").write_text("x = 2\n", encoding="utf-8")
            self.assertNotEqual(first, fresh_run.source_hashes(root))
            second = fresh_run.source_hashes(root)
            (root / "b.py").write_text("x = 2\n", encoding="utf-8")
            self.assertNotEqual(second, fresh_run.source_hashes(root))

    def test_fixed_schedule_excludes_throughput_and_external_inputs(self):
        import time
        self.assertEqual(("unit", "finite", "sqlite-strict", "crash", "joint", "retained"), fresh_run.STAGES)
        self.assertLessEqual(fresh_run.WHOLE_SECONDS, 850)
        self.assertLessEqual(fresh_run.COMMAND_SECONDS, 175)
        self.assertEqual(780, fresh_run.COMMAND_SECONDS * len(fresh_run.STAGES))
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            result = fresh_run.execute("finite", output, time.monotonic() - 1)
            self.assertEqual("WHOLE_LIMIT_NOT_LAUNCHED", result["status"])
            self.assertFalse(result["refunded"])
            self.assertTrue((output / "finite/stdout.txt").is_file())
            self.assertTrue((output / "finite/stderr.txt").is_file())

    def test_json_records_use_lf_and_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "record.json"
            fresh_run.write_json(path, {"status": "RESERVED", "refunded": False})
            self.assertNotIn(b"\r\n", path.read_bytes())
            self.assertEqual({"status": "RESERVED", "refunded": False}, json.loads(path.read_text()))
