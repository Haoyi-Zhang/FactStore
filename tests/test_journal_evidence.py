"""Journal-evidence and recursive manuscript-surface regression checks."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from experiments import journal_robustness
from experiments import verify_publication_references as references


class JournalEvidenceTests(unittest.TestCase):
    def test_robustness_reconstruction_is_deterministic_and_complete(self) -> None:
        artifact = Path(__file__).resolve().parents[1]
        results = artifact / "results" / "current"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first" / "journal-robustness.json"
            second = root / "second" / "journal-robustness.json"
            left = journal_robustness.run(results, first)
            right = journal_robustness.run(results, second)
            self.assertEqual(left, right)
            self.assertEqual(264, left["input_observations"])
            self.assertEqual(9, len(left["distributions"]))
            self.assertEqual(3, len(left["paired_frontier_rooted"]))
            for suffix in (".json", ".csv", ".tex"):
                self.assertEqual(
                    first.with_suffix(suffix).read_bytes(),
                    second.with_suffix(suffix).read_bytes(),
                )
            self.assertEqual(
                first.with_name("journal-robustness-paired.csv").read_bytes(),
                second.with_name("journal-robustness-paired.csv").read_bytes(),
            )
            retained = results / "summary" / "journal-robustness.json"
            self.assertEqual(left, json.loads(retained.read_text(encoding="utf-8")))
            # The documented package check is byte-exact, including on Windows.
            for suffix in (".json", ".csv", ".tex"):
                self.assertEqual(retained.with_suffix(suffix).read_bytes(), first.with_suffix(suffix).read_bytes())
            self.assertEqual(
                retained.with_name("journal-robustness-paired.csv").read_bytes(),
                first.with_name("journal-robustness-paired.csv").read_bytes(),
            )

    def test_reference_surface_recurses_through_local_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "main.tex").write_text(
                "Before\\input{section}After\\input{data.csv}\n", encoding="utf-8"
            )
            (root / "section.tex").write_text(
                "Text \\cite{alpha}.\\input{sub/appendix}\n", encoding="utf-8"
            )
            (root / "sub").mkdir()
            (root / "sub" / "appendix.tex").write_text(
                "More \\cite{beta}.\n", encoding="utf-8"
            )
            self.assertEqual(
                [["alpha"], ["beta"]],
                references.citation_groups(root / "main.tex"),
            )

    def test_reference_surface_rejects_input_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "a.tex").write_text("\\input{b}\n", encoding="utf-8")
            (root / "b.tex").write_text("\\input{a}\n", encoding="utf-8")
            with self.assertRaisesRegex(AssertionError, "input cycle"):
                references.citation_groups(root / "a.tex")


if __name__ == "__main__":
    unittest.main()
