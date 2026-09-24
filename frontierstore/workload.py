"""Deterministic synthetic states and updates shared by all storage designs."""

from __future__ import annotations

import random
from typing import Iterable

from .model import Fact, Source, State, Transaction, canonical_state_bytes, check_closure, transaction_for_changed_sources


def _nonnegative_integer(value: int, role: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{role} must be a nonnegative integer")
    return value


def _positive_integer(value: int, role: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{role} must be a positive integer")
    return value


def source_id(index: int) -> str:
    return f"s{_nonnegative_integer(index, 'source index'):06d}"


def fact_id(owner: int, slot: int) -> str:
    owner = _nonnegative_integer(owner, "fact owner")
    slot = _nonnegative_integer(slot, "fact slot")
    return f"f{owner:06d}.{slot:02d}"


def _dependency_indices(owner: int, slot: int, width: int, source_count: int) -> tuple[int, ...]:
    if width <= 0 or width > source_count:
        raise ValueError("dependency width must be between one and the source count")
    chosen = [owner]
    candidate = 1
    while len(chosen) < width:
        index = (owner + slot * width + candidate) % source_count
        if index not in chosen:
            chosen.append(index)
        candidate += 1
    return tuple(sorted(chosen))


def make_synthetic_state(
    source_count: int,
    *,
    facts_per_source: int = 6,
    dependency_width: int = 2,
    epoch: int = 1,
    schema: int = 1,
) -> State:
    source_count = _positive_integer(source_count, "source count")
    facts_per_source = _nonnegative_integer(facts_per_source, "facts per source")
    dependency_width = _positive_integer(dependency_width, "dependency width")
    epoch = _positive_integer(epoch, "epoch")
    schema = _positive_integer(schema, "schema")
    if dependency_width > source_count:
        raise ValueError("dependency width must not exceed the source count")
    sources = {
        source_id(index): Source(source_id(index), epoch, f"source:{index}:base")
        for index in range(source_count)
    }
    reverse_lists: dict[str, list[str]] = {identifier: [] for identifier in sources}
    facts: dict[str, Fact] = {}
    for owner in range(source_count):
        for slot in range(facts_per_source):
            identifier = fact_id(owner, slot)
            dependency_ids = tuple(
                source_id(index)
                for index in _dependency_indices(owner, slot, dependency_width, source_count)
            )
            dependencies = tuple((identifier_, epoch) for identifier_ in dependency_ids)
            fact = Fact(identifier, f"fact:{owner}:{slot}:base", schema, dependencies)
            facts[identifier] = fact
            for identifier_ in dependency_ids:
                reverse_lists[identifier_].append(identifier)
    reverse = {identifier: tuple(sorted(values)) for identifier, values in reverse_lists.items()}
    state = State(epoch, schema, sources, facts, reverse)
    errors = check_closure(state)
    if errors:
        raise AssertionError(errors[0])
    return state


def choose_sources(state: State, batch: int, seed: int) -> tuple[str, ...]:
    batch = _nonnegative_integer(batch, "batch")
    if batch > len(state.sources):
        raise ValueError("batch larger than source set")
    randomizer = random.Random(seed)
    return tuple(sorted(randomizer.sample(sorted(state.sources), batch)))


def make_update(state: State, *, batch: int, seed: int) -> Transaction:
    selected = choose_sources(state, batch, seed)
    return transaction_for_changed_sources(state, selected, payload_suffix=f":u{seed}")


def query_fact_ids(state: State, count: int = 31) -> tuple[str, ...]:
    count = _nonnegative_integer(count, "fact query count")
    identifiers = sorted(state.facts)
    if count == 0 or not identifiers:
        return tuple()
    if len(identifiers) <= count:
        return tuple(identifiers)
    step = max(1, len(identifiers) // count)
    selected = identifiers[::step][:count]
    return tuple(selected)


def logical_size(state: State) -> int:
    return len(canonical_state_bytes(state))


def affected_facts(state: State, source_ids: Iterable[str]) -> tuple[str, ...]:
    affected: set[str] = set()
    for identifier in source_ids:
        affected.update(state.reverse.get(identifier, ()))
    return tuple(sorted(affected))
