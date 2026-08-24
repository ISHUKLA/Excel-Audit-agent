"""Step 12 acceptance tests for the complete staged audit journey.

These tests use a real recalculated workbook and the real parser, anomaly
detector, reconciliation, gates, state store, traceability builder, report
assembly, and PDF generator.  Only Anthropic is replaced with a deterministic
fake; no test in this module can make a live LLM call.
"""

import json
import pathlib
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import openpyxl
import pytest

import agents.documentation as documentation_module
from agents.orchestrator import Orchestrator
from agents.reconciliation import calculate_delta
from core.audit_log import AuditLog
from core.gates import GateBlockedError
from core.workbook_identity import WorkbookIdentityError, sha256_bytes
from core.models import (
    FileContext,
    MappingReviewDecision,
    ReferenceFigureLine,
    ReferenceFigures,
)
from core.state_store import StateStore
from report.generator import generate_report_pdf

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"

ACTOR = "Isaac Shukla"
NOW = datetime.now(timezone.utc)
DEFAULT_PCT = 0.01
DEFAULT_ABSOLUTE = 100.0
LONG_PRIVATE_NOTE = (
    "Named-customer commentary deliberately longer than forty characters and withheld."
)


class _FakeMessagesAPI:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="text",
                    text=json.dumps(
                        {
                            "method_summary": "Documents the tab's calculation structure.",
                            "assumptions": ["Human review remains required."],
                            "data_sources": ["Minimized workbook payload"],
                            "anomalies_noted": [],
                            "role_notes": "Review formulas and source-cell lineage.",
                        }
                    ),
                )
            ]
        )


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessagesAPI()


@pytest.fixture(scope="module")
def workbook_path(tmp_path_factory) -> str:
    """Copy the static recalculated reserves.xlsx fixture to a temp location."""
    fixture_path = FIXTURES_DIR / "reserves.xlsx"
    if not fixture_path.exists():
        raise FileNotFoundError(f"Fixture not found: {fixture_path}")

    dest = tmp_path_factory.mktemp("step12_workbook") / "reserves.xlsx"
    dest.write_bytes(fixture_path.read_bytes())
    return str(dest)


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    # Agent 4 normally spaces calls by one second. The fake has no rate limit.
    monkeypatch.setattr(documentation_module.time, "sleep", lambda _: None)
    db_path = str(tmp_path / "audit.db")
    audit_log = AuditLog(db_path)
    state_store = StateStore(db_path, audit_log=audit_log)
    client = _FakeClient()
    orchestrator = Orchestrator(
        audit_log=audit_log,
        state_store=state_store,
        documentation_client=client,
        code_version="step-12-acceptance",
    )
    return orchestrator, audit_log, state_store, client


def _file_context(confirmed_workbook_hash: str = "a" * 64) -> FileContext:
    return FileContext(
        filename="reserves.xlsx",
        description="Q4 life-insurance provision calculation and control total.",
        user_role="actuary",
        entity="Acme Life SA",
        period="2025-Q4",
        currency="EUR",
        basis="IFRS 17",
        confirmed_workbook_hash=confirmed_workbook_hash,
        uploaded_at=NOW,
    )


def _reference_figures(period: str = "2025-Q4") -> ReferenceFigures:
    common = {
        "entity": "Acme Life SA",
        "period": period,
        "currency": "EUR",
        "ledger_source": "Q4 trial balance extract",
        "debit_credit": "debit",
    }
    return ReferenceFigures(
        source_label="Q4 trial balance extract",
        entity="Acme Life SA",
        period=period,
        currency="EUR",
        basis="IFRS 17",
        lines=[
            ReferenceFigureLine(
                line_id="L1",
                account_number="3000",
                label="Provisions total",
                amount=1755.0,
                evidence_ref="trial-balance.csv row 2",
                **common,
            ),
            ReferenceFigureLine(
                line_id="L2",
                account_number="3001",
                label="Provisions total",
                amount=500.0,
                evidence_ref="trial-balance.csv row 3",
                **common,
            ),
            ReferenceFigureLine(
                line_id="L3",
                account_number="3200",
                label="Reserve for claims",
                amount=9999.0,
                evidence_ref="trial-balance.csv row 4",
                **common,
            ),
        ],
        uploaded_at=NOW,
    )


def _review(findings):
    return [
        finding.model_copy(
            update={
                "human_decision": "confirmed",
                "human_reason": "Reviewed for the Step 12 acceptance run.",
                "decided_by": ACTOR,
                "decided_at": NOW,
            }
        )
        for finding in findings
    ]


def _start_to_gate3(orchestrator, workbook_path, reference_figures):
    workbook_bytes = pathlib.Path(workbook_path).read_bytes()
    confirmed_hash = sha256_bytes(workbook_bytes)
    report_id, parsed_file, findings = orchestrator.run(
        workbook_bytes,
        _file_context(confirmed_workbook_hash=confirmed_hash),
        reference_figures,
        expected_workbook_hash=confirmed_hash,
        context_confirmed=True,
        actor=ACTOR,
    )
    label, preview = orchestrator.submit_gate2_decisions(
        report_id,
        _review(findings),
        ["Provisions!C5", "Controls!D1"],
        actor=ACTOR,
    )
    assert label == "Preview, pending Gate 3 approval"
    return report_id, parsed_file, findings, preview


def _mapping_decisions(preview):
    return [
        MappingReviewDecision(mapping_id=mapping.mapping_id, action="approve")
        for mapping in preview.mappings
    ]


def _submit_gate3(
    orchestrator,
    report_id,
    preview,
    *,
    external_pct=DEFAULT_PCT,
    external_reason=None,
):
    return orchestrator.submit_gate3_decisions(
        report_id,
        preview,
        mapping_decisions=_mapping_decisions(preview),
        internal_pct_threshold=DEFAULT_PCT,
        internal_absolute_threshold=DEFAULT_ABSOLUTE,
        external_pct_threshold=external_pct,
        external_absolute_threshold=DEFAULT_ABSOLUTE,
        internal_threshold_deviation_reason=None,
        external_threshold_deviation_reason=external_reason,
        actor=ACTOR,
        acknowledge_incomplete=True,
    )


def _line(result, check_type, *, mapping_id=None, output_ref=None):
    for line in result.lines:
        if line.check_type != check_type:
            continue
        if mapping_id is not None and line.mapping_id != mapping_id:
            continue
        if output_ref is not None and not any(
            step.cell_ref == output_ref for step in line.derivation
        ):
            continue
        return line
    raise AssertionError(
        f"missing {check_type} line for mapping={mapping_id!r}, output={output_ref!r}"
    )


def test_full_pipeline_mapping_traceability_reporting_and_evidence_integrity(
    workbook_path, pipeline
):
    orchestrator, audit_log, state_store, client = pipeline
    report_id, parsed_file, findings, preview = _start_to_gate3(
        orchestrator, workbook_path, _reference_figures()
    )

    # Gate 1 and the real parser/anomaly detector.
    assert orchestrator.get_context_match_verdict(report_id) == "match"
    assert parsed_file.tab_names == ["Hypotheses", "Provisions", "Controls"]
    assert any("0.035" in finding.raw_value for finding in findings)
    assert parsed_file.cells["Provisions!C5"].cached_value == pytest.approx(1750.0)
    assert parsed_file.cells["Provisions!C6"].cached_value == pytest.approx(61.25)
    assert parsed_file.cells["Provisions!C7"].is_error is True
    assert parsed_file.cells["Controls!D1"].is_error is True

    # Agent 3 preview: reconstruction, proposals, duplicate preservation, and
    # bidirectional completeness.
    internal_c5 = _line(preview, "excel_vs_python", output_ref="Provisions!C5")
    internal_control = _line(preview, "excel_vs_python", output_ref="Controls!D1")
    assert internal_c5.completeness == "complete"
    assert internal_control.completeness == "partial"
    assert internal_control.verdict == "incomplete"

    assert len(preview.mappings) == 1
    mapping = preview.mappings[0]
    assert mapping.python_output_cell_ref == "Provisions!C5"
    assert mapping.reference_line_id == "L1"
    assert mapping.is_approved is False
    assert not any(item.is_approved for item in preview.mappings)

    preliminary = _line(preview, "python_vs_accounts", mapping_id=mapping.mapping_id)
    assert preliminary.delta == pytest.approx(5.0)
    assert preliminary.delta_pct == pytest.approx(5.0 / 1755.0)
    assert preliminary.verdict == "warn"
    assert preview.verdicts_are_final is False
    assert preview.unmatched_reference_items == ["L2", "L3"]
    assert preview.unmapped_python_outputs == ["Controls!D1"]
    assert calculate_delta(0.0, 10.0) == (10.0, 1.0)

    # Destroy process-local state and recover the evidence saved after Gate 2.
    recovered_snapshot = state_store.load_latest_snapshot(report_id)
    recovered_json = json.loads(recovered_snapshot.state_json)
    assert recovered_json["authoritative_outputs"] == ["Provisions!C5", "Controls!D1"]
    assert recovered_json["findings"] == [
        finding.model_dump(mode="json") for finding in _review(findings)
    ]
    recovered = Orchestrator(
        audit_log=audit_log,
        state_store=state_store,
        documentation_client=client,
        code_version="step-12-acceptance",
    )
    recovered.resume(report_id)
    _, preview = recovered.get_reconciliation_preview(report_id)

    # An unapproved mapping and incomplete populations can never read as pass.
    _, unapproved_external, unapproved_preview = recovered.preview_gate3_decisions(
        report_id,
        preview,
        mapping_decisions=[],
        internal_pct_threshold=DEFAULT_PCT,
        internal_absolute_threshold=DEFAULT_ABSOLUTE,
        external_pct_threshold=DEFAULT_PCT,
        external_absolute_threshold=DEFAULT_ABSOLUTE,
        actor=ACTOR,
    )
    assert unapproved_external in {"incomplete", "block"}
    assert not any(
        line.check_type == "python_vs_accounts" for line in unapproved_preview.lines
    )
    with pytest.raises(GateBlockedError, match="mapping proposals still need"):
        recovered.submit_gate3_decisions(
            report_id,
            preview,
            mapping_decisions=[],
            internal_pct_threshold=DEFAULT_PCT,
            internal_absolute_threshold=DEFAULT_ABSOLUTE,
            external_pct_threshold=DEFAULT_PCT,
            external_absolute_threshold=DEFAULT_ABSOLUTE,
            internal_threshold_deviation_reason=None,
            external_threshold_deviation_reason=None,
            actor=ACTOR,
            acknowledge_incomplete=True,
        )

    internal_verdict, external_verdict, final_result = _submit_gate3(
        recovered, report_id, preview
    )
    approved = next(item for item in final_result.mappings if item.mapping_id == mapping.mapping_id)
    final_external = _line(
        final_result, "python_vs_accounts", mapping_id=mapping.mapping_id
    )
    assert approved.is_approved is True
    assert approved.approved_by == ACTOR
    assert final_external.verdict == "warn"
    assert final_result.verdicts_are_final is True
    assert (internal_verdict, external_verdict) == ("incomplete", "incomplete")

    # Traceability preserves the exact derivation and the accounting evidence.
    report = recovered.get_report(report_id)
    excel_trace = next(
        entry
        for entry in report.traceability_index
        if entry.report_figure_label == internal_c5.label
        and entry.accounting_provenance is None
        and entry.derivation == internal_c5.derivation
    )
    assert len(excel_trace.derivation) >= 2
    assert excel_trace.derivation == internal_c5.derivation
    accounts_trace = next(
        entry
        for entry in report.traceability_index
        if entry.accounting_provenance is not None
        and entry.accounting_provenance.reference_line_id == "L1"
    )
    assert accounts_trace.accounting_provenance.account_number == "3000"
    assert accounts_trace.accounting_provenance.ledger_source == "Q4 trial balance extract"
    assert accounts_trace.accounting_provenance.approved_by == ACTOR

    # Agent 4 is mocked, but minimization, manifests, report assembly, and the
    # report generator are real.
    assert len(report.documentation) == 3
    assert {item.tab_name for item in report.documentation} == {
        "Hypotheses",
        "Provisions",
        "Controls",
    }
    assert len(report.llm_data_manifest) == 3
    assert any(
        manifest.exclusion_reasons.get("Hypotheses!A1", "").startswith("free text")
        for manifest in report.llm_data_manifest
    )
    assert LONG_PRIVATE_NOTE not in json.dumps(client.messages.calls)
    assert report.internal_verdict == "incomplete"
    assert report.external_verdict == "incomplete"
    assert report.translation_and_reconciliation_verdict == "incomplete"
    assert len(report.workbook_hash) == 64
    assert "does not constitute actuarial validation" in report.disclaimer
    assert any(item.is_approved for item in report.mappings)

    final_report = recovered.submit_approval_record(report_id, ACTOR, "actuary")
    assert final_report.report_approval_name == ACTOR
    assert final_report.report_approval_at is not None
    assert "No independent review was performed" in final_report.independence_disclosure
    pdf_bytes = generate_report_pdf(final_report, recovered.get_audit_rows(report_id))
    assert pdf_bytes.startswith(b"%PDF")

    # The chain is intact after the full run; a raw database edit becomes
    # detectable after the append-only trigger is deliberately removed.
    assert audit_log.verify_chain() == (True, [])
    tampered_row_id = recovered.get_audit_rows(report_id)[1]["row_id"]
    with sqlite3.connect(audit_log.db_path) as connection:
        connection.execute("DROP TRIGGER log_rows_no_update")
        connection.execute(
            "UPDATE log_rows SET payload_json = ? WHERE row_id = ?",
            ('{"tampered":true}', tampered_row_id),
        )
    assert audit_log.verify_chain() == (False, [str(tampered_row_id)])


def test_gate3_recomputes_looser_and_stricter_thresholds(workbook_path, tmp_path, monkeypatch):
    monkeypatch.setattr(documentation_module.time, "sleep", lambda _: None)

    # A looser approved threshold turns the €5 / 0.285% line from warn to pass.
    loose_log = AuditLog(str(tmp_path / "loose.db"))
    loose = Orchestrator(
        audit_log=loose_log,
        documentation_client=_FakeClient(),
        code_version="step-12-acceptance",
    )
    report_id, _, _, preview = _start_to_gate3(loose, workbook_path, _reference_figures())
    _, external, loose_result = _submit_gate3(
        loose,
        report_id,
        preview,
        external_pct=0.03,
        external_reason="CFO-approved looser threshold for the acceptance scenario.",
    )
    loose_mapping = next(item for item in loose_result.mappings if item.is_approved)
    assert _line(
        loose_result, "python_vs_accounts", mapping_id=loose_mapping.mapping_id
    ).verdict == "pass"
    assert external == "incomplete"  # the remaining population gaps still cap the result
    gate3_payload = next(
        json.loads(row["payload_json"])
        for row in reversed(loose_log.get_rows(report_id))
        if json.loads(row["payload_json"]).get("gate") == 3
    )
    assert gate3_payload["threshold_deviation"]["external"]["deviated"] is True
    assert gate3_payload["threshold_deviation"]["external"]["reason"]

    # A stricter approved threshold turns that same comparison into a blocker.
    strict_log = AuditLog(str(tmp_path / "strict.db"))
    strict = Orchestrator(
        audit_log=strict_log,
        documentation_client=_FakeClient(),
        code_version="step-12-acceptance",
    )
    strict_id, _, _, strict_preview = _start_to_gate3(
        strict, workbook_path, _reference_figures()
    )
    approved_preview = strict.preview_gate3_decisions(
        strict_id,
        strict_preview,
        mapping_decisions=_mapping_decisions(strict_preview),
        internal_pct_threshold=DEFAULT_PCT,
        internal_absolute_threshold=DEFAULT_ABSOLUTE,
        external_pct_threshold=0.001,
        external_absolute_threshold=DEFAULT_ABSOLUTE,
        actor=ACTOR,
    )[2]
    strict_mapping = next(item for item in approved_preview.mappings if item.is_approved)
    assert _line(
        approved_preview, "python_vs_accounts", mapping_id=strict_mapping.mapping_id
    ).verdict == "block"
    with pytest.raises(GateBlockedError, match="external_verdict=block"):
        _submit_gate3(
            strict,
            strict_id,
            strict_preview,
            external_pct=0.001,
            external_reason="CFO-approved stricter threshold for the acceptance scenario.",
        )


def test_context_mismatch_blocks_external_reconciliation(workbook_path, tmp_path):
    audit_log = AuditLog(str(tmp_path / "mismatch.db"))
    orchestrator = Orchestrator(
        audit_log=audit_log,
        documentation_client=_FakeClient(),
        code_version="step-12-acceptance",
    )
    report_id, _, _, preview = _start_to_gate3(
        orchestrator, workbook_path, _reference_figures(period="2025-Q3")
    )
    assert orchestrator.get_context_match_verdict(report_id) == "mismatch"
    with pytest.raises(GateBlockedError, match="external_verdict=block"):
        _submit_gate3(orchestrator, report_id, preview)

    payloads = [json.loads(row["payload_json"]) for row in audit_log.get_rows(report_id)]
    gate3 = next(payload for payload in reversed(payloads) if payload.get("gate") == 3)
    assert gate3["context_match_verdict"] == "mismatch"
    assert gate3["external_verdict"] == "block"


def test_pipeline_without_reference_figures_is_explicitly_not_performed(
    workbook_path, tmp_path, monkeypatch
):
    monkeypatch.setattr(documentation_module.time, "sleep", lambda _: None)
    audit_log = AuditLog(str(tmp_path / "no-reference.db"))
    orchestrator = Orchestrator(
        audit_log=audit_log,
        documentation_client=_FakeClient(),
        code_version="step-12-acceptance",
    )
    report_id, _, _, preview = _start_to_gate3(orchestrator, workbook_path, None)

    assert orchestrator.get_context_match_verdict(report_id) == "not_checked"
    assert not any(line.check_type == "python_vs_accounts" for line in preview.lines)
    assert preview.mappings == []
    assert preview.unmatched_reference_items == []
    assert preview.unmapped_python_outputs == []

    _, external, final_result = _submit_gate3(orchestrator, report_id, preview)
    assert external == "not_performed"
    assert not any(line.check_type == "python_vs_accounts" for line in final_result.lines)
    report = orchestrator.get_report(report_id)
    assert report.external_verdict == "not_performed"
    assert report.context_match_verdict == "not_checked"


def test_a_substituted_workbook_is_refused_before_any_work_happens(
    workbook_path, tmp_path, monkeypatch
):
    """Acceptance-level proof of Recommendation 2: a reviewer confirms one
    workbook, a different one with the same name arrives, and the run stops
    before parsing, before Gate 1 is recorded, and before any snapshot."""
    monkeypatch.setattr(documentation_module.time, "sleep", lambda _: None)
    audit_log = AuditLog(str(tmp_path / "substituted.db"))
    orchestrator = Orchestrator(
        audit_log=audit_log,
        documentation_client=_FakeClient(),
        code_version="step-12-acceptance",
    )

    confirmed_bytes = pathlib.Path(workbook_path).read_bytes()
    confirmed_hash = sha256_bytes(confirmed_bytes)
    substituted_bytes = confirmed_bytes + b"\x00"  # same name, one byte different

    with pytest.raises(WorkbookIdentityError, match="not the one that was confirmed"):
        orchestrator.run(
            substituted_bytes,
            _file_context(confirmed_workbook_hash=confirmed_hash),
            _reference_figures(),
            expected_workbook_hash=confirmed_hash,
            context_confirmed=True,
            actor=ACTOR,
        )

    with sqlite3.connect(audit_log.db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM log_rows").fetchall()
        snapshots = connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]

    # Exactly one row: the blocked attempt. No gate decision, no snapshot.
    assert [row["event_type"] for row in rows] == ["workbook_identity_mismatch"]
    assert snapshots == 0
    payload = json.loads(rows[0]["payload_json"])
    assert payload["confirmed_workbook_hash"] == confirmed_hash
    assert payload["observed_workbook_hash"] == sha256_bytes(substituted_bytes)
    assert payload["outcome"] == "blocked"
    assert audit_log.verify_chain() == (True, [])
