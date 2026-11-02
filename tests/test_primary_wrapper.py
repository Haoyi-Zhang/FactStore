"""Current paper-source checks are distinct from retained render metadata."""
import csv
import json
from pathlib import Path
import re
import tempfile
import unittest

from experiments.verify_artifact import verify_primary_wrapper


class PrimaryWrapperTests(unittest.TestCase):
    def _assert_scoped_retention(self, source):
        # Locate by semantic table/engine identifiers, not line breaks or row count.
        table = re.search(
            r"\\label\s*\{\s*tab:representations\s*\}(.*?)\\end\s*\{tabularx\}",
            source, re.DOTALL)
        self.assertIsNotNone(table, "representation contract table is missing")
        row = re.search(r"\\sqlroot\s*&(.*?)\\\\", table.group(1), re.DOTALL)
        self.assertIsNotNone(row, "SQLite-R representation row is missing")
        policy = " ".join(row.group(1).split("&")[-1].lower().split())
        clauses = policy.split(";")
        fixed = next((c for c in clauses if re.search(r"fixed[- ]order", c)), "")
        native = next((c for c in clauses if re.search(r"\bnative\b", c)), "")
        self.assertRegex(fixed, r"\ball\b.*\bimages\b.*\bretained\b")
        self.assertRegex(native, r"\bonly\b.*\bcurrent\b.*\bleased\b.*\bpairs\b.*\bretained\b")

    def _check_delivery_metadata(self):
        root = Path(__file__).resolve().parents[1]
        paper = root.parent / "paper"
        with self.subTest(metadata="historical versus discovered test inventory"):
            text = " ".join((root / "FORMAT.md").read_text(encoding="utf-8").split())
            self.assertRegex(text, r"source-bound Linux run passed 140 methods in its recorded source")
            self.assertRegex(text, r"current test inventory is obtained by discovery")
            self.assertIn("no current full-suite execution is implied", text)
            self.assertNotRegex(text, r"delivered suite has \d+ methods")
        with self.subTest(metadata="SQL control versus measured wrapper"):
            text = " ".join((root / "proofs/representation.md").read_text(encoding="utf-8").split())
            summary = json.loads((root / "results/logical-sql-export/summary.json").read_text(encoding="utf-8"))
            self.assertIn("measured SQLite-N wrapper applies a shared precomputed delta", text)
            self.assertIn("separate, untimed `independent_sql_export` control", text)
            self.assertIn("derives invalidation through SQL dependency queries", text)
            self.assertIn(str(summary["endpoint_pairs"]) + " endpoint pairs", text)
            self.assertIs(summary["scientific_timings_collected"], False)
            self.assertIn("does not measure SQL-derived invalidation cost", text)
            self.assertIn("or support an equal-work latency comparison", text)
            self.assertNotIn("remain theorem-level rather than separately executed", text)
        with self.subTest(metadata="template provenance"):
            with (root / "external_resources.csv").open(encoding="utf-8", newline="") as stream:
                rows = {row["resource_name"]: row for row in csv.DictReader(stream)}
            old = rows["USENIX conference paper style"]
            self.assertRegex(old["integration_mode"], r"(?i)historical.*not active")
            self.assertNotIn("used unchanged in paper/", old["integration_mode"])
            cls = rows["ACM acmart class (bundled)"]
            bst = rows["ACM Reference Format bibliography style (bundled)"]
            for row in (cls, bst):
                self.assertIn("acquisition history not established", row["acquisition_method"])
                self.assertIn("compiler search-path resolution not attested", row["integration_mode"])
            self.assertIn("LPPL 1.3 or later", cls["license_or_terms"])
            self.assertIn("Public domain", bst["license_or_terms"])
        # The artifact is also distributed flat, without the companion paper.
        # Check source-selected dependencies and cohort semantics when it is present.
        if (paper / "main.tex").is_file():
            with self.subTest(metadata="source-selected ACM dependencies"):
                main = (paper / "main.tex").read_text(encoding="utf-8")
                front = (paper / "journal-frontmatter.tex").read_text(encoding="utf-8")
                self.assertRegex(main, r"\\documentclass\s*\[\s*acmsmall\s*,\s*screen\s*,\s*review\s*\]\s*\{acmart\}")
                self.assertRegex(front, r"\\bibliographystyle\s*\{ACM-Reference-Format\}")
                class_header = re.search(r"\\ProvidesClass\{acmart\}\s*\[([^\]]+)\]",
                                         (paper / "acmart.cls").read_text(encoding="utf-8"))
                self.assertIsNotNone(class_header)
                self.assertIn(" ".join(class_header.group(1).split()[:2]), cls["supported_claim"])
            with self.subTest(metadata="cohort-scoped SQLite-R retention"):
                source = (paper / "journal-sections.tex").read_text(encoding="utf-8")
                self._assert_scoped_retention(source)
                self._assert_scoped_retention(re.sub(r"\s+", " ", source))
                # A universal all-images policy must fail even with both cohort names.
                unscoped = re.sub(r"\bnative:\s*[^;\\]+", "native: all images retained ", source, count=1)
                self.assertNotEqual(source, unscoped)
                with self.assertRaises(AssertionError):
                    self._assert_scoped_retention(unscoped)

    def write_wrapper(self, directory, options):
        root = Path(directory)
        (root/"main.tex").write_text(
            r"\documentclass["+options+r"]{acmart}"+"\n"
            +(r"\def\JournalAuthorVisible{}"+"\n" if options == "acmsmall,screen,review" else "")+
            r"\input{journal-preamble.tex}"+"\n"
            r"\input{journal-frontmatter.tex}"+"\n", encoding="utf-8")
        return root

    def test_current_manuscript_wrapper(self):
        for options in ("acmsmall,screen,review,anonymous", "acmsmall,screen,review"):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                root = self.write_wrapper(directory, options)
                result = verify_primary_wrapper(root)
                self.assertEqual("SOURCE_CHECKED", result["status"])
                self.assertFalse(result["pdf_inspected"])
                if options == "acmsmall,screen,review":
                    text = (root/"main.tex").read_text(encoding="utf-8")
                    (root/"main.tex").write_text(text.replace(r"\def\JournalAuthorVisible{}", ""), encoding="utf-8")
                    with self.assertRaisesRegex(AssertionError, "must enable"):
                        verify_primary_wrapper(root)
        self._check_delivery_metadata()

    def test_production_format_is_not_current(self):
        # Retain the registered historical test identity; the current assertion
        # now follows the journal-specific TOS submission rule, not its old name.
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(AssertionError, "format differs"):
                verify_primary_wrapper(self.write_wrapper(directory, "manuscript,screen,review,anonymous"))

    def test_standalone_code_checkout_does_not_claim_paper_inspection(self):
        with tempfile.TemporaryDirectory() as directory:
            result = verify_primary_wrapper(Path(directory))
            self.assertEqual("NOT_INCLUDED", result["status"])
            self.assertFalse(result["source_inspected"])

    def test_additional_manuscript_entry_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.write_wrapper(directory, "acmsmall,screen,review,anonymous")
            (root / "alternate.tex").write_text(
                r"\documentclass[manuscript,review]{acmart}", encoding="utf-8")
            with self.assertRaisesRegex(AssertionError, "multiple manuscript entry points"):
                verify_primary_wrapper(root)

    def test_included_fragment_is_not_another_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.write_wrapper(directory, "acmsmall,screen,review,anonymous")
            (root / "body.tex").write_text(r"\section{Introduction}", encoding="utf-8")
            result = verify_primary_wrapper(root)
            self.assertEqual(["main.tex"], result["entry_points"])
            self.assertFalse(result["pdf_inspected"])
