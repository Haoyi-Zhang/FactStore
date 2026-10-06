"""Data-only negative controls; never import or execute a storage engine."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from experiments import journal_robustness, summarize_current
from experiments.verify_artifact import parse_python_sources, verify_retained_syntax_coverage

ARTIFACT = Path(__file__).resolve().parents[1]


class EvidenceAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="p021-data-admission-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.case = self.root / "current-small"
        shutil.copytree(ARTIFACT / "results/current/current-small", self.case)

    def aggregate(self):
        return summarize_current.aggregate_case("small", "current-small", self.root, "accepted")

    def alter_json(self, name, change) -> None:
        path = self.case / name
        value = json.loads(path.read_text(encoding="utf-8"))
        change(value)
        path.write_text(json.dumps(value), encoding="utf-8")

    def alter_rows(self, change, *, json_too=True) -> None:
        path = self.case / "observations.csv"
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fields, rows = reader.fieldnames, list(reader)
        change(rows)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        if json_too:
            self.alter_json("observations.json", lambda value: change(value["rows"]))

    def test_all_retained_cases_are_admitted_without_changing_rows(self) -> None:
        current = ARTIFACT / "results/current"
        for scale, directory in [("pilot", summarize_current.PILOT), *summarize_current.ACCEPTED.items()]:
            with self.subTest(scale=scale):
                rows = summarize_current.load_admitted_rows(scale, directory, current)
                config = summarize_current.CASE_CONFIGS[scale]
                self.assertEqual(3 * config["repetitions"] * config["updates"], len(rows))

    def test_missing_accounting_is_rejected(self) -> None:
        (self.case / "accounting.json").unlink()
        with self.assertRaises(FileNotFoundError):
            self.aggregate()

    def test_failed_controller_is_rejected(self) -> None:
        self.alter_json("accounting.json", lambda value: value.update(status="CASE_FAILED"))
        with self.assertRaisesRegex(AssertionError, "controller accounting"):
            self.aggregate()

    def test_nonzero_exit_monitor_failure_and_wrong_case_are_rejected(self) -> None:
        original = (self.case / "accounting.json").read_text(encoding="utf-8")
        for change in ({"exit_code": 1}, {"exit_code": False}, {"monitor_error": "stopped"}, {"case": "current-large"}):
            with self.subTest(change=change):
                (self.case / "accounting.json").write_text(original, encoding="utf-8")
                self.alter_json("accounting.json", lambda value: value.update(change))
                with self.assertRaisesRegex(AssertionError, "controller accounting"):
                    self.aggregate()

    def test_duplicate_identity_is_rejected_even_if_both_formats_agree(self) -> None:
        self.alter_rows(lambda rows: rows[1].update(update=rows[0]["update"]))
        with self.assertRaisesRegex(AssertionError, "observation grid"):
            self.aggregate()

    def test_missing_observation_is_rejected(self) -> None:
        self.alter_rows(lambda rows: rows.pop())
        with self.assertRaisesRegex(AssertionError, "observation grid"):
            self.aggregate()

    def test_csv_json_disagreement_is_rejected(self) -> None:
        self.alter_rows(lambda rows: rows[0].update(update_ms=987.0), json_too=False)
        with self.assertRaisesRegex(AssertionError, "CSV/JSON observation"):
            self.aggregate()

    def test_nonfinite_or_negative_time_is_rejected(self) -> None:
        csv_original = (self.case / "observations.csv").read_bytes()
        json_original = (self.case / "observations.json").read_bytes()
        for number in (float("nan"), float("inf"), -1.0):
            with self.subTest(number=number):
                (self.case / "observations.csv").write_bytes(csv_original)
                (self.case / "observations.json").write_bytes(json_original)
                self.alter_rows(lambda rows: rows[0].update(update_ms=number))
                with self.assertRaisesRegex(AssertionError, "observation (differs|time)"):
                    self.aggregate()

    def test_changed_configuration_is_rejected(self) -> None:
        self.alter_json("observations.json", lambda value: value["configuration"].update(updates=15))
        with self.assertRaisesRegex(AssertionError, "case declaration"):
            self.aggregate()

    def test_robustness_and_order_diagnostics_use_the_same_admission(self) -> None:
        self.alter_json("accounting.json", lambda value: value.update(status="CASE_FAILED"))
        for load in (journal_robustness._load_rows, summarize_current.build_order_diagnostics):
            with self.subTest(consumer=load.__name__):
                with self.assertRaisesRegex(AssertionError, "controller accounting"):
                    load(self.root)

    def test_retained_syntax_inventory_remains_separate_from_current_additions(self) -> None:
        retained = {"coverage": ["original.py"], "python_sources_parsed": 1}
        verify_retained_syntax_coverage(retained, ["added.py", "original.py"])
        for bad in ({"coverage": ["original.py"], "python_sources_parsed": 2},
                    {"coverage": ["original.py", "original.py"], "python_sources_parsed": 2}):
            with self.subTest(record=bad):
                with self.assertRaises(AssertionError):
                    verify_retained_syntax_coverage(bad, ["added.py", "original.py"])
        with self.assertRaisesRegex(AssertionError, "missing"):
            verify_retained_syntax_coverage(retained, ["added.py"])

    def test_checkout_metadata_is_excluded_but_unimported_source_is_parsed(self) -> None:
        root = self.root / "syntax"
        (root / ".git").mkdir(parents=True)
        (root / ".git/metadata.py").write_text("not executable Python ???", encoding="utf-8")
        (root / "source.py").write_text("value = 1\n", encoding="utf-8")
        self.assertEqual(["source.py"], parse_python_sources(root))
        (root / "unimported.py").write_text("def broken(:\n", encoding="utf-8")
        with self.assertRaisesRegex(AssertionError, "unimported.py"):
            parse_python_sources(root)


if __name__ == "__main__":
    unittest.main()
