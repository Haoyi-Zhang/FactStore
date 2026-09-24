"""Counterbalanced engine-order check for the narrowest accepted latency margin.

This bounded repair experiment reuses the largest accepted logical instance and
compares only the two cross-interface representations.  Four fresh blocks use
two executions of each engine order.  The result is descriptive: it checks that
the accepted ordering is not an artifact of always running one engine first,
but it is not a confidence interval or a machine-independent performance claim.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any

from experiments.common import directory_bytes, median, process_write_bytes, write_csv
from experiments.current_validation import audit, create_engine, persist
from frontierstore.baselines import SQLiteRootedAudit
from frontierstore.model import apply_transaction
from frontierstore.workload import make_synthetic_state, make_update

SOURCE_COUNT = 6000
FACTS_PER_SOURCE = 6
DEPENDENCY_WIDTH = 2
BATCH = 8
UPDATES = 4
ORDERS = (
    ("FrontierStore", SQLiteRootedAudit.name),
    (SQLiteRootedAudit.name, "FrontierStore"),
    (SQLiteRootedAudit.name, "FrontierStore"),
    ("FrontierStore", SQLiteRootedAudit.name),
)


def run(output: Path) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError("refusing to replace retained order-sensitivity evidence")
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="frontier-order-") as temporary:
        scratch = Path(temporary)
        initial = make_synthetic_state(
            SOURCE_COUNT,
            facts_per_source=FACTS_PER_SOURCE,
            dependency_width=DEPENDENCY_WIDTH,
        )
        for block, order in enumerate(ORDERS):
            order_name = "-then-".join(order)
            for position, engine_name in enumerate(order, start=1):
                case = scratch / f"block-{block}-{position}"
                engine = create_engine(engine_name, case, initial)
                state = initial
                try:
                    for update_index in range(1, UPDATES + 1):
                        transaction = make_update(
                            state,
                            batch=BATCH,
                            seed=970_000 + block * 1000 + update_index,
                        )
                        expected, delta = apply_transaction(state, transaction)
                        before = process_write_bytes()
                        started = time.perf_counter_ns()
                        persist(engine, expected, delta)
                        update_ms = (time.perf_counter_ns() - started) / 1_000_000.0
                        after = process_write_bytes()
                        write_bytes = None if before is None or after is None else max(0, after - before)
                        audit_kind, audit_ms = audit(engine, case, expected)
                        rows.append({
                            "block": block,
                            "order": order_name,
                            "position": position,
                            "engine": engine_name,
                            "source_count": SOURCE_COUNT,
                            "fact_count": len(expected.facts),
                            "update": update_index,
                            "batch": BATCH,
                            "dependency_width": DEPENDENCY_WIDTH,
                            "update_ms": update_ms,
                            "audit_ms": audit_ms,
                            "combined_ms": update_ms + audit_ms,
                            "write_bytes": write_bytes,
                            "stored_bytes": directory_bytes(case),
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
    for scope_name, predicate in (
        ("overall", lambda row: True),
        ("frontier-first", lambda row: row["order"].startswith("FrontierStore")),
        ("rooted-first", lambda row: row["order"].startswith(SQLiteRootedAudit.name)),
    ):
        for engine_name in ("FrontierStore", SQLiteRootedAudit.name):
            chosen = [row for row in rows if predicate(row) and row["engine"] == engine_name]
            summaries.append({
                "scope": scope_name,
                "engine": engine_name,
                "observations": len(chosen),
                "update_median_ms": median([row["update_ms"] for row in chosen]),
                "audit_median_ms": median([row["audit_ms"] for row in chosen]),
                "combined_median_ms": median([row["combined_ms"] for row in chosen]),
                "all_durable_equal": all(row["durable_equal"] for row in chosen),
            })

    pairs: list[dict[str, Any]] = []
    for block in range(len(ORDERS)):
        for update_index in range(1, UPDATES + 1):
            pair = {
                row["engine"]: row
                for row in rows
                if row["block"] == block and row["update"] == update_index
            }
            frontier = pair["FrontierStore"]["combined_ms"]
            rooted = pair[SQLiteRootedAudit.name]["combined_ms"]
            pairs.append({
                "block": block,
                "order": pair["FrontierStore"]["order"],
                "update": update_index,
                "frontier_combined_ms": frontier,
                "rooted_combined_ms": rooted,
                "frontier_minus_rooted_ms": frontier - rooted,
                "frontier_to_rooted_ratio": frontier / rooted,
                "winner": "FrontierStore" if frontier < rooted else SQLiteRootedAudit.name if rooted < frontier else "tie",
            })

    block_summaries: list[dict[str, Any]] = []
    for block, order in enumerate(ORDERS):
        frontier = [row["combined_ms"] for row in rows if row["block"] == block and row["engine"] == "FrontierStore"]
        rooted = [row["combined_ms"] for row in rows if row["block"] == block and row["engine"] == SQLiteRootedAudit.name]
        f_median = median(frontier)
        r_median = median(rooted)
        block_summaries.append({
            "block": block,
            "order": "-then-".join(order),
            "frontier_median_ms": f_median,
            "rooted_median_ms": r_median,
            "frontier_to_rooted_ratio": f_median / r_median,
            "winner": "FrontierStore" if f_median < r_median else SQLiteRootedAudit.name if r_median < f_median else "tie",
        })

    by_scope = {(row["scope"], row["engine"]): row for row in summaries}
    overall_rooted_lower = (
        by_scope[("overall", SQLiteRootedAudit.name)]["combined_median_ms"]
        < by_scope[("overall", "FrontierStore")]["combined_median_ms"]
    )
    both_order_strata_rooted_lower = all(
        by_scope[(scope, SQLiteRootedAudit.name)]["combined_median_ms"]
        < by_scope[(scope, "FrontierStore")]["combined_median_ms"]
        for scope in ("frontier-first", "rooted-first")
    )
    result = {
        "status": "CASE_COMPLETED",
        "purpose": "counterbalance engine order at the accepted scale with the narrowest median margin",
        "configuration": {
            "source_count": SOURCE_COUNT,
            "facts_per_source": FACTS_PER_SOURCE,
            "fact_count": SOURCE_COUNT * FACTS_PER_SOURCE,
            "dependency_width": DEPENDENCY_WIDTH,
            "batch": BATCH,
            "updates_per_engine_block": UPDATES,
            "blocks": len(ORDERS),
            "orders": [list(order) for order in ORDERS],
            "observations_per_engine": len(rows) // 2,
        },
        "timing_scope": "same persistence-return and immediately following full-audit paths as the accepted comparison",
        "interpretation": "bounded order-sensitivity repair; descriptive only, not a confidence interval or hardware-general performance claim",
        "rows": rows,
        "summary": summaries,
        "pairs": pairs,
        "block_summary": block_summaries,
        "checks": {
            "all_durable_equal": all(row["durable_equal"] for row in rows),
            "overall_rooted_median_lower": overall_rooted_lower,
            "both_order_strata_rooted_median_lower": both_order_strata_rooted_lower,
            "all_block_medians_rooted_lower": all(row["winner"] == SQLiteRootedAudit.name for row in block_summaries),
            "rooted_pair_wins": sum(row["winner"] == SQLiteRootedAudit.name for row in pairs),
            "frontier_pair_wins": sum(row["winner"] == "FrontierStore" for row in pairs),
            "ties": sum(row["winner"] == "tie" for row in pairs),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    write_csv(output.with_suffix(".csv"), rows)
    write_csv(output.with_name("pairs.csv"), pairs)
    write_csv(output.with_name("block-summary.csv"), block_summaries)
    print(json.dumps({"status": result["status"], "rows": len(rows), **result["checks"]}, sort_keys=True))
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    run(arguments.output)
