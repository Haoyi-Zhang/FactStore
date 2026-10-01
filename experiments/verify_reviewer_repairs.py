"""Targeted deterministic validation for reviewer findings F1--F6.

This command is not a performance experiment.  It validates the repaired
abstract selector oracle, the replacement dependency semantics, the legal
closure counterexample, the retained SQLite rooted-audit call count, and the
all-source Python syntax parser (including an unimported negative file).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from experiments import exhaustive
from experiments.current_validation import audit, rooted_audit_timed_operations
from experiments.verify_artifact import parse_python_sources
from frontierstore.baselines import SQLiteNormalized, SQLiteRootedAudit
from frontierstore.model import (
    Transaction,
    apply_transaction,
    bootstrap_state,
    check_closure,
    states_equal,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class CountingRootedAudit(SQLiteRootedAudit):
    """Count complete selected-pair reads without changing their behavior."""

    def __init__(self, path):
        self.selected_pair_reads = 0
        super().__init__(path)

    def _read_selected_pair(self):
        self.selected_pair_reads += 1
        return super()._read_selected_pair()


def run(artifact_root: Path) -> dict[str, object]:
    # F1/F3: legal two-source endpoints, a closed nonendpoint mix, and a wrong
    # selector that still selects a complete closed endpoint.
    old, new, mixed = exhaustive.closure_counterexample()
    require(check_closure(old) == [], "old counterexample endpoint is not closed")
    require(check_closure(new) == [], "new counterexample endpoint is not closed")
    require(check_closure(mixed) == [], "mixed counterexample is not closed")
    require(not states_equal(old, mixed, include_epoch=False), "mix equals old without epoch")
    require(not states_equal(new, mixed, include_epoch=False), "mix equals new without epoch")
    mixed_error = exhaustive.endpoint_identity_errors(old, new, "after_root_fsync", mixed)
    require(mixed_error and mixed_error[0]["code"] == "NON_ENDPOINT_OBSERVATION", "mix not rejected")
    wrong_publication = exhaustive.publication_at_cut(
        old, new, "after_root_fsync", selector_override="new"
    )
    wrong_visible = exhaustive.materialize(wrong_publication)
    require(check_closure(wrong_visible) == [], "wrong selector did not select a closed value")
    wrong_error = exhaustive.endpoint_identity_errors(old, new, "after_root_fsync", wrong_visible)
    require(wrong_error and wrong_error[0]["code"] == "CUT_ENDPOINT_MISMATCH", "wrong selector not rejected")

    # F2: I is empty, but replacement p changes dependency a -> b.  The old
    # dependency group and inverse membership must disappear in the logical
    # transition and in the normalized relational decode.
    dependency_old = bootstrap_state(
        {"a": "a0", "b": "b0"},
        [{"id": "p", "payload": "p0", "sources": ["a"]}],
    )
    dependency_new, dependency_delta = apply_transaction(
        dependency_old,
        Transaction(fact_replacements=[{"id": "p", "payload": "p1", "sources": ["b"]}]),
    )
    require(dependency_delta.fact_del == ("p",), "replacement did not clear the old fact group")
    require(dependency_new.facts["p"].dependencies == (("b", 1),), "new dependency group differs")
    require(dependency_new.reverse == {"a": (), "b": ("p",)}, "new inverse relation differs")
    require(check_closure(dependency_new) == [], "replacement result is not closed")

    with tempfile.TemporaryDirectory(prefix="reviewer-repairs-") as temporary:
        temporary_path = Path(temporary)
        normalized = SQLiteNormalized.create(temporary_path / "normalized", dependency_old)
        normalized.persist_precomputed(dependency_new, dependency_delta)
        normalized_decode_equal = states_equal(normalized.durable_state(), dependency_new)
        require(normalized_decode_equal, "normalized relational decode differs from logical transition")
        normalized.close()

        # F4: the frozen current_validation.audit path performs durable_state()
        # and audit_report(), and each performs one complete pair read.
        rooted = CountingRootedAudit.create(temporary_path / "rooted", dependency_old)
        rooted.selected_pair_reads = 0
        _, _elapsed_ms = audit(rooted, temporary_path / "rooted", dependency_old)
        observed_pair_reads = rooted.selected_pair_reads
        expected_operations = rooted_audit_timed_operations()
        require(observed_pair_reads == 2, f"expected two selected-pair reads, observed {observed_pair_reads}")
        require(expected_operations["sqlite_decodes"] == 2, "static SQLite decode count differs")
        require(expected_operations["export_parses"] == 2, "static export parse count differs")
        require(expected_operations["database_export_comparisons"] == 2, "static pair comparison count differs")
        rooted.close()

        # F6: parse every delivered Python source, then prove that an unimported
        # syntax error on the same parser surface fails closed.
        parsed_sources = parse_python_sources(artifact_root)
        negative_root = temporary_path / "syntax-negative"
        negative_root.mkdir()
        (negative_root / "not_imported.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
        syntax_negative_rejected = False
        syntax_negative_message = ""
        try:
            parse_python_sources(negative_root)
        except AssertionError as error:
            syntax_negative_rejected = True
            syntax_negative_message = str(error)
        require(syntax_negative_rejected, "unimported syntax error was not rejected")
        require("not_imported.py" in syntax_negative_message, "syntax witness omits the bad path")

    return {
        "status": "REVIEWER_REPAIRS_VALIDATED",
        "scientific_measurement": False,
        "f1": {
            "abstract_model": "abstract-selector-object-v1",
            "closed_nonendpoint_rejected_as": mixed_error[0]["code"],
            "wrong_selector_rejected_as": wrong_error[0]["code"],
        },
        "f2": {
            "invalidation_set_empty": True,
            "replacement_clear_set": ["p"],
            "final_dependencies": [["b", 1]],
            "final_reverse": {"a": [], "b": ["p"]},
            "normalized_decode_equal": normalized_decode_equal,
        },
        "f3": {
            "changed_source": "b",
            "old_generation": old.sources["b"].generation,
            "new_generation": new.sources["b"].generation,
            "mixed_closed": True,
            "differs_from_old_without_epoch": True,
            "differs_from_new_without_epoch": True,
        },
        "f4": {
            "observed_selected_pair_reads": observed_pair_reads,
            **expected_operations,
            "frozen_results_reinterpreted_only": True,
        },
        "f6": {
            "python_sources_parsed": len(parsed_sources),
            "coverage": parsed_sources,
            "unimported_syntax_error_rejected": syntax_negative_rejected,
            "negative_witness": syntax_negative_message,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    result = run(arguments.artifact_root)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "checks": ["F1", "F2", "F3", "F4", "F6"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
