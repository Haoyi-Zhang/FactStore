"""Fail-closed grammar checks for both store parsers and the audit export."""
from __future__ import annotations

import dbm.dumb
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from frontierstore import baselines
from frontierstore.audit_export import check_audit_export
from frontierstore.checker import run as independent_check
from frontierstore.model import bootstrap_state
from frontierstore.store import FrontierStore, StoreFormatError


class SegmentGrammarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "store"
        state = bootstrap_state(
            {"a": "one", "b": "two"},
            [{"id": "p", "payload": "fact", "sources": ["a", "b"]}],
        )
        self.store = FrontierStore.create(self.path, state)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _segment(self) -> Path:
        return self.path / "segments" / self.store.selected_segments[0]

    def _rows(self) -> list[dict[str, object]]:
        return [json.loads(line) for line in self._segment().read_text(encoding="ascii").splitlines()]

    def _write_rows(self, rows: list[dict[str, object]]) -> None:
        self._segment().write_text(
            "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
            encoding="ascii",
        )

    def _reject_both(self, witness: str) -> None:
        result = independent_check(self.path)
        self.assertEqual("REJECT", result["status"])
        self.assertEqual(witness, result["witness"])
        with self.assertRaises(StoreFormatError) as caught:
            FrontierStore(self.path)
        self.assertEqual(witness, caught.exception.code)

    def test_root_rejects_unknown_field(self) -> None:
        root_path = self.path / "ROOT"
        value = json.loads(root_path.read_text(encoding="ascii"))
        value["ignored"] = "not part of the grammar"
        root_path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
        self._reject_both("FIELD_SET")

    def test_header_rejects_unknown_field(self) -> None:
        rows = self._rows()
        rows[0]["ignored"] = 1
        self._write_rows(rows)
        self._reject_both("FIELD_SET")

    def test_record_rejects_unknown_field(self) -> None:
        rows = self._rows()
        rows[1]["ignored"] = 1
        self._write_rows(rows)
        self._reject_both("FIELD_SET")

    def test_footer_rejects_unknown_field(self) -> None:
        rows = self._rows()
        rows[-1]["ignored"] = 1
        self._write_rows(rows)
        self._reject_both("FIELD_SET")


class AuditExportGrammarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "audit.json"
        self.state = bootstrap_state(
            {"a": "one", "b": "two"},
            [{"id": "p", "payload": "fact", "sources": ["a", "b"]}],
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _check(self, value: dict[str, object]) -> dict[str, object]:
        self.path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
        return check_audit_export(self.path)

    def test_export_rejects_unknown_top_level_field(self) -> None:
        value = self.state.as_dict()
        value["ignored"] = 1
        self.assertEqual("TOP_LEVEL_SHAPE", self._check(value)["witness"])

    def test_export_rejects_unknown_record_field(self) -> None:
        value = self.state.as_dict()
        value["sources"]["a"]["ignored"] = 1
        self.assertEqual("SOURCE_SHAPE", self._check(value)["witness"])

    def test_export_rejects_stale_dependency(self) -> None:
        value = self.state.as_dict()
        value["facts"]["p"]["dependencies"][0]["generation"] = 99
        self.assertEqual("STALE_DEPENDENCY", self._check(value)["witness"])

    def test_export_rejects_key_identity_mismatch(self) -> None:
        value = self.state.as_dict()
        value["sources"]["a"]["id"] = "b"
        self.assertEqual("SOURCE_KEY_MISMATCH", self._check(value)["witness"])

    def test_export_rejects_dependency_order_drift(self) -> None:
        value = self.state.as_dict()
        value["facts"]["p"]["dependencies"].reverse()
        self.assertEqual("DEPENDENCY_ORDER", self._check(value)["witness"])


class BaselineGrammarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state = bootstrap_state(
            {"a": "one", "b": "two"},
            [{"id": "p", "payload": "fact", "sources": ["a", "b"]}],
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_sqlite_reader_rejects_extra_meta_row(self) -> None:
        engine = baselines.SQLiteRebuild.create(self.root / "sqlite", self.state)
        connection = sqlite3.connect(engine.db_path)
        try:
            connection.execute("INSERT INTO meta(key,value) VALUES('unexpected',1)")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(baselines.BaselineFormatError, "META_SET"):
            engine.durable_state()

    def test_sqlite_reader_rejects_dangling_dependency(self) -> None:
        engine = baselines.SQLiteRebuild.create(self.root / "sqlite", self.state)
        connection = sqlite3.connect(engine.db_path)
        try:
            connection.execute(
                "UPDATE dependencies SET source_id='missing' WHERE fact_id='p' AND source_id='a'"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(baselines.BaselineFormatError, "MISSING_SOURCE"):
            engine.durable_state()

    def test_read_only_sqlite_open_does_not_create_missing_image(self) -> None:
        missing = self.root / "missing.sqlite"
        with self.assertRaisesRegex(baselines.BaselineFormatError, "SQLITE_OPEN"):
            baselines._read_sqlite_state(missing)
        self.assertFalse(missing.exists())

    def test_normalized_open_rejects_missing_database_without_creating_it(self) -> None:
        target = self.root / "normalized"
        target.mkdir()
        with self.assertRaisesRegex(baselines.BaselineFormatError, "SQLITE_MISSING"):
            baselines.SQLiteNormalized(target)
        self.assertFalse((target / "normalized.sqlite").exists())

    def test_dbm_selector_cannot_escape_images_directory(self) -> None:
        engine = baselines.DBMDumbRebuild.create(self.root / "dbm", self.state)
        engine.root_path.write_text("../outside\n", encoding="ascii")
        with self.assertRaisesRegex(baselines.BaselineFormatError, "DBM_ROOT_NAME"):
            engine.durable_state()

    def test_dbm_selector_epoch_must_match_decoded_state(self) -> None:
        engine = baselines.DBMDumbRebuild.create(self.root / "dbm", self.state)
        selected = engine.root_path.read_text(encoding="ascii").strip()
        shutil.copytree(engine.images / selected, engine.images / "image-00000002")
        engine.root_path.write_text("image-00000002\n", encoding="ascii")
        with self.assertRaisesRegex(baselines.BaselineFormatError, "DBM_EPOCH_NAME"):
            engine.durable_state()

    def test_dbm_state_rejects_duplicate_json_field(self) -> None:
        engine = baselines.DBMDumbRebuild.create(self.root / "dbm", self.state)
        selected = engine.root_path.read_text(encoding="ascii").strip()
        database = dbm.dumb.open(str(engine.images / selected / "state"), "w")
        try:
            original = bytes(database[b"state"]).decode("ascii")
            database[b"state"] = original.replace('"epoch":1', '"epoch":0,"epoch":1').encode("ascii")
            database.sync()
        finally:
            database.close()
        with self.assertRaisesRegex(baselines.BaselineFormatError, "DUPLICATE_FIELD"):
            engine.durable_state()


if __name__ == "__main__":
    unittest.main()
