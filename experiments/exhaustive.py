"""Bounded logical histories and abstract publication cuts."""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Iterable

from frontierstore.model import Transaction, apply_transaction, bootstrap_state, check_closure

OPERATIONS = ("replace_a", "replace_b", "retract_fact", "install_fact", "advance_schema")
CUTS = (
    "before_segment",
    "after_header",
    "before_footer",
    "after_footer",
    "after_segment_fsync",
    "after_segment_dirsync",
    "after_root_write",
    "after_root_fsync",
    "after_root_replace",
    "after_root_dirsync",
)


def initial_state():
    return bootstrap_state(
        {"a": "a0", "b": "b0"},
        [{"id": "p", "payload": "p0", "sources": ["a", "b"]}],
    )


def transaction_for(state, operation: str):
    replacement = {"id": "p", "payload": f"p{state.epoch + 1}", "sources": ["a", "b"]}
    if operation == "replace_a":
        return Transaction(source_changes={"a": f"a{state.epoch + 1}"}, fact_replacements=[replacement])
    if operation == "replace_b":
        return Transaction(source_changes={"b": f"b{state.epoch + 1}"}, fact_replacements=[replacement])
    if operation == "retract_fact":
        return Transaction(fact_retractions=["p"])
    if operation == "install_fact":
        return Transaction(fact_replacements=[replacement])
    if operation == "advance_schema":
        return Transaction(new_schema=state.schema + 1, fact_replacements=[replacement])
    raise ValueError(operation)


def histories() -> Iterable[tuple[str, ...]]:
    """Enumerate the complete declared bounded universe.

    The universe consists of every length-two, length-three, and length-four
    sequence over ``OPERATIONS``.  Keeping the bounds explicit prevents a
    prefix sample from being misreported as complete enumeration.
    """
    for length in (4, 3, 2):
        yield from itertools.product(OPERATIONS, repeat=length)


def run(output: str | Path) -> dict[str, object]:
    history_count = 0
    intermediate_states = 0
    crash_cuts = 0
    violations: list[dict[str, object]] = []
    operation_counts = {name: 0 for name in OPERATIONS}
    cut_counts = {name: 0 for name in CUTS}

    for history_index, history in enumerate(histories()):
        history_count += 1
        state = initial_state()
        final_old = state
        final_new = state
        for step, operation in enumerate(history):
            operation_counts[operation] += 1
            final_old = state
            transaction = transaction_for(state, operation)
            state, _ = apply_transaction(state, transaction)
            final_new = state
            intermediate_states += 1
            errors = check_closure(state)
            if errors:
                violations.append(
                    {"history": history_index, "step": step, "operation": operation, "error": errors[0]}
                )
        for cut_index, cut in enumerate(CUTS):
            cut_counts[cut] += 1
            crash_cuts += 1
            visible = final_old if cut_index < 8 else final_new
            errors = check_closure(visible)
            if errors:
                violations.append(
                    {"history": history_index, "cut": cut, "error": errors[0]}
                )

    result = {
        "operations": list(OPERATIONS),
        "cuts": list(CUTS),
        "histories": history_count,
        "intermediate_states": intermediate_states,
        "crash_cuts": crash_cuts,
        "operation_counts": operation_counts,
        "cut_counts": cut_counts,
        "violations": violations,
        "all_closed": not violations,
    }
    operation_count = len(OPERATIONS)
    expected_histories = sum(operation_count ** length for length in (4, 3, 2))
    expected_states = sum(length * (operation_count ** length) for length in (4, 3, 2))
    expected_cuts = expected_histories * len(CUTS)
    if (result["histories"], result["intermediate_states"], result["crash_cuts"]) != (
        expected_histories, expected_states, expected_cuts
    ):
        raise AssertionError(result)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    summary = run(arguments.output)
    print(json.dumps(summary, sort_keys=True))
