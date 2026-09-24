"""Regression checks for the declared bounded-history universe."""
from __future__ import annotations

from collections import Counter
import itertools
import json
from pathlib import Path
import tempfile
import unittest

from experiments import exhaustive


class BoundedEnumerationTests(unittest.TestCase):
    def test_declared_universe_contains_every_length_two_to_four_history(self) -> None:
        observed = list(exhaustive.histories())
        expected = [
            history
            for length in (4, 3, 2)
            for history in itertools.product(exhaustive.OPERATIONS, repeat=length)
        ]
        self.assertEqual(expected, observed)
        self.assertEqual(775, len(observed))
        self.assertEqual({4: 625, 3: 125, 2: 25}, dict(Counter(map(len, observed))))
        self.assertEqual(len(observed), len(set(observed)))

    def test_runner_emits_counts_derived_from_the_full_universe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "bounded.json"
            result = exhaustive.run(output)
            self.assertEqual(775, result["histories"])
            self.assertEqual(2925, result["intermediate_states"])
            self.assertEqual(7750, result["crash_cuts"])
            self.assertTrue(result["all_closed"])
            self.assertEqual([], result["violations"])
            self.assertEqual(result, json.loads(output.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
