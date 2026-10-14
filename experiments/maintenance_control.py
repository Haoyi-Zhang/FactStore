"""Measure SQLite maintenance and aligned current/history retention separately."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import platform
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from frontierstore.baselines import SQLiteNormalized, _insert_full_state, _read_sqlite_state, _sqlite_schema
from frontierstore.audit_export import parse_audit_export
from frontierstore.model import apply_transaction, canonical_state_bytes, states_equal
from frontierstore.workload import make_synthetic_state, make_update


def disk_bytes(path):
    return sum(file.stat().st_size for file in path.iterdir() if file.is_file())


def run(source_count=500, updates=16):
    if source_count < 2 or updates < 1:
        raise ValueError("requires at least two sources and one update")
    with tempfile.TemporaryDirectory(prefix="factstore-maintenance-") as temporary:
        root = Path(temporary)
        path = root / "normalized.sqlite"
        connection = sqlite3.connect(path)
        state = make_synthetic_state(source_count)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        _sqlite_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        _insert_full_state(connection, state)
        connection.commit()
        initial_checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        # Use the existing normalized delta implementation without replacing its
        # writes, transactions or durability settings. Bootstrap directory fsync
        # is excluded here: this experiment measures the SQLite API on this host.
        store = SQLiteNormalized.__new__(SQLiteNormalized)
        store.path, store.db_path, store.connection, store._state = root, path, connection, state
        history = root / "history"
        history.mkdir()
        current_bytes = []
        for index in range(updates + 1):
            if index:
                state, delta = apply_transaction(state, make_update(state, batch=min(8, source_count), seed=100 + index))
                store.persist_precomputed(state, delta)
            image = history / f"image-{index}.sqlite"
            with closing(sqlite3.connect(image)) as destination:
                connection.backup(destination)
                # Backups inherit WAL mode. A subsequent read-only open can
                # leave per-image WAL/SHM sidecars, so use SQLite's documented
                # rollback-mode representation for closed archive images.
                if destination.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                    raise AssertionError("archive rollback-mode conversion failed")
            export = history / f"image-{index}.json"
            export.write_bytes(canonical_state_bytes(state))
            if not states_equal(_read_sqlite_state(image), state):
                raise AssertionError("history image differs from exact endpoint")
            current_bytes.append(image.stat().st_size + export.stat().st_size)
        all_history_bytes = disk_bytes(history)
        start = time.perf_counter_ns()
        latest_image = history / f"image-{updates}.sqlite"
        latest_export = history / f"image-{updates}.json"
        removed = []
        for index in range(updates):
            for suffix in ("sqlite", "json"):
                candidate = history / f"image-{index}.{suffix}"
                candidate.unlink()
                removed.append(candidate.name)
        reclamation_ns = time.perf_counter_ns() - start
        if (not states_equal(_read_sqlite_state(latest_image), state) or
                parse_audit_export(latest_export) != state.as_dict()):
            raise AssertionError("actual latest-only reclamation changed its retained endpoint")
        current_history_bytes = disk_bytes(history)
        before = disk_bytes(root)
        start = time.perf_counter_ns()
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        checkpoint_ns = time.perf_counter_ns() - start
        after = disk_bytes(root)
        if checkpoint[0] != 0 or not states_equal(store.durable_state(), state):
            raise AssertionError("checkpoint blocked or changed the endpoint")
        result = {"sources": source_count, "facts": len(state.facts), "updates": updates,
                  "sqlite_before_checkpoint_bytes": before, "sqlite_after_checkpoint_bytes": after,
                  "checkpoint_elapsed_ns": checkpoint_ns, "checkpoint_result": list(checkpoint),
                  "initial_checkpoint_result": list(initial_checkpoint),
                  "full_image_export_all_endpoints_bytes": all_history_bytes,
                  "full_image_export_current_endpoint_bytes": current_history_bytes,
                  "last_pair_size_before_reclamation_bytes": current_bytes[-1],
                  "history_reclamation_elapsed_ns": reclamation_ns,
                  "history_removed_files": removed,
                  "history_remaining_files": sorted(file.name for file in history.iterdir() if file.is_file()),
                  "history_latest_endpoint_equal": True,
                  "retained_endpoints_all": updates + 1, "retained_endpoints_current": 1,
                  "endpoint_equal": True,
                  "scope": "SQLite API; all regular DB/WAL/SHM files; image+canonical export; no segment-store timing"}
        store.close()
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = {"environment": {"python": sys.version, "sqlite": sqlite3.sqlite_version,
                               "platform": platform.platform()},
              "rows": [run(count, updates) for count, updates in ((500, 16), (2000, 12), (6000, 8))]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["rows"]))


if __name__ == "__main__":
    main()
