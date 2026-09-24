"""Small representation, publication, export, and read-boundary cases."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from frontierstore import baselines
from frontierstore import store as storage
from frontierstore.checker import run as check_bytes
from frontierstore.model import Transaction, TransitionError, apply_transaction, bootstrap_state
from frontierstore.store import FrontierStore, StoreFormatError, StoreUnavailableError


class RepresentationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "store"
        self.initial = bootstrap_state(
            {"a": "stable", "b": "old source"},
            [{"id": "p", "payload": "old fact", "sources": ["a"]}],
        )
        self.store = FrontierStore.create(self.path, self.initial)

    def tearDown(self):
        self.tmp.cleanup()

    def selected(self):
        return self.path / "segments" / self.store.selected_segments[-1]

    def records(self):
        return [json.loads(line) for line in self.selected().read_text().splitlines()]

    def write_records(self, records):
        records[-1]["records"] = len(records) - 2
        self.selected().write_text("".join(json.dumps(row) + "\n" for row in records))

    def reject_both(self, code):
        self.assertEqual(check_bytes(self.path)["status"], "REJECT")
        self.assertEqual(check_bytes(self.path)["witness"], code)
        with self.assertRaises(StoreFormatError) as caught:
            FrontierStore(self.path)
        self.assertEqual(caught.exception.code, code)

    def update(self):
        return self.store.commit(Transaction(
            source_changes={"b": "new source"},
            fact_replacements=[{"id": "p", "payload": "new fact", "sources": ["a"]}],
        ))

    def test_identifier_grammar_is_checked_independently(self):
        rows = self.records()
        for row in rows:
            if row.get("id") == "a":
                row["id"] = "1bad"
            for dep in row.get("dependencies", []):
                if dep["source"] == "a":
                    dep["source"] = "1bad"
        self.write_records(rows)
        self.reject_both("IDENTIFIER")

    def test_footer_boolean_is_not_integer_count(self):
        rows = self.records(); rows[-1]["counts"]["facts"] = True
        self.write_records(rows); self.reject_both("FIELD_TYPE")

    def test_footer_float_is_not_integer_count(self):
        rows = self.records(); rows[-1]["counts"]["facts"] = 1.0
        self.write_records(rows); self.reject_both("FIELD_TYPE")

    def test_parent_boolean_is_not_epoch(self):
        self.update(); rows = self.records(); rows[0]["parent"] = True
        self.write_records(rows); self.reject_both("FIELD_TYPE")

    def test_segment_name_must_equal_header_epoch(self):
        self.update(); rows = self.records(); rows[0]["epoch"] = 3
        self.write_records(rows); self.reject_both("EPOCH_NAME")

    def test_delta_epoch_cannot_skip(self):
        self.update(); old_path = self.selected(); rows = self.records()
        rows[0]["epoch"] = 3
        for row in rows:
            if row.get("kind") == "source_put": row["generation"] = 3
        self.write_records(rows)
        new_path = old_path.with_name("segment-00000003.jsonl"); old_path.rename(new_path)
        root_path = self.path / "ROOT"; root = json.loads(root_path.read_text())
        root["epoch"] = 3; root["segments"][-1] = new_path.name
        root_path.write_text(json.dumps(root))
        self.reject_both("EPOCH_CHAIN")

    def test_closed_changed_source_needs_new_stamp(self):
        self.update(); rows = self.records()
        for row in rows:
            if row.get("kind") == "source_put": row["generation"] = 1
        self.write_records(rows); self.reject_both("SOURCE_STAMP")

    def test_duplicate_json_field_is_not_last_value_wins(self):
        root = self.path / "ROOT"
        root.write_text(root.read_text().replace('"epoch":1', '"epoch":0,"epoch":1'))
        # The encoder has compact separators; assert the fixture was modified.
        self.assertEqual(root.read_text().count('"epoch"'), 2)
        self.reject_both("DUPLICATE_FIELD")

    def test_container_record_kind_returns_witness(self):
        rows = self.records(); rows[1]["kind"] = []
        self.write_records(rows); self.reject_both("RECORD_KIND")

    def test_container_mode_returns_witness(self):
        rows = self.records(); rows[0]["mode"] = []
        self.write_records(rows)
        self.assertEqual(check_bytes(self.path)["witness"], "BAD_MODE")
        with self.assertRaises(StoreFormatError): FrontierStore(self.path)

    def test_nonlive_delete_identifier_cannot_escape_validation(self):
        self.store.commit(Transaction()); rows = self.records()
        rows.insert(1, {"kind": "fact_del", "id": "1bad"})
        self.write_records(rows); self.reject_both("IDENTIFIER")

    def test_source_put_delete_overlap_is_rejected(self):
        self.store.commit(Transaction()); rows = self.records()
        rows[1:1] = [
            {"kind": "source_put", "id": "z", "generation": 2, "payload": "x"},
            {"kind": "source_del", "id": "z"},
        ]
        self.write_records(rows); self.reject_both("DELTA_SOURCE_OVERLAP")

    def test_reverse_put_delete_overlap_is_rejected(self):
        self.store.commit(Transaction()); rows = self.records()
        rows[1:1] = [{"kind": "reverse_put", "id": "z", "facts": []},
                     {"kind": "reverse_del", "id": "z"}]
        self.write_records(rows); self.reject_both("DELTA_REVERSE_OVERLAP")

    def test_redundant_unchanged_source_record_remains_legal(self):
        self.store.commit(Transaction()); rows = self.records()
        rows.insert(1, {"kind": "source_put", "id": "a", "generation": 1, "payload": "stable"})
        self.write_records(rows)
        self.assertEqual(check_bytes(self.path)["status"], "ACCEPT")
        self.assertEqual(FrontierStore(self.path).state, self.store.state)

    def test_unreadable_root_returns_witness(self):
        (self.path / "ROOT").write_bytes(bytes([255]))
        self.reject_both("ROOT_PARSE")

    def test_failed_publication_blocks_cached_operations_and_collection(self):
        def abort(_): raise OSError("synthetic publication interruption")
        for cut in ("before_segment", "after_root_replace", "after_root_dirsync"):
            with self.subTest(cut=cut):
                path = Path(self.tmp.name) / cut
                store = FrontierStore.create(path, self.initial)
                old = store.snapshot(); captured = old.materialize()
                with self.assertRaises(OSError):
                    store.commit(Transaction(source_changes={"b": "new"}), failpoint=cut, failure_action=abort)
                root_after = (path / "ROOT").read_bytes()
                for operation in (lambda: store.state, lambda: store.selected_segments,
                                  store.snapshot, lambda: store.preview(Transaction()),
                                  lambda: store.commit(Transaction()), store.compact, store.collect):
                    with self.assertRaises(StoreUnavailableError): operation()
                self.assertEqual((path / "ROOT").read_bytes(), root_after)
                self.assertEqual(old.materialize(), captured); old.close()
                # No operations on the abandoned instance after quiescent reopen.
                recovered = FrontierStore(path)
                self.assertEqual(check_bytes(path)["status"], "ACCEPT")
                self.assertEqual(recovered.state.epoch, 1 if cut == "before_segment" else 2)

    def test_prewrite_validation_does_not_disable_instance(self):
        with self.assertRaises(TransitionError):
            self.store.commit(Transaction(new_schema=True))
        self.assertEqual(self.store.state, self.initial)
        self.store.commit(Transaction())

    def test_shared_handle_close_preserves_another_pin(self):
        first = self.store.snapshot(); second = self.store.snapshot()
        needed = set(self.store.selected_segments)
        self.store.compact()
        threads = [threading.Thread(target=first.close) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=5); self.assertFalse(thread.is_alive())
        self.store.collect()
        self.assertTrue(all((self.path / "segments" / name).exists() for name in needed))
        second.close(); self.store.collect()
        self.assertTrue(all(not (self.path / "segments" / name).exists() for name in needed))

    def test_raw_write_completes_short_progress(self):
        emitted = bytearray()
        class PartialWriter:
            def write(self, data):
                count = min(3, len(data))
                emitted.extend(data[:count])
                return count
        storage._write_all(PartialWriter(), b"complete record\n")
        self.assertEqual(bytes(emitted), b"complete record\n")

    def test_raw_write_rejects_no_or_invalid_progress(self):
        for answer in (0, None, -1, True, 100):
            with self.subTest(answer=answer):
                class NoProgress:
                    def write(self, data): return answer
                with self.assertRaises(OSError):
                    storage._write_all(NoProgress(), b"record\n")

    def test_root_write_error_disables_instance_without_replacing_root(self):
        old_root = (self.path / "ROOT").read_bytes()
        actual = storage._write_all
        def interrupt_root(handle, payload):
            value = json.loads(payload)
            if value.get("format") == "frontierstore-root":
                raise OSError("synthetic root write failure")
            return actual(handle, payload)
        with mock.patch.object(storage, "_write_all", side_effect=interrupt_root):
            with self.assertRaises(OSError): self.update()
        self.assertEqual((self.path / "ROOT").read_bytes(), old_root)
        with self.assertRaises(StoreUnavailableError): self.store.collect()
        # The failed instance is abandoned before this quiescent read.
        self.assertEqual(FrontierStore(self.path).state, self.initial)

    def test_sql_materialization_keeps_one_snapshot_across_commit(self):
        path = Path(self.tmp.name) / "sql"
        writer = baselines.SQLiteNormalized.create(path, self.initial)
        next_state, delta = apply_transaction(self.initial, Transaction(
            source_changes={"b": "new source"},
            fact_replacements=[{"id": "p", "payload": "new fact", "sources": ["a"]}],
        ))
        connection = sqlite3.connect(writer.db_path)
        fired = []
        class Reader:
            def execute(self, statement, *args):
                if statement.startswith("SELECT id,payload,schema") and not fired:
                    fired.append(True)
                    writer.persist_precomputed(next_state, delta)
                return connection.execute(statement, *args)
            def close(self): connection.close()
        try:
            with mock.patch.object(baselines.sqlite3, "connect", return_value=Reader()):
                observed = baselines._read_sqlite_state(writer.db_path)
            self.assertEqual(fired, [True])
            self.assertEqual(observed, self.initial)
            self.assertEqual(writer.durable_state(), next_state)
        finally:
            connection.close(); writer.close()

    def test_sql_acquisition_after_acknowledgement_is_current(self):
        path = Path(self.tmp.name) / "sql-current"
        writer = baselines.SQLiteNormalized.create(path, self.initial)
        try:
            next_state, delta = apply_transaction(self.initial, Transaction(source_changes={"b": "new"}))
            writer.persist_precomputed(next_state, delta)
            self.assertEqual(writer.durable_state(), next_state)
        finally:
            writer.close()


if __name__ == "__main__":
    unittest.main()
