"""Check retained results, current summaries, ledgers, and artifact hygiene."""

from __future__ import annotations

import ast
import csv
import json
import re
import tempfile
from datetime import date
from pathlib import Path

from experiments.generate_paper_values import render_paper_values
from experiments.journal_robustness import run as reconstruct_journal_robustness
from experiments.summarize_current import (
    ACCEPTED,
    ENGINE_ORDER,
    EXCLUDED,
    PILOT,
    aggregate_case,
    build_comparisons,
    build_order_diagnostics,
    load_current_test_count,
    write_csv,
    write_paper_inputs,
)
from experiments.verify_checked_results import verify


REQUIRED_CLAIM_COLUMNS = {
    "claim_id",
    "manuscript_claim",
    "argument_or_theorem",
    "implementation_or_test",
    "experiment",
    "manuscript_location",
    "raw_result_path",
    "maturity",
    "fresh_recheck_status",
    "limitation",
}
REQUIRED_MANIFEST_COLUMNS = {"bibtex_key", "title", "manuscript_role", "stable_locator"}
REQUIRED_REFERENCE_COLUMNS = {
    "bibtex_key",
    "title",
    "publication_kind",
    "audited_authors",
    "audited_year",
    "audited_venue",
    "stable_locator",
    "metadata_authority",
    "claim_supported",
    "paper_location",
    "metadata_match",
    "passage_fit",
    "verified_on",
    "notes",
}
FORBIDDEN_PATH_PARTS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache"}
FORBIDDEN_FILE_SUFFIXES = {".pyc", ".pyo"}
FORBIDDEN_TEXT = (
    "REPOSITORY_" + "URL_PENDING",
    "/" + "mnt/data/",
)
EMAIL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9.-])"
)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def verify_primary_wrapper(paper: Path) -> dict:
    """Inspect the current wrapper when papers accompany the code checkout.

    Retained publication metadata describes its own earlier rendering, not
    the current wrapper. A standalone artifact does not include the paper.
    """
    source = paper / "main.tex"
    if not source.is_file():
        return {"status": "NOT_INCLUDED", "source_inspected": False}
    text = source.read_text(encoding="utf-8")
    match = re.search(r"\\documentclass\[([^]]+)\]\{acmart\}", text)
    require(match is not None, "current primary ACM wrapper missing")
    options = tuple(part.strip() for part in match.group(1).split(","))
    require(options == ("acmsmall", "screen", "review", "anonymous"),
            "current primary wrapper format differs")
    for name in ("journal-preamble.tex", "journal-frontmatter.tex"):
        require(r"\input{" + name + "}" in text, "current primary shared source differs")
    return {"status": "SOURCE_CHECKED", "source_inspected": True,
            "class_options": list(options), "pdf_inspected": False}




def parse_python_sources(root: Path) -> list[str]:
    """Parse every Python source below ``root`` without importing it.

    This catches syntax errors in implementation, test, and utility modules that
    are not reached by the normal import graph.  The returned relative-path list
    is sorted so callers can audit the exact coverage surface.
    """

    parsed: list[str] = []
    for path in sorted(root.rglob("*.py")):
        parts = path.relative_to(root).parts
        # Checkout metadata is not a delivered source. Nested .git directories
        # remain prohibited by the package-hygiene check below.
        if parts[0] == ".git":
            continue
        relative = path.relative_to(root).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
            ast.parse(source, filename=relative)
        except (OSError, UnicodeError, SyntaxError) as error:
            raise AssertionError(f"Python syntax parse failed for {relative}: {error}") from error
        parsed.append(relative)
    require(bool(parsed), "no Python sources found for syntax parsing")
    return parsed


def verify_retained_syntax_coverage(retained: dict, current: list[str]) -> None:
    """Check the original inventory without claiming it covered later files.

    All current sources have already been parsed in this invocation, including
    additions. Historical coverage and count remain bound to their own record.
    """
    coverage = retained.get("coverage")
    require(isinstance(coverage, list) and bool(coverage), "retained syntax inventory missing")
    require(all(isinstance(path, str) for path in coverage), "retained syntax path type")
    require(coverage == sorted(set(coverage)), "retained syntax inventory is not unique and sorted")
    require(type(retained.get("python_sources_parsed")) is int
            and retained["python_sources_parsed"] == len(coverage), "retained syntax count differs")
    require(set(coverage) <= set(current), "retained syntax source is missing from current tree")


def verify_reviewer_repairs_and_environment(root: Path, parsed_python_sources: list[str]) -> None:
    """Check reviewer-repair evidence and historical environment boundaries."""

    repair_path = root / "results/current/reviewer-repairs/verification.json"
    repair = json.loads(repair_path.read_text(encoding="utf-8"))
    require(repair.get("status") == "REVIEWER_REPAIRS_VALIDATED", "reviewer repair status")
    require(repair.get("scientific_measurement") is False, "reviewer repair measurement boundary")
    require(repair["f1"]["abstract_model"] == "abstract-selector-object-v1", "abstract publication model")
    require(repair["f1"]["closed_nonendpoint_rejected_as"] == "NON_ENDPOINT_OBSERVATION", "closed mix negative")
    require(repair["f1"]["wrong_selector_rejected_as"] == "CUT_ENDPOINT_MISMATCH", "wrong selector negative")
    require(repair["f2"]["invalidation_set_empty"] is True, "replacement-only invalidation premise")
    require(repair["f2"]["replacement_clear_set"] == ["p"], "replacement clear set")
    require(repair["f2"]["final_dependencies"] == [["b", 1]], "replacement dependency result")
    require(repair["f2"]["final_reverse"] == {"a": [], "b": ["p"]}, "replacement reverse result")
    require(repair["f2"]["normalized_decode_equal"] is True, "relational replacement decode")
    require(repair["f3"]["old_generation"] == 1 and repair["f3"]["new_generation"] == 2, "legal source generation")
    require(repair["f3"]["mixed_closed"] is True, "mixed closure")
    require(repair["f3"]["differs_from_old_without_epoch"] is True, "mixed/old identity")
    require(repair["f3"]["differs_from_new_without_epoch"] is True, "mixed/new identity")
    for field in ("selected_pair_reads", "sqlite_decodes", "export_parses", "database_export_comparisons"):
        require(repair["f4"][field] == 2, f"rooted audit operation count: {field}")
    require(repair["f4"]["observed_selected_pair_reads"] == 2, "observed rooted pair reads")
    require(repair["f4"]["following_expected_state_comparisons"] == 1, "rooted expected-state comparisons")
    require(repair["f4"]["frozen_results_reinterpreted_only"] is True, "frozen timing boundary")
    verify_retained_syntax_coverage(repair["f6"], parsed_python_sources)
    require(repair["f6"]["unimported_syntax_error_rejected"] is True, "unimported syntax negative")
    require("not_imported.py" in repair["f6"]["negative_witness"], "syntax negative witness")

    environment = json.loads((root / "results/current/accepted-environment.json").read_text(encoding="utf-8"))
    require(environment.get("status") == "PARTIALLY_RECOVERED_WITH_EXPLICIT_UNKNOWNS", "accepted environment status")
    require(environment.get("scope") == "accepted fixed-order engine runs only", "accepted environment scope")
    confirmed = environment.get("confirmed_common", {})
    require(confirmed.get("platform_family") == "Linux-compatible POSIX environment", "platform family")
    require(confirmed.get("procfs_required_and_observed") is True, "procfs record")
    require(confirmed.get("cgroup_v2_controls_required_and_observed") is True, "cgroup record")
    require(confirmed.get("swap_total_required_zero") is True, "swap record")
    require(confirmed.get("child_cpu_affinity_count") == 1, "affinity record")
    require(tuple(confirmed.get("engine_order", ())) == ENGINE_ORDER, "environment engine order")
    require(confirmed.get("rooted_audit_timed_pair_passes") == 2, "environment rooted audit count")
    unknown = environment.get("unknown_not_recorded_at_measurement_time", [])
    for phrase in ("Python interpreter", "SQLite library", "filesystem type", "host CPU", "virtualization", "immutable source"):
        require(any(phrase in item for item in unknown), f"missing explicit unknown: {phrase}")
    warning = environment.get("warning", "")
    require(
        "does not substitute the current review machine" in warning or "not backfilled" in warning,
        "historical environment non-backfill warning",
    )

    expected_dirs = {"small": "current-small", "medium": "current-medium-retry", "large": "current-large"}
    require(set(environment.get("cases", {})) == set(expected_dirs), "accepted environment case set")
    for scale, directory in expected_dirs.items():
        case = environment["cases"][scale]
        require(case["directory"] == directory and case["mode"] == scale, f"{scale}: environment identity")
        observations = json.loads((root / case["observations_file"]).read_text(encoding="utf-8"))
        accounting = json.loads((root / case["accounting_file"]).read_text(encoding="utf-8"))
        require(case["observations"] == len(observations["rows"]), f"{scale}: observation count")
        require(case["configuration"] == observations["configuration"], f"{scale}: configuration")
        for field in (
            "admission_available_memory_bytes", "admission_worker_headroom",
            "child_cpu_affinity_count", "sampled_aggregate_threads_peak",
            "sampled_concurrent_tree_rss_peak_bytes", "sampled_process_count_peak",
        ):
            require(case[field] == accounting[field], f"{scale}: accounting field {field}")
        require(all(row.get("write_bytes") is not None for row in observations["rows"]), f"{scale}: write counter availability")


def verify_current(root: Path) -> int:
    current = root / "results/current"
    retained_summary = current / "summary/current_summary.csv"
    retained_comparison = current / "summary/current_comparison.csv"
    retained_diagnostics = current / "summary/current_order_diagnostics.csv"
    retained_repetitions = current / "summary/current_order_repetitions.csv"
    retained_summary_json = current / "summary/current_summary.json"
    retained_diagnostics_json = current / "summary/current_order_diagnostics.json"
    retained_paper_inputs = current / "paper-inputs"
    current_test_count = load_current_test_count(current)

    summaries: list[dict[str, object]] = []
    cases: list[dict[str, object]] = []
    pilot_rows, pilot_case = aggregate_case("pilot", PILOT, current, "pilot")
    summaries.extend(pilot_rows)
    cases.append(pilot_case)
    for scale, directory in ACCEPTED.items():
        rows, case = aggregate_case(scale, directory, current, "accepted")
        summaries.extend(rows)
        cases.append(case)
    comparisons = build_comparisons(summaries)
    diagnostics, repetition_diagnostics = build_order_diagnostics(current)
    require(all(bool(case["all_durable_equal"]) for case in cases), "current durable equality")

    with tempfile.TemporaryDirectory(prefix="frontier-current-check-") as temporary:
        regenerated = Path(temporary)
        regenerated_summary = regenerated / "summary"
        regenerated_paper = regenerated / "paper-inputs"
        write_csv(regenerated_summary / "current_summary.csv", summaries)
        write_csv(regenerated_summary / "current_comparison.csv", comparisons)
        write_csv(regenerated_summary / "current_order_diagnostics.csv", diagnostics)
        write_csv(regenerated_summary / "current_order_repetitions.csv", repetition_diagnostics)
        summary_object = {
            "cases": cases,
            "summary": summaries,
            "comparisons": comparisons,
            "order_diagnostics": diagnostics,
            "order_repetitions": repetition_diagnostics,
        }
        (regenerated_summary / "current_summary.json").write_text(
            json.dumps(summary_object, indent=2) + "\n", encoding="utf-8"
        )
        (regenerated_summary / "current_order_diagnostics.json").write_text(
            json.dumps({
                "execution_order": list(ENGINE_ORDER),
                "order_control_status": "NO_ACCEPTED_COUNTERBALANCED_OBSERVATIONS",
                "scale_diagnostics": diagnostics,
                "repetition_diagnostics": repetition_diagnostics,
            }, indent=2) + "\n",
            encoding="utf-8",
        )
        write_paper_inputs(
            regenerated_paper,
            summaries,
            comparisons,
            diagnostics,
            current_test_count,
        )
        reconstructed_robustness = reconstruct_journal_robustness(
            current, regenerated_summary / "journal-robustness.json"
        )
        require(reconstructed_robustness["input_observations"] == 264, "journal robustness input count")
        require(len(reconstructed_robustness["distributions"]) == 9, "journal robustness distribution shape")
        require(len(reconstructed_robustness["paired_frontier_rooted"]) == 3, "journal robustness paired shape")
        for retained, generated in (
            (retained_summary, regenerated_summary / "current_summary.csv"),
            (retained_comparison, regenerated_summary / "current_comparison.csv"),
            (retained_diagnostics, regenerated_summary / "current_order_diagnostics.csv"),
            (retained_repetitions, regenerated_summary / "current_order_repetitions.csv"),
            (retained_summary_json, regenerated_summary / "current_summary.json"),
            (retained_diagnostics_json, regenerated_summary / "current_order_diagnostics.json"),
        ):
            require(
                retained.read_text(encoding="utf-8") == generated.read_text(encoding="utf-8"),
                f"current derived surface differs from retained observations: {retained.name}",
            )
        for name in ("current-latency.csv", "current-cost.csv", "current-plot.csv", "current-values.tex"):
            require(
                (retained_paper_inputs / name).read_text(encoding="utf-8")
                == (regenerated_paper / name).read_text(encoding="utf-8"),
                f"current paper input differs from retained observations: {name}",
            )
        for name in (
            "journal-robustness.json",
            "journal-robustness.csv",
            "journal-robustness-paired.csv",
            "journal-robustness.tex",
        ):
            require(
                (current / "summary" / name).read_bytes()
                == (regenerated_summary / name).read_bytes(),
                f"journal robustness surface differs from retained observations: {name}",
            )

    acceptance = json.loads((current / "acceptance.json").read_text(encoding="utf-8"))
    require(
        acceptance["accepted"]
        == [{"scale": scale, "directory": directory} for scale, directory in ACCEPTED.items()],
        "current accepted-directory set",
    )
    require(tuple(acceptance["execution_order"]) == ENGINE_ORDER, "fixed execution order record")
    require(
        acceptance["order_control_status"] == "NO_ACCEPTED_COUNTERBALANCED_OBSERVATIONS",
        "order-control status",
    )
    excluded = {row["directory"]: row["reason"] for row in acceptance["excluded"]}
    require(excluded == EXCLUDED, "excluded-attempt record")

    accepted_observations = sum(
        int(row["observations"])
        for row in summaries
        if row["role"] == "accepted"
    )
    require(accepted_observations == 264, "accepted current observation count")
    require(len(diagnostics) == 3 and len(repetition_diagnostics) == 7, "order diagnostics shape")
    expected = {
        "small": (48, 44, 4),
        "medium": (24, 20, 4),
        "large": (16, 11, 5),
    }
    for row in diagnostics:
        require(row["scale"] in expected, "unknown diagnostic scale")
        pairs, rooted_wins, frontier_wins = expected[str(row["scale"])]
        require(int(row["aligned_updates"]) == pairs, "diagnostic pair count")
        require(int(row["rooted_faster_aligned_updates"]) == rooted_wins, "rooted pair wins")
        require(int(row["frontier_faster_aligned_updates"]) == frontier_wins, "frontier pair wins")
        require(bool(row["all_repetition_medians_favor_rooted"]), "repetition-median direction")
        require(float(row["frontier_to_rooted_pooled_median_ratio"]) > 1.0, "pooled median direction")

    for directory, expected_status, expected_charge in (
        ("order-sensitivity", "CASE_FAILED", 600.0),
        ("order-counterbalance", "CONTROLLER_TERMINATED_NO_OBSERVATIONS", 480.0),
    ):
        attempt_dir = current / directory
        attempt = json.loads((attempt_dir / "attempt.json").read_text(encoding="utf-8"))
        accounting = json.loads((attempt_dir / "accounting.json").read_text(encoding="utf-8"))
        require(attempt["status"] == expected_status, f"{directory}: attempt status")
        require(accounting["status"] == expected_status, f"{directory}: accounting status")
        require(float(attempt["charged_cpu_reservation_seconds"]) == expected_charge, f"{directory}: charge")
        require(attempt["campaign_charge_refunded"] is False, f"{directory}: refund")
        require(not (attempt_dir / "observations.csv").exists(), f"{directory}: unexpected CSV observations")
        require(not (attempt_dir / "observations.json").exists(), f"{directory}: unexpected JSON observations")

    accounting = json.loads((root / "resource-accounting.json").read_text(encoding="utf-8"))
    require(float(accounting["campaign_cpu_upper_bound_seconds"]) == 28800.0, "campaign CPU upper bound")
    require(float(accounting["remaining_experiment_allowance_seconds"]) == 0.0, "experiment allowance")
    require(float(accounting["remaining_repair_reproduction_allowance_seconds"]) == 0.0, "repair allowance")
    uncontrolled = {row["case"]: row for row in accounting["uncontrolled_attempts"]}
    require({"current-medium", "order-sensitivity", "order-counterbalance"} <= uncontrolled.keys(), "failed-attempt accounting")
    return accepted_observations


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    current_primary_wrapper = verify_primary_wrapper(root.parent / "paper")
    parsed_python_sources = parse_python_sources(root)
    verify_reviewer_repairs_and_environment(root, parsed_python_sources)
    historical = verify(root / "results")
    current_observations = verify_current(root)
    current_test_count = load_current_test_count(root / "results/current")
    retained_paper_values = root / "results/current/paper-inputs/paper-values.tex"
    require(
        retained_paper_values.read_text(encoding="ascii") == render_paper_values(root),
        "static paper macro surface differs from retained evidence",
    )

    claims = read_rows(root / "claim_evidence_ledger.csv")
    require(claims and set(claims[0]) == REQUIRED_CLAIM_COLUMNS, "claim ledger columns")
    claim_ids = [row["claim_id"] for row in claims]
    require(len(claim_ids) == len(set(claim_ids)), "duplicate claim identifier")
    allowed_status = {
        "CURRENT_PROVED_AND_EXECUTED",
        "CURRENT_EXECUTED_PASS",
        "CURRENT_PROVED",
        "CURRENT_RECONCILED",
        "CURRENT_DERIVED",
        "TERMINATED_POSITIVE_CLAIM",
        "RETAINED_NOT_PROMOTED",
    }
    require(all(row["fresh_recheck_status"] in allowed_status for row in claims), "unrecognized claim status")
    for row in claims:
        for relative in row["raw_result_path"].split(";"):
            require((root / relative).is_file(), f"missing claim evidence: {relative}")

    references = read_rows(root / "reference_audit.csv")
    require(bool(references), "reference audit is empty")
    require(set(references[0]) == REQUIRED_REFERENCE_COLUMNS, "reference audit columns")
    keys = [row["bibtex_key"] for row in references]
    require(len(keys) == len(set(keys)), "duplicate reference key")
    require(all(row["metadata_match"] == "yes" for row in references), "reference metadata audit is not closed")
    require(all(row["passage_fit"] == "yes" for row in references), "reference passage audit is not closed")
    required_reference_text = ("title", "publication_kind", "audited_authors", "audited_year", "audited_venue", "stable_locator", "metadata_authority", "claim_supported", "paper_location", "verified_on", "notes")
    require(all(all(row[field].strip() for field in required_reference_text) for row in references), "incomplete reference audit row")
    try:
        for row in references:
            date.fromisoformat(row["verified_on"])
    except ValueError as error:
        raise AssertionError(f"invalid reference verification date: {error}") from error
    require(len(references) == 62, "reference audit count changed")

    reference_manifest = read_rows(root / "publication_reference_manifest.csv")
    require(bool(reference_manifest), "publication reference manifest is empty")
    require(set(reference_manifest[0]) == REQUIRED_MANIFEST_COLUMNS, "reference manifest columns")
    manifest_keys = [row["bibtex_key"] for row in reference_manifest]
    require(len(manifest_keys) == len(set(manifest_keys)), "duplicate reference manifest key")
    require(set(manifest_keys) == set(keys), "reference audit/manifest key mismatch")
    audit_by_key = {row["bibtex_key"]: row for row in references}
    manifest_by_key = {row["bibtex_key"]: row for row in reference_manifest}
    for key in keys:
        require(audit_by_key[key]["title"] == manifest_by_key[key]["title"], f"reference title mismatch: {key}")
        require(audit_by_key[key]["stable_locator"] == manifest_by_key[key]["stable_locator"], f"reference locator mismatch: {key}")

    calibration = read_rows(root / "calibration_matrix.csv")
    require(len(calibration) == 18, "calibration matrix count changed")
    role_sets = [set(part.strip() for part in row["calibration_roles"].split(";") if part.strip()) for row in calibration]
    require(sum("FAST-primary" in roles for roles in role_sets) == 12, "FAST-primary calibration count")
    require(sum(bool({"distinguished", "high-visibility"} & roles) for roles in role_sets) == 5, "distinguished/high-visibility calibration count")
    require(sum("adjacent" in roles for roles in role_sets) == 5, "adjacent calibration count")

    publication = json.loads((root / "results/publication-surface.json").read_text(encoding="utf-8"))
    require(
        publication["title"]
        == "Auditable Source--Fact Storage: Transactional Equivalence, Closure Counterexamples, and Representation Costs",
        "paper title mismatch",
    )
    require(publication["target_journal"] == "ACM Transactions on Storage", "journal target mismatch")
    require(publication["author_presentation"] == "anonymous", "paper anonymity mismatch")
    # These fields validate the retained rendering record only. The current
    # primary source is inspected separately, not inferred from this record.
    require(publication["review_format"] == "manuscript,screen,review,anonymous", "retained review format record")
    require(publication["layout_format"] == "acmsmall,screen,anonymous", "TOS layout format record")
    require(int(publication["review_pages"]) > 0 and int(publication["tos_layout_pages"]) > 0, "paper page counts")
    require(publication["review_page_size"] == "US Letter", "review page size")
    require(publication["same_scientific_source"] is True, "review/layout source divergence")
    require(publication["acceptance_critical_material_in_main_paper"] is True, "main-paper evidence surface")
    require(publication["paper_input_reconstruction_status"] == "PASS", "paper input reconstruction incomplete")
    require(publication["reference_count"] == len(references), "publication reference count mismatch")
    require(publication["reference_inventory_status"] == "PASS", "publication reference inventory incomplete")
    require(publication.get("fully_verified_reference_count") == len(references), "publication verified-reference count mismatch")
    require(publication.get("maximum_citation_cluster_size") == 1, "publication citation cluster boundary")
    require(publication.get("bibtex_metadata_surface_checked") is True, "publication BibTeX metadata check missing")
    require(publication.get("author_order_year_venue_checked") is True, "publication author/year/venue check missing")
    require(publication.get("conference_reference_count") == 47, "publication conference-reference count")
    require(publication.get("journal_reference_count") == 14, "publication journal-reference count")
    require(publication.get("preprint_reference_count") == 1, "publication preprint-reference count")
    try:
        date.fromisoformat(publication["journal_rules_rechecked_on"])
    except (KeyError, ValueError) as error:
        raise AssertionError(f"invalid journal-rule recheck date: {error}") from error
    expected_source_values = {
        "paper/current-latency.csv",
        "artifact/results/current/paper-inputs/current-latency.csv",
        "paper/current-cost.csv",
        "artifact/results/current/paper-inputs/current-cost.csv",
        "paper/current-plot.csv",
        "artifact/results/current/paper-inputs/current-plot.csv",
        "paper/current-values.tex",
        "artifact/results/current/paper-inputs/current-values.tex",
        "paper/paper-values.tex",
        "artifact/results/current/paper-inputs/paper-values.tex",
        "paper/journal-robustness.tex",
        "artifact/results/current/summary/journal-robustness.tex",
        "artifact/results/current/summary/journal-robustness.json",
        "artifact/results/current/summary/journal-robustness.csv",
        "artifact/results/current/summary/journal-robustness-paired.csv",
        "artifact/results/current/summary/current_order_diagnostics.csv",
    }
    require(set(publication["source_values"]) == expected_source_values, "paper source inventory")
    require(publication["full_page_visual_inspection_status"] == "PASS", "paper visual inspection incomplete")
    require(publication["renderer_parity_status"] == "PASS", "paper renderer parity incomplete")
    require(publication["fonts_embedded"] is True, "paper fonts are not embedded")
    require(
        publication["unresolved_references"] is False
        and publication["overfull_boxes"] is False
        and publication["bibtex_warnings"] == 0,
        "paper compile defects",
    )

    # The maintenance summary is a public state surface, not an informal note.
    # Recompute every count that it reports so stale publication/reference values
    # cannot survive an otherwise successful artifact verification.
    maintenance = json.loads((root / "results/maintenance.json").read_text(encoding="utf-8"))
    abstract = json.loads((root / "results/raw/abstract_crash_cuts.json").read_text(encoding="utf-8"))
    require(abstract.get("model") == "abstract-selector-object-v1", "bounded publication model")
    require(abstract.get("all_closed") is True and abstract.get("all_endpoints") is True, "bounded publication status")
    require(abstract.get("closure_violations") == [] and abstract.get("endpoint_violations") == [], "bounded publication violations")
    require(abstract.get("selected_endpoint_counts") == {"old": 6200, "new": 1550}, "bounded selector counts")
    joint = json.loads((root / "results/current/joint-history/observations.json").read_text(encoding="utf-8"))
    resource = json.loads((root / "resource-accounting.json").read_text(encoding="utf-8"))
    joint_compactions = sum(row.get("compaction_ns") is not None for row in joint["rows"])
    expected_maintenance = {
        "scientific_decision": "COMPLETE_BOUNDED_NEGATIVE_LOGICAL_RESULT",
        "positive_mechanism_claim": "TERMINATED",
        "performance_inference": "DESCRIPTIVE_FIXED_ORDER_ONLY",
        "order_control_accepted_observations": 0,
        "historical_summary_rows": historical["summary_rows"],
        "current_accepted_observation_rows": current_observations,
        "claim_ledger_rows": len(claims),
        "reference_audit_rows": len(references),
        "calibration_matrix_rows": len(calibration),
        "current_executable_methods_passed": current_test_count,
        "bounded_histories": int(abstract["histories"]),
        "bounded_intermediate_states": int(abstract["intermediate_states"]),
        "abstract_publication_cuts": int(abstract["crash_cuts"]),
        "abstract_publication_model": "abstract-selector-object-v1",
        "abstract_closure_violations": 0,
        "abstract_endpoint_violations": 0,
        "abstract_selected_old": 6200,
        "abstract_selected_new": 1550,
        "reviewer_repair_status": "REVIEWER_REPAIRS_VALIDATED",
        "accepted_environment_status": "PARTIALLY_RECOVERED_WITH_EXPLICIT_UNKNOWNS",
        "rooted_audit_timed_pair_passes": 2,
        # Maintenance metadata accompanies the retained repair record, not a
        # claim that later files were parsed by that historical execution.
        "python_sources_parsed": json.loads(
            (root / "results/current/reviewer-repairs/verification.json").read_text(encoding="utf-8")
        )["f6"]["python_sources_parsed"],
        "joint_updates": int(joint["updates"]),
        "joint_compactions": joint_compactions,
        "journal_target": "ACM Transactions on Storage",
        "journal_review_template": "ACM manuscript,review,anonymous",
        "journal_layout_template": "ACM acmsmall,anonymous",
        "journal_robustness_input_rows": 264,
        "journal_robustness_distribution_rows": 9,
        "journal_robustness_paired_rows": 3,
        "retired_prefix_history_count": 769,
        "retired_prefix_intermediate_states": 2913,
        "retired_prefix_publication_cuts": 7690,
        "paper_review_pages": int(publication["review_pages"]),
        "paper_tos_layout_pages": int(publication["tos_layout_pages"]),
        "resource_cpu_upper_bound_seconds": float(resource["campaign_cpu_upper_bound_seconds"]),
        "remaining_scientific_run_allowance_seconds": 0,
        "resource_accounting": "resource-accounting.json",
        "sqlite_normalized_update_scope": "PRECOMPUTED_DELTA_APPLICATION; INVALIDATION_DERIVATION_THEOREM_LEVEL_AND_UNTIMED",
        "evaluation_timing_scope": "PERSISTENCE_RETURN_AND_FOLLOWING_FULL_AUDIT; SHARED_TRANSITION_EXCLUDED",
    }
    for key, expected in expected_maintenance.items():
        require(maintenance.get(key) == expected, f"maintenance state mismatch for {key}")
    require(
        maintenance.get("scope")
        == "non-experiment artifact, data, ACM journal manuscript, render, and package consistency checks",
        "maintenance scope",
    )
    require(bool(maintenance.get("interpretation", "").strip()), "maintenance interpretation")

    required = {
        "README.md",
        "LICENSE",
        "FORMAT.md",
        "PLATFORM.md",
        "proofs/model.md",
        "proofs/representation.md",
        "external_resources.csv",
        "claim_evidence_ledger.csv",
        "reference_audit.csv",
        "publication_reference_manifest.csv",
        "experiments/verify_publication_references.py",
        "experiments/verify_reviewer_repairs.py",
        "resource-accounting.json",
        "results/summary/all_summary.csv",
        "results/summary/headline.json",
        "results/summary/historical-values.tex",
        "results/current/summary/current_summary.csv",
        "results/current/summary/current_summary.json",
        "results/current/summary/current_comparison.csv",
        "results/current/summary/current_order_diagnostics.csv",
        "results/current/summary/current_order_repetitions.csv",
        "results/current/summary/current_order_diagnostics.json",
        "results/current/summary/journal-robustness.json",
        "results/current/summary/journal-robustness.csv",
        "results/current/summary/journal-robustness-paired.csv",
        "results/current/summary/journal-robustness.tex",
        "experiments/journal_robustness.py",
        "tests/test_exhaustive.py",
        "tests/test_journal_evidence.py",
        "results/retired/abstract-crash-cuts-prefix19.json",
        "results/retired/tiny-histories-prefix19.json",
        "results/retired/README.md",
        "results/current/paper-inputs/current-latency.csv",
        "results/current/paper-inputs/current-cost.csv",
        "results/current/paper-inputs/current-plot.csv",
        "results/current/paper-inputs/current-values.tex",
        "results/current/paper-inputs/paper-values.tex",
        "experiments/generate_paper_values.py",
        "results/current/acceptance.json",
        "results/current/accepted-environment.json",
        "results/current/reviewer-repairs/verification.json",
        "results/current/reviewer-repairs/stdout.txt",
        "results/current/reviewer-repairs/stderr.txt",
        "results/current/reviewer-repairs/record.json",
        "results/current/order-sensitivity/attempt.json",
        "results/current/order-sensitivity/accounting.json",
        "results/current/order-counterbalance/attempt.json",
        "results/current/order-counterbalance/accounting.json",
        "results/current/order-counterbalance/launcher-incident.json",
        "results/publication-surface.json",
        "results/maintenance.json",
        "calibration_matrix.csv",
    }
    present = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    require(required <= present, f"missing required artifact files: {sorted(required - present)}")

    nested_archives: list[str] = []
    bad_paths: list[str] = []
    text_violations: list[str] = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if rel.parts[0] == ".git":
            continue
        if any(part in FORBIDDEN_PATH_PARTS for part in rel.parts):
            bad_paths.append(rel.as_posix())
        if path.is_file() and path.suffix in FORBIDDEN_FILE_SUFFIXES:
            bad_paths.append(rel.as_posix())
        if path.is_file() and path.suffix.lower() in {".zip", ".tar", ".gz", ".7z"}:
            nested_archives.append(rel.as_posix())
        if path.is_file() and path.suffix.lower() in {".py", ".md", ".csv", ".json", ".txt"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            for token in FORBIDDEN_TEXT:
                if token in text:
                    text_violations.append(f"{rel.as_posix()}: {token}")
            if EMAIL_PATTERN.search(text):
                text_violations.append(f"{rel.as_posix()}: email address")
    require(not bad_paths, f"cache or bytecode residue: {bad_paths}")
    require(not nested_archives, f"nested archives: {nested_archives}")
    require(not text_violations, f"private or placeholder text: {text_violations}")

    print(json.dumps({
        "status": "PACKAGE_DATA_CONSISTENT",
        "scientific_decision": "COMPLETE_BOUNDED_NEGATIVE_LOGICAL_RESULT",
        "positive_mechanism_claim": "TERMINATED",
        "performance_inference": "DESCRIPTIVE_FIXED_ORDER_ONLY",
        "full_paper_calibration_rows": len(calibration),
        "claims": len(claims),
        "references": len(references),
        "historical_summary_rows": historical["summary_rows"],
        "current_accepted_observations": current_observations,
        "maintenance_state": "CONSISTENT",
        "python_sources_parsed": len(parsed_python_sources),
        "current_primary_wrapper": current_primary_wrapper,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
