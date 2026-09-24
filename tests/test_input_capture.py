"""Input-capture, generator, and rooted-audit boundary regressions."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from frontierstore import baselines
from frontierstore import store as storage
from frontierstore.extractor import extract_fact_specs
from frontierstore.model import (
    Delta,
    Fact,
    Source,
    State,
    Transaction,
    TransitionError,
    apply_transaction,
    bootstrap_state,
    capture_and_validate_delta,
    check_closure,
)
from frontierstore.store import FrontierStore
from frontierstore.workload import (
    choose_sources,
    fact_id,
    make_synthetic_state,
    make_update,
    query_fact_ids,
    source_id,
)


def _tree_bytes(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _unclosed_next_state(initial: State) -> State:
    return State(
        initial.epoch + 1,
        initial.schema,
        dict(initial.sources),
        {"p": Fact("p", "bad", initial.schema, (("a", 99), ("b", 1)))},
        {"a": ("p",), "b": ("p",)},
    )


class InputCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.initial = bootstrap_state(
            {"a": "old", "b": "stable"},
            [{"id": "p", "payload": "old fact", "sources": ["a", "b"]}],
        )

    def test_transaction_defensively_captures_all_mutable_inputs(self) -> None:
        changes = {"a": "new"}
        dependencies = ["a", "b"]
        replacement = {"id": "p", "payload": "new fact", "sources": dependencies}
        replacements = [replacement]
        retractions: list[str] = []
        transaction = Transaction(
            source_changes=changes,
            fact_replacements=replacements,
            fact_retractions=retractions,
        )

        changes["a"] = "attacker change"
        dependencies.clear()
        replacement["payload"] = "attacker fact"
        replacements.clear()
        retractions.append("p")

        state, _ = apply_transaction(self.initial, transaction)
        self.assertEqual("new", state.sources["a"].payload)
        self.assertEqual("new fact", state.facts["p"].payload)
        self.assertEqual(("a", "b"), tuple(source for source, _ in state.facts["p"].dependencies))

    def test_transaction_exposes_immutable_captured_containers(self) -> None:
        transaction = Transaction(
            source_changes={"a": "new"},
            fact_replacements=[{"id": "p", "payload": "new", "sources": ["a", "b"]}],
            fact_retractions=["q"],
        )
        with self.assertRaises(TypeError):
            transaction.source_changes["a"] = "other"  # type: ignore[index]
        with self.assertRaises(TypeError):
            transaction.fact_replacements[0]["payload"] = "other"  # type: ignore[index]
        self.assertIsInstance(transaction.fact_replacements, tuple)
        self.assertIsInstance(transaction.fact_replacements[0]["sources"], tuple)
        self.assertIsInstance(transaction.fact_retractions, tuple)

    def test_transaction_shape_errors_have_stable_witnesses(self) -> None:
        cases = [
            (Transaction(source_changes=[]), "SOURCE_CHANGES_TYPE"),  # type: ignore[arg-type]
            (Transaction(fact_replacements="not-a-sequence-of-specs"), "FACT_REPLACEMENTS_TYPE"),  # type: ignore[arg-type]
            (Transaction(fact_retractions="not-a-sequence-of-ids"), "FACT_RETRACTIONS_TYPE"),  # type: ignore[arg-type]
        ]
        for transaction, witness in cases:
            with self.subTest(witness=witness):
                with self.assertRaises(TransitionError) as caught:
                    apply_transaction(self.initial, transaction)
                self.assertEqual(witness, caught.exception.code)

    def test_bootstrap_shape_errors_have_stable_witnesses(self) -> None:
        with self.assertRaises(TransitionError) as source_error:
            bootstrap_state([], [])  # type: ignore[arg-type]
        self.assertEqual("SOURCE_MAP_TYPE", source_error.exception.code)
        with self.assertRaises(TransitionError) as facts_error:
            bootstrap_state({"a": "x"}, "not-fact-specs")  # type: ignore[arg-type]
        self.assertEqual("FACT_SPECS_TYPE", facts_error.exception.code)

    def test_frontier_create_uses_one_captured_state(self) -> None:
        expected = self.initial.as_dict()
        real_encoder = storage._base_records

        def mutate_caller_then_encode(captured: State):
            self.initial.sources["a"] = Source("a", 1, "mutated after capture")
            return real_encoder(captured)

        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(storage, "_base_records", side_effect=mutate_caller_then_encode):
                store = FrontierStore.create(Path(temporary) / "store", self.initial)
            self.assertEqual(expected, store.state.as_dict())
            self.assertEqual(expected, FrontierStore(Path(temporary) / "store").state.as_dict())

    def test_rooted_audit_create_uses_one_captured_state(self) -> None:
        expected = self.initial.as_dict()
        real_writer = baselines.SQLiteRebuild._write_image

        def mutate_caller_then_write(captured: State, path: Path) -> None:
            self.initial.sources["a"] = Source("a", 1, "mutated after capture")
            real_writer(captured, path)

        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(
                baselines.SQLiteRebuild,
                "_write_image",
                side_effect=mutate_caller_then_write,
            ):
                engine = baselines.SQLiteRootedAudit.create(Path(temporary) / "rooted", self.initial)
            self.assertEqual(expected, engine.durable_state().as_dict())

    def test_rooted_audit_rejects_duplicate_selector_field(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            engine = baselines.SQLiteRootedAudit.create(Path(temporary) / "rooted", self.initial)
            selector = engine.root_path.read_text(encoding="ascii")
            engine.root_path.write_text(
                selector.replace('"epoch":1', '"epoch":0,"epoch":1'),
                encoding="ascii",
            )
            self.assertEqual(2, engine.root_path.read_text(encoding="ascii").count('"epoch"'))
            with self.assertRaisesRegex(baselines.BaselineFormatError, "DUPLICATE_FIELD: epoch"):
                baselines.SQLiteRootedAudit(engine.path)

    def test_rooted_audit_rejects_skipped_epoch_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            engine = baselines.SQLiteRootedAudit.create(Path(temporary) / "rooted", self.initial)
            skipped = State(
                3,
                self.initial.schema,
                {
                    key: Source(value.identifier, 3 if key == "a" else value.generation, value.payload)
                    for key, value in self.initial.sources.items()
                },
                {
                    "p": Fact("p", "old fact", 1, (("a", 3), ("b", 1))),
                },
                {"a": ("p",), "b": ("p",)},
            )
            self.assertEqual([], check_closure(skipped))
            before_images = sorted(path.name for path in engine.images.iterdir())
            before_exports = sorted(path.name for path in engine.exports.iterdir())
            with self.assertRaises(TransitionError) as caught:
                engine.persist_precomputed(skipped, Delta({}, (), (), {}, {}, ()))
            self.assertEqual("EPOCH_MISMATCH", caught.exception.code)
            self.assertEqual(before_images, sorted(path.name for path in engine.images.iterdir()))
            self.assertEqual(before_exports, sorted(path.name for path in engine.exports.iterdir()))

    def test_rooted_audit_rejects_unclosed_state_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            engine = baselines.SQLiteRootedAudit.create(Path(temporary) / "rooted", self.initial)
            bad = _unclosed_next_state(self.initial)
            before = sorted(path.name for path in engine.images.iterdir())
            with self.assertRaises(TransitionError) as caught:
                engine.persist_precomputed(bad, Delta({}, (), (), {}, {}, ()))
            self.assertEqual("STALE_DEPENDENCY", caught.exception.code)
            self.assertEqual(before, sorted(path.name for path in engine.images.iterdir()))

    def test_invalid_replace_create_preserves_existing_frontier_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "store"
            target.mkdir()
            marker = target / "keep.txt"
            marker.write_text("original", encoding="ascii")
            with self.assertRaises(TransitionError):
                FrontierStore.create(target, _unclosed_next_state(self.initial), replace=True)
            self.assertEqual("original", marker.read_text(encoding="ascii"))
            self.assertEqual({"keep.txt"}, {path.name for path in target.iterdir()})

    def test_invalid_baseline_create_preserves_existing_directory(self) -> None:
        classes = (
            baselines.SQLiteNormalized,
            baselines.SQLiteRebuild,
            baselines.DBMDumbRebuild,
            baselines.SQLiteRootedAudit,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for engine_class in classes:
                with self.subTest(engine=engine_class.name):
                    target = root / engine_class.name
                    target.mkdir()
                    marker = target / "keep.txt"
                    marker.write_text("original", encoding="ascii")
                    with self.assertRaises(TransitionError):
                        engine_class.create(target, _unclosed_next_state(self.initial))
                    self.assertEqual("original", marker.read_text(encoding="ascii"))
                    self.assertEqual({"keep.txt"}, {path.name for path in target.iterdir()})

    def test_all_engines_reject_wrong_delta_without_persistent_change(self) -> None:
        next_state, _ = apply_transaction(
            self.initial,
            Transaction(
                source_changes={"a": "new"},
                fact_replacements=[
                    {"id": "p", "payload": "new fact", "sources": ["a", "b"]}
                ],
            ),
        )
        wrong = Delta({}, (), (), {}, {}, ())
        classes = (
            FrontierStore,
            baselines.SQLiteNormalized,
            baselines.SQLiteRebuild,
            baselines.DBMDumbRebuild,
            baselines.SQLiteRootedAudit,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for engine_class in classes:
                with self.subTest(engine=getattr(engine_class, "name", "FrontierStore")):
                    engine = engine_class.create(root / engine_class.__name__, self.initial)
                    before = _tree_bytes(engine.path)
                    with self.assertRaises(TransitionError) as caught:
                        if isinstance(engine, FrontierStore):
                            engine.publish_precomputed(next_state, wrong)
                        else:
                            engine.persist_precomputed(next_state, wrong)
                    self.assertEqual("DELTA_STATE_MISMATCH", caught.exception.code)
                    self.assertEqual(before, _tree_bytes(engine.path))
                    self.assertEqual(self.initial.as_dict(), engine.state.as_dict())
                    durable = (
                        engine.durable_state()
                        if hasattr(engine, "durable_state")
                        else FrontierStore(engine.path).state
                    )
                    self.assertEqual(self.initial.as_dict(), durable.as_dict())
                    if hasattr(engine, "close"):
                        engine.close()

    def test_delta_dependency_shape_has_stable_witness(self) -> None:
        next_state, delta = apply_transaction(
            self.initial,
            Transaction(
                source_changes={"a": "new"},
                fact_replacements=[
                    {"id": "p", "payload": "new fact", "sources": ["a", "b"]}
                ],
            ),
        )
        malformed = Delta(
            delta.source_put,
            delta.source_del,
            delta.fact_del,
            {"p": Fact("p", "new fact", 1, (7,))},  # type: ignore[arg-type]
            delta.reverse_put,
            delta.reverse_del,
        )
        with self.assertRaises(TransitionError) as caught:
            capture_and_validate_delta(self.initial, next_state, malformed)
        self.assertEqual("DEPENDENCY_SHAPE", caught.exception.code)

    def test_malformed_states_return_witnesses_instead_of_python_errors(self) -> None:
        malformed = State(
            1,
            1,
            {"a": Source("a", 1, "x")},
            {"p": Fact("p", "fact", 1, (7,))},  # type: ignore[arg-type]
            {"a": ["p", 3]},  # type: ignore[dict-item]
        )
        codes = {row["code"] for row in check_closure(malformed)}
        self.assertIn("DEPENDENCY_SHAPE", codes)
        self.assertIn("REVERSE_MEMBER", codes)

    def test_fact_grammar_and_mixed_source_keys_fail_stably(self) -> None:
        with self.assertRaises(TransitionError) as fields:
            bootstrap_state(
                {"a": "x"},
                [{"id": "p", "payload": "fact", "sources": ["a"], "ignored": 1}],
            )
        self.assertEqual("FACT_FIELDS", fields.exception.code)
        with self.assertRaises(TransitionError) as identifier:
            bootstrap_state({"a": "x", 7: "bad"}, [])  # type: ignore[dict-item]
        self.assertEqual("SOURCE_ID", identifier.exception.code)


class DifferentialPersistenceTests(unittest.TestCase):
    def test_multistage_sequence_matches_all_durable_decoders(self) -> None:
        state = bootstrap_state(
            {"a": "a0", "b": "b0", "c": "c0"},
            [
                {"id": "p", "payload": "p0", "sources": ["a", "b"]},
                {"id": "q", "payload": "q0", "sources": ["b", "c"]},
            ],
        )
        transactions = (
            Transaction(
                source_changes={"a": "a1"},
                fact_replacements=[
                    {"id": "p", "payload": "p1", "sources": ["a", "b"]}
                ],
            ),
            Transaction(source_changes={"c": None}),
            Transaction(
                source_changes={"c": "c2"},
                fact_replacements=[
                    {"id": "q", "payload": "q2", "sources": ["b", "c"]}
                ],
            ),
            Transaction(
                new_schema=2,
                fact_replacements=[
                    {"id": "p", "payload": "p-schema2", "sources": ["a"]},
                    {"id": "q", "payload": "q-schema2", "sources": ["c"]},
                ],
            ),
            Transaction(fact_retractions=["p"]),
            Transaction(source_changes={"b": "b5"}),
        )
        classes = (
            FrontierStore,
            baselines.SQLiteNormalized,
            baselines.SQLiteRebuild,
            baselines.DBMDumbRebuild,
            baselines.SQLiteRootedAudit,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            engines = [engine_class.create(root / engine_class.__name__, state) for engine_class in classes]
            try:
                for transaction in transactions:
                    state, delta = apply_transaction(state, transaction)
                    for engine in engines:
                        if isinstance(engine, FrontierStore):
                            engine.publish_precomputed(state, delta)
                            durable = FrontierStore(engine.path).state
                        else:
                            engine.persist_precomputed(state, delta)
                            durable = engine.durable_state()
                        self.assertEqual(state.as_dict(), engine.state.as_dict())
                        self.assertEqual(state.as_dict(), durable.as_dict())
            finally:
                for engine in engines:
                    if hasattr(engine, "close"):
                        engine.close()


class ExtractorAndWorkloadTests(unittest.TestCase):
    def test_extractor_is_deterministic_and_sorted(self) -> None:
        first = {
            "b": "contract B { event Seen(); }",
            "a": "contract A { function f() public { require(true); } }",
        }
        second = {"a": first["a"], "b": first["b"]}
        left = extract_fact_specs(first)
        right = extract_fact_specs(second)
        self.assertEqual(left, right)
        self.assertEqual([row["id"] for row in left], sorted(row["id"] for row in left))

    def test_extractor_emits_fallback_and_bridge_facts(self) -> None:
        facts = extract_fact_specs({"a": "plain text", "b": "event Seen(uint x);"})
        by_id = {str(row["id"]): row for row in facts}
        self.assertIn("lex.a.summary", by_id)
        self.assertEqual(["a", "b"], by_id["lex.a.bridge"]["sources"])
        self.assertEqual(["a", "b"], by_id["lex.b.bridge"]["sources"])

    def test_extractor_covers_each_declared_lexical_family(self) -> None:
        text = "\n".join(
            [
                "contract C {",
                "mapping(address => uint) balances;",
                "event Seen(address who);",
                "function f() public { require(true); target.call(\"\"); }",
                "}",
            ]
        )
        payloads = [str(row["payload"]) for row in extract_fact_specs({"a": text})]
        for family in ("contract:", "mapping:", "event:", "function:", "require:", "call:"):
            self.assertTrue(any(payload.startswith(family) for payload in payloads), family)

    def test_extractor_rejects_invalid_inputs(self) -> None:
        with self.assertRaises(TypeError):
            extract_fact_specs([])  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            extract_fact_specs({"../bad": "text"})
        with self.assertRaises(TypeError):
            extract_fact_specs({"a": 7})  # type: ignore[dict-item]

    def test_synthetic_workload_is_deterministic_and_closed(self) -> None:
        left = make_synthetic_state(7, facts_per_source=3, dependency_width=3, epoch=4, schema=2)
        right = make_synthetic_state(7, facts_per_source=3, dependency_width=3, epoch=4, schema=2)
        self.assertEqual(left, right)
        self.assertEqual([], check_closure(left))
        self.assertEqual(make_update(left, batch=3, seed=17), make_update(right, batch=3, seed=17))

    def test_synthetic_workload_parameter_validation(self) -> None:
        invalid_calls = [
            lambda: make_synthetic_state(0),
            lambda: make_synthetic_state(True),
            lambda: make_synthetic_state(2, facts_per_source=-1),
            lambda: make_synthetic_state(2, dependency_width=0),
            lambda: make_synthetic_state(2, dependency_width=3),
            lambda: make_synthetic_state(2, epoch=False),
            lambda: make_synthetic_state(2, schema=0),
            lambda: source_id(-1),
            lambda: fact_id(0, -1),
        ]
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises(ValueError):
                    call()

    def test_source_choice_and_query_count_validation(self) -> None:
        state = make_synthetic_state(4, facts_per_source=2, dependency_width=2)
        self.assertEqual((), choose_sources(state, 0, 1))
        self.assertEqual(choose_sources(state, 2, 7), choose_sources(state, 2, 7))
        with self.assertRaises(ValueError):
            choose_sources(state, -1, 1)
        with self.assertRaises(ValueError):
            choose_sources(state, 5, 1)
        with self.assertRaises(ValueError):
            query_fact_ids(state, -1)
        self.assertEqual((), query_fact_ids(state, 0))

    def test_zero_fact_synthetic_state_remains_valid(self) -> None:
        state = make_synthetic_state(3, facts_per_source=0, dependency_width=1)
        self.assertEqual({}, state.facts)
        self.assertEqual({key: () for key in state.sources}, state.reverse)
        self.assertEqual([], check_closure(state))


if __name__ == "__main__":
    unittest.main()
