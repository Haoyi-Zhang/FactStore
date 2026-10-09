"""Finite owned-input stages for fresh_run; no benchmark or external execution."""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import traceback
import unittest
from unittest.mock import patch

from experiments.fresh_run import ROOT, child_cpu, source_inventory, write_json
from frontierstore import baselines
from frontierstore.audit_export import check_audit_export
from frontierstore.model import Transaction, apply_transaction, bootstrap_state, canonical_state_bytes


def initial_state():
    return bootstrap_state({"a": "stable", "b": "old", "c": "unused"},
        [{"id": "p", "payload": "old fact", "sources": ["a"]},
         {"id": "q", "payload": "other fact", "sources": ["a", "b"]}])


def replacement():
    return Transaction(source_changes={"b": "new"}, fact_replacements=[
        {"id": "p", "payload": "new fact", "sources": ["a"]},
        {"id": "q", "payload": "new dependent", "sources": ["a", "b"]}])


def initialize_sql(path: Path, state, mode="WAL") -> None:
    """Owned SQL setup, not an emulation of adapter create/directory fsync."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        actual = connection.execute(f"PRAGMA journal_mode={mode}").fetchone()[0]
        if actual.lower() != mode.lower():
            raise AssertionError("journal mode not established")
        connection.execute("PRAGMA synchronous=FULL")
        if connection.execute("PRAGMA synchronous").fetchone()[0] != 2:
            raise AssertionError("FULL synchronization not established")
        baselines._sqlite_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        baselines._insert_full_state(connection, state)
        connection.commit()


def unit_checks(report):
    inventory = source_inventory(ROOT)
    # The retained 132-method baseline predates both run-admission checks
    # and paper-wrapper checks. All current methods are still executed below.
    baseline = [name for name in inventory if not name.startswith(
        ("tests.test_fresh_run.", "tests.test_primary_wrapper."))]
    if len(baseline) != 132:
        raise AssertionError(f"expected the existing 132-method surface, found {len(baseline)}")
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests"), pattern="test_*.py", top_level_dir=str(ROOT))

    def flatten(value):
        for item in value:
            if isinstance(item, unittest.TestSuite):
                yield from flatten(item)
            else:
                yield item.id()

    runtime_inventory = sorted(flatten(suite))
    report.update(static_methods=len(inventory), existing_methods=len(baseline),
                  static_inventory=inventory, discovered_inventory=runtime_inventory,
                  import_errors=loader.errors)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report.update(tests_run=result.testsRun, failures=[(t.id(), text) for t, text in result.failures],
                  errors=[(t.id(), text) for t, text in result.errors],
                  skipped=[(t.id(), reason) for t, reason in result.skipped])
    if (runtime_inventory != inventory or not result.wasSuccessful()
            or result.testsRun != len(inventory) or result.skipped):
        raise AssertionError("incomplete, failed, or skipped test surface; see raw transcript")


def sqlite_strict(report, scratch):
    state = initial_state()
    target = scratch / "normalized"
    initialize_sql(target / "normalized.sqlite", state)
    engine = baselines.SQLiteNormalized(target)
    rows = []
    report["sqlite_transitions"] = rows
    report["sqlite_setup_scope"] = "SQLite API FULL/WAL setup; adapter create/directory fsync not exercised"
    transactions = [replacement(), Transaction(),
        Transaction(fact_replacements=[{"id": "p", "payload": "move", "sources": ["c"]}]),
        Transaction(source_changes={"a": None}),
        Transaction(source_changes={"a": "returned"}, fact_replacements=[
            {"id": "q", "payload": "returned fact", "sources": ["a", "b"]}]),
        Transaction(new_schema=2, fact_replacements=[
            {"id": "p", "payload": "new schema", "sources": ["c"]}]),
        Transaction(fact_retractions=["p"]), Transaction(source_changes={"c": "updated"})]
    try:
        for step, transaction in enumerate(transactions, 1):
            expected, delta = apply_transaction(state, transaction)
            engine.persist_precomputed(expected, delta)
            if engine.durable_state() != expected or engine.state != expected:
                raise AssertionError(f"normalized endpoint mismatch at {step}")
            rows.append({"step": step, "epoch": expected.epoch, "endpoint_equal": True})
            state = expected
    finally:
        engine.close()

    # Invoke the actual one-transaction reader across a commit by another
    # connection, rather than merely checking a hand-built held value.
    target = scratch / "interleave"
    old = initial_state()
    new, delta = apply_transaction(old, replacement())
    initialize_sql(target / "normalized.sqlite", old)
    writer = baselines.SQLiteNormalized(target)
    connection = sqlite3.connect(writer.db_path)
    fired = []
    class InterleavingReader:
        def execute(self, statement, *args):
            if statement.startswith("SELECT id,payload,schema") and not fired:
                fired.append(True)
                writer.persist_precomputed(new, delta)
            return connection.execute(statement, *args)
        def close(self):
            connection.close()
    try:
        with patch.object(baselines.sqlite3, "connect", return_value=InterleavingReader()):
            held = baselines._read_sqlite_state(writer.db_path)
        if held != old or writer.durable_state() != new or fired != [True]:
            raise AssertionError("read transaction mixed endpoints or fresh read lagged")
        report["actual_reader_interleave"] = {"held_old": True, "fresh_new": True, "commits": 1}
    finally:
        connection.close()
        writer.close()

    sql_cases = [
        ("extra-meta", "INSERT INTO meta VALUES('unexpected',1)", "META_SET"),
        ("missing-source", "UPDATE dependencies SET source_id='missing' WHERE fact_id='p'", "MISSING_SOURCE"),
        ("future-source", "UPDATE sources SET generation=99 WHERE id='c'", "SOURCE_GENERATION_HORIZON"),
        ("schema", "UPDATE facts SET schema=2 WHERE id='p'", "FACT_SCHEMA"),
        ("missing-fact", "INSERT INTO dependencies VALUES('missing','a',1)", "MISSING_FACT"),
        ("empty-dependency", "DELETE FROM dependencies WHERE fact_id='p'", "EMPTY_DEPENDENCY"),
    ]
    rejects = []
    report["sqlite_rejections"] = rejects
    for name, statement, expected in sql_cases:
        path = scratch / name / "state.sqlite"
        initialize_sql(path, old)
        with sqlite3.connect(path) as connection:
            connection.execute(statement)
            connection.commit()
        try:
            baselines._read_sqlite_state(path)
        except baselines.BaselineFormatError as error:
            if error.code != expected:
                raise AssertionError(f"{name}: {error.code} != {expected}") from error
            rejects.append({"case": name, "witness": error.code})
        else:
            raise AssertionError(f"corrupt owned database accepted: {name}")

    export = scratch / "export.json"
    export.write_bytes(canonical_state_bytes(old))
    if check_audit_export(export)["status"] != "ACCEPT":
        raise AssertionError("valid owned export rejected")
    report["export_valid_controls"] = 1
    mutations = [
        ("unknown", lambda x: x.update(ignored=1), "TOP_LEVEL_SHAPE"),
        ("bool-epoch", lambda x: x.update(epoch=True), "EPOCH_TYPE"),
        ("horizon", lambda x: x["sources"]["a"].update(generation=99), "SOURCE_HORIZON"),
        ("inverse", lambda x: x["reverse"].update(a=[]), "REVERSE_MISMATCH"),
        ("stale", lambda x: x["facts"]["p"]["dependencies"][0].update(generation=99), "STALE_DEPENDENCY"),
        ("schema", lambda x: x["facts"]["p"].update(schema=2), "SCHEMA_MISMATCH"),
        ("identity", lambda x: x["sources"]["a"].update(id="b"), "SOURCE_KEY_MISMATCH"),
        ("dependency-order", lambda x: x["facts"]["q"]["dependencies"].reverse(), "DEPENDENCY_ORDER"),
    ]
    report["export_rejections"] = []
    for name, mutation, expected in mutations:
        value = copy.deepcopy(old.as_dict())
        mutation(value)
        export.write_text(json.dumps(value), encoding="utf-8")
        rejected = check_audit_export(export)
        if rejected.get("witness") != expected:
            raise AssertionError(f"{name}: unexpected export result {rejected}")
        report["export_rejections"].append({"case": name, "witness": expected})
    export.write_text(canonical_state_bytes(old).decode("ascii").replace('"epoch":1', '"epoch":0,"epoch":1'), encoding="utf-8")
    if check_audit_export(export).get("witness") != "DUPLICATE_FIELD":
        raise AssertionError("duplicate export field accepted")
    report["export_rejections"].append({"case": "duplicate", "witness": "DUPLICATE_FIELD"})


def sql_crash_worker(path: Path, cut: str) -> None:
    old = baselines._read_sqlite_state(path)
    new, _ = apply_transaction(old, replacement())
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("BEGIN IMMEDIATE")
    baselines._insert_full_state(connection, new)
    if cut == "before-commit":
        os._exit(73)
    connection.commit()
    if cut == "after-commit":
        os._exit(73)
    connection.close()
    os._exit(73)


def crash_checks(report, scratch):
    rows = []
    report["sqlite_process_exits"] = rows
    old = initial_state()
    new, _ = apply_transaction(old, replacement())
    env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    for mode in ("WAL", "DELETE"):
        for cut in ("before-commit", "after-commit", "after-close"):
            path = scratch / f"sqlite-{mode}-{cut}" / "state.sqlite"
            initialize_sql(path, old, mode)
            command = [sys.executable, "-B", "-m", "experiments.owned_checks", "--sql-worker", str(path), "--cut", cut]
            with path.with_suffix(".stdout.txt").open("wb") as stdout, path.with_suffix(".stderr.txt").open("wb") as stderr:
                process = subprocess.Popen(command, env=env, stdout=stdout, stderr=stderr)
                try:
                    process.wait(timeout=20)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)
                measured = child_cpu(process)
            if process.returncode != 73:
                raise AssertionError(f"SQL worker did not reach {mode}/{cut}: {process.returncode}")
            expected = old if cut == "before-commit" else new
            if baselines._read_sqlite_state(path) != expected:
                raise AssertionError(f"SQL recovery selected wrong endpoint at {mode}/{cut}")
            rows.append({"mode": mode, "cut": cut, "exit_code": 73, "epoch": expected.epoch, "endpoint_equal": True,
                         "child_cpu": measured})
    report["crash_scope"] = "owned process exits with OS alive; no raw-device power-loss claim"
    if sys.platform != "linux":
        report["segment_publication"] = {"status": "UNSUPPORTED_PLATFORM", "reason": "fcntl and directory synchronization required; no substitutes used"}
        raise RuntimeError("Linux segment publication/crash campaign remains unexecuted")
    from experiments.exhaustive import CUTS
    from frontierstore.checker import run as independent_check
    from frontierstore.store import FrontierStore
    from frontierstore.workload import make_synthetic_state, make_update
    initial = make_synthetic_state(6, facts_per_source=2, dependency_width=2)
    report["segment_process_exits"] = []
    for seed in (701, 702):
        expected, _ = apply_transaction(initial, make_update(initial, batch=2, seed=seed))
        for cut in CUTS:
            path = scratch / f"segment-{seed}-{cut}"
            FrontierStore.create(path, initial)
            command = [sys.executable, "-B", "-m", "tests.crash_worker", str(path), cut, str(seed)]
            with (path / "worker.stdout.txt").open("wb") as stdout, (path / "worker.stderr.txt").open("wb") as stderr:
                completed = subprocess.run(command, env=env, stdout=stdout, stderr=stderr, timeout=20, check=False)
            if completed.returncode != 73:
                raise AssertionError(f"segment worker did not reach {cut}")
            selected = expected if cut in ("after_root_replace", "after_root_dirsync") else initial
            reopened = FrontierStore(path)
            if reopened.state != selected or independent_check(path)["status"] != "ACCEPT":
                raise AssertionError(f"segment recovery mismatch at {cut}")
            # Old-root retry must recover unselected same-epoch leftovers safely.
            if selected == initial:
                reopened.commit(make_update(initial, batch=2, seed=seed))
                if reopened.state != expected:
                    raise AssertionError(f"retry mismatch at {cut}")
            report["segment_process_exits"].append({"seed": seed, "cut": cut, "exit_code": 73, "epoch": selected.epoch, "endpoint_equal": True})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("unit", "finite", "sqlite-strict", "crash", "joint", "retained"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--scratch", type=Path)
    parser.add_argument("--sql-worker", type=Path)
    parser.add_argument("--cut", choices=("before-commit", "after-commit", "after-close"))
    args = parser.parse_args()
    if args.sql_worker is not None:
        if args.cut is None:
            parser.error("worker requires a named cut")
        sql_crash_worker(args.sql_worker, args.cut)
    if args.case is None or args.output is None or args.scratch is None:
        parser.error("stage, output, and owned scratch are required")
    report = {"case": args.case, "status": "INCOMPLETE", "scientific_measurement": False}
    try:
        if args.case == "unit":
            unit_checks(report)
        elif args.case == "finite":
            from experiments.exhaustive import run
            report["finite"] = run(args.scratch / "histories.json")
        elif args.case == "sqlite-strict":
            sqlite_strict(report, args.scratch)
        elif args.case == "crash":
            crash_checks(report, args.scratch)
        elif args.case == "joint":
            from experiments.joint_history import execute
            report["joint"] = execute(args.scratch / "joint.json")
        elif args.case == "retained":
            from experiments.verify_artifact import main as verify
            if verify() != 0:
                raise AssertionError("retained-data verification failed")
            report["retained_data_reconstructed"] = True
            report["reference_scope"] = "retained metadata consistency, not primary-paper fulltext verification"
        report["status"] = "COMPLETED"
    except Exception as error:
        report["status"] = "FAILED_OR_PARTIAL"
        report["error"] = f"{type(error).__name__}: {error}"
        traceback.print_exc()
    finally:
        write_json(args.output, report)
    print(json.dumps({"case": args.case, "status": report["status"]}))
    return 0 if report["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
