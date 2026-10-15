"""Owned selector/segment fixtures plus native pin-lifetime regressions."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from frontierstore.checker import check_selected, run, run_selected


def endpoint(epoch, payload):
    return {
        "epoch": epoch, "schema": 1,
        "sources": {"a": {"id": "a", "generation": epoch, "payload": payload}},
        "facts": {"p": {"id": "p", "payload": "fact " + payload, "schema": 1,
                          "dependencies": [{"source": "a", "generation": epoch}]}},
        "reverse": {"a": ["p"]},
    }


def write_base(path, epoch, payload):
    """A byte fixture independent of the store/model serializers."""
    name = f"segment-{epoch:08d}.jsonl"
    values = [
        {"kind": "header", "format": "frontierstore-segment", "epoch": epoch,
         "parent": None, "mode": "base", "schema": 1},
        {"kind": "source_put", "id": "a", "generation": epoch, "payload": payload},
        {"kind": "fact_put", "id": "p", "payload": "fact " + payload, "schema": 1,
         "dependencies": [{"source": "a", "generation": epoch}]},
        {"kind": "reverse_put", "id": "a", "facts": ["p"]},
        {"kind": "footer", "records": 3, "counts": {"sources": 1, "facts": 1, "reverse": 1}},
    ]
    (path / "segments").mkdir(exist_ok=True)
    (path / "segments" / name).write_text(
        "".join(json.dumps(value) + "\n" for value in values), encoding="ascii")
    return json.dumps({"format": "frontierstore-root", "epoch": epoch, "schema": 1,
                       "counts": {"sources": 1, "facts": 1, "reverse": 1},
                       "segments": [name]}).encode("ascii")


class SelectedRootFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name)
        self.old = write_base(self.path, 1, "old")
        self.current = write_base(self.path, 2, "new")
        (self.path / "ROOT").write_bytes(self.current)

    def tearDown(self):
        self.temporary.cleanup()

    def test_live_summary_interface_is_unchanged(self):
        result = run(self.path)
        self.assertEqual("ACCEPT", result["status"])
        self.assertEqual(2, result["epoch"])
        self.assertEqual({"status", "epoch", "schema", "segments", "records", "counts"}, set(result))

    def test_saved_selection_decodes_old_bytes_not_current_root(self):
        self.assertEqual(endpoint(1, "old"), check_selected(self.path, self.old)["endpoint"])
        self.assertEqual(endpoint(2, "new"), check_selected(self.path, self.current)["endpoint"])

    def test_explicit_selection_does_not_consult_live_root(self):
        (self.path / "ROOT").write_bytes(b"not a selector")
        self.assertEqual("REJECT", run(self.path)["status"])
        self.assertEqual(endpoint(1, "old"), check_selected(self.path, self.old)["endpoint"])

    def test_missing_protected_file_is_rejected(self):
        (self.path / "segments/segment-00000001.jsonl").unlink()
        self.assertEqual("MISSING_SEGMENT", run_selected(self.path, self.old)["witness"])
        self.assertEqual("ACCEPT", run(self.path)["status"])

    def test_truncated_protected_file_is_rejected(self):
        segment = self.path / "segments/segment-00000001.jsonl"
        segment.write_text(segment.read_text(encoding="ascii").splitlines()[0] + "\n", encoding="ascii")
        self.assertEqual("INCOMPLETE_SEGMENT", run_selected(self.path, self.old)["witness"])

    def test_captured_selector_uses_same_strict_validation(self):
        selectors = [b"not-json", b"\xff", self.old.decode("ascii"), bytearray(self.old)]
        duplicate = self.old.decode("ascii").replace('"epoch": 1', '"epoch": 0, "epoch": 1')
        selectors.append(duplicate.encode("ascii"))
        for mutation in (
            lambda value: value.update(epoch=True),
            lambda value: value.update(ignored=1),
            lambda value: value["counts"].update(facts=2),
            lambda value: value.update(segments=[]),
            lambda value: value.update(epoch=2),
        ):
            value = json.loads(self.old)
            mutation(value)
            selectors.append(json.dumps(value).encode("ascii"))
        for value in selectors:
            with self.subTest(selector=repr(value)):
                self.assertEqual("REJECT", run_selected(self.path, value)["status"])

    def test_captured_path_cannot_escape_segment_directory(self):
        value = json.loads(self.old)
        for name in ("../ROOT", "/segment-00000001.jsonl", "segment-00000001.jsonl/extra"):
            value["segments"] = [name]
            self.assertEqual("PATH_ESCAPE", run_selected(self.path, json.dumps(value).encode("ascii"))["witness"])

    def test_well_formed_payload_change_is_exposed_by_endpoint_comparison(self):
        segment = self.path / "segments/segment-00000001.jsonl"
        segment.write_text(segment.read_text(encoding="ascii").replace('"old"', '"different"'), encoding="ascii")
        actual = check_selected(self.path, self.old)
        self.assertEqual("ACCEPT", actual["status"])
        self.assertNotEqual(endpoint(1, "old"), actual["endpoint"])

    def test_returned_endpoint_does_not_alias_future_decodes(self):
        first = check_selected(self.path, self.old)["endpoint"]
        first["facts"]["p"]["dependencies"].clear()
        first["sources"].clear()
        self.assertEqual(endpoint(1, "old"), check_selected(self.path, self.old)["endpoint"])


@unittest.skipUnless(os.name == "posix", "native file locking and directory synchronization require POSIX")
class NativeHistoricalByteTests(unittest.TestCase):
    def make_store(self, path):
        from frontierstore.model import bootstrap_state
        from frontierstore.store import FrontierStore
        return FrontierStore.create(path, bootstrap_state(
            {"a": "old"}, [{"id": "p", "payload": "fact old", "sources": ["a"]}]))

    def update(self, store, number):
        from frontierstore.model import Transaction
        store.commit(Transaction(source_changes={"a": f"new {number}"}, fact_replacements=[
            {"id": "p", "payload": f"fact new {number}", "sources": ["a"]}]))

    def test_four_updates_and_compactions_keep_old_physical_endpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "store"
            store = self.make_store(path)
            with store.snapshot() as held:
                selected = (path / "ROOT").read_bytes()
                for number in range(4):
                    self.update(store, number)
                    store.compact()
                    store.collect()
                    self.assertEqual(endpoint(1, "old"), held.materialize())
                    self.assertEqual(endpoint(1, "old"), check_selected(path, selected)["endpoint"])
                    self.assertEqual(store.state.as_dict(), check_selected(path, (path / "ROOT").read_bytes())["endpoint"])
            store.collect()
            self.assertEqual("MISSING_SEGMENT", run_selected(path, selected)["witness"])

    def test_two_leases_and_double_close_preserve_remaining_pin(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "store"
            store = self.make_store(path)
            selected = (path / "ROOT").read_bytes()
            first, second = store.snapshot(), store.snapshot()
            try:
                self.update(store, 1)
                store.compact()
                first.close()
                first.close()
                store.collect()
                self.assertEqual(endpoint(1, "old"), check_selected(path, selected)["endpoint"])
            finally:
                first.close()
                second.close()
            store.collect()
            self.assertEqual("MISSING_SEGMENT", run_selected(path, selected)["witness"])
            self.assertEqual("ACCEPT", run(path)["status"])

    def test_joint_schedule_checks_physical_history_at_all_events(self):
        from experiments.joint_history import execute
        with tempfile.TemporaryDirectory() as temporary:
            report = execute(Path(temporary) / "joint.json")
            self.assertEqual("CASE_COMPLETED", report["status"])
            self.assertEqual(20, len(report["rows"]))
            self.assertEqual(50, report["historical_byte_checks"])
            self.assertEqual(45, sum(len(row["historical_byte_events"]) for row in report["rows"]))
            self.assertTrue(all(row["historical_bytes_equal"] for row in report["rows"]))

    def test_joint_schedule_detects_changed_history_despite_cached_snapshot(self):
        from experiments.joint_history import execute
        from frontierstore.store import FrontierStore
        original = FrontierStore.collect
        changed = []

        def change_old_bytes(store, **kwargs):
            result = original(store, **kwargs)
            protected = set().union(*(set(names) for names in store._pins)) if store._pins else set()
            obsolete = protected - set(store.selected_segments)
            if obsolete and not changed:
                segment = store.segment_dir / sorted(obsolete)[0]
                values = [json.loads(line) for line in segment.read_text(encoding="ascii").splitlines()]
                for value in values:
                    if value.get("kind") == "source_put":
                        value["payload"] += " changed owned fixture"
                        break
                segment.write_text("".join(json.dumps(value) + "\n" for value in values), encoding="ascii")
                changed.append(segment.name)
            return result

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "joint.json"
            with patch.object(FrontierStore, "collect", change_old_bytes):
                with self.assertRaisesRegex(AssertionError, "historical bytes differ"):
                    execute(output)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("INCOMPLETE", report["status"])
            self.assertTrue(changed)
            self.assertLess(len(report["rows"]), 20)

    def test_initial_parser_failure_closes_reader_and_releases_pin(self):
        from experiments import joint_history
        from frontierstore.checker import Rejection
        from frontierstore.store import FrontierStore
        original_create = FrontierStore.create
        original_thread = joint_history.threading.Thread
        stores, threads = [], []

        def capture_store(*args, **kwargs):
            store = original_create(*args, **kwargs)
            stores.append(store)
            return store

        def capture_thread(*args, **kwargs):
            thread = original_thread(*args, **kwargs)
            threads.append(thread)
            return thread

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "joint.json"
            with patch.object(FrontierStore, "create", side_effect=capture_store), \
                    patch.object(joint_history.threading, "Thread", side_effect=capture_thread), \
                    patch.object(joint_history, "check_selected", side_effect=Rejection("OWNED_TEST", "initial read")):
                with self.assertRaisesRegex(Rejection, "OWNED_TEST"):
                    joint_history.execute(output)
            self.assertEqual(1, len(stores))
            self.assertEqual(1, len(threads))
            self.assertFalse(threads[0].is_alive())
            self.assertEqual({}, stores[0]._pins)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("INCOMPLETE", report["status"])
            self.assertEqual(0, report["historical_byte_checks"])
            self.assertEqual([], report["rows"])


if __name__ == "__main__":
    unittest.main()
