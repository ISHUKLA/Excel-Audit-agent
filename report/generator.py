"""Renders the Jinja2 template to a signed, timestamped PDF via weasyprint; disabled until Gate 4 sign-off."""

from datetime import datetime, timezone
from pathlib import Path

import weasyprint
from jinja2 import Environment, FileSystemLoader, select_autoescape

from core.models import AuditReport

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def generate_report_pdf(report: AuditReport, decisions: list[dict]) -> bytes:
    if report.signed_by is None or report.signed_at is None:
        raise ValueError("Cannot generate a report PDF before Gate 4 sign-off")

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("report.html")

    internal_lines = [line for line in report.reconciliation if line.check_type == "excel_vs_python"]
    external_lines = [line for line in report.reconciliation if line.check_type == "python_vs_accounts"]

    html = template.render(
        report=report,
        decisions=decisions,
        internal_lines=internal_lines,
        external_lines=external_lines,
        generated_at=datetime.now(timezone.utc),
    )

    return weasyprint.HTML(string=html).write_pdf()
