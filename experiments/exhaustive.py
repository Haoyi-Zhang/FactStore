"""Bounded logical histories and an independent abstract selector model.

The logical-history loop checks transition closure after each step.  The
publication-cut loop separately constructs an abstract durable object set and a
selector for each cut, materializes the selected object, and compares that value
with an independent old/new endpoint oracle.  The model is deliberately small:
it is not a filesystem or power-loss model.
"""

from __future__ import annotations

from dataclasses import dataclass
import itertools
import json
from pathlib import Path
from typing import Iterable, Mapping

from frontierstore.model import (
    State,
    Transaction,
    apply_transaction,
    bootstrap_state,
    check_closure,
    states_equal,
)

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
_EXPECTED_ENDPOINT_BY_CUT = {
    "before_segment": "old",
    "after_header": "old",
    "before_footer": "old",
    "after_footer": "old",
    "after_segment_fsync": "old",
    "after_segment_dirsync": "old",
    "after_root_write": "old",
    "after_root_fsync": "old",
    "after_root_replace": "new",
    "after_root_dirsync": "new",
}


@dataclass(frozen=True, slots=True)
class AbstractPublication:
    """Abstract immutable objects plus the selector visible at one cut."""

    objects: Mapping[str, State]
    selector: str


def initial_state() -> State:
    return bootstrap_state(
        {"a": "a0", "b": "b0"},
        [{"id": "p", "payload": "p0", "sources": ["a", "b"]}],
    )


def transaction_for(state: State, operation: str) -> Transaction:
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


def expected_selector(cut: str) -> str:
    """Return the endpoint identity required by the abstract cut semantics."""

    try:
        return _EXPECTED_ENDPOINT_BY_CUT[cut]
    except KeyError as error:
        raise ValueError(f"unknown publication cut: {cut}") from error


def publication_at_cut(
    old: State,
    new: State,
    cut: str,
    *,
    selector_override: str | None = None,
) -> AbstractPublication:
    """Construct the abstract durable objects and selector at ``cut``.

    The selected observation is derived from this object/selector state rather
    than assigned directly to the expected endpoint.  ``selector_override`` is
    used only by negative validation cases.
    """

    try:
        index = CUTS.index(cut)
    except ValueError as error:
        raise ValueError(f"unknown publication cut: {cut}") from error

    objects: dict[str, State] = {"old": old.clone()}
    selector = "old"
    # Replay the abstract publication actions through the requested cut.  This
    # state machine is separate from the explicit endpoint oracle above.
    for event in CUTS[: index + 1]:
        if event == "after_footer":
            objects["new"] = new.clone()
        elif event == "after_root_replace":
            selector = "new"
    if selector_override is not None:
        selector = selector_override
    if selector not in objects:
        raise ValueError(f"selector {selector!r} names no complete object at {cut}")
    return AbstractPublication(objects=objects, selector=selector)


def materialize(publication: AbstractPublication) -> State:
    """Decode the complete object named by the abstract selector."""

    try:
        return publication.objects[publication.selector].clone()
    except KeyError as error:
        raise ValueError(f"selector {publication.selector!r} is unresolved") from error


def endpoint_identity_errors(old: State, new: State, cut: str, visible: State) -> list[dict[str, object]]:
    """Check membership in {old,new} and correspondence to the cut rule."""

    if states_equal(visible, old):
        observed = "old"
    elif states_equal(visible, new):
        observed = "new"
    else:
        return [{"code": "NON_ENDPOINT_OBSERVATION", "cut": cut}]

    expected = expected_selector(cut)
    if observed != expected:
        return [{
            "code": "CUT_ENDPOINT_MISMATCH",
            "cut": cut,
            "expected": expected,
            "observed": observed,
        }]
    return []


def closure_counterexample() -> tuple[State, State, State]:
    """Return two legal endpoints and a closed value equal to neither.

    Source ``a`` and fact ``p`` share a stable dependency.  Source ``b`` advances
    generation while ``p`` changes payload.  Mixing old sources with the new fact
    remains closed, but differs from the old endpoint in ``p`` and from the new
    endpoint in ``b`` even when epoch is ignored.
    """

    old = bootstrap_state(
        {"a": "a0", "b": "b0"},
        [{"id": "p", "payload": "old", "sources": ["a"]}],
    )
    new, _ = apply_transaction(
        old,
        Transaction(
            source_changes={"b": "b1"},
            fact_replacements=[{"id": "p", "payload": "new", "sources": ["a"]}],
        ),
    )
    mixed = State(
        epoch=old.epoch,
        schema=old.schema,
        sources=dict(old.sources),
        facts=dict(new.facts),
        reverse={key: tuple(value) for key, value in old.reverse.items()},
    )
    return old, new, mixed


def run(output: str | Path) -> dict[str, object]:
    history_count = 0
    intermediate_states = 0
    publication_cuts = 0
    closure_violations: list[dict[str, object]] = []
    endpoint_violations: list[dict[str, object]] = []
    operation_counts = {name: 0 for name in OPERATIONS}
    cut_counts = {name: 0 for name in CUTS}
    selected_endpoint_counts = {"old": 0, "new": 0}

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
                closure_violations.append(
                    {"history": history_index, "step": step, "operation": operation, "error": errors[0]}
                )

        for cut in CUTS:
            cut_counts[cut] += 1
            publication_cuts += 1
            publication = publication_at_cut(final_old, final_new, cut)
            visible = materialize(publication)
            selected_endpoint_counts[publication.selector] += 1

            errors = check_closure(visible)
            if errors:
                closure_violations.append(
                    {"history": history_index, "cut": cut, "error": errors[0]}
                )
            endpoint_errors = endpoint_identity_errors(final_old, final_new, cut, visible)
            if endpoint_errors:
                endpoint_violations.append(
                    {"history": history_index, "cut": cut, "error": endpoint_errors[0]}
                )

    violations = closure_violations + endpoint_violations
    result = {
        "model": "abstract-selector-object-v1",
        "operations": list(OPERATIONS),
        "cuts": list(CUTS),
        "histories": history_count,
        "intermediate_states": intermediate_states,
        "crash_cuts": publication_cuts,
        "operation_counts": operation_counts,
        "cut_counts": cut_counts,
        "selected_endpoint_counts": selected_endpoint_counts,
        "closure_violations": closure_violations,
        "endpoint_violations": endpoint_violations,
        "violations": violations,
        "all_closed": not closure_violations,
        "all_endpoints": not endpoint_violations,
    }
    operation_count = len(OPERATIONS)
    expected_histories = sum(operation_count ** length for length in (4, 3, 2))
    expected_states = sum(length * (operation_count ** length) for length in (4, 3, 2))
    expected_cuts = expected_histories * len(CUTS)
    if (result["histories"], result["intermediate_states"], result["crash_cuts"]) != (
        expected_histories,
        expected_states,
        expected_cuts,
    ):
        raise AssertionError(result)
    if selected_endpoint_counts != {"old": expected_histories * 8, "new": expected_histories * 2}:
        raise AssertionError(selected_endpoint_counts)

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
