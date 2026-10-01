"""Generate static paper macros from retained correctness and resource evidence.

This command performs data-only reconstruction. It never imports or executes a
storage engine. The output is intentionally limited to values consumed by the
paper outside the current fixed-order performance summaries.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def csv_row_count(path: Path) -> int:
    with path.open(newline="", encoding="utf-8") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def render_paper_values(artifact_root: str | Path) -> str:
    """Return the canonical static paper-macro surface for ``artifact_root``."""

    root = Path(artifact_root)
    abstract = load_json(root / "results/raw/abstract_crash_cuts.json")
    require(abstract.get("all_closed") is True, "bounded abstract cuts are not all closed")
    require(abstract.get("all_endpoints") is True, "bounded abstract cuts violate endpoint identity")
    require(abstract.get("closure_violations") == [], "bounded closure violations are present")
    require(abstract.get("endpoint_violations") == [], "bounded endpoint violations are present")
    require(abstract.get("violations") == [], "bounded abstract-cut violations are present")
    require(abstract.get("model") == "abstract-selector-object-v1", "bounded abstract selector model differs")
    require(
        abstract.get("selected_endpoint_counts") == {"old": 6200, "new": 1550},
        "bounded abstract selector counts differ",
    )

    histories = int(abstract["histories"])
    logical_states = int(abstract["intermediate_states"])
    abstract_cuts = int(abstract["crash_cuts"])
    require(histories > 0 and logical_states > 0 and abstract_cuts > 0, "nonpositive bounded count")

    joint = load_json(root / "results/current/joint-history/observations.json")
    require(joint.get("status") == "CASE_COMPLETED", "joint history is not complete")
    rows = joint.get("rows")
    require(isinstance(rows, list) and rows, "joint history has no rows")
    joint_updates = int(joint["updates"])
    require(len(rows) == joint_updates, "joint-history row count differs from declared updates")
    require(
        [int(row["update"]) for row in rows] == list(range(1, joint_updates + 1)),
        "joint-history update identifiers are not contiguous",
    )
    for row in rows:
        require(row.get("current_endpoint_equal") is True, "joint current endpoint mismatch")
        require(row.get("historical_endpoint_equal") is True, "joint historical endpoint mismatch")
        require(row.get("protected_present") is True, "joint protected file missing")
        require(row.get("quiescent_checker_accepted") is True, "joint checker rejection")
    joint_compactions = sum(row.get("compaction_ns") is not None for row in rows)
    require(joint_compactions > 0, "joint history contains no compaction")
    require(
        sum(row.get("released_history_collected") is True for row in rows) == joint_compactions,
        "joint-history collection evidence does not align with compactions",
    )

    process_exits = csv_row_count(root / "results/raw/crash.csv")
    mutations = csv_row_count(root / "results/raw/mutation.csv")
    require(process_exits > 0 and mutations > 0, "historical retained rows are empty")

    accounting = load_json(root / "resource-accounting.json")
    campaign_upper_cpu = int(float(accounting["campaign_cpu_upper_bound_seconds"]))
    require(campaign_upper_cpu > 0, "nonpositive campaign CPU upper bound")

    values = (
        ("NumHistories", histories),
        ("NumLogicalStates", logical_states),
        ("NumAbstractCuts", abstract_cuts),
        ("NumJointUpdates", joint_updates),
        ("NumJointCompactions", joint_compactions),
        ("NumInheritedProcessExits", process_exits),
        ("NumInheritedMutations", mutations),
        ("CampaignUpperCPU", campaign_upper_cpu),
    )
    lines = ["% Generated deterministically from retained bounded evidence."]
    lines.extend(rf"\newcommand{{\{name}}}{{{value:,}}}" for name, value in values)
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="artifact root (default: parent of experiments/)",
    )
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    text = render_paper_values(arguments.artifact_root)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(text, encoding="ascii")
    print(json.dumps({"status": "PAPER_VALUES_WRITTEN", "output": arguments.output.name}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
