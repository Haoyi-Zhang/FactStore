"""Regression checks for the declared bounded-history universe."""
from __future__ import annotations

from collections import Counter
import itertools
import json
from pathlib import Path
import tempfile
import unittest

from experiments import exhaustive
from frontierstore.model import check_closure, states_equal


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
            self.assertTrue(result["all_endpoints"])
            self.assertEqual({"old": 6200, "new": 1550}, result["selected_endpoint_counts"])
            self.assertEqual([], result["closure_violations"])
            self.assertEqual([], result["endpoint_violations"])
            self.assertEqual([], result["violations"])

            old, new, mixed = exhaustive.closure_counterexample()
            self.assertEqual([], check_closure(mixed))
            self.assertFalse(states_equal(old, mixed, include_epoch=False))
            self.assertFalse(states_equal(new, mixed, include_epoch=False))
            self.assertEqual(
                "NON_ENDPOINT_OBSERVATION",
                exhaustive.endpoint_identity_errors(old, new, "after_root_fsync", mixed)[0]["code"],
            )

            wrong = exhaustive.publication_at_cut(
                old, new, "after_root_fsync", selector_override="new"
            )
            visible = exhaustive.materialize(wrong)
            self.assertEqual([], check_closure(visible))
            self.assertEqual(
                "CUT_ENDPOINT_MISMATCH",
                exhaustive.endpoint_identity_errors(old, new, "after_root_fsync", visible)[0]["code"],
            )
            self.assertEqual(result, json.loads(output.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
