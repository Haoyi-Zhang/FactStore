"""Independent parser for a complete canonical audit-state export.

This module intentionally imports only the Python standard library.  It does not
import the transition model, storage writer, SQLite wrapper, or FrontierStore
parser.  The format is deliberately simple so a transaction engine can expose a
separate, database-independent audit surface at the cost of rewriting the live
logical image.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
MAX_EPOCH = 99_999_999


class AuditExportError(ValueError):
    def __init__(self, witness: str, detail: str = "") -> None:
        super().__init__(f"{witness}: {detail}" if detail else witness)
        self.witness = witness
        self.detail = detail


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AuditExportError("DUPLICATE_FIELD", key)
        result[key] = value
    return result


def _integer(value: Any, witness: str, *, positive: bool = False) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise AuditExportError(witness, "expected integer")
    if positive and value <= 0:
        raise AuditExportError(witness, "expected positive integer")
    return value


def _identifier(value: Any, witness: str) -> str:
    if not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None:
        raise AuditExportError(witness, repr(value))
    return value


def _exact_keys(value: Any, keys: set[str], witness: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise AuditExportError(witness, f"expected {sorted(keys)}")
    return value


def parse_audit_export(path: str | Path) -> dict[str, Any]:
    try:
        text = Path(path).read_text(encoding="utf-8")
        value = json.loads(text, object_pairs_hook=_pairs)
    except AuditExportError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AuditExportError("UNREADABLE_EXPORT", str(error)) from error

    top = _exact_keys(value, {"epoch", "schema", "sources", "facts", "reverse"}, "TOP_LEVEL_SHAPE")
    epoch = _integer(top["epoch"], "EPOCH_TYPE", positive=True)
    if epoch > MAX_EPOCH:
        raise AuditExportError("EPOCH_RANGE", str(epoch))
    schema = _integer(top["schema"], "SCHEMA_TYPE", positive=True)
    if not isinstance(top["sources"], dict) or not isinstance(top["facts"], dict) or not isinstance(top["reverse"], dict):
        raise AuditExportError("CONTAINER_TYPE")

    sources: dict[str, dict[str, Any]] = {}
    for key, raw in top["sources"].items():
        source_id = _identifier(key, "SOURCE_KEY")
        row = _exact_keys(raw, {"id", "generation", "payload"}, "SOURCE_SHAPE")
        if _identifier(row["id"], "SOURCE_ID") != source_id:
            raise AuditExportError("SOURCE_KEY_MISMATCH", source_id)
        generation = _integer(row["generation"], "SOURCE_GENERATION", positive=True)
        if generation > epoch:
            raise AuditExportError("SOURCE_HORIZON", source_id)
        if not isinstance(row["payload"], str):
            raise AuditExportError("SOURCE_PAYLOAD", source_id)
        sources[source_id] = {"id": source_id, "generation": generation, "payload": row["payload"]}

    facts: dict[str, dict[str, Any]] = {}
    expected_reverse = {source_id: [] for source_id in sources}
    for key, raw in top["facts"].items():
        fact_id = _identifier(key, "FACT_KEY")
        row = _exact_keys(raw, {"id", "payload", "schema", "dependencies"}, "FACT_SHAPE")
        if _identifier(row["id"], "FACT_ID") != fact_id:
            raise AuditExportError("FACT_KEY_MISMATCH", fact_id)
        if not isinstance(row["payload"], str):
            raise AuditExportError("FACT_PAYLOAD", fact_id)
        if _integer(row["schema"], "FACT_SCHEMA", positive=True) != schema:
            raise AuditExportError("SCHEMA_MISMATCH", fact_id)
        dependencies = row["dependencies"]
        if not isinstance(dependencies, list) or not dependencies:
            raise AuditExportError("DEPENDENCY_SHAPE", fact_id)
        seen: set[str] = set()
        normalized: list[dict[str, Any]] = []
        for raw_edge in dependencies:
            edge = _exact_keys(raw_edge, {"source", "generation"}, "DEPENDENCY_SHAPE")
            source_id = _identifier(edge["source"], "DEPENDENCY_SOURCE")
            generation = _integer(edge["generation"], "DEPENDENCY_GENERATION", positive=True)
            if source_id in seen:
                raise AuditExportError("DUPLICATE_DEPENDENCY", f"{fact_id}:{source_id}")
            seen.add(source_id)
            if source_id not in sources:
                raise AuditExportError("MISSING_SOURCE", f"{fact_id}:{source_id}")
            if sources[source_id]["generation"] != generation:
                raise AuditExportError("STALE_DEPENDENCY", f"{fact_id}:{source_id}")
            normalized.append({"source": source_id, "generation": generation})
            expected_reverse[source_id].append(fact_id)
        if [edge["source"] for edge in normalized] != sorted(edge["source"] for edge in normalized):
            raise AuditExportError("DEPENDENCY_ORDER", fact_id)
        facts[fact_id] = {"id": fact_id, "payload": row["payload"], "schema": schema, "dependencies": normalized}

    if set(top["reverse"]) != set(sources):
        raise AuditExportError("REVERSE_DOMAIN")
    reverse: dict[str, list[str]] = {}
    for raw_source, raw_members in top["reverse"].items():
        source_id = _identifier(raw_source, "REVERSE_KEY")
        if not isinstance(raw_members, list):
            raise AuditExportError("REVERSE_SHAPE", source_id)
        members = [_identifier(member, "REVERSE_MEMBER") for member in raw_members]
        if members != sorted(set(members)):
            raise AuditExportError("REVERSE_ORDER", source_id)
        if members != sorted(expected_reverse[source_id]):
            raise AuditExportError("REVERSE_MISMATCH", source_id)
        reverse[source_id] = members

    return {"epoch": epoch, "schema": schema, "sources": sources, "facts": facts, "reverse": reverse}


def check_audit_export(path: str | Path) -> dict[str, Any]:
    try:
        value = parse_audit_export(path)
        return {"status": "ACCEPT", "epoch": value["epoch"], "sources": len(value["sources"]), "facts": len(value["facts"])}
    except AuditExportError as error:
        return {"status": "REJECT", "witness": error.witness, "detail": error.detail}
