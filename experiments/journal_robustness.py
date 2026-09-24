"""Deterministic descriptive robustness summaries for the retained journal dataset.

This script performs no storage-engine execution.  It reads only the three
accepted observation files and reports distributional summaries and paired
FrontierStore/SQLite-R diagnostics.  Bootstrap intervals are conditional on the
retained fixed-order rows and therefore do not remove order, workload, or host
confounding.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import random
import statistics
from typing import Any, Iterable

ACCEPTED = (
    ("small", "current-small"),
    ("medium", "current-medium-retry"),
    ("large", "current-large"),
)
ENGINES = ("FrontierStore", "SQLite-Normalized", "SQLite-Rooted-Audit")
BOOTSTRAP_DRAWS = 10_000
BOOTSTRAP_SEED = 73_991


def _median(values: Iterable[float]) -> float:
    materialized = list(values)
    if not materialized:
        raise ValueError("median of empty collection")
    return float(statistics.median(materialized))


def _quantile(values: Iterable[float], probability: float) -> float:
    materialized = sorted(float(value) for value in values)
    if not materialized:
        raise ValueError("quantile of empty collection")
    if not 0.0 <= probability <= 1.0:
        raise ValueError(probability)
    position = (len(materialized) - 1) * probability
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return materialized[low]
    weight = position - low
    return materialized[low] * (1.0 - weight) + materialized[high] * weight


def _bootstrap_median_interval(values: list[float], *, seed: int) -> tuple[float, float]:
    if not values:
        raise ValueError("bootstrap of empty collection")
    generator = random.Random(seed)
    n = len(values)
    estimates = [
        _median(values[generator.randrange(n)] for _ in range(n))
        for _ in range(BOOTSTRAP_DRAWS)
    ]
    return _quantile(estimates, 0.025), _quantile(estimates, 0.975)


def _load_rows(results: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scale, directory in ACCEPTED:
        path = results / directory / "observations.csv"
        with path.open(newline="", encoding="utf-8") as handle:
            for raw in csv.DictReader(handle):
                row: dict[str, Any] = dict(raw)
                row["scale"] = scale
                for key in ("source_count", "fact_count", "repetition", "update", "batch", "dependency_width"):
                    row[key] = int(row[key])
                for key in ("update_ms", "audit_ms", "write_bytes", "stored_bytes"):
                    row[key] = float(row[key])
                row["combined_ms"] = row["update_ms"] + row["audit_ms"]
                if row.get("durable_equal") != "True":
                    raise ValueError(f"non-oracle row in accepted input: {path}")
                rows.append(row)
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("no rows")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run(results: Path, output: Path) -> dict[str, Any]:
    rows = _load_rows(results)
    distributions: list[dict[str, Any]] = []
    for scale_index, (scale, _) in enumerate(ACCEPTED):
        for engine_index, engine in enumerate(ENGINES):
            chosen = [row for row in rows if row["scale"] == scale and row["engine"] == engine]
            combined = [float(row["combined_ms"]) for row in chosen]
            low, high = _bootstrap_median_interval(
                combined,
                seed=BOOTSTRAP_SEED + 100 * scale_index + engine_index,
            )
            maximum_update = max(int(row["update"]) for row in chosen)
            split = maximum_update / 2.0
            first = [float(row["combined_ms"]) for row in chosen if int(row["update"]) <= split]
            second = [float(row["combined_ms"]) for row in chosen if int(row["update"]) > split]
            distributions.append({
                "scale": scale,
                "engine": engine,
                "observations": len(chosen),
                "combined_median_ms": _median(combined),
                "combined_q1_ms": _quantile(combined, 0.25),
                "combined_q3_ms": _quantile(combined, 0.75),
                "combined_min_ms": min(combined),
                "combined_max_ms": max(combined),
                "bootstrap_median_low_ms": low,
                "bootstrap_median_high_ms": high,
                "first_half_median_ms": _median(first),
                "second_half_median_ms": _median(second),
                "persistence_median_ms": _median(float(row["update_ms"]) for row in chosen),
                "audit_median_ms": _median(float(row["audit_ms"]) for row in chosen),
                "write_median_bytes": _median(float(row["write_bytes"]) for row in chosen),
                "stored_final_median_bytes": _median(
                    float(row["stored_bytes"])
                    for row in chosen
                    if int(row["update"]) == maximum_update
                ),
            })

    paired: list[dict[str, Any]] = []
    for scale, _ in ACCEPTED:
        indexed = {
            (int(row["repetition"]), int(row["update"]), str(row["engine"])): row
            for row in rows
            if row["scale"] == scale
        }
        identities = sorted({(key[0], key[1]) for key in indexed})
        differences: list[float] = []
        ratios: list[float] = []
        rooted_lower = frontier_lower = ties = 0
        for repetition, update in identities:
            frontier = float(indexed[(repetition, update, "FrontierStore")]["combined_ms"])
            rooted = float(indexed[(repetition, update, "SQLite-Rooted-Audit")]["combined_ms"])
            difference = frontier - rooted
            differences.append(difference)
            ratios.append(frontier / rooted)
            if difference > 0:
                rooted_lower += 1
            elif difference < 0:
                frontier_lower += 1
            else:
                ties += 1
        paired.append({
            "scale": scale,
            "pairs": len(identities),
            "median_frontier_minus_rooted_ms": _median(differences),
            "q1_frontier_minus_rooted_ms": _quantile(differences, 0.25),
            "q3_frontier_minus_rooted_ms": _quantile(differences, 0.75),
            "median_frontier_to_rooted_ratio": _median(ratios),
            "rooted_lower_pairs": rooted_lower,
            "frontier_lower_pairs": frontier_lower,
            "ties": ties,
        })

    result = {
        "status": "DESCRIPTIVE_ROBUSTNESS_RECONSTRUCTED",
        "input_observations": len(rows),
        "accepted_directories": [directory for _, directory in ACCEPTED],
        "bootstrap_draws": BOOTSTRAP_DRAWS,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "inference_boundary": (
            "Intervals and paired summaries are conditional on the retained fixed-order rows; "
            "they do not remove execution-order, host, workload, page-cache, or selection confounding."
        ),
        "distributions": distributions,
        "paired_frontier_rooted": paired,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_csv(output.with_suffix(".csv"), distributions)
    _write_csv(output.with_name(output.stem + "-paired.csv"), paired)

    scale_names = {"small": "Small", "medium": "Medium", "large": "Large"}
    engine_names = {
        "FrontierStore": "FS",
        "SQLite-Normalized": "SQL",
        "SQLite-Rooted-Audit": "Rooted",
    }
    macro_lines = ["% Generated deterministically from retained fixed-order rows."]
    for row in distributions:
        prefix = scale_names[str(row["scale"])] + engine_names[str(row["engine"])]
        macro_lines.extend([
            f"\\newcommand{{\\{prefix}BootLow}}{{{float(row['bootstrap_median_low_ms']):.2f}}}",
            f"\\newcommand{{\\{prefix}BootHigh}}{{{float(row['bootstrap_median_high_ms']):.2f}}}",
            f"\\newcommand{{\\{prefix}QOne}}{{{float(row['combined_q1_ms']):.2f}}}",
            f"\\newcommand{{\\{prefix}QThree}}{{{float(row['combined_q3_ms']):.2f}}}",
            f"\\newcommand{{\\{prefix}FirstHalf}}{{{float(row['first_half_median_ms']):.2f}}}",
            f"\\newcommand{{\\{prefix}SecondHalf}}{{{float(row['second_half_median_ms']):.2f}}}",
        ])
    for row in paired:
        prefix = scale_names[str(row["scale"])]
        macro_lines.extend([
            f"\\newcommand{{\\{prefix}PairDiffMedian}}{{{float(row['median_frontier_minus_rooted_ms']):.2f}}}",
            f"\\newcommand{{\\{prefix}PairDiffQOne}}{{{float(row['q1_frontier_minus_rooted_ms']):.2f}}}",
            f"\\newcommand{{\\{prefix}PairDiffQThree}}{{{float(row['q3_frontier_minus_rooted_ms']):.2f}}}",
        ])
    output.with_suffix(".tex").write_text("\n".join(macro_lines) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    summary = run(arguments.results, arguments.output)
    print(json.dumps({
        "status": summary["status"],
        "input_observations": summary["input_observations"],
        "distribution_rows": len(summary["distributions"]),
        "paired_rows": len(summary["paired_frontier_rooted"]),
    }, sort_keys=True))
