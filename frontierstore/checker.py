"""Independent byte-level checker for a selected FrontierStore root.

This module deliberately imports no writer, transition, or shared-model module.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

ROOT_NAME = "ROOT"
SEGMENT_DIR = "segments"
ROOT_FORMAT = "frontierstore-root"
SEGMENT_FORMAT = "frontierstore-segment"
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_FINAL_SEGMENT = re.compile(r"^segment-[0-9]{8}\.jsonl$")
_BASE_ORDER = {"source_put": 0, "fact_put": 1, "reverse_put": 2}
_DELTA_ORDER = {
    "source_put": 0,
    "source_del": 1,
    "fact_del": 2,
    "fact_put": 3,
    "reverse_put": 4,
    "reverse_del": 5,
}

_ROOT_FIELDS = {"format", "epoch", "schema", "counts", "segments"}
_HEADER_FIELDS = {"kind", "format", "epoch", "parent", "mode", "schema"}
_FOOTER_FIELDS = {"kind", "records", "counts"}
_RECORD_FIELDS = {
    "source_put": {"kind", "id", "generation", "payload"},
    "source_del": {"kind", "id"},
    "fact_del": {"kind", "id"},
    "fact_put": {"kind", "id", "payload", "schema", "dependencies"},
    "reverse_put": {"kind", "id", "facts"},
    "reverse_del": {"kind", "id"},
}


class Rejection(Exception):
    def __init__(self, code: str, detail: Any):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def reject(code: str, detail: Any) -> None:
    raise Rejection(code, detail)


def exact_fields(value: dict[str, Any], expected: set[str], role: str) -> None:
    actual = set(value)
    if actual != expected:
        reject(
            "FIELD_SET",
            {"role": role, "missing": sorted(expected - actual), "extra": sorted(actual - expected)},
        )


def strict_int(value: Any, role: str, *, positive: bool = False) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        reject("FIELD_TYPE", role)
    if positive and value <= 0:
        reject("FIELD_VALUE", role)
    return value


def strict_text(value: Any, role: str) -> str:
    if not isinstance(value, str):
        reject("FIELD_TYPE", role)
    return value


def identifier_text(value: Any, role: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        reject("IDENTIFIER", role)
    return value


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in pairs:
        if key in result:
            reject("DUPLICATE_FIELD", key)
        result[key] = item
    return result


def count_map(value: Any, role: str) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != {"sources", "facts", "reverse"}:
        reject("FIELD_TYPE", role)
    for family in sorted(value):
        number = strict_int(value[family], f"{role}:{family}")
        if number < 0:
            reject("FIELD_VALUE", f"{role}:{family}")
    return value


def read_json(path: Path, code: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="ascii"), object_pairs_hook=unique_object)
    except FileNotFoundError:
        reject(code, str(path.name))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        reject(code, str(exc))


def confined_segment(segment_dir: Path, value: Any) -> Path:
    if not isinstance(value, str) or not _FINAL_SEGMENT.fullmatch(value):
        reject("PATH_ESCAPE", value)
    directory = segment_dir.resolve()
    candidate = (segment_dir / value).resolve()
    if os.path.commonpath([str(directory), str(candidate)]) != str(directory):
        reject("PATH_ESCAPE", value)
    return candidate


def check(store_path: str | os.PathLike[str]) -> dict[str, Any]:
    path = Path(store_path)
    segment_dir = path / SEGMENT_DIR
    root = read_json(path / ROOT_NAME, "ROOT_PARSE")
    if not isinstance(root, dict) or root.get("format") != ROOT_FORMAT:
        reject("ROOT_FORMAT", "unrecognized root")
    exact_fields(root, _ROOT_FIELDS, "root")
    root_epoch = strict_int(root.get("epoch"), "root epoch", positive=True)
    root_schema = strict_int(root.get("schema"), "root schema", positive=True)
    root_counts = count_map(root.get("counts"), "root counts")
    selected = root.get("segments")
    if not isinstance(selected, list) or not selected:
        reject("ROOT_SEGMENTS", selected)

    sources: dict[str, dict[str, Any]] = {}
    facts: dict[str, dict[str, Any]] = {}
    reverse: dict[str, tuple[str, ...]] = {}
    previous_epoch: int | None = None
    active_schema: int | None = None
    records_checked = 0

    for position, raw_name in enumerate(selected):
        name = strict_text(raw_name, "selected segment")
        segment_path = confined_segment(segment_dir, name)
        try:
            lines = segment_path.read_text(encoding="ascii").splitlines()
        except FileNotFoundError:
            reject("MISSING_SEGMENT", name)
        except (OSError, UnicodeDecodeError) as exc:
            reject("UNREADABLE_SEGMENT", f"{name}:{exc}")
        if len(lines) < 2:
            reject("INCOMPLETE_SEGMENT", name)
        values: list[Any] = []
        for line_number, line in enumerate(lines, 1):
            try:
                values.append(json.loads(line, object_pairs_hook=unique_object))
            except json.JSONDecodeError:
                reject("UNREADABLE_SEGMENT", f"{name}:{line_number}")
        header, footer = values[0], values[-1]
        if not isinstance(header, dict) or header.get("kind") != "header" or header.get("format") != SEGMENT_FORMAT:
            reject("BAD_HEADER", name)
        exact_fields(header, _HEADER_FIELDS, f"{name}:header")
        if not isinstance(footer, dict) or footer.get("kind") != "footer":
            reject("INCOMPLETE_SEGMENT", name)
        exact_fields(footer, _FOOTER_FIELDS, f"{name}:footer")
        epoch = strict_int(header.get("epoch"), "segment epoch", positive=True)
        schema = strict_int(header.get("schema"), "segment schema", positive=True)
        if epoch != int(name[8:16]):
            reject("EPOCH_NAME", name)
        mode = header.get("mode")
        parent = header.get("parent")
        if not isinstance(mode, str) or mode not in {"base", "delta"}:
            reject("BAD_MODE", name)
        if position == 0:
            if mode != "base" or parent is not None:
                reject("FIRST_NOT_BASE", name)
            sources, facts, reverse = {}, {}, {}
        else:
            parent = strict_int(parent, "parent epoch", positive=True)
            if mode != "delta" or parent != previous_epoch:
                reject("PARENT_CHAIN", name)
            if epoch != int(previous_epoch) + 1:
                reject("EPOCH_CHAIN", name)
        previous_epoch = epoch
        active_schema = schema

        body = values[1:-1]
        order = _BASE_ORDER if mode == "base" else _DELTA_ORDER
        phase = -1
        identifiers_by_kind: dict[str, set[str]] = {kind: set() for kind in order}
        for record in body:
            records_checked += 1
            if not isinstance(record, dict):
                reject("FIELD_TYPE", f"{name}:record")
            kind = record.get("kind")
            if not isinstance(kind, str) or kind not in order:
                reject("RECORD_KIND", f"{name}:{kind}")
            exact_fields(record, _RECORD_FIELDS[kind], f"{name}:{kind}")
            next_phase = order[kind]
            if next_phase < phase:
                reject("PHASE_ORDER", f"{name}:{kind}")
            phase = next_phase
            identifier = identifier_text(record.get("id"), f"{kind} id")
            if identifier in identifiers_by_kind[kind]:
                reject("DUPLICATE_RECORD", f"{name}:{kind}:{identifier}")
            identifiers_by_kind[kind].add(identifier)

            if kind == "source_put":
                generation = strict_int(record.get("generation"), "source generation", positive=True)
                if generation > epoch:
                    reject("SOURCE_GENERATION_HORIZON", identifier)
                payload = strict_text(record.get("payload"), "source payload")
                source = {"id": identifier, "generation": generation, "payload": payload}
                if mode == "delta" and source != sources.get(identifier) and generation != epoch:
                    reject("SOURCE_STAMP", identifier)
                sources[identifier] = source
            elif kind == "source_del":
                sources.pop(identifier, None)
            elif kind == "fact_del":
                facts.pop(identifier, None)
            elif kind == "fact_put":
                payload = strict_text(record.get("payload"), "fact payload")
                fact_schema = strict_int(record.get("schema"), "fact schema", positive=True)
                raw_dependencies = record.get("dependencies")
                if not isinstance(raw_dependencies, list) or not raw_dependencies:
                    reject("DEPENDENCY_SHAPE", identifier)
                dependencies: list[tuple[str, int]] = []
                seen_sources: set[str] = set()
                for item in raw_dependencies:
                    if not isinstance(item, dict) or set(item) != {"source", "generation"}:
                        reject("DEPENDENCY_SHAPE", identifier)
                    source_id = identifier_text(item.get("source"), "dependency source")
                    generation = strict_int(item.get("generation"), "dependency generation", positive=True)
                    if source_id in seen_sources:
                        reject("DUPLICATE_DEPENDENCY", f"{identifier}:{source_id}")
                    seen_sources.add(source_id)
                    dependencies.append((source_id, generation))
                if tuple(dependencies) != tuple(sorted(dependencies)):
                    reject("DEPENDENCY_ORDER", identifier)
                facts[identifier] = {
                    "id": identifier,
                    "payload": payload,
                    "schema": fact_schema,
                    "dependencies": tuple(dependencies),
                }
            elif kind == "reverse_put":
                members = record.get("facts")
                if not isinstance(members, list) or not all(isinstance(member, str) for member in members):
                    reject("FIELD_TYPE", f"reverse facts:{identifier}")
                for member in members:
                    identifier_text(member, "reverse fact identifier")
                if members != sorted(set(members)):
                    reject("REVERSE_ORDER", identifier)
                reverse[identifier] = tuple(members)
            elif kind == "reverse_del":
                reverse.pop(identifier, None)

        if mode == "delta":
            if identifiers_by_kind["source_put"] & identifiers_by_kind["source_del"]:
                reject("DELTA_SOURCE_OVERLAP", name)
            if identifiers_by_kind["reverse_put"] & identifiers_by_kind["reverse_del"]:
                reject("DELTA_REVERSE_OVERLAP", name)
        declared_records = strict_int(footer.get("records"), "footer records")
        if declared_records != len(body):
            reject("FOOTER_COUNT", name)
        actual_counts = {"sources": len(sources), "facts": len(facts), "reverse": len(reverse)}
        declared_counts = count_map(footer.get("counts"), "footer counts")
        if declared_counts != actual_counts:
            reject("FOOTER_COUNT", {"segment": name, "declared": footer.get("counts"), "actual": actual_counts})

        if set(reverse) != set(sources):
            reject("REVERSE_DOMAIN", {"missing": sorted(set(sources) - set(reverse)), "extra": sorted(set(reverse) - set(sources))})
        expected_reverse: dict[str, set[str]] = {source_id: set() for source_id in sources}
        for fact_id, fact in facts.items():
            if fact["schema"] != schema:
                reject("SCHEMA_MISMATCH", fact_id)
            for source_id, required_generation in fact["dependencies"]:
                source = sources.get(source_id)
                if source is None:
                    reject("MISSING_SOURCE", f"{fact_id}:{source_id}")
                if source["generation"] != required_generation:
                    reject(
                        "STALE_DEPENDENCY",
                        {
                            "fact": fact_id,
                            "source": source_id,
                            "required": required_generation,
                            "visible": source["generation"],
                        },
                    )
                expected_reverse[source_id].add(fact_id)
        for source_id in sources:
            expected = tuple(sorted(expected_reverse[source_id]))
            if reverse.get(source_id) != expected:
                reject("REVERSE_MISMATCH", {"source": source_id, "declared": reverse.get(source_id), "expected": expected})

    if previous_epoch != root_epoch or active_schema != root_schema:
        reject("ROOT_TIP", {"epoch": previous_epoch, "schema": active_schema})
    actual_root_counts = {"sources": len(sources), "facts": len(facts), "reverse": len(reverse)}
    if root_counts != actual_root_counts:
        reject("ROOT_COUNT", {"declared": root_counts, "actual": actual_root_counts})
    return {
        "status": "ACCEPT",
        "epoch": root_epoch,
        "schema": root_schema,
        "segments": len(selected),
        "records": records_checked,
        "counts": actual_root_counts,
    }


def run(store_path: str | os.PathLike[str]) -> dict[str, Any]:
    try:
        return check(store_path)
    except Rejection as exc:
        return {"status": "REJECT", "witness": exc.code, "detail": exc.detail}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("store")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = run(args.store)
    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif result["status"] == "ACCEPT":
        print(
            f"ACCEPT epoch={result['epoch']} segments={result['segments']} "
            f"sources={result['counts']['sources']} facts={result['counts']['facts']}"
        )
    else:
        print(f"REJECT {result['witness']}: {result['detail']}")
    return 0 if result["status"] == "ACCEPT" else 1


if __name__ == "__main__":
    sys.exit(main())
