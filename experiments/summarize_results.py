"""Recompute the retained historical aggregates from retained raw rows."""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from experiments.common import median, percentile, read_csv, write_csv

SUMMARY_FIELDS = [
    "family",
    "key",
    "variant",
    "samples",
    "update_p50_ms",
    "update_p95_ms",
    "query_p50_us",
    "write_amp_p50",
    "space_amp_p50",
    "time_p50_us",
    "open_p50_ms",
    "base_p50_ms",
    "writer_rate_p50",
    "writer_p95_ms",
    "reader_rate_p50",
    "errors",
    "passes",
    "extra",
]


def numbers(rows: Sequence[Mapping[str, str]], field: str) -> list[float]:
    values = []
    for row in rows:
        value = row.get(field, "")
        if value not in {"", None}:
            values.append(float(value))
    return values


def truth(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}


def summary_row(
    family: str,
    key: str,
    variant: str,
    rows: Sequence[Mapping[str, str]],
    *,
    errors: int = 0,
    passes: int | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    update = numbers(rows, "update_ms")
    query = numbers(rows, "query_p50_us")
    write_amp = numbers(rows, "write_amplification")
    space_amp = numbers(rows, "space_amplification")
    time_us = numbers(rows, "time_us")
    open_ms = numbers(rows, "open_ms")
    base_ms = numbers(rows, "base_p50_ms")
    writer_rate = numbers(rows, "writer_updates_per_s")
    writer_p95 = numbers(rows, "writer_p95_ms")
    reader_rate = numbers(rows, "reader_snapshots_per_s")
    return {
        "family": family,
        "key": key,
        "variant": variant,
        "samples": len(rows),
        "update_p50_ms": "" if not update else f"{median(update):.9f}",
        "update_p95_ms": "" if not update else f"{percentile(update, 0.95):.9f}",
        "query_p50_us": "" if not query else f"{median(query):.9f}",
        "write_amp_p50": "" if not write_amp else f"{median(write_amp):.9f}",
        "space_amp_p50": "" if not space_amp else f"{median(space_amp):.9f}",
        "time_p50_us": "" if not time_us else f"{median(time_us):.9f}",
        "open_p50_ms": "" if not open_ms else f"{median(open_ms):.9f}",
        "base_p50_ms": "" if not base_ms else f"{median(base_ms):.9f}",
        "writer_rate_p50": "" if not writer_rate else f"{median(writer_rate):.9f}",
        "writer_p95_ms": "" if not writer_p95 else f"{median(writer_p95):.9f}",
        "reader_rate_p50": "" if not reader_rate else f"{median(reader_rate):.9f}",
        "errors": errors,
        "passes": len(rows) - errors if passes is None else passes,
        "extra": json.dumps(dict(extra or {}), sort_keys=True, separators=(",", ":")),
    }


def group_rows(rows: Sequence[Mapping[str, str]], keys: Sequence[str]):
    groups: dict[tuple[str, ...], list[Mapping[str, str]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row[key] for key in keys)].append(row)
    return groups


def _find(rows: Sequence[Mapping[str, Any]], family: str, key: str, variant: str) -> Mapping[str, Any]:
    for row in rows:
        if row["family"] == family and row["key"] == key and row["variant"] == variant:
            return row
    raise KeyError((family, key, variant))


def _float(row: Mapping[str, Any], field: str) -> float:
    return float(row[field])


def _fmt(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}"


def _tex_int(value: int) -> str:
    return f"{value:,}"


def summarize(results: str | Path, output: str | Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    results_path = Path(results)
    raw = results_path / "raw"
    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=True)

    scale = read_csv(raw / "scale.csv")
    sensitivity = read_csv(raw / "sensitivity.csv")
    invalidation = read_csv(raw / "invalidation.csv")
    compaction = read_csv(raw / "compaction.csv")
    crash = read_csv(raw / "crash.csv")
    mutation = read_csv(raw / "mutation.csv")
    concurrency = read_csv(raw / "concurrency.csv")
    public = read_csv(raw / "public_input.csv")
    abstract = json.loads((raw / "abstract_crash_cuts.json").read_text(encoding="utf-8"))
    resources = json.loads((raw / "resources.json").read_text(encoding="utf-8"))

    rows: list[dict[str, Any]] = []
    for (source_count, engine), group in sorted(
        group_rows(scale, ["source_count", "engine"]).items(),
        key=lambda item: (int(item[0][0]), item[0][1]),
    ):
        failures = sum(not (truth(row["durable_equal"]) and truth(row["checker_ok"])) for row in group)
        rows.append(summary_row("scale", source_count, engine, group, errors=failures))

    for (setting, engine), group in sorted(group_rows(sensitivity, ["setting", "engine"]).items()):
        failures = sum(not (truth(row["durable_equal"]) and truth(row["checker_ok"])) for row in group)
        rows.append(summary_row("sensitivity", setting, engine, group, errors=failures))

    for (method,), group in sorted(group_rows(invalidation, ["method"]).items()):
        failures = sum(not truth(row["equal_set"]) for row in group)
        rows.append(summary_row("invalidation", "96000-facts", method, group, errors=failures))

    for (interval,), group in sorted(group_rows(compaction, ["interval"]).items(), key=lambda item: int(item[0][0])):
        failures = sum(not (truth(row["checker_ok"]) and truth(row["state_equal"])) for row in group)
        rows.append(
            summary_row(
                "compaction",
                f"interval-{interval}",
                "FrontierStore",
                group,
                errors=failures,
                extra={"selected_segments_p50": median(numbers(group, "selected_segments"))},
            )
        )

    cut_order = {name: index for index, name in enumerate([
        "before_segment",
        "after_header",
        "before_footer",
        "after_footer",
        "after_segment_fsync",
        "after_segment_dirsync",
        "after_root_write",
        "after_root_fsync",
        "after_root_replace",
        "after_root_dirsync",
    ])}
    for (boundary,), group in sorted(group_rows(crash, ["boundary"]).items(), key=lambda item: cut_order[item[0][0]]):
        failures = sum(
            not (
                truth(row["checker_ok"])
                and truth(row["following_update_ok"])
                and row["expected_root"] == row["observed_root"]
                and not truth(row["timeout"])
            )
            for row in group
        )
        rows.append(
            summary_row(
                "crash",
                boundary,
                group[0]["expected_root"],
                group,
                errors=failures,
                extra={"exit_code": int(group[0]["exit_code"])},
            )
        )

    for (name,), group in sorted(group_rows(mutation, ["mutation"]).items()):
        failures = sum(not truth(row["rejected"]) or row["expected_witness"] != row["observed_witness"] for row in group)
        rows.append(
            summary_row(
                "mutation",
                name,
                group[0]["observed_witness"],
                group,
                errors=failures,
            )
        )

    for (readers,), group in sorted(group_rows(concurrency, ["readers"]).items(), key=lambda item: int(item[0][0])):
        failures = sum(int(row["mixed_violations"]) != 0 or not truth(row["checker_ok"]) for row in group)
        rows.append(
            summary_row(
                "concurrency",
                f"readers-{readers}",
                "FrontierStore",
                group,
                errors=failures,
                extra={"closure_violations": sum(int(row["mixed_violations"]) for row in group)},
            )
        )

    public_failures = sum(not truth(row["checker_ok"]) for row in public)
    rows.append(
        summary_row(
            "public-input",
            "solidity-excerpts",
            "FrontierStore",
            public,
            errors=public_failures,
            extra={
                "updates": len(public),
                "final_schema": int(public[-1]["schema"]),
                "final_facts": int(public[-1]["fact_count"]),
                "verify_p50_ms": median(numbers(public, "verify_ms")),
                "final_compaction_ms": float(public[-1].get("final_compaction_ms") or 0.0),
            },
        )
    )

    mode = resources.get("mode")
    if mode == "full" and len(rows) != 52:
        raise AssertionError(f"expected 52 summary rows, found {len(rows)}")
    write_csv(destination / "all_summary.csv", rows, SUMMARY_FIELDS)

    source_counts = sorted({int(row["source_count"]) for row in scale})
    central_source = max(source_counts)
    central: dict[str, Mapping[str, Any]] = {}
    for engine in ["FrontierStore", "SQLite-Normalized", "SQLite-Rebuild", "DBM-Dumb-Rebuild"]:
        central[engine] = _find(rows, "scale", str(central_source), engine)

    batch_one_fs = _find(rows, "sensitivity", "batch-1", "FrontierStore")
    batch_one_sqlite = _find(rows, "sensitivity", "batch-1", "SQLite-Normalized")
    indexed = _find(rows, "invalidation", "96000-facts", "reverse-index")
    scanned = _find(rows, "invalidation", "96000-facts", "full-scan")

    compaction_headline = {}
    for interval in sorted({int(row["interval"]) for row in compaction}):
        row = _find(rows, "compaction", f"interval-{interval}", "FrontierStore")
        compaction_headline[str(interval)] = {
            "open_p50_ms": _float(row, "open_p50_ms"),
            "space_amplification_p50": _float(row, "space_amp_p50"),
            "base_p50_ms": None if row["base_p50_ms"] == "" else _float(row, "base_p50_ms"),
        }

    concurrency_headline = {}
    for readers in sorted({int(row["readers"]) for row in concurrency}):
        row = _find(rows, "concurrency", f"readers-{readers}", "FrontierStore")
        concurrency_headline[str(readers)] = {
            "writer_updates_per_s_p50": _float(row, "writer_rate_p50"),
            "writer_p95_ms_p50": _float(row, "writer_p95_ms"),
            "reader_snapshots_per_s_p50": _float(row, "reader_rate_p50"),
        }

    public_row = rows[-1]
    public_extra = json.loads(public_row["extra"])
    headline = {
        "mode": mode,
        "evidence_scope": "Retained earlier implementation; current repaired evidence is recorded separately; no individual old test transcript.",
        "correctness": {
            "archived_asserted_tests": 23,
            "abstract_histories": int(abstract["histories"]),
            "intermediate_states": int(abstract["intermediate_states"]),
            "abstract_crash_cuts": int(abstract["crash_cuts"]),
            "abstract_violations": len(abstract["violations"]),
            "process_exits": len(crash),
            "process_exit_failures": sum(int(row["errors"]) for row in rows if row["family"] == "crash"),
            "mutations": len(mutation),
            "mutation_failures": sum(int(row["errors"]) for row in rows if row["family"] == "mutation"),
            "concurrency_runs": len(concurrency),
            "reader_closure_violations": sum(int(row["mixed_violations"]) for row in concurrency),
            "public_updates": len(public),
        },
        "central": {
            "source_count": central_source,
            "fact_count": int(next(row["fact_count"] for row in scale if int(row["source_count"]) == central_source)),
            "batch": int(next(row["batch"] for row in scale if int(row["source_count"]) == central_source)),
            "dependency_width": int(next(row["dependency_width"] for row in scale if int(row["source_count"]) == central_source)),
            "engines": {
                engine: {
                    "update_p50_ms": _float(summary, "update_p50_ms"),
                    "update_p95_ms": _float(summary, "update_p95_ms"),
                    "query_p50_us": _float(summary, "query_p50_us"),
                    "write_amplification_p50": _float(summary, "write_amp_p50"),
                    "space_amplification_p50": _float(summary, "space_amp_p50"),
                }
                for engine, summary in central.items()
            },
        },
        "ratios": {
            "sqlite_normalized_over_frontier_update": _float(central["SQLite-Normalized"], "update_p50_ms") / _float(central["FrontierStore"], "update_p50_ms"),
            "sqlite_rebuild_over_frontier_update": _float(central["SQLite-Rebuild"], "update_p50_ms") / _float(central["FrontierStore"], "update_p50_ms"),
            "dbm_dumb_rebuild_over_frontier_update": _float(central["DBM-Dumb-Rebuild"], "update_p50_ms") / _float(central["FrontierStore"], "update_p50_ms"),
            "scan_over_reverse_invalidation": _float(scanned, "time_p50_us") / _float(indexed, "time_p50_us"),
        },
        "batch_one": {
            "FrontierStore_update_p50_ms": _float(batch_one_fs, "update_p50_ms"),
            "SQLite-Normalized_update_p50_ms": _float(batch_one_sqlite, "update_p50_ms"),
        },
        "invalidation": {
            "fact_count": int(invalidation[0]["fact_count"]),
            "affected_facts": int(invalidation[0]["affected_facts"]),
            "reverse_p50_us": _float(indexed, "time_p50_us"),
            "scan_p50_us": _float(scanned, "time_p50_us"),
        },
        "compaction": compaction_headline,
        "concurrency": concurrency_headline,
        "public_input": {
            "updates": len(public),
            "update_p50_ms": _float(public_row, "update_p50_ms"),
            "verify_p50_ms": float(public_extra["verify_p50_ms"]),
            "final_facts": int(public_extra["final_facts"]),
        },
        "resources": {
            "retained_sample": resources,
            "campaign_total_known": False,
            "rss_is_aggregate_concurrent": False,
            "worker_count_is_observed": False,
            "interpretation": "Earlier sampled interval only; actual historical use is not reconstructed. Current admission uses a separate conservative policy upper bound.",
        },
        "raw_counts": {
            "scale": len(scale),
            "sensitivity": len(sensitivity),
            "invalidation": len(invalidation),
            "compaction": len(compaction),
            "crash": len(crash),
            "mutation": len(mutation),
            "concurrency": len(concurrency),
            "public_input": len(public),
            "summary": len(rows),
        },
    }
    (destination / "headline.json").write_text(json.dumps(headline, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    plot_rows = []
    for source_count in source_counts:
        plot = {"source_count": source_count}
        for engine, column in [
            ("FrontierStore", "frontier_ms"),
            ("SQLite-Normalized", "sqlite_normalized_ms"),
            ("SQLite-Rebuild", "sqlite_rebuild_ms"),
            ("DBM-Dumb-Rebuild", "dbm_dumb_rebuild_ms"),
        ]:
            plot[column] = _float(_find(rows, "scale", str(source_count), engine), "update_p50_ms")
        plot_rows.append(plot)
    write_csv(destination / "scale_plot.csv", plot_rows)
    write_csv(
        destination / "invalidation_plot.csv",
        [
            {"method": "reverse index", "time_us": _float(indexed, "time_p50_us")},
            {"method": "full scan", "time_us": _float(scanned, "time_p50_us")},
        ],
    )

    fs = headline["central"]["engines"]["FrontierStore"]
    sql = headline["central"]["engines"]["SQLite-Normalized"]
    sql_rebuild = headline["central"]["engines"]["SQLite-Rebuild"]
    dbm_dumb = headline["central"]["engines"]["DBM-Dumb-Rebuild"]
    macros = {
        "NumTests": str(headline["correctness"]["archived_asserted_tests"]),
        "NumHistories": _tex_int(headline["correctness"]["abstract_histories"]),
        "NumLogicalStates": _tex_int(headline["correctness"]["intermediate_states"]),
        "NumAbstractCuts": _tex_int(headline["correctness"]["abstract_crash_cuts"]),
        "NumProcessExits": str(headline["correctness"]["process_exits"]),
        "NumMutations": str(headline["correctness"]["mutations"]),
        "NumConcurrencyRuns": str(headline["correctness"]["concurrency_runs"]),
        "NumPublicUpdates": str(headline["correctness"]["public_updates"]),
        "CentralSources": _tex_int(headline["central"]["source_count"]),
        "CentralFacts": _tex_int(headline["central"]["fact_count"]),
        "FSUpdate": _fmt(fs["update_p50_ms"]),
        "FSUpdateP": _fmt(fs["update_p95_ms"]),
        "FSQuery": _fmt(fs["query_p50_us"], 1),
        "FSWA": _fmt(fs["write_amplification_p50"]),
        "FSSA": _fmt(fs["space_amplification_p50"]),
        "SQLUpdate": _fmt(sql["update_p50_ms"]),
        "SQLUpdateP": _fmt(sql["update_p95_ms"]),
        "SQLQuery": _fmt(sql["query_p50_us"], 1),
        "SQLWA": _fmt(sql["write_amplification_p50"]),
        "SQLSA": _fmt(sql["space_amplification_p50"]),
        "SQLRebuild": _fmt(sql_rebuild["update_p50_ms"]),
        "SQLRebuildP": _fmt(sql_rebuild["update_p95_ms"]),
        "SQLRebuildQuery": _fmt(sql_rebuild["query_p50_us"], 1),
        "SQLRebuildWA": _fmt(sql_rebuild["write_amplification_p50"]),
        "SQLRebuildSA": _fmt(sql_rebuild["space_amplification_p50"]),
        "DBMDumbRebuild": _fmt(dbm_dumb["update_p50_ms"]),
        "DBMDumbRebuildP": _fmt(dbm_dumb["update_p95_ms"]),
        "DBMDumbQuery": _fmt(dbm_dumb["query_p50_us"], 1),
        "DBMDumbWA": _fmt(dbm_dumb["write_amplification_p50"]),
        "DBMDumbSA": _fmt(dbm_dumb["space_amplification_p50"]),
        "SQLRatio": _fmt(headline["ratios"]["sqlite_normalized_over_frontier_update"]),
        "SQLFaster": _fmt(1.0 / headline["ratios"]["sqlite_normalized_over_frontier_update"]),
        "WALower": _fmt(sql["write_amplification_p50"] / fs["write_amplification_p50"]),
        "SQLRebuildRatio": _fmt(headline["ratios"]["sqlite_rebuild_over_frontier_update"]),
        "DBMDumbRatio": _fmt(headline["ratios"]["dbm_dumb_rebuild_over_frontier_update"]),
        "BatchOneFS": _fmt(headline["batch_one"]["FrontierStore_update_p50_ms"]),
        "BatchOneSQL": _fmt(headline["batch_one"]["SQLite-Normalized_update_p50_ms"]),
        "ReverseUS": _fmt(headline["invalidation"]["reverse_p50_us"]),
        "ScanMS": _fmt(headline["invalidation"]["scan_p50_us"] / 1000.0),
        "InvalidationRatio": _fmt(headline["ratios"]["scan_over_reverse_invalidation"], 0),
        "OpenReduction": _fmt(compaction_headline["0"]["open_p50_ms"] / compaction_headline["10"]["open_p50_ms"]),
        "WriterSlowdown": _fmt(concurrency_headline["0"]["writer_updates_per_s_p50"] / concurrency_headline["3"]["writer_updates_per_s_p50"]),
        "PublicUpdate": _fmt(headline["public_input"]["update_p50_ms"]),
        "PublicVerify": _fmt(headline["public_input"]["verify_p50_ms"]),
        "WallSeconds": _fmt(float(resources["wall_seconds"]), 3),
        "PeakMiB": _fmt(float(resources["peak_resident_mib"]), 3),
    }
    interval_names = {0: "Zero", 10: "Ten", 40: "Forty"}
    for interval, word in interval_names.items():
        if str(interval) in compaction_headline:
            item = compaction_headline[str(interval)]
            macros[f"Open{word}"] = _fmt(item["open_p50_ms"])
            macros[f"SA{word}"] = _fmt(item["space_amplification_p50"])
            macros[f"Base{word}"] = "--" if item["base_p50_ms"] is None else _fmt(item["base_p50_ms"])
    reader_names = {0: "Zero", 1: "One", 3: "Three"}
    for readers, word in reader_names.items():
        if str(readers) in concurrency_headline:
            item = concurrency_headline[str(readers)]
            macros[f"WriterRate{word}"] = _fmt(item["writer_updates_per_s_p50"], 1)
            macros[f"ReaderRate{word}"] = _fmt(item["reader_snapshots_per_s_p50"], 1)
    macro_text = "\n".join(f"\\newcommand{{\\{name}}}{{{value}}}" for name, value in macros.items()) + "\n"
    (destination / "historical-values.tex").write_text(macro_text, encoding="ascii")
    return rows, headline


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    rows, headline = summarize(arguments.results, arguments.output)
    print(json.dumps({"summary_rows": len(rows), "mode": headline["mode"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
