from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

from frontierstore.audit_export import check_audit_export, parse_audit_export
from frontierstore.baselines import (
    BaselineFormatError,
    BaselineUnavailableError,
    SQLiteRootedAudit,
)
from frontierstore.model import apply_transaction
from frontierstore.workload import make_synthetic_state, make_update


class RootedAuditTests(unittest.TestCase):
    def test_create_binds_database_and_independent_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            initial = make_synthetic_state(8, facts_per_source=3, dependency_width=2)
            engine = SQLiteRootedAudit.create(Path(temporary) / "rooted", initial)
            self.assertEqual(initial.as_dict(), engine.durable_state().as_dict())
            self.assertEqual("ACCEPT", engine.audit_report()["status"])

    def test_update_selects_matching_new_pair(self):
        with tempfile.TemporaryDirectory() as temporary:
            initial = make_synthetic_state(8, facts_per_source=3, dependency_width=2)
            engine = SQLiteRootedAudit.create(Path(temporary) / "rooted", initial)
            expected, delta = apply_transaction(initial, make_update(initial, batch=2, seed=91))
            engine.persist_precomputed(expected, delta)
            self.assertEqual(expected.as_dict(), engine.durable_state().as_dict())
            self.assertEqual(expected.epoch, engine.audit_report()["epoch"])

    def test_export_checker_rejects_inverse_corruption(self):
        with tempfile.TemporaryDirectory() as temporary:
            initial = make_synthetic_state(4, facts_per_source=2, dependency_width=2)
            engine = SQLiteRootedAudit.create(Path(temporary) / "rooted", initial)
            root = json.loads(engine.root_path.read_text())
            export = engine.exports / root["export"]
            value = parse_audit_export(export)
            source = sorted(value["reverse"])[0]
            value["reverse"][source] = []
            export.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
            report = check_audit_export(export)
            self.assertEqual("REJECT", report["status"])
            self.assertEqual("REVERSE_MISMATCH", report["witness"])

    def test_export_checker_rejects_duplicate_field(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "audit.json"
            path.write_text('{"epoch":1,"epoch":1,"schema":1,"sources":{},"facts":{},"reverse":{}}\n')
            self.assertEqual("DUPLICATE_FIELD", check_audit_export(path)["witness"])

    def test_selector_epoch_must_bind_both_selected_objects(self):
        with tempfile.TemporaryDirectory() as temporary:
            initial = make_synthetic_state(4, facts_per_source=2, dependency_width=2)
            engine = SQLiteRootedAudit.create(Path(temporary) / "rooted", initial)
            root = json.loads(engine.root_path.read_text(encoding="ascii"))
            database_two = engine.images / "image-00000002.sqlite"
            export_two = engine.exports / "audit-00000002.json"
            shutil.copyfile(engine.images / root["database"], database_two)
            shutil.copyfile(engine.exports / root["export"], export_two)
            root.update(epoch=2, database=database_two.name, export=export_two.name)
            engine.root_path.write_text(
                json.dumps(root, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="ascii",
            )
            report = engine.audit_report()
            self.assertEqual("REJECT", report["status"])
            self.assertEqual("ROOT_EPOCH_MISMATCH", report["witness"])
            with self.assertRaisesRegex(BaselineFormatError, "ROOT_EPOCH_MISMATCH"):
                SQLiteRootedAudit(engine.path)

    def test_audit_report_rejects_database_export_disagreement(self):
        with tempfile.TemporaryDirectory() as temporary:
            initial = make_synthetic_state(4, facts_per_source=2, dependency_width=2)
            engine = SQLiteRootedAudit.create(Path(temporary) / "rooted", initial)
            root = json.loads(engine.root_path.read_text(encoding="ascii"))
            source_id = sorted(initial.sources)[0]
            connection = sqlite3.connect(engine.images / root["database"])
            try:
                connection.execute(
                    "UPDATE sources SET payload='different' WHERE id=?", (source_id,)
                )
                connection.commit()
            finally:
                connection.close()
            report = engine.audit_report()
            self.assertEqual("REJECT", report["status"])
            self.assertEqual("PAIR_MISMATCH", report["witness"])

    def test_prepublication_validation_error_does_not_quarantine(self):
        with tempfile.TemporaryDirectory() as temporary:
            initial = make_synthetic_state(6, facts_per_source=2, dependency_width=2)
            engine = SQLiteRootedAudit.create(Path(temporary) / "rooted", initial)
            expected, delta = apply_transaction(initial, make_update(initial, batch=2, seed=101))
            wrong, _ = apply_transaction(initial, make_update(initial, batch=2, seed=102))
            with self.assertRaises(Exception):
                engine.persist_precomputed(wrong, delta)
            self.assertEqual(initial, engine.state)
            self.assertEqual(initial, engine.durable_state())
            engine.persist_precomputed(expected, delta)
            self.assertEqual(expected, engine.state)

    def test_publication_exception_quarantines_cached_instance_before_selector_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            initial = make_synthetic_state(6, facts_per_source=2, dependency_width=2)
            engine = SQLiteRootedAudit.create(Path(temporary) / "rooted", initial)
            expected, delta = apply_transaction(initial, make_update(initial, batch=2, seed=103))
            with mock.patch.object(
                SQLiteRootedAudit, "_publish_full", side_effect=OSError("synthetic publish failure")
            ):
                with self.assertRaises(OSError):
                    engine.persist_precomputed(expected, delta)
            with self.assertRaises(BaselineUnavailableError):
                _ = engine.state
            self.assertEqual("REOPEN_REQUIRED", engine.audit_report()["witness"])
            reopened = SQLiteRootedAudit(engine.path)
            self.assertEqual(initial, reopened.state)

    def test_postselector_exception_quarantines_and_reopen_observes_published_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            initial = make_synthetic_state(6, facts_per_source=2, dependency_width=2)
            engine = SQLiteRootedAudit.create(Path(temporary) / "rooted", initial)
            expected, delta = apply_transaction(initial, make_update(initial, batch=2, seed=104))
            original_fsync = __import__("frontierstore.baselines", fromlist=["_fsync_directory"])._fsync_directory

            def fail_after_selector(path):
                if Path(path) == engine.path:
                    raise OSError("synthetic final directory fsync failure")
                return original_fsync(path)

            with mock.patch("frontierstore.baselines._fsync_directory", side_effect=fail_after_selector):
                with self.assertRaises(OSError):
                    engine.persist_precomputed(expected, delta)
            with self.assertRaises(BaselineUnavailableError):
                engine.query_fact(next(iter(initial.facts)))
            reopened = SQLiteRootedAudit(engine.path)
            self.assertEqual(expected, reopened.state)
            self.assertEqual("ACCEPT", reopened.audit_report()["status"])

    def test_preselector_orphan_is_reclaimed_after_reopen_and_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            initial = make_synthetic_state(6, facts_per_source=2, dependency_width=2)
            engine = SQLiteRootedAudit.create(Path(temporary) / "rooted", initial)
            expected, delta = apply_transaction(initial, make_update(initial, batch=2, seed=105))
            original_export = __import__(
                "frontierstore.baselines", fromlist=["_write_canonical_export"]
            )._write_canonical_export

            with mock.patch(
                "frontierstore.baselines._write_canonical_export",
                side_effect=OSError("synthetic export failure after image creation"),
            ):
                with self.assertRaises(OSError):
                    engine.persist_precomputed(expected, delta)
            self.assertTrue((engine.images / "image-00000002.sqlite").exists())
            self.assertFalse((engine.exports / "audit-00000002.json").exists())
            reopened = SQLiteRootedAudit(engine.path)
            self.assertEqual(initial, reopened.state)
            # Restoring the writer allows the same logical update to reclaim the
            # invisible orphan and complete without manual cleanup.
            with mock.patch(
                "frontierstore.baselines._write_canonical_export", side_effect=original_export
            ):
                reopened.persist_precomputed(expected, delta)
            self.assertEqual(expected, SQLiteRootedAudit(engine.path).state)

    def test_external_selector_advance_quarantines_stale_instance(self):
        with tempfile.TemporaryDirectory() as temporary:
            initial = make_synthetic_state(6, facts_per_source=2, dependency_width=2)
            first = SQLiteRootedAudit.create(Path(temporary) / "rooted", initial)
            second = SQLiteRootedAudit(first.path)
            expected, delta = apply_transaction(initial, make_update(initial, batch=2, seed=106))
            first.persist_precomputed(expected, delta)
            later, later_delta = apply_transaction(initial, make_update(initial, batch=2, seed=107))
            with self.assertRaises(BaselineUnavailableError):
                second.persist_precomputed(later, later_delta)
            with self.assertRaises(BaselineUnavailableError):
                _ = second.state


if __name__ == "__main__":
    unittest.main()
