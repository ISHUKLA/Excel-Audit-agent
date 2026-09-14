"""Blocking checks that keep public evidence aligned with implementation."""

from __future__ import annotations

import ast
import csv
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from agents.anomaly_detector import detect_anomalies
from agents.parser import parse_workbook
from agents.reconciliation import _EVALUATORS, run_reconciliation
from core.formula_catalogue import SUPPORTED_FUNCTIONS
from core.gates import preview_reconciliation
from core.models import ReferenceFigureLine, ReferenceFigures
from core.numeric_utils import excel_round

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
FIXTURES = ROOT / "tests" / "fixtures"
DEMO = ROOT / "demo"
QUALIFICATION_MANIFEST = json.loads(
    (FIXTURES / "qualification_manifest.json").read_text(encoding="utf-8")
)


def _demo_reference(case: dict, expected: dict):
    if not case["reference_csv_path"]:
        return None
    with Path(case["reference_csv_path"]).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    context = expected["gate1_context"]
    currency = "GBP" if expected["case_number"] == 3 else context["currency"]
    lines = [
        ReferenceFigureLine(
            line_id=f"REF-{index:04d}",
            account_number=row["account_number"],
            label=row["label"],
            amount=float(row["amount"]),
            debit_credit=row["debit_credit"],
            entity=context["entity"],
            period=context["period"],
            currency=currency,
            ledger_source=row["ledger_source"],
            evidence_ref=row["evidence_reference"],
        )
        for index, row in enumerate(rows, start=1)
    ]
    return ReferenceFigures(
        source_label="Synthetic demonstration reference figures",
        entity=context["entity"],
        period=context["period"],
        currency=currency,
        basis=context["basis"],
        lines=lines,
        uploaded_at=datetime.now(timezone.utc),
    )


def _finding_categories(findings) -> dict[str, int]:
    categories = Counter()
    for finding in findings:
        text = finding.description.lower()
        if "circular reference" in text:
            categories["circular_reference"] += 1
        elif "hardcoded literal" in text:
            categories["hardcoded_literal"] += 1
        elif "sum formula skips rows" in text:
            categories["omitted_sum_rows"] += 1
        else:
            categories["unclassified"] += 1
    return dict(categories)


def _aggregate_internal(result) -> str:
    verdicts = [line.verdict for line in result.lines if line.check_type == "excel_vs_python"]
    for candidate in ("block", "incomplete", "warn", "pass"):
        if candidate in verdicts:
            return candidate
    return "incomplete"


def test_readme_supported_function_count_matches_catalogue():
    match = re.search(r"supports\s+(\d+)\s+functions", README, flags=re.IGNORECASE)
    assert match, "README must state the supported function count"
    documented = int(match.group(1))
    assert documented == len(SUPPORTED_FUNCTIONS), (
        f"README says {documented}; catalogue has {len(SUPPORTED_FUNCTIONS)}: "
        f"{sorted(SUPPORTED_FUNCTIONS)}"
    )


def test_every_supported_function_has_an_evaluator_handler():
    missing = sorted(SUPPORTED_FUNCTIONS - set(_EVALUATORS))
    unexpected = sorted(set(_EVALUATORS) - SUPPORTED_FUNCTIONS)
    assert missing == [], f"catalogue functions without evaluators: {missing}"
    assert unexpected == [], f"evaluators not declared in catalogue: {unexpected}"


def test_every_supported_function_appears_in_qualified_workbook_manifest():
    actual = set(QUALIFICATION_MANIFEST["formula_manifest"])
    missing = sorted(SUPPORTED_FUNCTIONS - actual)
    assert missing == [], f"qualified workbook does not exercise: {missing}"
    for name in SUPPORTED_FUNCTIONS:
        assert QUALIFICATION_MANIFEST["formula_manifest"][name], name


def test_cases_one_to_three_match_their_machine_readable_contracts():
    import demo_cases

    for case_number in (1, 2, 3):
        expected = json.loads(
            (DEMO / "expected_results" / f"case_{case_number}_expected.json").read_text(
                encoding="utf-8"
            )
        )
        case = demo_cases.load_case(case_number)
        parsed = parse_workbook(case["workbook_bytes"])
        findings = detect_anomalies(parsed)
        result = run_reconciliation(
            parsed,
            expected["authoritative_output_cells"],
            _demo_reference(case, expected),
        )
        assert len(findings) == expected["expected_finding_count"], case_number
        assert _finding_categories(findings) == expected["expected_finding_categories"], case_number
        assert _aggregate_internal(result) == expected["expected_internal_verdict"], case_number

        if expected["expected_external_verdict"] == "not_performed":
            assert not any(line.check_type == "python_vs_accounts" for line in result.lines)
            continue
        context_verdict = "mismatch" if case_number == 3 else "match"
        _, external, _ = preview_reconciliation(
            result,
            internal_pct_threshold=0.01,
            internal_absolute_threshold=100.0,
            external_pct_threshold=0.01,
            external_absolute_threshold=100.0,
            default_pct_threshold=0.01,
            default_absolute_threshold=100.0,
            context_match_verdict=context_verdict,
            control_total_verdict="not_checked",
        )
        assert external == expected["expected_external_verdict"], case_number


def test_demo_acceptance_tests_do_not_mock_parser_or_reconciliation_path():
    paths = (
        ROOT / "tests" / "test_demo_cases.py",
        ROOT / "tests" / "test_evidence_integrity.py",
    )
    sources = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert "parse_workbook(" in sources
    assert "run_reconciliation(" in sources
    forbidden_targets = {"parse_workbook", "run_reconciliation"}
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function_name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            if function_name != "patch":
                continue
            patched_names = {
                value.rsplit(".", 1)[-1]
                for argument in node.args
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
                for value in (argument.value,)
            }
            assert not patched_names & forbidden_targets, path


def test_round_semantics_match_excel_not_python_bankers_rounding():
    assert excel_round(2.5, 0) == 3
    assert excel_round(-2.5, 0) == -3
    catalogue_section = README.split("## Supported Formula Catalogue", 1)[1].split(
        "## Deployment Posture", 1
    )[0]
    assert "away from zero" in catalogue_section
    assert "banker's rounding" not in catalogue_section


def test_unsupported_and_roadmap_sections_do_not_name_supported_functions_as_unsupported():
    out_of_scope = README.split("**Out of scope:**", 1)[1].split("## Deployment Posture", 1)[0]
    roadmap = README.split("## Roadmap", 1)[1].split("## Licence", 1)[0]
    named = {
        token
        for token in re.findall(r"`([A-Z][A-Z0-9.]*)`", out_of_scope + roadmap)
    }
    contradiction = sorted(named & SUPPORTED_FUNCTIONS)
    assert contradiction == [], f"README still calls supported functions unsupported: {contradiction}"


def test_documentation_does_not_freeze_a_passing_test_count():
    for path in (ROOT / "README.md", ROOT / "AGENTS.md"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\b\d+\s+tests?\s+passed\b", text, flags=re.IGNORECASE), path


def test_benchmark_evidence_exists_without_ci_timing_thresholds():
    report = (ROOT / "benchmark" / "BENCHMARK_REPORT.md").read_text(encoding="utf-8")
    assert (ROOT / "benchmark" / "summary.csv").exists()
    assert list((ROOT / "benchmark" / "runs").glob("run_*.json"))
    assert "## Limitations" in report
    for path in (ROOT / "tests").glob("test_*.py"):
        if path == Path(__file__):
            continue
        source = path.read_text(encoding="utf-8")
        assert "total_seconds <" not in source
        assert "parse_seconds <" not in source
