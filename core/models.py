"""Pydantic models that validate every agent's output before it reaches a human gate."""

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel


class FileContext(BaseModel):
    filename: str
    description: str
    user_role: Literal["actuary", "cro", "cfo", "auditor"]
    uploaded_at: datetime


class ReferenceFigures(BaseModel):
    source_label: str
    line_items: dict[str, float]
    uploaded_at: datetime


class ParsedFile(BaseModel):
    tab_names: list[str]
    cells: dict[str, Any]
    cached_values: dict[str, Any]
    named_ranges: dict
    external_links: list[str]
    has_vba: bool
    dependency_graph: dict
    warnings: list[str]


class AnomalyFinding(BaseModel):
    finding_id: str
    severity: Literal["blocker", "warning", "info"]
    tab: str
    cell_ref: str
    description: str
    raw_value: str
    human_decision: Optional[Literal["confirmed", "overridden", "dismissed"]] = None
    human_reason: Optional[str] = None
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None


class ReconciliationLine(BaseModel):
    check_type: Literal["excel_vs_python", "python_vs_accounts"]
    label: str
    source_value: float
    target_value: float
    delta: float
    delta_pct: float
    verdict: Literal["pass", "warn", "block"]
    materiality_threshold: float
    source_cell: Optional[str] = None
    match_note: Optional[str] = None


class TraceabilityEntry(BaseModel):
    report_figure_label: str
    report_value: float
    source_tab: Optional[str] = None
    source_cell: Optional[str] = None
    source_formula: Optional[str] = None
    derivation_note: str


class TabDocumentation(BaseModel):
    tab_name: str
    method_summary: str
    assumptions: list[str]
    data_sources: list[str]
    anomalies_noted: list[str]
    role_notes: str


class AuditReport(BaseModel):
    file_context: FileContext
    reference_figures: Optional[ReferenceFigures] = None
    parsed_file: ParsedFile
    findings: list[AnomalyFinding]
    reconciliation: list[ReconciliationLine]
    unmatched_reference_items: list[str]
    traceability_index: list[TraceabilityEntry]
    documentation: list[TabDocumentation]
    overall_verdict: Literal["pass", "warn", "block"]
    internal_verdict: Literal["pass", "warn", "block", "not_performed"]
    external_verdict: Literal["pass", "warn", "block", "not_performed"]
    signed_by: Optional[str] = None
    signed_at: Optional[datetime] = None
    signed_role: Optional[str] = None
    report_id: str
