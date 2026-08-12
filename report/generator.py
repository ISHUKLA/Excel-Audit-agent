"""Render the Translation & Reconciliation Report as HTML and PDF bytes."""

import json
from pathlib import Path
from typing import Any

import weasyprint
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from core.accounting import signed_reference_amount
from core.models import AuditReport

_TEMPLATE_DIR = Path(__file__).parent / "templates"

ANTHROPIC_RETENTION_URL = (
    "https://privacy.claude.com/en/articles/"
    "7996866-how-long-do-you-store-my-organization-s-data"
)


def generate_report_pdf(report: AuditReport, audit_rows: list[dict]) -> bytes:
    """Return PDF bytes; filesystem persistence belongs to the caller."""
    html = render_report_html(report, audit_rows)
    return weasyprint.HTML(string=html, base_url=str(_TEMPLATE_DIR)).write_pdf()


def render_report_html(report: AuditReport, audit_rows: list[dict]) -> str:
    """Render a fully assembled report model into its reviewable HTML representation."""
    _require_approval_record(report)
    environment = _environment()
    template = environment.get_template("report.html")

    internal_lines = [
        line for line in report.reconciliation if line.check_type == "excel_vs_python"
    ]
    mapping_rows = _mapping_rows(report)
    prepared_audit_rows, acknowledge_incomplete = _prepare_audit_rows(audit_rows)
    stale_cells = [
        record for record in report.parsed_file.cells.values() if record.is_stale
    ]

    return template.render(
        report=report,
        internal_lines=internal_lines,
        approved_mapping_rows=[row for row in mapping_rows if row["is_approved"]],
        proposed_mapping_rows=[row for row in mapping_rows if not row["is_approved"]],
        manual_mapping_rows=[row for row in mapping_rows if row["mapping_type"] != "one_to_one"],
        stale_cells=stale_cells,
        incomplete_pct=_incomplete_percentage(internal_lines),
        audit_rows=prepared_audit_rows,
        acknowledge_incomplete=acknowledge_incomplete,
        anthropic_retention_url=ANTHROPIC_RETENTION_URL,
        short_workbook_hash=f"{report.workbook_hash[:12]}…",
    )


def _require_approval_record(report: AuditReport) -> None:
    missing = []
    if not report.report_approval_name:
        missing.append("report_approval_name")
    if report.report_approval_at is None:
        missing.append("report_approval_at")
    if not report.report_approval_role:
        missing.append("report_approval_role")
    if missing:
        raise ValueError(
            "Cannot generate a report PDF before Gate 4 creates a complete named "
            f"approval record; missing: {', '.join(missing)}"
        )


def _environment() -> Environment:
    environment = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
    )
    environment.filters.update(
        number=_format_number,
        percentage=_format_percentage,
        timestamp=_format_timestamp,
    )
    return environment


def _mapping_rows(report: AuditReport) -> list[dict[str, Any]]:
    lines_by_mapping = {
        line.mapping_id: line
        for line in report.reconciliation
        if line.check_type == "python_vs_accounts" and line.mapping_id
    }
    references_by_id = (
        {line.line_id: line for line in report.reference_figures.lines}
        if report.reference_figures
        else {}
    )
    rows = []
    for mapping in report.mappings:
        line = lines_by_mapping.get(mapping.mapping_id)
        reference = references_by_id.get(mapping.reference_line_id)
        rows.append(
            {
                "mapping_id": mapping.mapping_id,
                "label": line.label if line else mapping.python_output_cell_ref,
                "account_number": reference.account_number if reference else None,
                "python_value": line.source_value if line else None,
                "accounts_value": line.target_value if line else (
                    signed_reference_amount(reference) if reference else None
                ),
                "delta": line.delta if line else None,
                "delta_pct": line.delta_pct if line else None,
                "verdict": line.verdict if line else "incomplete",
                "approved_by": mapping.approved_by,
                "is_approved": mapping.is_approved,
                "source_label": (
                    report.reference_figures.source_label if report.reference_figures else None
                ),
                "suggested_confidence": mapping.suggested_confidence,
                "mapping_type": mapping.mapping_type,
                "approval_note": mapping.approval_note,
                "python_output_cell_ref": mapping.python_output_cell_ref,
                "reference_line_id": mapping.reference_line_id,
            }
        )
    return rows


def _prepare_audit_rows(rows: list[dict]) -> tuple[list[dict[str, Any]], bool | None]:
    prepared = []
    acknowledge_incomplete: bool | None = None
    for row in rows:
        payload = _payload(row.get("payload_json"))
        if payload.get("gate") == 3 and "acknowledge_incomplete" in payload:
            acknowledge_incomplete = bool(payload["acknowledge_incomplete"])
        prepared.append(
            {
                "row_id": row.get("row_id", "-"),
                "event_type": row.get("event_type", "unknown"),
                "gate": payload.get("gate", "-"),
                "action": payload.get("action") or payload.get("outcome") or "-",
                "actor": row.get("actor") or "-",
                "timestamp": row.get("timestamp") or "-",
                "row_hash": row.get("row_hash") or "-",
            }
        )
    return prepared, acknowledge_incomplete


def _payload(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _incomplete_percentage(lines: list) -> float | None:
    incomplete = [
        line for line in lines if line.verdict == "incomplete" or line.completeness == "partial"
    ]
    if not incomplete:
        return None
    return sum(100.0 - line.reconstruction_coverage_pct for line in incomplete) / len(incomplete)


def _format_number(value: Any) -> str:
    if value is None:
        return "not comparable"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _format_percentage(value: Any) -> str:
    if value is None:
        return "not comparable"
    return f"{float(value) * 100:.2f}%"


def _format_timestamp(value: Any) -> str:
    if value is None:
        return "-"
    return value.isoformat() if hasattr(value, "isoformat") else str(value)
