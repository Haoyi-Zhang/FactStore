"""Current paper-source checks are distinct from retained render metadata."""
from pathlib import Path
import tempfile
import unittest

from experiments.verify_artifact import verify_primary_wrapper


class PrimaryWrapperTests(unittest.TestCase):
    def write_wrapper(self, directory, options):
        root = Path(directory)
        (root/"main.tex").write_text(
            r"\documentclass["+options+r"]{acmart}"+"\n"
            r"\input{journal-preamble.tex}"+"\n"
            r"\input{journal-frontmatter.tex}"+"\n", encoding="utf-8")
        return root

    def test_current_acmsmall_wrapper(self):
        with tempfile.TemporaryDirectory() as directory:
            result = verify_primary_wrapper(self.write_wrapper(directory, "acmsmall,screen,review,anonymous"))
            self.assertEqual("SOURCE_CHECKED", result["status"])
            self.assertFalse(result["pdf_inspected"])

    def test_retained_manuscript_format_is_not_current(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(AssertionError, "format differs"):
                verify_primary_wrapper(self.write_wrapper(directory, "manuscript,screen,review,anonymous"))

    def test_standalone_code_checkout_does_not_claim_paper_inspection(self):
        with tempfile.TemporaryDirectory() as directory:
            result = verify_primary_wrapper(Path(directory))
            self.assertEqual("NOT_INCLUDED", result["status"])
            self.assertFalse(result["source_inspected"])
