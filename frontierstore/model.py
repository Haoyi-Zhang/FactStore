"""Logical state and deterministic transition semantics for FrontierStore."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


class TransitionError(ValueError):
    """A transaction is rejected before persistent output is created."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Source:
    identifier: str
    generation: int
    payload: str

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.identifier, "generation": self.generation, "payload": self.payload}


@dataclass(frozen=True, slots=True)
class Fact:
    identifier: str
    payload: str
    schema: int
    dependencies: tuple[tuple[str, int], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.identifier,
            "payload": self.payload,
            "schema": self.schema,
            "dependencies": [
                {"source": source_id, "generation": generation}
                for source_id, generation in self.dependencies
            ],
        }


@dataclass(frozen=True, slots=True)
class State:
    epoch: int
    schema: int
    sources: Mapping[str, Source]
    facts: Mapping[str, Fact]
    reverse: Mapping[str, tuple[str, ...]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "epoch": self.epoch,
            "schema": self.schema,
            "sources": {key: self.sources[key].as_dict() for key in sorted(self.sources)},
            "facts": {key: self.facts[key].as_dict() for key in sorted(self.facts)},
            "reverse": {key: list(self.reverse[key]) for key in sorted(self.reverse)},
        }

    def observational_dict(self) -> dict[str, Any]:
        value = self.as_dict()
        value.pop("epoch", None)
        return value

    def clone(self) -> "State":
        return State(
            epoch=self.epoch,
            schema=self.schema,
            sources=dict(self.sources),
            facts={
                key: Fact(value.identifier, value.payload, value.schema,
                          tuple((source, generation) for source, generation in value.dependencies))
                for key, value in self.facts.items()
            },
            reverse={key: tuple(value) for key, value in self.reverse.items()},
        )


@dataclass(frozen=True, slots=True)
class Transaction:
    source_changes: Mapping[str, str | None] = field(default_factory=dict)
    fact_replacements: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    fact_retractions: Sequence[str] = field(default_factory=tuple)
    new_schema: int | None = None

    def __post_init__(self) -> None:
        """Capture well-shaped mutable inputs at construction time.

        Validation remains in :func:`apply_transaction`, so malformed values get
        stable ``TransitionError`` witnesses instead of constructor-dependent
        Python exceptions.  Correctly shaped containers, however, cannot be
        changed by their caller after the transaction is handed to the store.
        """
        if isinstance(self.source_changes, Mapping):
            object.__setattr__(
                self, "source_changes", MappingProxyType(dict(self.source_changes))
            )

        replacements = self.fact_replacements
        if isinstance(replacements, Sequence) and not isinstance(replacements, (str, bytes)):
            captured: list[Any] = []
            for spec in replacements:
                if isinstance(spec, Mapping):
                    copied = dict(spec)
                    dependencies = copied.get("sources")
                    if isinstance(dependencies, Sequence) and not isinstance(dependencies, (str, bytes)):
                        copied["sources"] = tuple(dependencies)
                    captured.append(MappingProxyType(copied))
                else:
                    captured.append(spec)
            object.__setattr__(self, "fact_replacements", tuple(captured))

        retractions = self.fact_retractions
        if isinstance(retractions, Sequence) and not isinstance(retractions, (str, bytes)):
            object.__setattr__(self, "fact_retractions", tuple(retractions))


@dataclass(frozen=True, slots=True)
class Delta:
    source_put: Mapping[str, Source]
    source_del: tuple[str, ...]
    fact_del: tuple[str, ...]
    fact_put: Mapping[str, Fact]
    reverse_put: Mapping[str, tuple[str, ...]]
    reverse_del: tuple[str, ...]


def valid_identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(_IDENTIFIER.fullmatch(value))


def _require_identifier(value: Any, code: str, role: str) -> str:
    if not valid_identifier(value):
        raise TransitionError(code, f"invalid {role}: {value!r}")
    return value


def _require_payload(value: Any, code: str, role: str) -> str:
    if not isinstance(value, str):
        raise TransitionError(code, f"{role} payload must be text")
    return value


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")


def canonical_state_bytes(state: State) -> bytes:
    return canonical_json_bytes(state.as_dict())


def build_reverse(sources: Mapping[str, Source], facts: Mapping[str, Fact]) -> dict[str, tuple[str, ...]]:
    relation: dict[str, list[str]] = {source_id: [] for source_id in sources}
    for fact_id in sorted(facts):
        fact = facts[fact_id]
        for source_id, _ in fact.dependencies:
            if source_id in relation:
                relation[source_id].append(fact_id)
    return {source_id: tuple(sorted(members)) for source_id, members in relation.items()}


def check_closure(state: State) -> list[dict[str, Any]]:
    """Return deterministic witnesses instead of raising on malformed state values."""
    errors: list[dict[str, Any]] = []
    if not isinstance(state, State):
        return [{"code": "STATE_TYPE", "detail": type(state).__name__}]

    if not isinstance(state.epoch, int) or isinstance(state.epoch, bool) or state.epoch <= 0:
        errors.append({"code": "BAD_EPOCH", "detail": state.epoch})
    if not isinstance(state.schema, int) or isinstance(state.schema, bool) or state.schema <= 0:
        errors.append({"code": "BAD_SCHEMA", "detail": state.schema})

    if not isinstance(state.sources, Mapping):
        errors.append({"code": "SOURCES_TYPE", "detail": type(state.sources).__name__})
        sources: Mapping[Any, Any] = {}
    else:
        sources = state.sources
    if not isinstance(state.facts, Mapping):
        errors.append({"code": "FACTS_TYPE", "detail": type(state.facts).__name__})
        facts: Mapping[Any, Any] = {}
    else:
        facts = state.facts
    if not isinstance(state.reverse, Mapping):
        errors.append({"code": "REVERSE_TYPE", "detail": type(state.reverse).__name__})
        reverse: Mapping[Any, Any] = {}
    else:
        reverse = state.reverse

    valid_sources: dict[str, Source] = {}
    for key, source in sources.items():
        if not isinstance(source, Source):
            errors.append({"code": "SOURCE_TYPE", "detail": repr(key)})
            continue
        if key != source.identifier or not valid_identifier(key):
            errors.append({"code": "SOURCE_KEY", "detail": repr(key)})
        if not isinstance(source.generation, int) or isinstance(source.generation, bool) or source.generation <= 0:
            errors.append({"code": "SOURCE_GENERATION", "detail": repr(key)})
        elif isinstance(state.epoch, int) and not isinstance(state.epoch, bool) and source.generation > state.epoch:
            errors.append({"code": "SOURCE_GENERATION_HORIZON", "detail": repr(key)})
        if not isinstance(source.payload, str):
            errors.append({"code": "SOURCE_PAYLOAD", "detail": repr(key)})
        if valid_identifier(key):
            valid_sources[key] = source

    computed: dict[str, set[str]] = {source_id: set() for source_id in valid_sources}
    for key, fact in facts.items():
        if not isinstance(fact, Fact):
            errors.append({"code": "FACT_TYPE", "detail": repr(key)})
            continue
        if key != fact.identifier or not valid_identifier(key):
            errors.append({"code": "FACT_KEY", "detail": repr(key)})
        if not isinstance(fact.schema, int) or isinstance(fact.schema, bool) or fact.schema != state.schema:
            errors.append({"code": "FACT_SCHEMA", "detail": repr(key)})
        if not isinstance(fact.payload, str):
            errors.append({"code": "FACT_PAYLOAD", "detail": repr(key)})
        dependencies = fact.dependencies
        if not isinstance(dependencies, Sequence) or isinstance(dependencies, (str, bytes)):
            errors.append({"code": "DEPENDENCY_TYPE", "detail": repr(key)})
            continue
        if not dependencies:
            errors.append({"code": "EMPTY_DEPENDENCY", "detail": repr(key)})
        normalized: list[tuple[str, int]] = []
        seen: set[str] = set()
        for raw_edge in dependencies:
            if (not isinstance(raw_edge, Sequence) or isinstance(raw_edge, (str, bytes))
                    or len(raw_edge) != 2):
                errors.append({"code": "DEPENDENCY_SHAPE", "detail": repr(key)})
                continue
            source_id, generation = raw_edge
            if not valid_identifier(source_id):
                errors.append({"code": "DEPENDENCY_ID", "detail": [repr(key), repr(source_id)]})
                continue
            if not isinstance(generation, int) or isinstance(generation, bool) or generation <= 0:
                errors.append({"code": "DEPENDENCY_GENERATION", "detail": [repr(key), source_id]})
                continue
            normalized.append((source_id, generation))
            if source_id in seen:
                errors.append({"code": "DUPLICATE_DEPENDENCY", "detail": [repr(key), source_id]})
            seen.add(source_id)
            source = valid_sources.get(source_id)
            if source is None:
                errors.append({"code": "MISSING_SOURCE", "detail": [repr(key), source_id]})
                continue
            if generation != source.generation:
                errors.append(
                    {
                        "code": "STALE_DEPENDENCY",
                        "detail": [repr(key), source_id, generation, source.generation],
                    }
                )
            if isinstance(key, str):
                computed[source_id].add(key)
        if tuple(normalized) != tuple(sorted(normalized)):
            errors.append({"code": "DEPENDENCY_ORDER", "detail": repr(key)})

    source_keys = set(valid_sources)
    reverse_keys = {key for key in reverse if isinstance(key, str)}
    if set(reverse) != source_keys:
        errors.append(
            {
                "code": "REVERSE_DOMAIN",
                "detail": {
                    "missing": sorted(source_keys - reverse_keys),
                    "extra": sorted((repr(key) for key in set(reverse) - source_keys)),
                },
            }
        )
    for source_id in sorted(source_keys | reverse_keys):
        raw_members = reverse.get(source_id, ())
        if not isinstance(raw_members, Sequence) or isinstance(raw_members, (str, bytes)):
            errors.append({"code": "REVERSE_VALUE_TYPE", "detail": source_id})
            actual: tuple[str, ...] = tuple()
        else:
            members: list[str] = []
            malformed = False
            for member in raw_members:
                if not valid_identifier(member):
                    errors.append({"code": "REVERSE_MEMBER", "detail": [source_id, repr(member)]})
                    malformed = True
                else:
                    members.append(member)
            actual = tuple(members)
            if not malformed and actual != tuple(sorted(set(actual))):
                errors.append({"code": "REVERSE_CANONICAL", "detail": source_id})
        expected = tuple(sorted(computed.get(source_id, set())))
        if actual != expected:
            errors.append(
                {
                    "code": "REVERSE_MISMATCH",
                    "detail": [source_id, list(actual), list(expected)],
                }
            )
    return errors

def require_closed(state: State) -> None:
    errors = check_closure(state)
    if errors:
        first = errors[0]
        raise TransitionError(
            first["code"], json.dumps(first["detail"], sort_keys=True, default=repr)
        )


def capture_closed_state(state: State) -> State:
    """Copy then validate a caller-owned state before persistent mutation.

    ``State`` is frozen only at the dataclass shell; its mapping values may still
    be caller-owned.  Capturing first avoids a validate-then-use window in which
    those mappings could change between closure checking and serialization.
    """
    if not isinstance(state, State):
        raise TransitionError("STATE_TYPE", "expected State")
    if not isinstance(state.sources, Mapping):
        raise TransitionError("SOURCES_TYPE", "sources must be a mapping")
    if not isinstance(state.facts, Mapping):
        raise TransitionError("FACTS_TYPE", "facts must be a mapping")
    if not isinstance(state.reverse, Mapping):
        raise TransitionError("REVERSE_TYPE", "reverse must be a mapping")
    try:
        sources = dict(state.sources)
        facts: dict[Any, Any] = {}
        for key, value in state.facts.items():
            if isinstance(value, Fact):
                dependencies = value.dependencies
                if isinstance(dependencies, Sequence) and not isinstance(dependencies, (str, bytes)):
                    copied_dependencies: Any = tuple(
                        tuple(edge)
                        if isinstance(edge, Sequence) and not isinstance(edge, (str, bytes))
                        else edge
                        for edge in dependencies
                    )
                else:
                    copied_dependencies = dependencies
                facts[key] = Fact(
                    value.identifier, value.payload, value.schema, copied_dependencies
                )
            else:
                facts[key] = value
        reverse = {
            key: tuple(value)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes))
            else value
            for key, value in state.reverse.items()
        }
    except (RuntimeError, TypeError, ValueError) as error:
        raise TransitionError("STATE_CAPTURE", str(error)) from error
    captured = State(state.epoch, state.schema, sources, facts, reverse)
    require_closed(captured)
    return captured


def bootstrap_state(
    source_payloads: Mapping[str, str],
    fact_specs: Sequence[Mapping[str, Any]],
    *,
    schema: int = 1,
    epoch: int = 1,
) -> State:
    if not isinstance(source_payloads, Mapping):
        raise TransitionError("SOURCE_MAP_TYPE", "source payloads must be a mapping")
    if not isinstance(fact_specs, Sequence) or isinstance(fact_specs, (str, bytes)):
        raise TransitionError("FACT_SPECS_TYPE", "fact specifications must be a sequence")
    if not isinstance(schema, int) or isinstance(schema, bool) or schema <= 0:
        raise TransitionError("BAD_SCHEMA", "schema must be a positive integer")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch <= 0:
        raise TransitionError("BAD_EPOCH", "epoch must be a positive integer")
    captured_sources: list[tuple[str, str]] = []
    for source_id, payload in source_payloads.items():
        sid = _require_identifier(source_id, "SOURCE_ID", "source identifier")
        captured_sources.append(
            (sid, _require_payload(payload, "SOURCE_PAYLOAD", sid))
        )
    sources: dict[str, Source] = {
        source_id: Source(source_id, epoch, payload)
        for source_id, payload in sorted(captured_sources)
    }
    facts: dict[str, Fact] = {}
    for spec in fact_specs:
        fact = _make_fact(spec, sources, schema)
        if fact.identifier in facts:
            raise TransitionError("DUPLICATE_FACT", fact.identifier)
        facts[fact.identifier] = fact
    state = State(epoch=epoch, schema=schema, sources=sources, facts=facts, reverse=build_reverse(sources, facts))
    require_closed(state)
    return state


def _make_fact(spec: Mapping[str, Any], sources: Mapping[str, Source], schema: int) -> Fact:
    if not isinstance(spec, Mapping):
        raise TransitionError("FACT_TYPE", "fact specification must be a mapping")
    if set(spec) != {"id", "payload", "sources"}:
        raise TransitionError(
            "FACT_FIELDS",
            f"expected id, payload, and sources; got {sorted(map(str, spec))!r}",
        )
    fact_id = _require_identifier(spec.get("id"), "FACT_ID", "fact identifier")
    payload = _require_payload(spec.get("payload"), "FACT_PAYLOAD", fact_id)
    dependency_ids = spec.get("sources")
    if not isinstance(dependency_ids, Sequence) or isinstance(dependency_ids, (str, bytes)):
        raise TransitionError("DEPENDENCY_TYPE", fact_id)
    if not dependency_ids:
        raise TransitionError("EMPTY_DEPENDENCY", fact_id)
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in dependency_ids:
        source_id = _require_identifier(raw, "DEPENDENCY_ID", "dependency identifier")
        if source_id in seen:
            raise TransitionError("DUPLICATE_DEPENDENCY", f"{fact_id}:{source_id}")
        if source_id not in sources:
            raise TransitionError("MISSING_SOURCE", f"{fact_id}:{source_id}")
        seen.add(source_id)
        normalized.append(source_id)
    dependencies = tuple((source_id, sources[source_id].generation) for source_id in sorted(normalized))
    return Fact(fact_id, payload, schema, dependencies)


def apply_transaction(old: State, transaction: Transaction) -> tuple[State, Delta]:
    old = capture_closed_state(old)
    if not isinstance(transaction, Transaction):
        raise TransitionError("TRANSACTION_TYPE", "expected Transaction")

    if not isinstance(transaction.source_changes, Mapping):
        raise TransitionError("SOURCE_CHANGES_TYPE", "source changes must be a mapping")
    if (not isinstance(transaction.fact_replacements, Sequence)
            or isinstance(transaction.fact_replacements, (str, bytes))):
        raise TransitionError("FACT_REPLACEMENTS_TYPE", "fact replacements must be a sequence")
    if (not isinstance(transaction.fact_retractions, Sequence)
            or isinstance(transaction.fact_retractions, (str, bytes))):
        raise TransitionError("FACT_RETRACTIONS_TYPE", "fact retractions must be a sequence")

    new_epoch = old.epoch + 1
    schema = old.schema if transaction.new_schema is None else transaction.new_schema
    if not isinstance(schema, int) or isinstance(schema, bool) or schema <= 0:
        raise TransitionError("BAD_SCHEMA", "new schema must be a positive integer")

    source_changes: dict[str, str | None] = {}
    for raw_id, raw_payload in transaction.source_changes.items():
        source_id = _require_identifier(raw_id, "SOURCE_ID", "source identifier")
        if raw_payload is not None:
            _require_payload(raw_payload, "SOURCE_PAYLOAD", source_id)
        source_changes[source_id] = raw_payload

    explicit_retractions: set[str] = set()
    for raw_id in transaction.fact_retractions:
        fact_id = _require_identifier(raw_id, "FACT_ID", "fact retraction identifier")
        if fact_id in explicit_retractions:
            raise TransitionError("DUPLICATE_RETRACTION", fact_id)
        explicit_retractions.add(fact_id)

    replacement_specs: dict[str, Mapping[str, Any]] = {}
    for spec in transaction.fact_replacements:
        if not isinstance(spec, Mapping):
            raise TransitionError("FACT_TYPE", "fact replacement must be a mapping")
        fact_id = _require_identifier(spec.get("id"), "FACT_ID", "fact identifier")
        if fact_id in replacement_specs:
            raise TransitionError("DUPLICATE_FACT", fact_id)
        replacement_specs[fact_id] = spec

    invalidated: set[str] = set(explicit_retractions)
    for source_id in source_changes:
        invalidated.update(old.reverse.get(source_id, ()))
    if schema != old.schema:
        invalidated.update(old.facts)
    removed_or_replaced = invalidated | set(replacement_specs)

    sources = dict(old.sources)
    source_put: dict[str, Source] = {}
    source_del: list[str] = []
    for source_id, payload in sorted(source_changes.items()):
        if payload is None:
            if source_id in sources:
                del sources[source_id]
            source_del.append(source_id)
        else:
            source = Source(source_id, new_epoch, payload)
            sources[source_id] = source
            source_put[source_id] = source

    facts = dict(old.facts)
    reverse_sets: dict[str, set[str]] = {
        source_id: set(old.reverse.get(source_id, ())) for source_id in old.sources
    }

    removed_existing: dict[str, Fact] = {}
    for fact_id in sorted(removed_or_replaced):
        old_fact = facts.pop(fact_id, None)
        if old_fact is None:
            continue
        removed_existing[fact_id] = old_fact
        for source_id, _ in old_fact.dependencies:
            members = reverse_sets.get(source_id)
            if members is not None:
                members.discard(fact_id)

    for source_id in source_del:
        reverse_sets.pop(source_id, None)
    for source_id in sources:
        reverse_sets.setdefault(source_id, set())

    fact_put: dict[str, Fact] = {}
    for fact_id in sorted(replacement_specs):
        fact = _make_fact(replacement_specs[fact_id], sources, schema)
        facts[fact_id] = fact
        fact_put[fact_id] = fact
        for source_id, _ in fact.dependencies:
            reverse_sets[source_id].add(fact_id)

    reverse = {source_id: tuple(sorted(members)) for source_id, members in sorted(reverse_sets.items())}
    new_state = State(epoch=new_epoch, schema=schema, sources=sources, facts=facts, reverse=reverse)
    require_closed(new_state)

    touched_reverse: set[str] = set()
    for fact in removed_existing.values():
        touched_reverse.update(source_id for source_id, _ in fact.dependencies)
    for fact in fact_put.values():
        touched_reverse.update(source_id for source_id, _ in fact.dependencies)
    touched_reverse.update(source_changes)

    reverse_put = {source_id: reverse[source_id] for source_id in sorted(touched_reverse & set(sources))}
    reverse_del = tuple(sorted(touched_reverse - set(sources)))
    delta = Delta(
        source_put=source_put,
        source_del=tuple(sorted(source_del)),
        fact_del=tuple(sorted(removed_existing)),
        fact_put=fact_put,
        reverse_put=reverse_put,
        reverse_del=reverse_del,
    )
    return new_state, delta


def capture_and_validate_delta(old: State, new: State, supplied: Delta) -> Delta:
    """Capture and prove that a supplied delta reconstructs one closed next state.

    Both endpoint values are copied before validation so direct callers receive
    the same validate-before-use guarantee as the persistent adapters.
    """
    old = capture_closed_state(old)
    new = capture_closed_state(new)
    if new.epoch != old.epoch + 1:
        raise TransitionError("EPOCH_MISMATCH", "precomputed state is not the next epoch")
    if not isinstance(supplied, Delta):
        raise TransitionError("DELTA_TYPE", "expected Delta")
    if not isinstance(supplied.source_put, Mapping):
        raise TransitionError("DELTA_SOURCE_PUT_TYPE", "source_put must be a mapping")
    if not isinstance(supplied.fact_put, Mapping):
        raise TransitionError("DELTA_FACT_PUT_TYPE", "fact_put must be a mapping")
    if not isinstance(supplied.reverse_put, Mapping):
        raise TransitionError("DELTA_REVERSE_PUT_TYPE", "reverse_put must be a mapping")

    source_put: dict[str, Source] = {}
    for key, value in supplied.source_put.items():
        if not valid_identifier(key) or not isinstance(value, Source):
            raise TransitionError("DELTA_SOURCE_PUT", repr(key))
        if key != value.identifier:
            raise TransitionError("DELTA_KEY_MISMATCH", f"source_put:{key}")
        source_put[key] = Source(value.identifier, value.generation, value.payload)

    fact_put: dict[str, Fact] = {}
    for key, value in supplied.fact_put.items():
        if not valid_identifier(key) or not isinstance(value, Fact):
            raise TransitionError("DELTA_FACT_PUT", repr(key))
        if key != value.identifier:
            raise TransitionError("DELTA_KEY_MISMATCH", f"fact_put:{key}")
        dependencies = value.dependencies
        if not isinstance(dependencies, Sequence) or isinstance(dependencies, (str, bytes)):
            raise TransitionError("DEPENDENCY_TYPE", key)
        captured_dependencies: list[tuple[Any, Any]] = []
        for edge in dependencies:
            if (
                not isinstance(edge, Sequence)
                or isinstance(edge, (str, bytes))
                or len(edge) != 2
            ):
                raise TransitionError("DEPENDENCY_SHAPE", key)
            captured_dependencies.append((edge[0], edge[1]))
        fact_put[key] = Fact(
            value.identifier,
            value.payload,
            value.schema,
            tuple(captured_dependencies),
        )

    reverse_put: dict[str, tuple[str, ...]] = {}
    for key, values in supplied.reverse_put.items():
        if not valid_identifier(key):
            raise TransitionError("DELTA_REVERSE_PUT", repr(key))
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise TransitionError("DELTA_REVERSE_PUT_TYPE", key)
        members = tuple(values)
        if not all(valid_identifier(member) for member in members):
            raise TransitionError("DELTA_IDENTIFIER", f"reverse_put:{key}")
        reverse_put[key] = members

    captured_deletes: dict[str, tuple[str, ...]] = {}
    for name in ("source_del", "fact_del", "reverse_del"):
        values = getattr(supplied, name)
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise TransitionError("DELTA_DELETE_TYPE", name)
        identifiers = tuple(values)
        if not all(valid_identifier(key) for key in identifiers):
            raise TransitionError("DELTA_IDENTIFIER", name)
        if len(identifiers) != len(set(identifiers)):
            raise TransitionError("DUPLICATE_DELTA_DELETE", name)
        captured_deletes[name] = identifiers

    delta = Delta(
        source_put=source_put,
        source_del=captured_deletes["source_del"],
        fact_del=captured_deletes["fact_del"],
        fact_put=fact_put,
        reverse_put=reverse_put,
        reverse_del=captured_deletes["reverse_del"],
    )
    if set(delta.source_put) & set(delta.source_del):
        raise TransitionError("DELTA_SOURCE_OVERLAP", "source put and delete overlap")
    if set(delta.reverse_put) & set(delta.reverse_del):
        raise TransitionError("DELTA_REVERSE_OVERLAP", "reverse put and delete overlap")

    sources, facts, reverse = dict(old.sources), dict(old.facts), dict(old.reverse)
    sources.update(delta.source_put)
    for key in delta.source_del:
        sources.pop(key, None)
    for key in delta.fact_del:
        facts.pop(key, None)
    facts.update(delta.fact_put)
    reverse.update(delta.reverse_put)
    for key in delta.reverse_del:
        reverse.pop(key, None)
    folded = State(new.epoch, new.schema, sources, facts, reverse)
    require_closed(folded)
    if folded != new:
        raise TransitionError("DELTA_STATE_MISMATCH", "delta does not reconstruct supplied state")
    for key, value in new.sources.items():
        if old.sources.get(key) != value and value.generation != new.epoch:
            raise TransitionError("SOURCE_STAMP", "changed or inserted source must use the new epoch")
    return delta


def states_equal(left: State, right: State, *, include_epoch: bool = True) -> bool:
    if include_epoch:
        return left.as_dict() == right.as_dict()
    return left.observational_dict() == right.observational_dict()


def source_ids_for_fact(fact: Fact) -> tuple[str, ...]:
    return tuple(source_id for source_id, _ in fact.dependencies)


def transaction_for_changed_sources(
    state: State,
    changed_source_ids: Iterable[str],
    *,
    payload_suffix: str,
) -> Transaction:
    changed = tuple(sorted(set(changed_source_ids)))
    changes = {
        source_id: f"{state.sources[source_id].payload}{payload_suffix}"
        for source_id in changed
    }
    affected: set[str] = set()
    for source_id in changed:
        affected.update(state.reverse[source_id])
    replacements = []
    for fact_id in sorted(affected):
        fact = state.facts[fact_id]
        replacements.append(
            {
                "id": fact_id,
                "payload": f"{fact.payload}{payload_suffix}",
                "sources": list(source_ids_for_fact(fact)),
            }
        )
    return Transaction(source_changes=changes, fact_replacements=replacements)
