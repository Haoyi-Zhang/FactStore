"""Fail closed unless retained raw rows and derived summaries agree."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import tempfile
from typing import Mapping

from experiments.common import read_csv
from experiments.summarize_results import summarize


def truth(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def verify(results: str | Path) -> dict[str, object]:
    results_path = Path(results)
    raw = results_path / "raw"
    summary = results_path / "summary"
    resources = json.loads((raw / "resources.json").read_text(encoding="utf-8"))
    mode = resources["mode"]
    rows = {
        name: read_csv(raw / f"{name}.csv")
        for name in [
            "scale",
            "sensitivity",
            "invalidation",
            "compaction",
            "crash",
            "mutation",
            "concurrency",
            "public_input",
        ]
    }
    if mode == "full":
        expected = {
            "scale": 96,
            "sensitivity": 168,
            "invalidation": 240,
            "compaction": 24,
            "crash": 80,
            "mutation": 7,
            "concurrency": 9,
            "public_input": 40,
        }
        for name, count in expected.items():
            require(len(rows[name]) == count, f"{name}: expected {count}, found {len(rows[name])}")

    exhaustive = json.loads((raw / "abstract_crash_cuts.json").read_text(encoding="utf-8"))
    require(exhaustive["histories"] == 775, "abstract history count")
    require(exhaustive["intermediate_states"] == 2925, "abstract intermediate-state count")
    require(exhaustive["crash_cuts"] == 7750, "abstract crash-cut count")
    require(exhaustive["all_closed"] is True, "abstract closure violation")
    require(exhaustive.get("all_endpoints") is True, "abstract endpoint violation")
    require(exhaustive.get("closure_violations") == [], "abstract closure witness")
    require(exhaustive.get("endpoint_violations") == [], "abstract endpoint witness")
    require(not exhaustive["violations"], "abstract publication violation")
    require(exhaustive.get("model") == "abstract-selector-object-v1", "abstract publication model identity")
    require(exhaustive.get("selected_endpoint_counts") == {"old": 6200, "new": 1550}, "abstract selector counts")

    for row in rows["scale"] + rows["sensitivity"]:
        require(truth(row["durable_equal"]), "durable equality failed")
        require(truth(row["checker_ok"]), "checker failed on retained performance row")
        require(float(row["update_ms"]) >= 0, "negative update time")
        require(float(row["write_amplification"]) >= 0, "negative write amplification")
        require(float(row["space_amplification"]) > 0, "nonpositive space amplification")
    for row in rows["invalidation"]:
        require(truth(row["equal_set"]), "invalidation methods disagree")
        require(float(row["time_us"]) >= 0, "negative invalidation time")
    for row in rows["compaction"]:
        require(truth(row["checker_ok"]) and truth(row["state_equal"]), "compaction failure")
        require(float(row["open_ms"]) >= 0, "negative open time")
    for row in rows["crash"]:
        require(int(row["exit_code"]) == 73, "unexpected crash-worker exit code")
        require(row["expected_root"] == row["observed_root"], "crash prefix mismatch")
        require(truth(row["checker_ok"]), "recovered checker rejection")
        require(truth(row["following_update_ok"]), "post-recovery update failed")
        require(not truth(row["timeout"]), "crash worker timeout")
    for row in rows["mutation"]:
        require(truth(row["rejected"]), "mutation accepted")
        require(row["expected_witness"] == row["observed_witness"], "mutation witness mismatch")
    for row in rows["concurrency"]:
        require(int(row["mixed_violations"]) == 0, "retained reader closure failure (not an endpoint-atomicity oracle)")
        require(truth(row["checker_ok"]), "concurrency final checker rejection")
    for row in rows["public_input"]:
        require(truth(row["checker_ok"]), "public-input checker rejection")
    require(truth(rows["public_input"][-1].get("final_checker_ok", "1")), "public final checker rejection")

    with tempfile.TemporaryDirectory(prefix="frontier-summary-check-") as temporary:
        regenerated = Path(temporary)
        computed_rows, computed_headline = summarize(results_path, regenerated)
        retained_rows = read_csv(summary / "all_summary.csv")
        regenerated_rows = read_csv(regenerated / "all_summary.csv")
        require(retained_rows == regenerated_rows, "retained aggregate CSV differs from raw recomputation")
        retained_headline = json.loads((summary / "headline.json").read_text(encoding="utf-8"))
        require(retained_headline == computed_headline, "retained headline differs from raw recomputation")
        require(
            (summary / "historical-values.tex").read_text(encoding="ascii")
            == (regenerated / "historical-values.tex").read_text(encoding="ascii"),
            "historical macro surface differs from raw recomputation",
        )
        require(
            (summary / "scale_plot.csv").read_text(encoding="utf-8")
            == (regenerated / "scale_plot.csv").read_text(encoding="utf-8"),
            "scale plot data differs from raw recomputation",
        )
        require(
            (summary / "invalidation_plot.csv").read_text(encoding="utf-8")
            == (regenerated / "invalidation_plot.csv").read_text(encoding="utf-8"),
            "invalidation plot data differs from raw recomputation",
        )

    headline = json.loads((summary / "headline.json").read_text(encoding="utf-8"))
    if mode == "full":
        require(headline["raw_counts"]["summary"] == 52, "summary row count is not 52")
        require(headline["correctness"]["process_exit_failures"] == 0, "process exit failures")
        require(headline["correctness"]["mutation_failures"] == 0, "mutation failures")
        require(headline["correctness"]["reader_closure_violations"] == 0, "reader violations")
    result = {
        "status": "RETAINED_DATA_CONSISTENT",
        "mode": mode,
        "raw_counts": {name: len(value) for name, value in rows.items()},
        "summary_rows": headline["raw_counts"]["summary"],
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True)
    arguments = parser.parse_args()
    result = verify(arguments.results)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
