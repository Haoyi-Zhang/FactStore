"""Immutable-segment store with one durable visibility frontier."""

from __future__ import annotations

from contextlib import contextmanager
import copy
import fcntl
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, BinaryIO, Callable, Iterator, Mapping, Sequence

from .model import (
    Delta,
    Fact,
    Source,
    State,
    Transaction,
    TransitionError,
    apply_transaction,
    canonical_json_bytes,
    capture_and_validate_delta,
    capture_closed_state,
    check_closure,
    require_closed,
    valid_identifier,
)

ROOT_FORMAT = "frontierstore-root"
SEGMENT_FORMAT = "frontierstore-segment"
ROOT_NAME = "ROOT"
SEGMENT_DIR = "segments"
_FINAL_SEGMENT = re.compile(r"^segment-[0-9]{8}\.jsonl$")

_BASE_ORDER = {"source_put": 0, "fact_put": 1, "reverse_put": 2}
_DELTA_ORDER = {
    "source_put": 0,
    "source_del": 1,
    "fact_del": 2,
    "fact_put": 3,
    "reverse_put": 4,
    "reverse_del": 5,
}

_ROOT_FIELDS = {"format", "epoch", "schema", "counts", "segments"}
_HEADER_FIELDS = {"kind", "format", "epoch", "parent", "mode", "schema"}
_FOOTER_FIELDS = {"kind", "records", "counts"}
_RECORD_FIELDS = {
    "source_put": {"kind", "id", "generation", "payload"},
    "source_del": {"kind", "id"},
    "fact_del": {"kind", "id"},
    "fact_put": {"kind", "id", "payload", "schema", "dependencies"},
    "reverse_put": {"kind", "id", "facts"},
    "reverse_del": {"kind", "id"},
}


class StoreFormatError(RuntimeError):
    """The selected durable representation is malformed or inconsistent."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class StoreUnavailableError(RuntimeError):
    """A failed publication makes the cached instance unusable."""

    code = "REOPEN_REQUIRED"

    def __init__(self) -> None:
        super().__init__("REOPEN_REQUIRED: abandon this instance and reopen quiescently")


def _unique_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise StoreFormatError("DUPLICATE_FIELD", key)
        value[key] = item
    return value


def _require_exact_fields(value: Mapping[str, Any], expected: set[str], role: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise StoreFormatError("FIELD_SET", f"{role}:missing={missing!r}:extra={extra!r}")


def _strict_identifier(value: Any, role: str) -> str:
    if not valid_identifier(value):
        raise StoreFormatError("IDENTIFIER", role)
    return value


def _strict_counts(value: Any, role: str) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != {"sources", "facts", "reverse"}:
        raise StoreFormatError("FIELD_TYPE", role)
    for key, count in value.items():
        _strict_int(count, f"{role}:{key}")
        if count < 0:
            raise StoreFormatError("FIELD_VALUE", f"{role}:{key}")
    return value


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_all(handle: BinaryIO, payload: bytes) -> None:
    """Complete a raw write or raise before its referent can be published."""
    remaining = memoryview(payload)
    while remaining:
        written = handle.write(remaining)
        if (not isinstance(written, int) or isinstance(written, bool)
                or written <= 0 or written > len(remaining)):
            raise OSError("raw write made no valid progress")
        remaining = remaining[written:]


def _strict_int(value: Any, role: str, *, positive: bool = False) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise StoreFormatError("FIELD_TYPE", f"{role} must be an integer")
    if positive and value <= 0:
        raise StoreFormatError("FIELD_VALUE", f"{role} must be positive")
    return value


def _strict_text(value: Any, role: str) -> str:
    if not isinstance(value, str):
        raise StoreFormatError("FIELD_TYPE", f"{role} must be text")
    return value


def _counts(state: State) -> dict[str, int]:
    return {"sources": len(state.sources), "facts": len(state.facts), "reverse": len(state.reverse)}


def _record_source(source: Source) -> dict[str, Any]:
    return {
        "kind": "source_put",
        "id": source.identifier,
        "generation": source.generation,
        "payload": source.payload,
    }


def _record_fact(fact: Fact) -> dict[str, Any]:
    return {
        "kind": "fact_put",
        "id": fact.identifier,
        "payload": fact.payload,
        "schema": fact.schema,
        "dependencies": [
            {"source": source_id, "generation": generation}
            for source_id, generation in fact.dependencies
        ],
    }


def _base_records(state: State) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    records.extend(_record_source(state.sources[key]) for key in sorted(state.sources))
    records.extend(_record_fact(state.facts[key]) for key in sorted(state.facts))
    records.extend(
        {"kind": "reverse_put", "id": key, "facts": list(state.reverse[key])}
        for key in sorted(state.reverse)
    )
    return records


def _delta_records(delta: Delta) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    records.extend(_record_source(delta.source_put[key]) for key in sorted(delta.source_put))
    records.extend({"kind": "source_del", "id": key} for key in sorted(delta.source_del))
    records.extend({"kind": "fact_del", "id": key} for key in sorted(delta.fact_del))
    records.extend(_record_fact(delta.fact_put[key]) for key in sorted(delta.fact_put))
    records.extend(
        {"kind": "reverse_put", "id": key, "facts": list(delta.reverse_put[key])}
        for key in sorted(delta.reverse_put)
    )
    records.extend({"kind": "reverse_del", "id": key} for key in sorted(delta.reverse_del))
    return records


def _json_line(value: Mapping[str, Any]) -> bytes:
    return canonical_json_bytes(dict(value))


def _copy_and_validate_delta(old: State, new: State, supplied: Delta) -> Delta:
    """Compatibility wrapper around the shared precomputed-transition check."""
    return capture_and_validate_delta(old, new, supplied)


class Snapshot:
    """An epoch-stable in-process reader view with defensive accessors."""

    def __init__(self, store: "FrontierStore", state: State, segments: tuple[str, ...]):
        self._store = store
        self._state = state
        self._segments = segments
        self._closed = False

    @property
    def epoch(self) -> int:
        return self._state.epoch

    @property
    def schema(self) -> int:
        return self._state.schema

    def get_source(self, source_id: str) -> dict[str, Any] | None:
        source = self._state.sources.get(source_id)
        return None if source is None else copy.deepcopy(source.as_dict())

    def get_fact(self, fact_id: str) -> dict[str, Any] | None:
        fact = self._state.facts.get(fact_id)
        return None if fact is None else copy.deepcopy(fact.as_dict())

    def reverse_for(self, source_id: str) -> tuple[str, ...] | None:
        members = self._state.reverse.get(source_id)
        return None if members is None else tuple(members)

    def materialize(self) -> dict[str, Any]:
        return copy.deepcopy(self._state.as_dict())

    def closure_errors(self) -> list[dict[str, Any]]:
        return check_closure(self._state)

    def close(self) -> None:
        # Idempotence and decrement share the collection lock, even when a
        # handle is closed by more than one reader thread.
        with self._store._lock:
            if not self._closed:
                self._closed = True
                self._store._release_pin(self._segments)

    def __enter__(self) -> "Snapshot":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


class FrontierStore:
    """One live instance per directory, with a writer and in-process readers.

    The file lock is not a cache-refresh or cross-instance pin protocol.
    A failed publication permanently disables current-state operations on this
    instance. Abandon it and reopen without overlapping writers or collectors.
    Previously captured handles retain only their in-memory contents after the
    instance is abandoned; their pins do not transfer to a reopened instance.
    """

    fail_exit_code = 73

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self.segment_dir = self.path / SEGMENT_DIR
        self.root_path = self.path / ROOT_NAME
        self.lock_path = self.path / "LOCK"
        self._lock = threading.RLock()
        self._pins: dict[tuple[str, ...], int] = {}
        self._failed = False
        self._state, self._segments = self._load_selected()

    @classmethod
    def create(
        cls,
        path: str | os.PathLike[str],
        state: State,
        *,
        replace: bool = False,
    ) -> "FrontierStore":
        captured = capture_closed_state(state)
        if not 1 <= captured.epoch <= 99_999_999:
            raise TransitionError("EPOCH_FORMAT_LIMIT", "epoch exceeds the segment-name domain")
        target = Path(path)
        if target.exists() and any(target.iterdir()):
            if not replace:
                raise FileExistsError(f"store path is not empty: {target}")
            import shutil

            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        segment_dir = target / SEGMENT_DIR
        segment_dir.mkdir(parents=True, exist_ok=True)
        (target / "LOCK").touch(exist_ok=True)
        writer = cls.__new__(cls)
        writer.path = target
        writer.segment_dir = segment_dir
        writer.root_path = target / ROOT_NAME
        writer.lock_path = target / "LOCK"
        writer._lock = threading.RLock()
        writer._pins = {}
        writer._failed = False
        writer._state = captured
        writer._segments = tuple()
        writer._publish(
            state=captured,
            records=_base_records(captured),
            mode="base",
            parent=None,
            selected_before=tuple(),
            failpoint=None,
            failure_action=None,
        )
        return cls(target)

    def _require_usable(self) -> None:
        if self._failed:
            raise StoreUnavailableError()

    @property
    def state(self) -> State:
        with self._lock:
            self._require_usable()
            return self._state.clone()

    @property
    def selected_segments(self) -> tuple[str, ...]:
        with self._lock:
            self._require_usable()
            return tuple(self._segments)

    def snapshot(self) -> Snapshot:
        with self._lock:
            self._require_usable()
            selected = tuple(self._segments)
            self._pins[selected] = self._pins.get(selected, 0) + 1
            return Snapshot(self, self._state, selected)

    def _release_pin(self, selected: tuple[str, ...]) -> None:
        with self._lock:
            count = self._pins.get(selected, 0)
            if count <= 1:
                self._pins.pop(selected, None)
            else:
                self._pins[selected] = count - 1

    def preview(self, transaction: Transaction) -> State:
        with self._lock:
            self._require_usable()
            state, _ = apply_transaction(self._state, transaction)
            return state.clone()

    @contextmanager
    def _writer_guard(self) -> Iterator[None]:
        with self._lock:
            self._require_usable()
            self.lock_path.touch(exist_ok=True)
            with self.lock_path.open("a+b") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def commit(
        self,
        transaction: Transaction,
        *,
        failpoint: str | None = None,
        failure_action: Callable[[str], None] | None = None,
    ) -> State:
        with self._writer_guard():
            state, delta = apply_transaction(self._state, transaction)
            selected = self._publish(
                state=state,
                records=_delta_records(delta),
                mode="delta",
                parent=self._state.epoch,
                selected_before=self._segments,
                failpoint=failpoint,
                failure_action=failure_action,
            )
            self._state = state
            self._segments = selected
            return state.clone()

    def publish_precomputed(
        self,
        state: State,
        delta: Delta,
        *,
        failpoint: str | None = None,
        failure_action: Callable[[str], None] | None = None,
    ) -> State:
        """Persist a closed, next-epoch transition after checking its delta.

        The state and delta are defensively captured before byte publication;
        caller mutation during or after the call cannot change the retained
        endpoint. This adapter checks representation agreement and source
        stamping, not the truth of fact payloads.
        """
        captured = capture_closed_state(state)
        with self._writer_guard():
            state = captured
            delta = _copy_and_validate_delta(self._state, state, delta)
            selected = self._publish(
                state=state,
                records=_delta_records(delta),
                mode="delta",
                parent=self._state.epoch,
                selected_before=self._segments,
                failpoint=failpoint,
                failure_action=failure_action,
            )
            self._state = state
            self._segments = selected
            return state.clone()

    def compact(
        self,
        *,
        failpoint: str | None = None,
        failure_action: Callable[[str], None] | None = None,
    ) -> State:
        with self._writer_guard():
            state = State(
                epoch=self._state.epoch + 1,
                schema=self._state.schema,
                sources=dict(self._state.sources),
                facts=dict(self._state.facts),
                reverse=dict(self._state.reverse),
            )
            require_closed(state)
            selected = self._publish(
                state=state,
                records=_base_records(state),
                mode="base",
                parent=None,
                selected_before=tuple(),
                failpoint=failpoint,
                failure_action=failure_action,
            )
            self._state = state
            self._segments = selected
            return state.clone()

    def collect(self, *, remove_temporary: bool = True) -> list[str]:
        with self._writer_guard():
            keep = set(self._segments)
            for pinned, count in self._pins.items():
                if count > 0:
                    keep.update(pinned)
            removed: list[str] = []
            for path in sorted(self.segment_dir.iterdir()):
                if path.name in keep:
                    continue
                if _FINAL_SEGMENT.fullmatch(path.name) or (remove_temporary and path.name.startswith(".")):
                    if path.is_file():
                        path.unlink()
                        removed.append(path.name)
            for path in sorted(self.path.glob(".ROOT.tmp-*")):
                if remove_temporary and path.is_file():
                    path.unlink()
                    removed.append(path.name)
            if removed:
                _fsync_directory(self.segment_dir)
                _fsync_directory(self.path)
            return removed

    def _trip(
        self,
        name: str,
        failpoint: str | None,
        failure_action: Callable[[str], None] | None,
    ) -> None:
        if failpoint != name:
            return
        if failure_action is not None:
            failure_action(name)
            return
        os._exit(self.fail_exit_code)

    def _publish(
        self,
        *,
        state: State,
        records: Sequence[Mapping[str, Any]],
        mode: str,
        parent: int | None,
        selected_before: tuple[str, ...],
        failpoint: str | None,
        failure_action: Callable[[str], None] | None,
    ) -> tuple[str, ...]:
        self._require_usable()
        if mode not in {"base", "delta"}:
            raise ValueError(mode)
        if not 1 <= state.epoch <= 99_999_999:
            raise TransitionError("EPOCH_FORMAT_LIMIT", "epoch exceeds the segment-name domain")
        try:
            selected = self._publish_bytes(
                state=state, records=records, mode=mode, parent=parent,
                selected_before=selected_before, failpoint=failpoint,
                failure_action=failure_action,
            )
            self._state = state
            self._segments = selected
            return selected
        except BaseException:
            # Replacement may already have happened. No cached-state operation,
            # especially collection, may follow a failed publication.
            self._failed = True
            raise

    def _publish_bytes(
        self,
        *,
        state: State,
        records: Sequence[Mapping[str, Any]],
        mode: str,
        parent: int | None,
        selected_before: tuple[str, ...],
        failpoint: str | None,
        failure_action: Callable[[str], None] | None,
    ) -> tuple[str, ...]:
        if mode not in {"base", "delta"}:
            raise ValueError(mode)
        if not 1 <= state.epoch <= 99_999_999:
            raise TransitionError("EPOCH_FORMAT_LIMIT", "epoch exceeds the segment-name domain")
        self._trip("before_segment", failpoint, failure_action)

        final_name = f"segment-{state.epoch:08d}.jsonl"
        final_path = self.segment_dir / final_name
        temp_path = self.segment_dir / f".{final_name}.tmp-{os.getpid()}"
        if final_path.exists():
            selected_or_pinned = final_name in set(self._segments) or any(
                final_name in pinned and count > 0 for pinned, count in self._pins.items()
            )
            if selected_or_pinned:
                raise FileExistsError(final_path)
            # A prior process may have synchronized this epoch file but died before
            # replacing the root.  Because visibility is root-selected, the file is
            # an inert orphan and can be removed before retrying the same epoch.
            final_path.unlink()
            _fsync_directory(self.segment_dir)
        if temp_path.exists():
            temp_path.unlink()

        header = {
            "kind": "header",
            "format": SEGMENT_FORMAT,
            "epoch": state.epoch,
            "parent": parent,
            "mode": mode,
            "schema": state.schema,
        }
        footer = {"kind": "footer", "records": len(records), "counts": _counts(state)}

        with temp_path.open("wb", buffering=0) as handle:
            _write_all(handle, _json_line(header))
            self._trip("after_header", failpoint, failure_action)
            for record in records:
                _write_all(handle, _json_line(record))
            self._trip("before_footer", failpoint, failure_action)
            _write_all(handle, _json_line(footer))
            self._trip("after_footer", failpoint, failure_action)
            os.fsync(handle.fileno())
        self._trip("after_segment_fsync", failpoint, failure_action)

        os.replace(temp_path, final_path)
        _fsync_directory(self.segment_dir)
        self._trip("after_segment_dirsync", failpoint, failure_action)

        selected = (final_name,) if mode == "base" else tuple(selected_before) + (final_name,)
        root = {
            "format": ROOT_FORMAT,
            "epoch": state.epoch,
            "schema": state.schema,
            "counts": _counts(state),
            "segments": list(selected),
        }
        root_temp = self.path / f".ROOT.tmp-{os.getpid()}"
        with root_temp.open("wb", buffering=0) as handle:
            _write_all(handle, _json_line(root))
            self._trip("after_root_write", failpoint, failure_action)
            os.fsync(handle.fileno())
        self._trip("after_root_fsync", failpoint, failure_action)

        os.replace(root_temp, self.root_path)
        self._trip("after_root_replace", failpoint, failure_action)
        _fsync_directory(self.path)
        self._trip("after_root_dirsync", failpoint, failure_action)
        return selected

    def _confined_segment(self, name: Any) -> Path:
        if not isinstance(name, str) or not _FINAL_SEGMENT.fullmatch(name):
            raise StoreFormatError("PATH", f"invalid selected segment path: {name!r}")
        candidate = (self.segment_dir / name).resolve()
        directory = self.segment_dir.resolve()
        if os.path.commonpath([str(candidate), str(directory)]) != str(directory):
            raise StoreFormatError("PATH", f"selected path escapes segment directory: {name!r}")
        return candidate

    def _load_selected(self) -> tuple[State, tuple[str, ...]]:
        try:
            raw_root = self.root_path.read_text(encoding="ascii")
        except FileNotFoundError as exc:
            raise StoreFormatError("ROOT_MISSING", ROOT_NAME) from exc
        except (OSError, UnicodeDecodeError) as exc:
            raise StoreFormatError("ROOT_PARSE", "unreadable root") from exc
        try:
            root = json.loads(raw_root, object_pairs_hook=_unique_members)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise StoreFormatError("ROOT_PARSE", str(exc)) from exc
        if not isinstance(root, dict) or root.get("format") != ROOT_FORMAT:
            raise StoreFormatError("ROOT_FORMAT", "unrecognized root")
        _require_exact_fields(root, _ROOT_FIELDS, "root")
        root_epoch = _strict_int(root.get("epoch"), "root epoch", positive=True)
        root_schema = _strict_int(root.get("schema"), "root schema", positive=True)
        root_counts = _strict_counts(root.get("counts"), "root counts")
        selected_raw = root.get("segments")
        if not isinstance(selected_raw, list) or not selected_raw:
            raise StoreFormatError("ROOT_SEGMENTS", "selected segment list is empty or malformed")
        selected = tuple(_strict_text(value, "selected segment") for value in selected_raw)

        sources: dict[str, Source] = {}
        facts: dict[str, Fact] = {}
        reverse: dict[str, tuple[str, ...]] = {}
        previous_epoch: int | None = None
        active_schema: int | None = None

        for index, name in enumerate(selected):
            path = self._confined_segment(name)
            if not path.exists():
                raise StoreFormatError("SEGMENT_MISSING", name)
            try:
                lines = path.read_text(encoding="ascii").splitlines()
            except (OSError, UnicodeDecodeError) as exc:
                raise StoreFormatError("SEGMENT_READ", f"{name}: {exc}") from exc
            if len(lines) < 2:
                raise StoreFormatError("SEGMENT_INCOMPLETE", name)
            try:
                values = [json.loads(line, object_pairs_hook=_unique_members) for line in lines]
            except json.JSONDecodeError as exc:
                raise StoreFormatError("SEGMENT_PARSE", f"{name}:{exc.lineno}") from exc
            header = values[0]
            footer = values[-1]
            if not isinstance(header, dict) or header.get("kind") != "header" or header.get("format") != SEGMENT_FORMAT:
                raise StoreFormatError("SEGMENT_HEADER", name)
            _require_exact_fields(header, _HEADER_FIELDS, f"{name}:header")
            if not isinstance(footer, dict) or footer.get("kind") != "footer":
                raise StoreFormatError("SEGMENT_FOOTER", name)
            _require_exact_fields(footer, _FOOTER_FIELDS, f"{name}:footer")
            epoch = _strict_int(header.get("epoch"), "segment epoch", positive=True)
            schema = _strict_int(header.get("schema"), "segment schema", positive=True)
            if epoch != int(name[8:16]):
                raise StoreFormatError("EPOCH_NAME", name)
            mode = header.get("mode")
            parent = header.get("parent")
            if not isinstance(mode, str) or mode not in {"base", "delta"}:
                raise StoreFormatError("SEGMENT_MODE", name)
            if index == 0:
                if mode != "base" or parent is not None:
                    raise StoreFormatError("FIRST_BASE", name)
                sources, facts, reverse = {}, {}, {}
            else:
                parent = _strict_int(parent, "parent epoch", positive=True)
                if mode != "delta" or parent != previous_epoch:
                    raise StoreFormatError("PARENT_CHAIN", name)
                if epoch != int(previous_epoch) + 1:
                    raise StoreFormatError("EPOCH_CHAIN", name)
            previous_epoch = epoch
            active_schema = schema

            body = values[1:-1]
            expected_order = _BASE_ORDER if mode == "base" else _DELTA_ORDER
            previous_phase = -1
            per_kind_ids: dict[str, set[str]] = {kind: set() for kind in expected_order}
            for record in body:
                if not isinstance(record, dict):
                    raise StoreFormatError("RECORD_TYPE", name)
                kind = record.get("kind")
                if not isinstance(kind, str) or kind not in expected_order:
                    raise StoreFormatError("RECORD_KIND", f"{name}:{kind!r}")
                _require_exact_fields(record, _RECORD_FIELDS[kind], f"{name}:{kind}")
                phase = expected_order[kind]
                if phase < previous_phase:
                    raise StoreFormatError("RECORD_ORDER", f"{name}:{kind}")
                previous_phase = phase
                identifier = _strict_identifier(record.get("id"), f"{kind} identifier")
                if identifier in per_kind_ids[kind]:
                    raise StoreFormatError("DUPLICATE_RECORD", f"{name}:{kind}:{identifier}")
                per_kind_ids[kind].add(identifier)
                if kind == "source_put":
                    generation = _strict_int(record.get("generation"), "source generation", positive=True)
                    payload = _strict_text(record.get("payload"), "source payload")
                    source = Source(identifier, generation, payload)
                    if mode == "delta" and sources.get(identifier) != source and generation != epoch:
                        raise StoreFormatError("SOURCE_STAMP", identifier)
                    sources[identifier] = source
                elif kind == "source_del":
                    sources.pop(identifier, None)
                elif kind == "fact_del":
                    facts.pop(identifier, None)
                elif kind == "fact_put":
                    payload = _strict_text(record.get("payload"), "fact payload")
                    fact_schema = _strict_int(record.get("schema"), "fact schema", positive=True)
                    raw_dependencies = record.get("dependencies")
                    if not isinstance(raw_dependencies, list):
                        raise StoreFormatError("DEPENDENCY_TYPE", identifier)
                    dependencies: list[tuple[str, int]] = []
                    for dependency in raw_dependencies:
                        if not isinstance(dependency, dict) or set(dependency) != {"source", "generation"}:
                            raise StoreFormatError("DEPENDENCY_SHAPE", identifier)
                        dependencies.append(
                            (
                                _strict_identifier(dependency["source"], "dependency source"),
                                _strict_int(dependency["generation"], "dependency generation", positive=True),
                            )
                        )
                    facts[identifier] = Fact(identifier, payload, fact_schema, tuple(dependencies))
                elif kind == "reverse_put":
                    raw_facts = record.get("facts")
                    if not isinstance(raw_facts, list) or not all(isinstance(value, str) for value in raw_facts):
                        raise StoreFormatError("REVERSE_TYPE", identifier)
                    for member in raw_facts:
                        _strict_identifier(member, "reverse fact identifier")
                    reverse[identifier] = tuple(raw_facts)
                elif kind == "reverse_del":
                    reverse.pop(identifier, None)

            if mode == "delta":
                if per_kind_ids["source_put"] & per_kind_ids["source_del"]:
                    raise StoreFormatError("DELTA_SOURCE_OVERLAP", name)
                if per_kind_ids["reverse_put"] & per_kind_ids["reverse_del"]:
                    raise StoreFormatError("DELTA_REVERSE_OVERLAP", name)
            record_count = _strict_int(footer.get("records"), "footer record count")
            if record_count != len(body):
                raise StoreFormatError("RECORD_COUNT", name)
            footer_counts = _strict_counts(footer.get("counts"), "footer counts")
            actual_counts = {"sources": len(sources), "facts": len(facts), "reverse": len(reverse)}
            if footer_counts != actual_counts:
                raise StoreFormatError("FOOTER_COUNTS", f"{name}:{footer_counts!r}!={actual_counts!r}")
            intermediate = State(epoch=epoch, schema=schema, sources=dict(sources), facts=dict(facts), reverse=dict(reverse))
            errors = check_closure(intermediate)
            if errors:
                raise StoreFormatError(errors[0]["code"], json.dumps(errors[0]["detail"], sort_keys=True))

        if previous_epoch != root_epoch or active_schema != root_schema:
            raise StoreFormatError("ROOT_TIP", "root does not match selected tip")
        state = State(epoch=root_epoch, schema=root_schema, sources=sources, facts=facts, reverse=reverse)
        actual_root_counts = {"sources": len(sources), "facts": len(facts), "reverse": len(reverse)}
        if root_counts != actual_root_counts:
            raise StoreFormatError("ROOT_COUNTS", f"{root_counts!r}!={actual_root_counts!r}")
        errors = check_closure(state)
        if errors:
            raise StoreFormatError(errors[0]["code"], json.dumps(errors[0]["detail"], sort_keys=True))
        return state, selected
