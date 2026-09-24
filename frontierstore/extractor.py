"""Deterministic, label-free lexical fact extraction for public Solidity text."""

from __future__ import annotations

import re
from typing import Mapping

from .model import valid_identifier

_KEYWORDS = (
    ("contract", re.compile(r"\b(?:contract|interface|library)\s+([A-Za-z_][A-Za-z0-9_]*)")),
    ("function", re.compile(r"\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)")),
    ("event", re.compile(r"\bevent\s+([A-Za-z_][A-Za-z0-9_]*)")),
    ("mapping", re.compile(r"\bmapping\s*\(")),
    ("require", re.compile(r"\brequire\s*\(")),
    ("call", re.compile(r"\.(?:call|delegatecall|staticcall|transfer|send)\b")),
)


def extract_fact_specs(source_payloads: Mapping[str, str]) -> list[dict[str, object]]:
    if not isinstance(source_payloads, Mapping):
        raise TypeError("source payloads must be a mapping")
    source_ids = sorted(source_payloads)
    for source_id in source_ids:
        if not valid_identifier(source_id):
            raise ValueError(f"invalid source identifier: {source_id!r}")
        if not isinstance(source_payloads[source_id], str):
            raise TypeError(f"source payload must be text: {source_id}")
    facts: list[dict[str, object]] = []
    for position, source_id in enumerate(source_ids):
        text = source_payloads[source_id]
        emitted = 0
        for line_number, line in enumerate(text.splitlines(), 1):
            for kind, pattern in _KEYWORDS:
                for match_index, match in enumerate(pattern.finditer(line), 1):
                    name = match.group(1) if match.lastindex else kind
                    facts.append(
                        {
                            "id": f"lex.{source_id}.{line_number:04d}.{kind}.{match_index}",
                            "payload": f"{kind}:{name}:{line.strip()[:96]}",
                            "sources": [source_id],
                        }
                    )
                    emitted += 1
        if emitted == 0:
            facts.append(
                {
                    "id": f"lex.{source_id}.summary",
                    "payload": f"lines:{len(text.splitlines())}:chars:{len(text)}",
                    "sources": [source_id],
                }
            )
        neighbor = source_ids[(position + 1) % len(source_ids)]
        dependencies = [source_id] if neighbor == source_id else sorted([source_id, neighbor])
        facts.append(
            {
                "id": f"lex.{source_id}.bridge",
                "payload": f"bridge:{len(text)}:{len(source_payloads[neighbor])}",
                "sources": dependencies,
            }
        )
    facts.sort(key=lambda fact: str(fact["id"]))
    return facts
