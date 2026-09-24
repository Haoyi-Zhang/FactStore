"""Retained experiment definitions; monolithic execution is disabled."""

from __future__ import annotations

import argparse
import csv
import gc
import json
import os
from pathlib import Path
import random
import resource
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable

from frontierstore.baselines import DBMDumbRebuild, SQLiteNormalized, SQLiteRebuild
from frontierstore.checker import run as independent_check
from frontierstore.extractor import extract_fact_specs
from frontierstore.model import (
    Fact,
    Source,
    State,
    Transaction,
    apply_transaction,
    bootstrap_state,
    canonical_state_bytes,
    check_closure,
    states_equal,
)
from frontierstore.store import FrontierStore
from frontierstore.workload import (
    affected_facts,
    choose_sources,
    logical_size,
    make_synthetic_state,
    make_update,
    query_fact_ids,
)

from experiments.common import (
    assert_states,
    bool_text,
    directory_bytes,
    float_text,
    int_text,
    median,
    percentile,
    process_write_bytes,
    transaction_size,
    write_csv,
)
from experiments.exhaustive import CUTS, run as run_exhaustive

ARTIFACT_ROOT = Path(__file__).resolve().parents[1]


def _make_engine(name: str, path: Path, state: State):
    if name == "FrontierStore":
        return FrontierStore.create(path, state)
    if name == SQLiteNormalized.name:
        return SQLiteNormalized.create(path, state)
    if name == SQLiteRebuild.name:
        return SQLiteRebuild.create(path, state)
    if name == DBMDumbRebuild.name:
        return DBMDumbRebuild.create(path, state)
    raise ValueError(name)


def _persist(engine: Any, state: State, delta: Any) -> None:
    if isinstance(engine, FrontierStore):
        engine.publish_precomputed(state, delta)
    else:
        engine.persist_precomputed(state, delta)


def _durable_state(engine: Any, path: Path) -> tuple[State, bool]:
    if isinstance(engine, FrontierStore):
        report = independent_check(path)
        if report["status"] != "ACCEPT":
            raise AssertionError(report)
        return FrontierStore(path).state, True
    return engine.durable_state(), True


def _query_once(engine: Any, fact_id: str) -> str | None:
    if isinstance(engine, FrontierStore):
        with engine.snapshot() as snapshot:
            value = snapshot.get_fact(fact_id)
            return None if value is None else str(value["payload"])
    return engine.query_fact(fact_id)


def _close_engine(engine: Any) -> None:
    close = getattr(engine, "close", None)
    if close is not None:
        close()


def _measure_persistence(
    *,
    engine_name: str,
    initial: State,
    transaction: Transaction,
    work_path: Path,
) -> dict[str, Any]:
    expected, delta = apply_transaction(initial, transaction)
    engine = _make_engine(engine_name, work_path, initial)
    logical_update = transaction_size(transaction)
    before_write = process_write_bytes()
    start = time.perf_counter_ns()
    _persist(engine, expected, delta)
    elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000.0
    after_write = process_write_bytes()
    measured_write = None if before_write is None or after_write is None else max(0, after_write - before_write)
    if measured_write is None:
        measured_write = logical_update

    durable, checker_ok = _durable_state(engine, work_path)
    assert_states(expected, durable)
    query_times: list[float] = []
    for fact_id in query_fact_ids(expected, 31):
        query_start = time.perf_counter_ns()
        payload = _query_once(engine, fact_id)
        query_times.append((time.perf_counter_ns() - query_start) / 1_000.0)
        if payload != expected.facts[fact_id].payload:
            raise AssertionError((engine_name, fact_id, payload, expected.facts[fact_id].payload))
    state_bytes = logical_size(expected)
    stored_bytes = directory_bytes(work_path)
    selected_segments = len(engine.selected_segments) if isinstance(engine, FrontierStore) else None
    _close_engine(engine)
    return {
        "engine": engine_name,
        "source_count": len(expected.sources),
        "fact_count": len(expected.facts),
        "update_ms": elapsed_ms,
        "query_p50_us": median(query_times) if query_times else 0.0,
        "write_bytes": measured_write,
        "logical_update_bytes": logical_update,
        "write_amplification": measured_write / max(1, logical_update),
        "stored_bytes": stored_bytes,
        "logical_state_bytes": state_bytes,
        "space_amplification": stored_bytes / max(1, state_bytes),
        "durable_equal": True,
        "checker_ok": checker_ok,
        "selected_segments": selected_segments,
    }


def run_scale(raw_dir: Path, scratch: Path, *, quick: bool) -> list[dict[str, Any]]:
    source_counts = [100, 400] if quick else [250, 2000, 6000]
    repetitions = 2 if quick else 8
    engines = ["FrontierStore", SQLiteNormalized.name, SQLiteRebuild.name, DBMDumbRebuild.name]
    rows: list[dict[str, Any]] = []
    for source_count in source_counts:
        initial = make_synthetic_state(source_count, facts_per_source=6, dependency_width=2)
        for engine_name in engines:
            for sample in range(repetitions):
                case = scratch / f"scale-{source_count}-{engine_name}-{sample}"
                transaction = make_update(initial, batch=min(8, source_count), seed=10_000 + source_count * 31 + sample)
                measured = _measure_persistence(
                    engine_name=engine_name,
                    initial=initial,
                    transaction=transaction,
                    work_path=case,
                )
                measured.update(
                    {
                        "sample": sample,
                        "dependency_width": 2,
                        "batch": min(8, source_count),
                    }
                )
                rows.append(measured)
                shutil.rmtree(case, ignore_errors=True)
        del initial
        gc.collect()
    write_csv(raw_dir / "scale.csv", rows)
    return rows


def run_sensitivity(raw_dir: Path, scratch: Path, *, quick: bool) -> list[dict[str, Any]]:
    source_count = 300 if quick else 1500
    repetitions = 2 if quick else 12
    settings: list[tuple[str, int, int]] = []
    for batch in ([1, 8] if quick else [1, 8, 32]):
        settings.append((f"batch-{batch}", batch, 2))
    for width in ([1, 4] if quick else [1, 2, 4, 8]):
        settings.append((f"width-{width}", 8, width))
    rows: list[dict[str, Any]] = []
    for setting, batch, width in settings:
        initial = make_synthetic_state(source_count, facts_per_source=6, dependency_width=width)
        for engine_name in ["FrontierStore", SQLiteNormalized.name]:
            for sample in range(repetitions):
                case = scratch / f"sensitivity-{setting}-{engine_name}-{sample}"
                transaction = make_update(initial, batch=batch, seed=20_000 + width * 101 + batch * 17 + sample)
                measured = _measure_persistence(
                    engine_name=engine_name,
                    initial=initial,
                    transaction=transaction,
                    work_path=case,
                )
                affected = set()
                for source in transaction.source_changes:
                    affected.update(initial.reverse[source])
                measured.update(
                    {
                        "setting": setting,
                        "sample": sample,
                        "dependency_width": width,
                        "batch": batch,
                        "affected_facts": len(affected),
                    }
                )
                rows.append(measured)
                shutil.rmtree(case, ignore_errors=True)
        del initial
        gc.collect()
    write_csv(raw_dir / "sensitivity.csv", rows)
    return rows


def run_invalidation(raw_dir: Path, *, quick: bool) -> list[dict[str, Any]]:
    source_count = 800 if quick else 8000
    facts_per_source = 6 if quick else 12
    repetitions = 10 if quick else 120
    state = make_synthetic_state(
        source_count,
        facts_per_source=facts_per_source,
        dependency_width=2,
    )
    rows: list[dict[str, Any]] = []
    fact_values = tuple(state.facts.values())
    source_ids = sorted(state.sources)
    for sample in range(repetitions):
        source_id = source_ids[(sample * 7919 + 17) % len(source_ids)]
        start = time.perf_counter_ns()
        indexed = tuple(state.reverse[source_id])
        reverse_us = (time.perf_counter_ns() - start) / 1_000.0
        start = time.perf_counter_ns()
        scanned = tuple(
            sorted(
                fact.identifier
                for fact in fact_values
                if any(dependency_source == source_id for dependency_source, _ in fact.dependencies)
            )
        )
        scan_us = (time.perf_counter_ns() - start) / 1_000.0
        if indexed != scanned:
            raise AssertionError((source_id, indexed[:5], scanned[:5]))
        rows.append(
            {
                "method": "reverse-index",
                "sample": sample,
                "source_count": source_count,
                "fact_count": len(state.facts),
                "affected_facts": len(indexed),
                "time_us": reverse_us,
                "equal_set": True,
            }
        )
        rows.append(
            {
                "method": "full-scan",
                "sample": sample,
                "source_count": source_count,
                "fact_count": len(state.facts),
                "affected_facts": len(scanned),
                "time_us": scan_us,
                "equal_set": True,
            }
        )
    write_csv(raw_dir / "invalidation.csv", rows)
    return rows


def run_compaction(raw_dir: Path, scratch: Path, *, quick: bool) -> list[dict[str, Any]]:
    source_count = 200 if quick else 1000
    updates = 12 if quick else 45
    repetitions = 2 if quick else 8
    policies = [0, 5] if quick else [0, 10, 40]
    initial = make_synthetic_state(source_count, facts_per_source=6, dependency_width=2)
    rows: list[dict[str, Any]] = []
    for interval in policies:
        for sample in range(repetitions):
            case = scratch / f"compaction-{interval}-{sample}"
            store = FrontierStore.create(case, initial)
            base_times: list[float] = []
            for update_index in range(1, updates + 1):
                transaction = make_update(store.state, batch=4, seed=30_000 + sample * 1000 + update_index)
                expected, delta = apply_transaction(store.state, transaction)
                store.publish_precomputed(expected, delta)
                if interval and update_index % interval == 0:
                    before = store.state
                    start = time.perf_counter_ns()
                    after = store.compact()
                    base_times.append((time.perf_counter_ns() - start) / 1_000_000.0)
                    if not states_equal(before, after, include_epoch=False):
                        raise AssertionError("compaction changed observations")
                    store.collect()
            checker = independent_check(case)
            if checker["status"] != "ACCEPT":
                raise AssertionError(checker)
            start = time.perf_counter_ns()
            reopened = FrontierStore(case)
            open_ms = (time.perf_counter_ns() - start) / 1_000_000.0
            assert_states(store.state, reopened.state)
            rows.append(
                {
                    "interval": interval,
                    "sample": sample,
                    "source_count": source_count,
                    "updates": updates,
                    "selected_segments": len(store.selected_segments),
                    "base_publications": len(base_times),
                    "base_p50_ms": median(base_times) if base_times else "",
                    "open_ms": open_ms,
                    "stored_bytes": directory_bytes(case),
                    "logical_state_bytes": logical_size(store.state),
                    "space_amplification": directory_bytes(case) / max(1, logical_size(store.state)),
                    "checker_ok": True,
                    "state_equal": True,
                }
            )
            shutil.rmtree(case, ignore_errors=True)
    write_csv(raw_dir / "compaction.csv", rows)
    return rows


def run_crashes(raw_dir: Path, scratch: Path, *, quick: bool) -> list[dict[str, Any]]:
    repetitions = 1 if quick else 8
    initial = make_synthetic_state(12, facts_per_source=3, dependency_width=2)
    rows: list[dict[str, Any]] = []
    for boundary_index, boundary in enumerate(CUTS):
        for sample in range(repetitions):
            case = scratch / f"crash-{boundary_index}-{sample}"
            FrontierStore.create(case, initial)
            transaction = make_update(initial, batch=2, seed=40_000 + boundary_index * 100 + sample)
            expected_new, _ = apply_transaction(initial, transaction)
            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tests.crash_worker",
                    str(case),
                    boundary,
                    str(40_000 + boundary_index * 100 + sample),
                ],
                cwd=ARTIFACT_ROOT,
                env={**os.environ, "PYTHONPATH": str(ARTIFACT_ROOT)},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            if process.returncode != FrontierStore.fail_exit_code:
                raise AssertionError(
                    {"boundary": boundary, "returncode": process.returncode, "stderr": process.stderr[-1000:]}
                )
            recovered = FrontierStore(case)
            report = independent_check(case)
            if report["status"] != "ACCEPT":
                raise AssertionError(report)
            matches_old = states_equal(recovered.state, initial)
            matches_new = states_equal(recovered.state, expected_new)
            expected_label = "old" if boundary_index < 8 else "new"
            observed_label = "old" if matches_old else "new" if matches_new else "neither"
            if observed_label != expected_label:
                raise AssertionError(
                    {"boundary": boundary, "expected": expected_label, "observed": observed_label}
                )
            follow = make_update(recovered.state, batch=1, seed=50_000 + boundary_index * 100 + sample)
            recovered.commit(follow)
            follow_report = independent_check(case)
            rows.append(
                {
                    "boundary": boundary,
                    "sample": sample,
                    "exit_code": process.returncode,
                    "expected_root": expected_label,
                    "observed_root": observed_label,
                    "matches_old": matches_old,
                    "matches_new": matches_new,
                    "checker_ok": report["status"] == "ACCEPT",
                    "following_update_ok": follow_report["status"] == "ACCEPT",
                    "timeout": False,
                }
            )
            shutil.rmtree(case, ignore_errors=True)
    write_csv(raw_dir / "crash.csv", rows)
    return rows


def _rewrite_json_lines(path: Path, mutator: Callable[[list[dict[str, Any]]], None]) -> None:
    values = [json.loads(line) for line in path.read_text(encoding="ascii").splitlines()]
    mutator(values)
    path.write_text(
        "".join(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n" for value in values),
        encoding="ascii",
    )


def run_mutations(raw_dir: Path, scratch: Path, *, quick: bool) -> list[dict[str, Any]]:
    del quick
    valid = scratch / "mutation-valid"
    state = make_synthetic_state(10, facts_per_source=3, dependency_width=2)
    store = FrontierStore.create(valid, state)
    store.commit(make_update(store.state, batch=2, seed=60_001))
    if independent_check(valid)["status"] != "ACCEPT":
        raise AssertionError("valid mutation seed rejected")

    cases: list[tuple[str, str, Callable[[Path], None]]] = []

    def missing_segment(path: Path) -> None:
        root = json.loads((path / "ROOT").read_text(encoding="ascii"))
        (path / "segments" / root["segments"][-1]).unlink()

    def truncate_footer(path: Path) -> None:
        root = json.loads((path / "ROOT").read_text(encoding="ascii"))
        segment = path / "segments" / root["segments"][-1]
        lines = segment.read_text(encoding="ascii").splitlines()
        segment.write_text("\n".join(lines[:-1]) + "\n", encoding="ascii")

    def phase_order(path: Path) -> None:
        root = json.loads((path / "ROOT").read_text(encoding="ascii"))
        segment = path / "segments" / root["segments"][-1]

        def mutate(values):
            body = values[1:-1]
            fact_index = next(index for index, record in enumerate(body) if record.get("kind") == "fact_put")
            fact = body.pop(fact_index)
            body.insert(0, fact)
            values[1:-1] = body

        _rewrite_json_lines(segment, mutate)

    def field_type(path: Path) -> None:
        root = json.loads((path / "ROOT").read_text(encoding="ascii"))
        segment = path / "segments" / root["segments"][-1]

        def mutate(values):
            record = next(record for record in values if record.get("kind") == "source_put")
            record["generation"] = "not-an-integer"

        _rewrite_json_lines(segment, mutate)

    def stale_dependency(path: Path) -> None:
        root = json.loads((path / "ROOT").read_text(encoding="ascii"))
        segment = path / "segments" / root["segments"][-1]

        def mutate(values):
            record = next(record for record in values if record.get("kind") == "fact_put")
            record["dependencies"][0]["generation"] += 1

        _rewrite_json_lines(segment, mutate)

    def root_count(path: Path) -> None:
        root_path = path / "ROOT"
        root = json.loads(root_path.read_text(encoding="ascii"))
        root["counts"]["facts"] += 1
        root_path.write_text(json.dumps(root, sort_keys=True, separators=(",", ":")) + "\n", encoding="ascii")

    def path_escape(path: Path) -> None:
        root_path = path / "ROOT"
        root = json.loads(root_path.read_text(encoding="ascii"))
        root["segments"][-1] = "../segment-00000002.jsonl"
        root_path.write_text(json.dumps(root, sort_keys=True, separators=(",", ":")) + "\n", encoding="ascii")

    cases.extend(
        [
            ("missing-selected-segment", "MISSING_SEGMENT", missing_segment),
            ("truncated-footer", "INCOMPLETE_SEGMENT", truncate_footer),
            ("record-phase-order", "PHASE_ORDER", phase_order),
            ("source-generation-type", "FIELD_TYPE", field_type),
            ("stale-dependency-generation", "STALE_DEPENDENCY", stale_dependency),
            ("root-fact-count", "ROOT_COUNT", root_count),
            ("selected-path-escape", "PATH_ESCAPE", path_escape),
        ]
    )
    rows: list[dict[str, Any]] = []
    for index, (mutation, expected_witness, mutator) in enumerate(cases):
        case = scratch / f"mutation-{index}"
        shutil.copytree(valid, case)
        mutator(case)
        report = independent_check(case)
        accepted = report["status"] == "ACCEPT"
        witness = "" if accepted else str(report["witness"])
        if accepted or witness != expected_witness:
            raise AssertionError({"mutation": mutation, "report": report, "expected": expected_witness})
        rows.append(
            {
                "mutation": mutation,
                "expected_witness": expected_witness,
                "observed_witness": witness,
                "rejected": not accepted,
            }
        )
        shutil.rmtree(case, ignore_errors=True)
    shutil.rmtree(valid, ignore_errors=True)
    write_csv(raw_dir / "mutation.csv", rows)
    return rows


def _materialized_closed(value: dict[str, Any]) -> bool:
    sources = value.get("sources", {})
    facts = value.get("facts", {})
    reverse = value.get("reverse", {})
    schema = value.get("schema")
    if set(sources) != set(reverse):
        return False
    expected = {source_id: set() for source_id in sources}
    for fact_id, fact in facts.items():
        if fact.get("schema") != schema:
            return False
        dependencies = fact.get("dependencies")
        if not isinstance(dependencies, list) or not dependencies:
            return False
        seen = set()
        for dependency in dependencies:
            source_id = dependency.get("source")
            generation = dependency.get("generation")
            if source_id in seen or source_id not in sources:
                return False
            seen.add(source_id)
            if sources[source_id].get("generation") != generation:
                return False
            expected[source_id].add(fact_id)
    return all(reverse[source_id] == sorted(expected[source_id]) for source_id in sources)


def run_concurrency(raw_dir: Path, scratch: Path, *, quick: bool) -> list[dict[str, Any]]:
    source_count = 120 if quick else 600
    updates = 30 if quick else 120
    reader_counts = [0, 1] if quick else [0, 1, 3]
    repetitions = 1 if quick else 3
    initial = make_synthetic_state(source_count, facts_per_source=6, dependency_width=2)
    rows: list[dict[str, Any]] = []
    for reader_count in reader_counts:
        for sample in range(repetitions):
            case = scratch / f"concurrency-{reader_count}-{sample}"
            store = FrontierStore.create(case, initial)
            stop = threading.Event()
            ready = threading.Barrier(reader_count + 1) if reader_count else None
            operations = [0 for _ in range(reader_count)]
            violations = [0 for _ in range(reader_count)]

            def reader(index: int) -> None:
                assert ready is not None
                ready.wait()
                while not stop.is_set():
                    with store.snapshot() as snapshot:
                        materialized = snapshot.materialize()
                    operations[index] += 1
                    if not _materialized_closed(materialized):
                        violations[index] += 1

            threads = [threading.Thread(target=reader, args=(index,), daemon=True) for index in range(reader_count)]
            for thread in threads:
                thread.start()
            if ready is not None:
                ready.wait()
            writer_latencies: list[float] = []
            started = time.perf_counter()
            for update_index in range(updates):
                transaction = make_update(store.state, batch=3, seed=70_000 + sample * 1000 + update_index)
                expected, delta = apply_transaction(store.state, transaction)
                start = time.perf_counter_ns()
                store.publish_precomputed(expected, delta)
                writer_latencies.append((time.perf_counter_ns() - start) / 1_000_000.0)
            elapsed = time.perf_counter() - started
            stop.set()
            for thread in threads:
                thread.join(timeout=30)
                if thread.is_alive():
                    raise AssertionError("reader thread did not stop")
            report = independent_check(case)
            if report["status"] != "ACCEPT" or sum(violations):
                raise AssertionError({"checker": report, "violations": violations})
            rows.append(
                {
                    "readers": reader_count,
                    "sample": sample,
                    "writer_updates": updates,
                    "writer_elapsed_s": elapsed,
                    "writer_updates_per_s": updates / elapsed,
                    "writer_p50_ms": median(writer_latencies),
                    "writer_p95_ms": percentile(writer_latencies, 0.95),
                    "reader_snapshots": sum(operations),
                    "reader_snapshots_per_s": sum(operations) / elapsed if elapsed else 0.0,
                    "mixed_violations": sum(violations),
                    "checker_ok": True,
                }
            )
            shutil.rmtree(case, ignore_errors=True)
    write_csv(raw_dir / "concurrency.csv", rows)
    return rows


def run_public_input(raw_dir: Path, scratch: Path, *, quick: bool) -> list[dict[str, Any]]:
    input_dir = ARTIFACT_ROOT / "external_inputs" / "solidity"
    files = sorted(input_dir.glob("*.sol"))
    if len(files) != 10:
        raise AssertionError(f"expected ten Solidity excerpts, found {len(files)}")
    source_payloads = {f"src{index:02d}": path.read_text(encoding="utf-8") for index, path in enumerate(files)}
    initial = bootstrap_state(source_payloads, extract_fact_specs(source_payloads), schema=1, epoch=1)
    case = scratch / "public-input"
    store = FrontierStore.create(case, initial)
    updates = 8 if quick else 40
    rows: list[dict[str, Any]] = []
    for update_index in range(1, updates + 1):
        source_id = f"src{(update_index - 1) % len(files):02d}"
        source_payloads[source_id] = source_payloads[source_id] + f"\n// require(marker_{update_index});\n"
        fresh_specs = extract_fact_specs(source_payloads)
        state = store.state
        new_schema = state.schema + 1 if update_index == (4 if quick else 20) else None
        if new_schema is not None:
            replacements = fresh_specs
        else:
            replacements = [
                spec
                for spec in fresh_specs
                if source_id in spec["sources"] or spec["id"] not in state.facts
            ]
        transaction = Transaction(
            source_changes={source_id: source_payloads[source_id]},
            fact_replacements=replacements,
            new_schema=new_schema,
        )
        invalidated = set(state.reverse.get(source_id, ()))
        if new_schema is not None:
            invalidated.update(state.facts)
        expected, delta = apply_transaction(state, transaction)
        start = time.perf_counter_ns()
        store.publish_precomputed(expected, delta)
        update_ms = (time.perf_counter_ns() - start) / 1_000_000.0
        start = time.perf_counter_ns()
        report = independent_check(case)
        verify_ms = (time.perf_counter_ns() - start) / 1_000_000.0
        if report["status"] != "ACCEPT":
            raise AssertionError(report)
        rows.append(
            {
                "update": update_index,
                "source": source_id,
                "schema": expected.schema,
                "fact_count": len(expected.facts),
                "invalidated_facts": len(invalidated),
                "replacement_facts": len(replacements),
                "update_ms": update_ms,
                "verify_ms": verify_ms,
                "checker_ok": True,
            }
        )
    before = store.state
    start = time.perf_counter_ns()
    after = store.compact()
    compact_ms = (time.perf_counter_ns() - start) / 1_000_000.0
    if not states_equal(before, after, include_epoch=False):
        raise AssertionError("public-input compaction changed observations")
    store.collect()
    final_report = independent_check(case)
    if final_report["status"] != "ACCEPT":
        raise AssertionError(final_report)
    rows[-1]["final_compaction_ms"] = compact_ms
    rows[-1]["final_selected_segments"] = len(store.selected_segments)
    rows[-1]["final_checker_ok"] = True
    write_csv(raw_dir / "public_input.csv", rows)
    shutil.rmtree(case, ignore_errors=True)
    return rows


def _run_tests() -> None:
    process = subprocess.run(
        [sys.executable, "-m", "unittest", "-q", "tests.test_store"],
        cwd=ARTIFACT_ROOT,
        env={**os.environ, "PYTHONPATH": str(ARTIFACT_ROOT)},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=120,
    )
    if process.returncode != 0 or "Ran 23 tests" not in process.stdout:
        raise AssertionError(process.stdout)


def main() -> int:
    """The unbounded monolithic orchestration is no longer an admissible entry."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["full", "quick"], default="full")
    parser.add_argument("--output", required=True)
    parser.parse_args()
    print(json.dumps({
        "status": "BLOCKED",
        "reason": "monolithic run has no resumable 180-second bound; campaign upper usage is unknown",
        "next_entry": "experiments/run_bounded.py",
        "output_modified": False,
    }, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
