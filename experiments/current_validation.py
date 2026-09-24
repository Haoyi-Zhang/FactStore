"""Bounded current-source sustained-update and audit-surface comparison."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any

from experiments.common import assert_states, directory_bytes, median, percentile, process_write_bytes, write_csv
from frontierstore.baselines import SQLiteNormalized, SQLiteRootedAudit
from frontierstore.checker import run as independent_check
from frontierstore.model import apply_transaction
from frontierstore.store import FrontierStore
from frontierstore.workload import make_synthetic_state, make_update

CONFIGS = {
    "pilot": {"source_counts": [500], "repetitions": 1, "updates": 8, "batch": 4},
    "small": {"source_counts": [500], "repetitions": 3, "updates": 16, "batch": 8},
    "medium": {"source_counts": [2000], "repetitions": 2, "updates": 12, "batch": 8},
    "large": {"source_counts": [6000], "repetitions": 2, "updates": 8, "batch": 8},
}
ENGINES = ("FrontierStore", SQLiteNormalized.name, SQLiteRootedAudit.name)


def create_engine(name: str, path: Path, state):
    if name == "FrontierStore":
        return FrontierStore.create(path, state)
    if name == SQLiteNormalized.name:
        return SQLiteNormalized.create(path, state)
    if name == SQLiteRootedAudit.name:
        return SQLiteRootedAudit.create(path, state)
    raise ValueError(name)


def persist(engine, expected, delta) -> None:
    if isinstance(engine, FrontierStore):
        engine.publish_precomputed(expected, delta)
    else:
        engine.persist_precomputed(expected, delta)


def audit(engine, path: Path, expected) -> tuple[str, float]:
    start = time.perf_counter_ns()
    if isinstance(engine, FrontierStore):
        report = independent_check(path)
        if report["status"] != "ACCEPT":
            raise AssertionError(report)
        durable = FrontierStore(path).state
        kind = "independent-segment-parser-plus-fresh-open"
    elif isinstance(engine, SQLiteRootedAudit):
        durable = engine.durable_state()
        report = engine.audit_report()
        if report["status"] != "ACCEPT":
            raise AssertionError(report)
        kind = "root-selected-sqlite-and-independent-full-export"
    else:
        durable = engine.durable_state()
        kind = "sqlite-one-read-transaction"
    assert_states(expected, durable)
    return kind, (time.perf_counter_ns() - start) / 1_000_000.0


def run(mode: str, output: Path) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError("refusing to replace retained current-validation evidence")
    config = CONFIGS[mode]
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="frontier-current-") as temporary:
        scratch = Path(temporary)
        for source_count in config["source_counts"]:
            initial = make_synthetic_state(source_count, facts_per_source=6, dependency_width=2)
            for repetition in range(config["repetitions"]):
                for engine_name in ENGINES:
                    case = scratch / f"n{source_count}-r{repetition}-{engine_name}"
                    engine = create_engine(engine_name, case, initial)
                    state = initial
                    try:
                        for update_index in range(1, config["updates"] + 1):
                            transaction = make_update(
                                state,
                                batch=min(config["batch"], source_count),
                                seed=910_000 + source_count * 101 + repetition * 1000 + update_index,
                            )
                            expected, delta = apply_transaction(state, transaction)
                            before = process_write_bytes()
                            started = time.perf_counter_ns()
                            persist(engine, expected, delta)
                            update_ms = (time.perf_counter_ns() - started) / 1_000_000.0
                            after = process_write_bytes()
                            write_bytes = None if before is None or after is None else max(0, after - before)
                            audit_kind, audit_ms = audit(engine, case, expected)
                            selected = len(engine.selected_segments) if isinstance(engine, FrontierStore) else None
                            rows.append({
                                "mode": mode,
                                "source_count": source_count,
                                "fact_count": len(expected.facts),
                                "repetition": repetition,
                                "update": update_index,
                                "batch": min(config["batch"], source_count),
                                "dependency_width": 2,
                                "engine": engine_name,
                                "update_ms": update_ms,
                                "audit_ms": audit_ms,
                                "write_bytes": write_bytes,
                                "stored_bytes": directory_bytes(case),
                                "selected_segments": selected,
                                "durable_equal": True,
                                "audit_kind": audit_kind,
                            })
                            state = expected
                    finally:
                        close = getattr(engine, "close", None)
                        if close is not None:
                            close()
                        shutil.rmtree(case, ignore_errors=True)
    summaries: list[dict[str, Any]] = []
    for source_count in config["source_counts"]:
        for engine_name in ENGINES:
            chosen = [row for row in rows if row["source_count"] == source_count and row["engine"] == engine_name]
            writes = [row["write_bytes"] for row in chosen if row["write_bytes"] is not None]
            final_sizes = [
                row["stored_bytes"] for row in chosen
                if row["update"] == config["updates"]
            ]
            summaries.append({
                "source_count": source_count,
                "fact_count": source_count * 6,
                "engine": engine_name,
                "observations": len(chosen),
                "update_median_ms": median([row["update_ms"] for row in chosen]),
                "update_p95_ms": percentile([row["update_ms"] for row in chosen], .95),
                "audit_median_ms": median([row["audit_ms"] for row in chosen]),
                "audit_p95_ms": percentile([row["audit_ms"] for row in chosen], .95),
                "write_median_bytes": None if not writes else median(writes),
                "final_stored_median_bytes": median(final_sizes),
                "all_durable_equal": all(row["durable_equal"] for row in chosen),
            })
    result = {
        "status": "CASE_COMPLETED",
        "mode": mode,
        "configuration": config,
        "engines": list(ENGINES),
        "timing_scope": "persistence return excludes shared apply_transaction; audit is timed separately after every update",
        "write_scope": "Linux process write_bytes delta; software-accounted block writes, not device traffic",
        "rooted_audit_scope": "full immutable SQLite image plus full canonical export selected by one root; no SQLite internals modified",
        "rows": rows,
        "summary": summaries,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    write_csv(output.with_suffix(".csv"), rows)
    print(json.dumps({"status": result["status"], "mode": mode, "rows": len(rows)}))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=CONFIGS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.mode, args.output)
