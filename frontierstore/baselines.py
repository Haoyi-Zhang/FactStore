"""Transparent durable storage design points used by the evaluation."""

from __future__ import annotations

import dbm.dumb
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from typing import Any

from .audit_export import AuditExportError, parse_audit_export
from .model import (
    Delta, Fact, Source, State, TransitionError, canonical_state_bytes,
    capture_and_validate_delta, capture_closed_state, check_closure, require_closed,
    valid_identifier,
)


class BaselineFormatError(RuntimeError):
    """A durable baseline image does not encode one exact closed state."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class BaselineUnavailableError(RuntimeError):
    """A publication exception makes the cached rooted-audit instance unusable."""

    code = "REOPEN_REQUIRED"

    def __init__(self) -> None:
        super().__init__(
            "REOPEN_REQUIRED: abandon this instance and reopen from the selected root"
        )


_DBM_IMAGE = re.compile(r"^image-([0-9]{8})$")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise BaselineFormatError("DUPLICATE_FIELD", key)
        value[key] = item
    return value


def _capture_full_state(state: State) -> State:
    captured = capture_closed_state(state)
    if not 1 <= captured.epoch <= 99_999_999:
        raise TransitionError("EPOCH_FORMAT_LIMIT", "epoch exceeds the immutable-image name domain")
    return captured


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _strict_positive_integer(value: Any, role: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise BaselineFormatError("FIELD_VALUE", f"{role} must be a positive integer")
    return value


def _strict_identifier(value: Any, role: str) -> str:
    if not valid_identifier(value):
        raise BaselineFormatError("IDENTIFIER", role)
    return value


def _exact_mapping(value: Any, fields: set[str], role: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise BaselineFormatError("FIELD_SET", role)
    return value


def _state_from_dict(value: dict[str, Any]) -> State:
    top = _exact_mapping(value, {"epoch", "schema", "sources", "facts", "reverse"}, "state")
    epoch = _strict_positive_integer(top["epoch"], "epoch")
    schema = _strict_positive_integer(top["schema"], "schema")
    if not isinstance(top["sources"], dict):
        raise BaselineFormatError("FIELD_TYPE", "sources")
    if not isinstance(top["facts"], dict):
        raise BaselineFormatError("FIELD_TYPE", "facts")
    if not isinstance(top["reverse"], dict):
        raise BaselineFormatError("FIELD_TYPE", "reverse")

    sources: dict[str, Source] = {}
    for key, raw in top["sources"].items():
        source_id = _strict_identifier(key, "source key")
        row = _exact_mapping(raw, {"id", "generation", "payload"}, f"source:{source_id}")
        if _strict_identifier(row["id"], "source id") != source_id:
            raise BaselineFormatError("KEY_MISMATCH", f"source:{source_id}")
        generation = _strict_positive_integer(row["generation"], f"source generation:{source_id}")
        if not isinstance(row["payload"], str):
            raise BaselineFormatError("FIELD_TYPE", f"source payload:{source_id}")
        sources[source_id] = Source(source_id, generation, row["payload"])

    facts: dict[str, Fact] = {}
    for key, raw in top["facts"].items():
        fact_id = _strict_identifier(key, "fact key")
        row = _exact_mapping(raw, {"id", "payload", "schema", "dependencies"}, f"fact:{fact_id}")
        if _strict_identifier(row["id"], "fact id") != fact_id:
            raise BaselineFormatError("KEY_MISMATCH", f"fact:{fact_id}")
        if not isinstance(row["payload"], str):
            raise BaselineFormatError("FIELD_TYPE", f"fact payload:{fact_id}")
        fact_schema = _strict_positive_integer(row["schema"], f"fact schema:{fact_id}")
        raw_dependencies = row["dependencies"]
        if not isinstance(raw_dependencies, list) or not raw_dependencies:
            raise BaselineFormatError("DEPENDENCY_SHAPE", fact_id)
        dependencies: list[tuple[str, int]] = []
        for raw_dependency in raw_dependencies:
            dependency = _exact_mapping(
                raw_dependency, {"source", "generation"}, f"dependency:{fact_id}"
            )
            dependencies.append((
                _strict_identifier(dependency["source"], "dependency source"),
                _strict_positive_integer(
                    dependency["generation"], f"dependency generation:{fact_id}"
                ),
            ))
        facts[fact_id] = Fact(fact_id, row["payload"], fact_schema, tuple(dependencies))

    reverse: dict[str, tuple[str, ...]] = {}
    for key, raw_members in top["reverse"].items():
        source_id = _strict_identifier(key, "reverse key")
        if not isinstance(raw_members, list):
            raise BaselineFormatError("FIELD_TYPE", f"reverse:{source_id}")
        reverse[source_id] = tuple(
            _strict_identifier(member, f"reverse member:{source_id}")
            for member in raw_members
        )

    state = State(epoch=epoch, schema=schema, sources=sources, facts=facts, reverse=reverse)
    errors = check_closure(state)
    if errors:
        raise BaselineFormatError(errors[0]["code"], repr(errors[0]["detail"]))
    return state


def _sqlite_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sources (
            id TEXT PRIMARY KEY,
            generation INTEGER NOT NULL,
            payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS facts (
            id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            schema INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dependencies (
            fact_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            generation INTEGER NOT NULL,
            PRIMARY KEY (fact_id, source_id)
        );
        CREATE INDEX IF NOT EXISTS dependencies_by_source
            ON dependencies(source_id, fact_id);
        """
    )


def _insert_full_state(connection: sqlite3.Connection, state: State) -> None:
    connection.execute("DELETE FROM dependencies")
    connection.execute("DELETE FROM facts")
    connection.execute("DELETE FROM sources")
    connection.execute("DELETE FROM meta")
    connection.executemany(
        "INSERT INTO sources(id,generation,payload) VALUES(?,?,?)",
        [
            (source.identifier, source.generation, source.payload)
            for source in (state.sources[key] for key in sorted(state.sources))
        ],
    )
    connection.executemany(
        "INSERT INTO facts(id,payload,schema) VALUES(?,?,?)",
        [
            (fact.identifier, fact.payload, fact.schema)
            for fact in (state.facts[key] for key in sorted(state.facts))
        ],
    )
    dependency_rows = []
    for fact_id in sorted(state.facts):
        for source_id, generation in state.facts[fact_id].dependencies:
            dependency_rows.append((fact_id, source_id, generation))
    connection.executemany(
        "INSERT INTO dependencies(fact_id,source_id,generation) VALUES(?,?,?)",
        dependency_rows,
    )
    connection.executemany(
        "INSERT INTO meta(key,value) VALUES(?,?)",
        [("epoch", state.epoch), ("schema", state.schema)],
    )


def _read_sqlite_state(path: Path) -> State:
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error as error:
        raise BaselineFormatError("SQLITE_OPEN", str(error)) from error
    try:
        # All families must come from one read transaction. Separate completed
        # SELECTs can otherwise straddle a writer's atomic commit and still
        # produce a closed, but nonendpoint, combination.
        connection.execute("BEGIN")
        meta_rows = list(connection.execute("SELECT key,value FROM meta ORDER BY key"))
        if len(meta_rows) != 2 or {row[0] for row in meta_rows} != {"epoch", "schema"}:
            raise BaselineFormatError("META_SET", repr(meta_rows))
        meta = {str(key): value for key, value in meta_rows}
        epoch = _strict_positive_integer(meta["epoch"], "meta epoch")
        schema = _strict_positive_integer(meta["schema"], "meta schema")

        sources: dict[str, Source] = {}
        for raw_id, generation, payload in connection.execute(
            "SELECT id,generation,payload FROM sources ORDER BY id"
        ):
            source_id = _strict_identifier(raw_id, "SQLite source id")
            if not isinstance(payload, str):
                raise BaselineFormatError("FIELD_TYPE", f"SQLite source payload:{source_id}")
            sources[source_id] = Source(
                source_id,
                _strict_positive_integer(generation, f"SQLite source generation:{source_id}"),
                payload,
            )

        facts_data: dict[str, dict[str, Any]] = {}
        for raw_id, payload, fact_schema in connection.execute(
            "SELECT id,payload,schema FROM facts ORDER BY id"
        ):
            fact_id = _strict_identifier(raw_id, "SQLite fact id")
            if not isinstance(payload, str):
                raise BaselineFormatError("FIELD_TYPE", f"SQLite fact payload:{fact_id}")
            facts_data[fact_id] = {
                "payload": payload,
                "schema": _strict_positive_integer(
                    fact_schema, f"SQLite fact schema:{fact_id}"
                ),
                "dependencies": [],
            }

        for raw_fact_id, raw_source_id, generation in connection.execute(
            "SELECT fact_id,source_id,generation FROM dependencies ORDER BY fact_id,source_id"
        ):
            fact_id = _strict_identifier(raw_fact_id, "SQLite dependency fact id")
            source_id = _strict_identifier(raw_source_id, "SQLite dependency source id")
            if fact_id not in facts_data:
                raise BaselineFormatError("MISSING_FACT", fact_id)
            if source_id not in sources:
                raise BaselineFormatError("MISSING_SOURCE", f"{fact_id}:{source_id}")
            facts_data[fact_id]["dependencies"].append(
                (
                    source_id,
                    _strict_positive_integer(
                        generation, f"SQLite dependency generation:{fact_id}:{source_id}"
                    ),
                )
            )

        facts = {
            fact_id: Fact(
                fact_id,
                data["payload"],
                data["schema"],
                tuple(data["dependencies"]),
            )
            for fact_id, data in facts_data.items()
        }
        reverse_sets: dict[str, list[str]] = {source_id: [] for source_id in sources}
        for fact_id, fact in facts.items():
            for source_id, _ in fact.dependencies:
                reverse_sets[source_id].append(fact_id)
        reverse = {
            source_id: tuple(sorted(members))
            for source_id, members in reverse_sets.items()
        }
        state = State(epoch, schema, sources, facts, reverse)
        errors = check_closure(state)
        if errors:
            raise BaselineFormatError(errors[0]["code"], repr(errors[0]["detail"]))
        return state
    except sqlite3.Error as error:
        raise BaselineFormatError("SQLITE_READ", str(error)) from error
    finally:
        connection.close()


class SQLiteNormalized:
    name = "SQLite-Normalized"

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self.db_path = self.path / "normalized.sqlite"
        if not self.db_path.is_file():
            raise BaselineFormatError("SQLITE_MISSING", self.db_path.name)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA foreign_keys=OFF")
        _sqlite_schema(self.connection)
        self._state = _read_sqlite_state(self.db_path)

    @classmethod
    def create(cls, path: str | os.PathLike[str], state: State) -> "SQLiteNormalized":
        captured = capture_closed_state(state)
        target = Path(path)
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        db_path = target / "normalized.sqlite"
        connection = sqlite3.connect(db_path)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            _sqlite_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            _insert_full_state(connection, captured)
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            connection.close()
        _fsync_directory(target)
        return cls(target)

    @property
    def state(self) -> State:
        return self._state.clone()

    def persist_precomputed(self, state: State, delta: Delta) -> None:
        captured = capture_closed_state(state)
        delta = capture_and_validate_delta(self._state, captured, delta)
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            for source_id in delta.source_del:
                connection.execute("DELETE FROM sources WHERE id=?", (source_id,))
            for source_id in sorted(delta.source_put):
                source = delta.source_put[source_id]
                connection.execute(
                    "INSERT INTO sources(id,generation,payload) VALUES(?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET generation=excluded.generation,payload=excluded.payload",
                    (source.identifier, source.generation, source.payload),
                )
            for fact_id in delta.fact_del:
                connection.execute("DELETE FROM dependencies WHERE fact_id=?", (fact_id,))
                connection.execute("DELETE FROM facts WHERE id=?", (fact_id,))
            for fact_id in sorted(delta.fact_put):
                fact = delta.fact_put[fact_id]
                connection.execute("DELETE FROM dependencies WHERE fact_id=?", (fact_id,))
                connection.execute(
                    "INSERT INTO facts(id,payload,schema) VALUES(?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,schema=excluded.schema",
                    (fact.identifier, fact.payload, fact.schema),
                )
                connection.executemany(
                    "INSERT INTO dependencies(fact_id,source_id,generation) VALUES(?,?,?)",
                    [(fact.identifier, source_id, generation) for source_id, generation in fact.dependencies],
                )
            connection.execute(
                "INSERT INTO meta(key,value) VALUES('epoch',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (captured.epoch,),
            )
            connection.execute(
                "INSERT INTO meta(key,value) VALUES('schema',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (captured.schema,),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        self._state = captured

    def durable_state(self) -> State:
        return _read_sqlite_state(self.db_path)

    def query_fact(self, fact_id: str) -> str | None:
        row = self.connection.execute("SELECT payload FROM facts WHERE id=?", (fact_id,)).fetchone()
        return None if row is None else str(row[0])

    def close(self) -> None:
        self.connection.close()


class SQLiteRebuild:
    name = "SQLite-Rebuild"

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self.db_path = self.path / "image.sqlite"
        self._state = _read_sqlite_state(self.db_path)

    @classmethod
    def create(cls, path: str | os.PathLike[str], state: State) -> "SQLiteRebuild":
        captured = capture_closed_state(state)
        target = Path(path)
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        instance = cls.__new__(cls)
        instance.path = target
        instance.db_path = target / "image.sqlite"
        instance._write_image(captured, instance.db_path)
        instance._state = captured
        _fsync_directory(target)
        return cls(target)

    @staticmethod
    def _write_image(state: State, path: Path) -> None:
        if path.exists():
            path.unlink()
        connection = sqlite3.connect(path)
        try:
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=OFF")
            _sqlite_schema(connection)
            connection.execute("BEGIN")
            _insert_full_state(connection, state)
            connection.commit()
        finally:
            connection.close()
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @property
    def state(self) -> State:
        return self._state.clone()

    def persist_precomputed(self, state: State, delta: Delta) -> None:
        captured = capture_closed_state(state)
        capture_and_validate_delta(self._state, captured, delta)
        temp = self.path / f".image.sqlite.tmp-{os.getpid()}"
        self._write_image(captured, temp)
        os.replace(temp, self.db_path)
        _fsync_directory(self.path)
        self._state = captured

    def durable_state(self) -> State:
        return _read_sqlite_state(self.db_path)

    def query_fact(self, fact_id: str) -> str | None:
        fact = self._state.facts.get(fact_id)
        return None if fact is None else fact.payload

    def close(self) -> None:
        return None


class DBMDumbRebuild:
    name = "DBM-Dumb-Rebuild"

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self.images = self.path / "images"
        self.root_path = self.path / "CURRENT"
        self._state = self.durable_state()

    @classmethod
    def create(cls, path: str | os.PathLike[str], state: State) -> "DBMDumbRebuild":
        captured = _capture_full_state(state)
        target = Path(path)
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        (target / "images").mkdir()
        instance = cls.__new__(cls)
        instance.path = target
        instance.images = target / "images"
        instance.root_path = target / "CURRENT"
        instance._write_generation(captured)
        instance._state = captured
        return cls(target)

    def _write_generation(self, state: State) -> None:
        generation_name = f"image-{state.epoch:08d}"
        generation_dir = self.images / generation_name
        if generation_dir.exists():
            shutil.rmtree(generation_dir)
        generation_dir.mkdir()
        database = dbm.dumb.open(str(generation_dir / "state"), "n")
        try:
            database[b"state"] = canonical_state_bytes(state)
            database.sync()
        finally:
            database.close()
        for file_path in generation_dir.iterdir():
            if file_path.is_file():
                descriptor = os.open(file_path, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        _fsync_directory(generation_dir)
        _fsync_directory(self.images)
        temporary = self.path / f".CURRENT.tmp-{os.getpid()}"
        with temporary.open("wb", buffering=0) as handle:
            _write_all_raw(handle, (generation_name + "\n").encode("ascii"))
            os.fsync(handle.fileno())
        os.replace(temporary, self.root_path)
        _fsync_directory(self.path)
        for candidate in self.images.iterdir():
            if candidate.name != generation_name and candidate.is_dir():
                shutil.rmtree(candidate)
        _fsync_directory(self.images)

    @property
    def state(self) -> State:
        return self._state.clone()

    def persist_precomputed(self, state: State, delta: Delta) -> None:
        captured = _capture_full_state(state)
        capture_and_validate_delta(self._state, captured, delta)
        self._write_generation(captured)
        self._state = captured

    def durable_state(self) -> State:
        try:
            root_text = self.root_path.read_text(encoding="ascii")
        except (OSError, UnicodeError) as error:
            raise BaselineFormatError("DBM_ROOT_READ", str(error)) from error
        generation_name = root_text.rstrip("\n")
        if root_text != generation_name + "\n":
            raise BaselineFormatError("DBM_ROOT_SHAPE", repr(root_text))
        match = _DBM_IMAGE.fullmatch(generation_name)
        if match is None:
            raise BaselineFormatError("DBM_ROOT_NAME", generation_name)
        generation_dir = self.images / generation_name
        try:
            database = dbm.dumb.open(str(generation_dir / "state"), "r")
            try:
                raw = bytes(database[b"state"]).decode("ascii")
            finally:
                database.close()
            value = json.loads(raw, object_pairs_hook=_unique_json_object)
        except BaselineFormatError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError) as error:
            raise BaselineFormatError("DBM_STATE_READ", str(error)) from error
        state = _state_from_dict(value)
        if state.epoch != int(match.group(1)):
            raise BaselineFormatError("DBM_EPOCH_NAME", generation_name)
        return state

    def query_fact(self, fact_id: str) -> str | None:
        fact = self._state.facts.get(fact_id)
        return None if fact is None else fact.payload

    def close(self) -> None:
        return None


def _write_all_raw(handle, data: bytes) -> None:
    view = memoryview(data)
    written = 0
    while written < len(view):
        progress = handle.write(view[written:])
        if (not isinstance(progress, int) or isinstance(progress, bool) or
                progress <= 0 or progress > len(view) - written):
            raise OSError("raw write made invalid progress")
        written += progress


def _write_canonical_export(path: Path, state: State) -> None:
    with path.open("wb", buffering=0) as handle:
        _write_all_raw(handle, canonical_state_bytes(state))
        os.fsync(handle.fileno())


class SQLiteRootedAudit:
    """Full-image SQLite plus a root-selected database-independent export.

    Both immutable files are completed before one small root replacement. This
    generic composition matches the old-or-new cross-interface publication shape
    but rewrites the complete database and audit image on every update.
    """

    name = "SQLite-Rooted-Audit"

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self.images = self.path / "images"
        self.exports = self.path / "exports"
        self.root_path = self.path / "ROOT"
        self._failed = False
        self._state = self._read_selected_pair()

    @classmethod
    def create(cls, path: str | os.PathLike[str], state: State) -> "SQLiteRootedAudit":
        captured = _capture_full_state(state)
        target = Path(path)
        if target.exists():
            shutil.rmtree(target)
        (target / "images").mkdir(parents=True)
        (target / "exports").mkdir()
        instance = cls.__new__(cls)
        instance.path = target
        instance.images = target / "images"
        instance.exports = target / "exports"
        instance.root_path = target / "ROOT"
        instance._failed = False
        instance._publish_full(captured)
        instance._state = captured
        return cls(target)

    def _require_usable(self) -> None:
        if self._failed:
            raise BaselineUnavailableError()

    def _read_root(self) -> dict[str, object]:
        try:
            raw = self.root_path.read_text(encoding="ascii")
            value = json.loads(raw, object_pairs_hook=_unique_json_object)
        except BaselineFormatError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise BaselineFormatError("ROOT_READ", str(error)) from error
        if not isinstance(value, dict) or set(value) != {"format", "epoch", "database", "export"}:
            raise BaselineFormatError("ROOT_SHAPE", "expected format, epoch, database, export")
        if value["format"] != "sqlite-rooted-audit-1":
            raise BaselineFormatError("ROOT_FORMAT", repr(value["format"]))
        epoch = value["epoch"]
        if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch <= 0 or epoch > 99_999_999:
            raise BaselineFormatError("ROOT_EPOCH", repr(epoch))
        if value["database"] != f"image-{epoch:08d}.sqlite" or value["export"] != f"audit-{epoch:08d}.json":
            raise BaselineFormatError("ROOT_FILENAMES", repr((value["database"], value["export"])))
        return value

    def _read_selected_pair(self) -> State:
        root = self._read_root()
        epoch = int(root["epoch"])
        state = _read_sqlite_state(self.images / str(root["database"]))
        exported = parse_audit_export(self.exports / str(root["export"]))
        if state.epoch != epoch or int(exported["epoch"]) != epoch:
            raise BaselineFormatError("ROOT_EPOCH_MISMATCH", str(epoch))
        if exported != state.as_dict():
            raise BaselineFormatError("PAIR_MISMATCH", "database and audit export differ")
        return state


    def _publish_full(self, state: State) -> None:
        # ``state`` is an already captured, closed value. Both immutable objects
        # are derived from this same copy before the selector is replaced.
        database_name = f"image-{state.epoch:08d}.sqlite"
        export_name = f"audit-{state.epoch:08d}.json"
        database_path = self.images / database_name
        export_path = self.exports / export_name
        # A prior process may have completed one or both immutable objects but
        # failed before replacing ROOT.  Such objects are invisible and, under
        # the documented single-writer premise, can be removed before retrying
        # the same next epoch.  Never remove an object selected by the root.
        selected_names: set[str] = set()
        if self.root_path.exists():
            selected_root = self._read_root()
            selected_names = {str(selected_root["database"]), str(selected_root["export"])}
        removed_database = False
        removed_export = False
        for candidate in (database_path, export_path):
            if not candidate.exists():
                continue
            if candidate.name in selected_names:
                raise FileExistsError("refusing to replace selected rooted-audit object")
            candidate.unlink()
            if candidate.parent == self.images:
                removed_database = True
            else:
                removed_export = True
        if removed_database:
            _fsync_directory(self.images)
        if removed_export:
            _fsync_directory(self.exports)
        SQLiteRebuild._write_image(state, database_path)
        _fsync_directory(self.images)
        _write_canonical_export(export_path, state)
        _fsync_directory(self.exports)
        selector = {
            "format": "sqlite-rooted-audit-1",
            "epoch": state.epoch,
            "database": database_name,
            "export": export_name,
        }
        temporary = self.path / f".ROOT.tmp-{os.getpid()}"
        with temporary.open("wb", buffering=0) as handle:
            _write_all_raw(
                handle,
                (json.dumps(selector, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii"),
            )
            os.fsync(handle.fileno())
        os.replace(temporary, self.root_path)
        _fsync_directory(self.path)

    @property
    def state(self) -> State:
        self._require_usable()
        return self._state.clone()

    def persist_precomputed(self, state: State, delta: Delta) -> None:
        self._require_usable()
        captured = _capture_full_state(state)
        captured_delta = capture_and_validate_delta(self._state, captured, delta)
        # Detect another publisher or external selector mutation before creating
        # new immutable objects.  Cross-instance concurrent writers are outside
        # the model; fail closed rather than publishing from a stale cache.
        try:
            selected_root = self._read_root()
            if int(selected_root["epoch"]) != self._state.epoch:
                raise BaselineUnavailableError()
        except BaseException:
            self._failed = True
            raise
        # Validation above is side-effect free and does not disable the instance.
        # Once publication starts, any escaping exception leaves the return value
        # indeterminate: the selector may name either endpoint and immutable
        # objects may remain.  Conservatively require a quiescent reopen.
        del captured_delta  # validation result is intentionally not re-used here
        try:
            self._publish_full(captured)
        except BaseException:
            self._failed = True
            raise
        self._state = captured

    def durable_state(self) -> State:
        self._require_usable()
        return self._read_selected_pair()

    def audit_report(self) -> dict[str, object]:
        try:
            self._require_usable()
            state = self._read_selected_pair()
            return {
                "status": "ACCEPT",
                "epoch": state.epoch,
                "sources": len(state.sources),
                "facts": len(state.facts),
            }
        except (BaselineUnavailableError, BaselineFormatError, AuditExportError) as error:
            witness = getattr(error, "code", getattr(error, "witness", "PAIR_REJECT"))
            detail = getattr(error, "detail", str(error))
            return {"status": "REJECT", "witness": witness, "detail": detail}

    def query_fact(self, fact_id: str) -> str | None:
        self._require_usable()
        fact = self._state.facts.get(fact_id)
        return None if fact is None else fact.payload

    def close(self) -> None:
        return None


ENGINE_CLASSES = {
    "FrontierStore": None,
    SQLiteNormalized.name: SQLiteNormalized,
    SQLiteRebuild.name: SQLiteRebuild,
    SQLiteRootedAudit.name: SQLiteRootedAudit,
    DBMDumbRebuild.name: DBMDumbRebuild,
}
