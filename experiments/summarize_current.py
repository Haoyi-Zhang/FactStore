"""Aggregate the explicitly admitted current-source validation cases.

The aggregator does not run storage engines. It reconstructs descriptive
statistics from retained rows, records every excluded attempt, exposes the
fixed execution order, and emits all paper inputs from the same observations.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path
from statistics import median
from typing import Any, Iterable

ACCEPTED = {
    "small": "current-small",
    "medium": "current-medium-retry",
    "large": "current-large",
}
PILOT = "current-pilot"
EXCLUDED = {
    "current-medium": "Controller was absent during the tail; retained only as failure evidence.",
    "boundary-tests": "Admission monitor parser failed before a scientific result.",
    "boundary-tests-retry": "A false operating-system thread gate terminated the case.",
    "order-sensitivity": "The first order-control implementation failed before emitting observations.",
    "order-counterbalance": "The corrected guarded order-control run emitted no observations before controller termination.",
}
ENGINE_ORDER = ("FrontierStore", "SQLite-Normalized", "SQLite-Rooted-Audit")
TEST_COUNT_PATTERN = re.compile(r"^Ran (\d+) tests? in [^\n]+$", re.MULTILINE)
TEST_ROW_PATTERN = re.compile(r"^(test[^\n]+) \.\.\. (ok|FAIL|ERROR|skipped[^\n]*)$", re.MULTILINE)


def load_current_test_count(results_root: Path) -> int:
    evidence = results_root / "code-audit-tests"
    transcript = (evidence / "stderr.txt").read_text(encoding="utf-8")
    stdout = (evidence / "stdout.txt").read_text(encoding="utf-8")
    record = json.loads((evidence / "record.json").read_text(encoding="utf-8"))
    matches = TEST_COUNT_PATTERN.findall(transcript)
    rows = TEST_ROW_PATTERN.findall(transcript)
    identifiers = [identifier for identifier, _status in rows]
    statuses = [status for _identifier, status in rows]
    if len(matches) != 1 or not transcript.rstrip().endswith("OK"):
        raise AssertionError("complete passing code-audit transcript is missing or ambiguous")
    count = int(matches[0])
    if count <= 0 or len(rows) != count or any(status != "ok" for status in statuses):
        raise AssertionError("test summary does not match exclusively passing method rows")
    if len(identifiers) != len(set(identifiers)):
        raise AssertionError("test transcript contains duplicate method identifiers")
    expected_record = {
        "case": "code-audit-tests",
        "status": "CASE_COMPLETED",
        "scope": "current-source deterministic executable correspondence and parser-boundary regression suite",
        "command": "PYTHONPATH=. python -m unittest discover -v tests",
        "exit_code": 0,
        "test_count": count,
        "passing_method_rows": count,
        "failures": 0,
        "errors": 0,
        "skips": 0,
        "scientific_measurement": False,
    }
    if record != expected_record:
        raise AssertionError("code-audit record does not match the complete passing transcript")
    if stdout:
        raise AssertionError("code-audit stdout is unexpectedly nonempty")
    return count


def percentile(values: Iterable[float], q: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("percentile of empty sequence")
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def load_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    converted: list[dict[str, Any]] = []
    for row in rows:
        converted.append({
            **row,
            "source_count": int(row["source_count"]),
            "fact_count": int(row["fact_count"]),
            "repetition": int(row["repetition"]),
            "update": int(row["update"]),
            "batch": int(row["batch"]),
            "dependency_width": int(row["dependency_width"]),
            "update_ms": float(row["update_ms"]),
            "audit_ms": float(row["audit_ms"]),
            "write_bytes": None if row["write_bytes"] == "" else int(float(row["write_bytes"])),
            "stored_bytes": int(float(row["stored_bytes"])),
            "durable_equal": row["durable_equal"].strip().lower() == "true",
        })
    return converted


def aggregate_case(scale: str, case_dir: str, results_root: Path, role: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source = results_root / case_dir / "observations.csv"
    rows = load_rows(source)
    engines = sorted({row["engine"] for row in rows}, key=ENGINE_ORDER.index)
    if tuple(engines) != ENGINE_ORDER:
        raise AssertionError(f"unexpected engine set in {case_dir}: {engines}")
    summaries: list[dict[str, Any]] = []
    for engine in engines:
        chosen = [row for row in rows if row["engine"] == engine]
        source_counts = {row["source_count"] for row in chosen}
        fact_counts = {row["fact_count"] for row in chosen}
        max_update = max(row["update"] for row in chosen)
        final_sizes = [row["stored_bytes"] for row in chosen if row["update"] == max_update]
        writes = [row["write_bytes"] for row in chosen if row["write_bytes"] is not None]
        combined = [row["update_ms"] + row["audit_ms"] for row in chosen]
        if len(source_counts) != 1 or len(fact_counts) != 1:
            raise AssertionError(f"mixed sizes in {case_dir}/{engine}")
        summaries.append({
            "role": role,
            "scale": scale,
            "case_directory": case_dir,
            "source_count": next(iter(source_counts)),
            "fact_count": next(iter(fact_counts)),
            "engine": engine,
            "observations": len(chosen),
            "update_median_ms": median(row["update_ms"] for row in chosen),
            "update_p95_ms": percentile((row["update_ms"] for row in chosen), 0.95),
            "audit_median_ms": median(row["audit_ms"] for row in chosen),
            "audit_p95_ms": percentile((row["audit_ms"] for row in chosen), 0.95),
            "combined_median_ms": median(combined),
            "combined_p95_ms": percentile(combined, 0.95),
            "write_median_bytes": None if not writes else median(writes),
            "final_stored_median_bytes": median(final_sizes),
            "all_durable_equal": all(row["durable_equal"] for row in chosen),
            "audit_kind": chosen[0]["audit_kind"],
        })
    case = {
        "role": role,
        "scale": scale,
        "directory": case_dir,
        "observation_rows": len(rows),
        "engines": list(ENGINE_ORDER),
        "execution_order": list(ENGINE_ORDER),
        "all_durable_equal": all(row["durable_equal"] for row in rows),
    }
    return summaries, case


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("no rows")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def format_num(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}"


def build_comparisons(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    accepted = [row for row in summaries if row["role"] == "accepted"]
    lookup = {(row["scale"], row["engine"]): row for row in accepted}
    comparisons: list[dict[str, Any]] = []
    for scale in ACCEPTED:
        fs = lookup[(scale, "FrontierStore")]
        sql = lookup[(scale, "SQLite-Normalized")]
        rooted = lookup[(scale, "SQLite-Rooted-Audit")]
        comparisons.append({
            "scale": scale,
            "source_count": fs["source_count"],
            "fact_count": fs["fact_count"],
            "frontier_to_rooted_combined_ratio": fs["combined_median_ms"] / rooted["combined_median_ms"],
            "rooted_to_frontier_write_ratio": rooted["write_median_bytes"] / fs["write_median_bytes"],
            "rooted_to_frontier_stored_ratio": rooted["final_stored_median_bytes"] / fs["final_stored_median_bytes"],
            "normalized_to_frontier_combined_ratio": sql["combined_median_ms"] / fs["combined_median_ms"],
            "rooted_latency_advantage_ms": fs["combined_median_ms"] - rooted["combined_median_ms"],
            "rooted_extra_write_kib": (rooted["write_median_bytes"] - fs["write_median_bytes"]) / 1024.0,
            "rooted_extra_stored_mib": (rooted["final_stored_median_bytes"] - fs["final_stored_median_bytes"]) / (1024.0 * 1024.0),
        })
    return comparisons


def build_order_diagnostics(results_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    scale_rows: list[dict[str, Any]] = []
    repetition_rows: list[dict[str, Any]] = []
    for scale, directory in ACCEPTED.items():
        rows = load_rows(results_root / directory / "observations.csv")
        repetitions = sorted({row["repetition"] for row in rows})
        all_pair_differences: list[float] = []
        total_rooted_wins = total_frontier_wins = total_ties = 0
        repetition_ratios: list[float] = []
        for repetition in repetitions:
            fs = {
                row["update"]: row["update_ms"] + row["audit_ms"]
                for row in rows
                if row["repetition"] == repetition and row["engine"] == "FrontierStore"
            }
            rooted = {
                row["update"]: row["update_ms"] + row["audit_ms"]
                for row in rows
                if row["repetition"] == repetition and row["engine"] == "SQLite-Rooted-Audit"
            }
            if fs.keys() != rooted.keys() or not fs:
                raise AssertionError(f"unaligned comparison rows in {directory}/repetition {repetition}")
            fs_median = median(fs.values())
            rooted_median = median(rooted.values())
            ratios = fs_median / rooted_median
            repetition_ratios.append(ratios)
            differences = [fs[update] - rooted[update] for update in sorted(fs)]
            rooted_wins = sum(value > 0 for value in differences)
            frontier_wins = sum(value < 0 for value in differences)
            ties = sum(value == 0 for value in differences)
            total_rooted_wins += rooted_wins
            total_frontier_wins += frontier_wins
            total_ties += ties
            all_pair_differences.extend(differences)
            repetition_rows.append({
                "scale": scale,
                "case_directory": directory,
                "repetition": repetition,
                "frontier_combined_median_ms": fs_median,
                "rooted_combined_median_ms": rooted_median,
                "frontier_to_rooted_median_ratio": ratios,
                "rooted_faster_aligned_updates": rooted_wins,
                "frontier_faster_aligned_updates": frontier_wins,
                "ties": ties,
                "aligned_updates": len(differences),
            })
        fs_all = [row["update_ms"] + row["audit_ms"] for row in rows if row["engine"] == "FrontierStore"]
        rooted_all = [row["update_ms"] + row["audit_ms"] for row in rows if row["engine"] == "SQLite-Rooted-Audit"]
        scale_rows.append({
            "scale": scale,
            "case_directory": directory,
            "source_count": rows[0]["source_count"],
            "fact_count": rows[0]["fact_count"],
            "fixed_execution_order": " -> ".join(ENGINE_ORDER),
            "repetitions": len(repetitions),
            "observations_per_engine": len(fs_all),
            "frontier_combined_pooled_median_ms": median(fs_all),
            "rooted_combined_pooled_median_ms": median(rooted_all),
            "frontier_to_rooted_pooled_median_ratio": median(fs_all) / median(rooted_all),
            "frontier_to_rooted_repetition_ratio_min": min(repetition_ratios),
            "frontier_to_rooted_repetition_ratio_max": max(repetition_ratios),
            "rooted_faster_aligned_updates": total_rooted_wins,
            "frontier_faster_aligned_updates": total_frontier_wins,
            "ties": total_ties,
            "aligned_updates": len(all_pair_differences),
            "median_aligned_difference_ms": median(all_pair_differences),
            "all_repetition_medians_favor_rooted": all(value > 1.0 for value in repetition_ratios),
            "order_control_status": "NO_ACCEPTED_COUNTERBALANCED_OBSERVATIONS",
        })
    return scale_rows, repetition_rows


def write_paper_inputs(
    paper_dir: Path,
    summaries: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
    diagnostics: list[dict[str, Any]],
    current_test_count: int,
) -> None:
    accepted = [row for row in summaries if row["role"] == "accepted"]
    latency_rows = [{
        "scale": row["scale"],
        "facts": row["fact_count"],
        "engine": row["engine"],
        "combined_median_ms": format_num(row["combined_median_ms"], 6),
        "combined_p95_ms": format_num(row["combined_p95_ms"], 6),
        "update_median_ms": format_num(row["update_median_ms"], 6),
        "audit_median_ms": format_num(row["audit_median_ms"], 6),
    } for row in accepted]
    cost_rows = [{
        "scale": row["scale"],
        "facts": row["fact_count"],
        "engine": row["engine"],
        "write_median_bytes": int(row["write_median_bytes"]),
        "final_stored_median_bytes": format_num(row["final_stored_median_bytes"], 1),
    } for row in accepted]
    write_csv(paper_dir / "current-latency.csv", latency_rows)
    write_csv(paper_dir / "current-cost.csv", cost_rows)

    lookup = {(row["scale"], row["engine"]): row for row in accepted}
    wide_rows: list[dict[str, Any]] = []
    for scale in ACCEPTED:
        fs = lookup[(scale, "FrontierStore")]
        sql = lookup[(scale, "SQLite-Normalized")]
        rooted = lookup[(scale, "SQLite-Rooted-Audit")]
        wide_rows.append({
            "scale": scale,
            "facts": fs["fact_count"],
            "fs_combined_ms": format_num(fs["combined_median_ms"], 6),
            "sql_combined_ms": format_num(sql["combined_median_ms"], 6),
            "rooted_combined_ms": format_num(rooted["combined_median_ms"], 6),
            "fs_write_bytes": int(fs["write_median_bytes"]),
            "sql_write_bytes": int(sql["write_median_bytes"]),
            "rooted_write_bytes": int(rooted["write_median_bytes"]),
            "fs_stored_bytes": format_num(fs["final_stored_median_bytes"], 1),
            "sql_stored_bytes": format_num(sql["final_stored_median_bytes"], 1),
            "rooted_stored_bytes": format_num(rooted["final_stored_median_bytes"], 1),
        })
    write_csv(paper_dir / "current-plot.csv", wide_rows)

    lines = ["% Generated deterministically from admitted fixed-order current-source observations."]
    for scale, prefix in (("small", "Small"), ("medium", "Medium"), ("large", "Large")):
        for engine, suffix in (("FrontierStore", "FS"), ("SQLite-Normalized", "SQL"), ("SQLite-Rooted-Audit", "Rooted")):
            row = lookup[(scale, engine)]
            lines.extend([
                rf"\newcommand{{\{prefix}{suffix}Update}}{{{format_num(row['update_median_ms'])}}}",
                rf"\newcommand{{\{prefix}{suffix}Audit}}{{{format_num(row['audit_median_ms'])}}}",
                rf"\newcommand{{\{prefix}{suffix}Combined}}{{{format_num(row['combined_median_ms'])}}}",
                rf"\newcommand{{\{prefix}{suffix}CombinedP}}{{{format_num(row['combined_p95_ms'])}}}",
                rf"\newcommand{{\{prefix}{suffix}WriteKiB}}{{{format_num(row['write_median_bytes'] / 1024.0)}}}",
                rf"\newcommand{{\{prefix}{suffix}StoredMiB}}{{{format_num(row['final_stored_median_bytes'] / (1024.0 * 1024.0))}}}",
            ])
    comparison_lookup = {row["scale"]: row for row in comparisons}
    diagnostic_lookup = {row["scale"]: row for row in diagnostics}
    for scale, prefix in (("small", "Small"), ("medium", "Medium"), ("large", "Large")):
        row = comparison_lookup[scale]
        diagnostic = diagnostic_lookup[scale]
        lines.extend([
            rf"\newcommand{{\{prefix}FSToRootedCombined}}{{{format_num(row['frontier_to_rooted_combined_ratio'])}}}",
            rf"\newcommand{{\{prefix}RootedWriteRatio}}{{{format_num(row['rooted_to_frontier_write_ratio'])}}}",
            rf"\newcommand{{\{prefix}RootedStoredRatio}}{{{format_num(row['rooted_to_frontier_stored_ratio'])}}}",
            rf"\newcommand{{\{prefix}RootedLatencySaved}}{{{format_num(row['rooted_latency_advantage_ms'])}}}",
            rf"\newcommand{{\{prefix}RootedExtraWriteKiB}}{{{format_num(row['rooted_extra_write_kib'])}}}",
            rf"\newcommand{{\{prefix}RootedExtraStoredMiB}}{{{format_num(row['rooted_extra_stored_mib'])}}}",
            rf"\newcommand{{\{prefix}RepetitionRatioLow}}{{{format_num(diagnostic['frontier_to_rooted_repetition_ratio_min'])}}}",
            rf"\newcommand{{\{prefix}RepetitionRatioHigh}}{{{format_num(diagnostic['frontier_to_rooted_repetition_ratio_max'])}}}",
            rf"\newcommand{{\{prefix}RootedPairWins}}{{{int(diagnostic['rooted_faster_aligned_updates'])}}}",
            rf"\newcommand{{\{prefix}FrontierPairWins}}{{{int(diagnostic['frontier_faster_aligned_updates'])}}}",
            rf"\newcommand{{\{prefix}PairCount}}{{{int(diagnostic['aligned_updates'])}}}",
        ])
    accepted_observation_rows = sum(int(row["observations"]) for row in accepted)
    lines.extend([
        rf"\newcommand{{\NumCurrentTests}}{{{current_test_count}}}",
        rf"\newcommand{{\NumAcceptedCurrentRows}}{{{accepted_observation_rows}}}",
        rf"\newcommand{{\NumCurrentScales}}{{{len(ACCEPTED)}}}",
    ])
    (paper_dir / "current-values.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=Path("results/current"))
    parser.add_argument("--output", type=Path, default=Path("results/current/summary"))
    parser.add_argument("--paper-dir", type=Path, default=Path("../paper"))
    args = parser.parse_args()

    current_test_count = load_current_test_count(args.results)
    summaries: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    pilot_rows, pilot_case = aggregate_case("pilot", PILOT, args.results, "pilot")
    summaries.extend(pilot_rows)
    cases.append(pilot_case)
    for scale, directory in ACCEPTED.items():
        rows, case = aggregate_case(scale, directory, args.results, "accepted")
        summaries.extend(rows)
        cases.append(case)

    comparisons = build_comparisons(summaries)
    diagnostics, repetition_diagnostics = build_order_diagnostics(args.results)
    acceptance = {
        "status": "CURRENT_EVIDENCE_SELECTION_FROZEN",
        "accepted": [{"scale": scale, "directory": directory} for scale, directory in ACCEPTED.items()],
        "pilot": {"directory": PILOT, "use": "method admission only; excluded from main comparison"},
        "excluded": [{"directory": directory, "reason": reason} for directory, reason in EXCLUDED.items()],
        "selection_rule": "Only attempts with attached controller accounting and durable-oracle equality enter the retained comparison tables.",
        "combined_metric": "Per-observation persistence-return time plus the immediately following full-audit time; medians and p95 are computed after pairing.",
        "execution_order": list(ENGINE_ORDER),
        "order_control_status": "NO_ACCEPTED_COUNTERBALANCED_OBSERVATIONS",
        "interpretation": "Latency ordering is descriptive for the retained fixed-order runs, not an order-controlled performance conclusion.",
    }
    if not all(case["all_durable_equal"] for case in cases):
        raise AssertionError("an accepted or pilot case has a durable mismatch")

    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / "current_summary.csv", summaries)
    write_csv(args.output / "current_comparison.csv", comparisons)
    write_csv(args.output / "current_order_diagnostics.csv", diagnostics)
    write_csv(args.output / "current_order_repetitions.csv", repetition_diagnostics)
    summary = {
        "cases": cases,
        "summary": summaries,
        "comparisons": comparisons,
        "order_diagnostics": diagnostics,
        "order_repetitions": repetition_diagnostics,
    }
    (args.output / "current_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (args.output / "current_order_diagnostics.json").write_text(json.dumps({
        "execution_order": list(ENGINE_ORDER),
        "order_control_status": "NO_ACCEPTED_COUNTERBALANCED_OBSERVATIONS",
        "scale_diagnostics": diagnostics,
        "repetition_diagnostics": repetition_diagnostics,
    }, indent=2) + "\n", encoding="utf-8")
    (args.results / "acceptance.json").write_text(json.dumps(acceptance, indent=2) + "\n", encoding="utf-8")
    write_paper_inputs(args.paper_dir, summaries, comparisons, diagnostics, current_test_count)
    print(json.dumps({
        "status": "CURRENT_SUMMARY_WRITTEN",
        "rows": len(summaries),
        "accepted_observation_rows": sum(int(row["observations"]) for row in summaries if row["role"] == "accepted"),
        "current_tests": current_test_count,
        "order_control_status": "NO_ACCEPTED_COUNTERBALANCED_OBSERVATIONS",
    }))


if __name__ == "__main__":
    main()
