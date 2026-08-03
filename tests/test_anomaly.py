"""Tests for agents/anomaly_detector.py: clean-fixture and messy-input anomaly detection."""

from agents.anomaly_detector import detect_anomalies
from core.models import ParsedFile


def _parsed_file(**overrides) -> ParsedFile:
    defaults = dict(
        tab_names=["TabA"],
        cells={},
        cached_values={},
        named_ranges={},
        external_links=[],
        has_vba=False,
        dependency_graph={"TabA": []},
        warnings=[],
    )
    defaults.update(overrides)
    return ParsedFile(**defaults)


def test_detects_hardcoded_literals_but_allows_0_1_100():
    parsed = _parsed_file(
        cells={
            "TabA!B1": "=A1*1.75",
            "TabA!B2": "=SUM(B1:B10)*0.035",
            "TabA!B3": "=A1*100",
            "TabA!B4": "=A1+0",
            "TabA!B5": "=A1*1",
        },
    )

    findings = detect_anomalies(parsed)
    literal_findings = [f for f in findings if "Hardcoded literal" in f.description]

    assert len(literal_findings) == 2
    descriptions = " | ".join(f.description for f in literal_findings)
    assert "1.75" in descriptions
    assert "0.035" in descriptions
    assert all(f.severity == "warning" for f in literal_findings)


def test_detects_cross_tab_inconsistency_in_named_ranges():
    parsed = _parsed_file(
        tab_names=["TabA", "TabB"],
        cells={"TabA!B5": 1.75, "TabB!B5": 1.80},
        named_ranges={
            "TabA::taux_technique": "TabA!$B$5",
            "TabB::taux_technique": "TabB!$B$5",
        },
        dependency_graph={"TabA": [], "TabB": []},
    )

    findings = detect_anomalies(parsed)
    blockers = [f for f in findings if "taux_technique" in f.description]

    assert len(blockers) == 1
    assert blockers[0].severity == "blocker"
    assert "1.75" in blockers[0].description
    assert "1.8" in blockers[0].description


def test_consistent_named_range_values_do_not_flag():
    parsed = _parsed_file(
        tab_names=["TabA", "TabB"],
        cells={"TabA!B5": 1.75, "TabB!B5": 1.75},
        named_ranges={
            "TabA::taux_technique": "TabA!$B$5",
            "TabB::taux_technique": "TabB!$B$5",
        },
        dependency_graph={"TabA": [], "TabB": []},
    )

    findings = detect_anomalies(parsed)
    assert not any("taux_technique" in f.description for f in findings)


def test_detects_excluded_rows_in_sum():
    parsed = _parsed_file(
        cells={"TabA!C1": "=SUM(A1:A5,A7:A10)"},
    )

    findings = detect_anomalies(parsed)
    sum_findings = [f for f in findings if "skips rows" in f.description]

    assert len(sum_findings) == 1
    assert "A6" in sum_findings[0].description
    assert sum_findings[0].severity == "warning"


def test_contiguous_sum_ranges_do_not_flag():
    parsed = _parsed_file(
        cells={"TabA!C1": "=SUM(A1:A5,A6:A10)"},
    )

    findings = detect_anomalies(parsed)
    assert not any("skips rows" in f.description for f in findings)


def test_detects_circular_tab_reference():
    parsed = _parsed_file(
        tab_names=["TabA", "TabB"],
        dependency_graph={"TabA": ["TabB"], "TabB": ["TabA"]},
    )

    findings = detect_anomalies(parsed)
    cycle_findings = [f for f in findings if "Circular reference" in f.description]

    assert len(cycle_findings) == 1
    assert cycle_findings[0].severity == "blocker"


def test_acyclic_dependency_graph_does_not_flag():
    parsed = _parsed_file(
        tab_names=["TabA", "TabB"],
        dependency_graph={"TabA": ["TabB"], "TabB": []},
    )

    findings = detect_anomalies(parsed)
    assert not any("Circular reference" in f.description for f in findings)


def test_findings_ranked_blockers_first_with_sequential_ids_and_undecided_state():
    parsed = _parsed_file(
        tab_names=["TabA", "TabB"],
        cells={"TabA!B1": "=A1*1.75"},
        dependency_graph={"TabA": ["TabB"], "TabB": ["TabA"]},
    )

    findings = detect_anomalies(parsed)

    ranks = [{"blocker": 0, "warning": 1, "info": 2}[f.severity] for f in findings]
    assert ranks == sorted(ranks)
    assert [f.finding_id for f in findings] == [f"F{i:04d}" for i in range(1, len(findings) + 1)]
    assert all(f.human_decision is None for f in findings)
    assert all(f.decided_by is None for f in findings)
