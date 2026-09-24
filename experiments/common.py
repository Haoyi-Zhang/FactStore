"""Shared deterministic measurement and serialization helpers."""

from __future__ import annotations

import csv
import json
import math
import os
from pathlib import Path
import statistics
import time
from typing import Any, Iterable, Mapping, Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from frontierstore.model import State, Transaction


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        raise ValueError("empty percentile input")
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def median(values: Sequence[float]) -> float:
    return float(statistics.median(float(value) for value in values))


def write_csv(path: str | Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str] | None = None) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        if not rows:
            raise ValueError("fieldnames required for empty row set")
        names: list[str] = []
        for row in rows:
            for key in row:
                if key not in names:
                    names.append(key)
        fieldnames = names
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    # Historical raw rows retain the mistaken label. This is an identity
    # correction, not a new baseline implementation or a new measurement.
    for row in rows:
        if row.get("engine") == "NDBM-Rebuild":
            row["engine"] = "DBM-Dumb-Rebuild"
    return rows


def directory_bytes(path: str | Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            candidate = Path(root) / name
            try:
                total += candidate.stat().st_size
            except FileNotFoundError:
                continue
    return total


def process_write_bytes() -> int | None:
    try:
        for line in Path("/proc/self/io").read_text(encoding="ascii").splitlines():
            if line.startswith("write_bytes:"):
                return int(line.split(":", 1)[1].strip())
    except (FileNotFoundError, PermissionError, ValueError):
        return None
    return None


def transaction_size(transaction: Transaction) -> int:
    from frontierstore.model import canonical_json_bytes
    value = {
        "source_changes": dict(sorted(transaction.source_changes.items())),
        "fact_replacements": [dict(item) for item in transaction.fact_replacements],
        "fact_retractions": list(transaction.fact_retractions),
        "new_schema": transaction.new_schema,
    }
    return len(canonical_json_bytes(value))


def timed(callable_):
    start = time.perf_counter_ns()
    value = callable_()
    end = time.perf_counter_ns()
    return value, (end - start) / 1_000_000.0


def assert_states(expected: State, actual: State) -> None:
    from frontierstore.model import states_equal
    if not states_equal(expected, actual):
        raise AssertionError(
            json.dumps(
                {"expected": expected.as_dict(), "actual": actual.as_dict()},
                sort_keys=True,
            )[:4000]
        )


def bool_text(value: bool) -> str:
    return "1" if value else "0"


def float_text(value: float | int | None, digits: int = 9) -> str:
    if value is None:
        return ""
    return f"{float(value):.{digits}f}"


def int_text(value: int | None) -> str:
    return "" if value is None else str(int(value))
