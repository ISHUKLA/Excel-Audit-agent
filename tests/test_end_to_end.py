"""Acceptance test: builds a real (small) Excel file and runs it through the full orchestrator
pipeline, all four human gates, and PDF generation -- both with and without reference figures.
If this test passes, the actuary, CFO, and auditor paths all work end to end.
"""

import json
import os
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from types import SimpleNamespace

import openpyxl
import pytest

from agents.orchestrator import Orchestrator
from core.models import FileContext, ReferenceFigures
from report.generator import generate_report_pdf

_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
ET.register_namespace("", _NS)


def _inject_cached_values(path: str, values_by_tab: dict[str, dict[str, float]]) -> None:
    """openpyxl never computes formulas, so a workbook it saves has no cached value for any
    formula cell. Real Excel-saved files do. This patches the saved .xlsx's XML directly to add
    them, so agents/parser.py's cached_values (and therefore Agent 3's reconciliation) has real
    numbers to work with, just like it would against a file a human actually opened in Excel."""
    with zipfile.ZipFile(path) as archive:
        workbook_xml = archive.read("xl/workbook.xml")
        rels_xml = archive.read("xl/_rels/workbook.xml.rels")

    rel_id_to_target = {
        rel.get("Id"): rel.get("Target").lstrip("/")
        for rel in ET.fromstring(rels_xml).findall(f"{{{_REL_NS}}}Relationship")
    }
    name_to_sheet_file = {
        sheet.get("name"): rel_id_to_target[sheet.get(f"{{{_R_NS}}}id")]
        for sheet in ET.fromstring(workbook_xml).findall(f"{{{_NS}}}sheets/{{{_NS}}}sheet")
    }
    # Target is already zip-root-relative once the leading "/" is stripped (e.g.
    # "xl/worksheets/sheet2.xml"), so it's used as-is -- no extra "xl/" prefix.
    sheet_file_values = {
        name_to_sheet_file[tab]: cell_values for tab, cell_values in values_by_tab.items()
    }

    tmp_path = path + ".tmp"
    with zipfile.ZipFile(path, "r") as zin, zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.namelist():
            data = zin.read(item)
            if item in sheet_file_values:
                root = ET.fromstring(data)
                for c in root.iter(f"{{{_NS}}}c"):
                    ref = c.get("r")
                    if ref in sheet_file_values[item]:
                        v = c.find(f"{{{_NS}}}v")
                        if v is None:
                            v = ET.SubElement(c, f"{{{_NS}}}v")
                        v.text = str(sheet_file_values[item][ref])
                data = ET.tostring(root, xml_declaration=True, encoding="UTF-8")
            zout.writestr(item, data)
    os.replace(tmp_path, path)


def _make_workbook(path: str) -> None:
    wb = openpyxl.Workbook()

    hypotheses = wb.active
    hypotheses.title = "Hypotheses"
    hypotheses["B12"] = 1.75
    hypotheses["B13"] = "TH00-02"

    provisions = wb.create_sheet("Provisions")
    provisions["B5"] = "Provisions total"  # label so Pass 1/2 have something to match against
    provisions["C5"] = "=Hypotheses!B12*1000"
    provisions["C6"] = "=C5*0.035"

    controls = wb.create_sheet("Controls")
    controls["D1"] = "=SUM(Provisions!C5:C6)"

    wb.save(path)

    # Real Excel-computed values, so agents/parser.py's cached_values has something to reconcile.
    _inject_cached_values(
        path,
        {
            "Provisions": {"C5": 1750.0, "C6": 61.25},
            "Controls": {"D1": 1811.25},
        },
    )


def _file_context() -> FileContext:
    return FileContext(
        filename="reserves.xlsx",
        description=(
            "This file calculates life insurance reserves; the Provisions tab holds the "
            "final reserve calculation."
        ),
        user_role="actuary",
        uploaded_at=datetime.now(),
    )


def _reference_figures() -> ReferenceFigures:
    # 1750.0 is the real Python-computed value for Provisions!C5 -- this is deliberately
    # close (0.5%) but not exact, to land in the "warn" band under the default 1% threshold.
    return ReferenceFigures(
        source_label="Q4 trial balance extract",
        line_items={"Provisions total": 1758.75},
        uploaded_at=datetime.now(),
    )


class _FakeMessagesAPI:
    def create(self, **kwargs):
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    text=json.dumps(
                        {
                            "method_summary": "Reserve calculation derived from a technical hypothesis.",
                            "assumptions": ["1.75% technical rate (TH00-02)"],
                            "data_sources": ["Hypotheses tab"],
                            "anomalies_noted": [],
                            "role_notes": "Actuary should confirm the technical rate is current.",
                        }
                    )
                )
            ]
        )


class _FakeClient:
    def __init__(self, *args, **kwargs):
        self.messages = _FakeMessagesAPI()


@pytest.fixture
def workbook_path(tmp_path) -> str:
    path = tmp_path / "reserves.xlsx"
    _make_workbook(str(path))
    return str(path)


def test_full_pipeline_with_reference_figures(workbook_path, tmp_path):
    from core.audit_log import AuditLog

    orchestrator = Orchestrator(
        audit_log=AuditLog(str(tmp_path / "audit.db")), documentation_client=_FakeClient()
    )

    # Step 3: run the pipeline through Gate 1 -> Agent 1 -> Agent 2.
    report_id, findings = orchestrator.run(
        workbook_path,
        _file_context(),
        user_name="Apoorva Ranjan",
        context_confirmed=True,
        reference_figures=_reference_figures(),
        reference_figures_confirmed=True,
    )

    # --- Step 4 assertions ---
    parsed_file = orchestrator._state[report_id]["parsed_file"]
    assert set(parsed_file.tab_names) == {"Hypotheses", "Provisions", "Controls"}

    literal_findings = [f for f in findings if "0.035" in f.description]
    assert literal_findings, "expected a hardcoded-literal finding for 0.035 in Provisions!C6"
    assert literal_findings[0].tab == "Provisions"
    assert literal_findings[0].cell_ref == "C6"

    # "Cross-tab reference" is captured by the parser's dependency graph (Step 4), not by the
    # anomaly detector -- Step 5's four categories don't include "tab A references tab B" as an
    # anomaly, since a plain cross-tab reference isn't itself a problem.
    assert "Hypotheses" in parsed_file.dependency_graph.get("Provisions", [])

    # Gate 2 -> Agent 3 (reconciliation).
    decided = [f.model_copy(update={"human_decision": "confirmed"}) for f in findings]
    lines, unmatched = orchestrator.submit_findings_decisions(report_id, decided)

    internal_lines = [line for line in lines if line.check_type == "excel_vs_python"]
    external_lines = [line for line in lines if line.check_type == "python_vs_accounts"]
    assert internal_lines, "expected at least one excel_vs_python line"
    assert external_lines, "expected at least one python_vs_accounts line"
    assert unmatched == []

    # Gate 3 -> traceability -> Agent 4 (mocked) -> assembled AuditReport.
    internal_verdict, external_verdict = orchestrator.submit_reconciliation_decisions(
        report_id, lines, internal_threshold=0.01, external_threshold=0.01, user_name="Apoorva Ranjan"
    )
    assert external_verdict == "warn"  # 0.5% deviation, within [0.1%, 1%)

    report = orchestrator.get_report(report_id)

    provisions_entries = [
        e
        for e in report.traceability_index
        if e.report_figure_label == "Provisions total"
        and e.source_tab == "Provisions"
        and e.source_cell is not None
    ]
    assert provisions_entries, "expected a traceability entry for 'Provisions total' sourced from Provisions"

    assert len(report.documentation) == 3
    assert {doc.tab_name for doc in report.documentation} == {"Hypotheses", "Provisions", "Controls"}

    assert report.internal_verdict in ("pass", "warn", "block", "not_performed")
    assert report.external_verdict in ("pass", "warn", "block", "not_performed")
    assert report.overall_verdict in ("pass", "warn", "block")

    # Step 6: simulate Gate 4 sign-off.
    signed_report = orchestrator.submit_signoff(report_id, signed_by="Apoorva Ranjan", role="actuary")

    # Step 7: final report is signed, overall_verdict is set.
    assert signed_report.signed_by == "Apoorva Ranjan"
    assert signed_report.signed_at is not None
    assert signed_report.overall_verdict in ("pass", "warn", "block")

    # Bonus: the whole point of building this thing -- a real PDF comes out the other end.
    decisions = orchestrator.get_decisions(report_id)
    pdf_bytes = generate_report_pdf(signed_report, decisions)
    assert pdf_bytes.startswith(b"%PDF")


def test_full_pipeline_without_reference_figures(workbook_path, tmp_path):
    from core.audit_log import AuditLog

    orchestrator = Orchestrator(
        audit_log=AuditLog(str(tmp_path / "audit_no_ref.db")), documentation_client=_FakeClient()
    )

    report_id, findings = orchestrator.run(
        workbook_path,
        _file_context(),
        user_name="Apoorva Ranjan",
        context_confirmed=True,
        reference_figures=None,
        reference_figures_confirmed=True,
    )

    decided = [f.model_copy(update={"human_decision": "confirmed"}) for f in findings]
    lines, unmatched = orchestrator.submit_findings_decisions(report_id, decided)

    assert not any(line.check_type == "python_vs_accounts" for line in lines)
    assert unmatched == []

    internal_verdict, external_verdict = orchestrator.submit_reconciliation_decisions(
        report_id, lines, internal_threshold=0.01, external_threshold=0.01, user_name="Apoorva Ranjan"
    )

    # Never allowed to silently read as "pass" -- accounts reconciliation was never attempted.
    assert external_verdict == "not_performed"

    report = orchestrator.get_report(report_id)
    assert report.external_verdict == "not_performed"
    assert report.reference_figures is None
