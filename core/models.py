"""Pydantic models that validate every agent's output before it reaches a human gate.

Data shapes only — no logic lives here. Field-level constraints are limited to
those that prevent a whole class of ambiguity from being representable at all
(see `ReferenceFigureLine.amount`).
"""

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class FileContext(BaseModel):
    """What the human says this workbook is, including the accounting context
    needed to confirm the workbook and any reference figures describe the same
    entity, period, currency and basis.

    entity/period/currency/basis are human-entered confirmation fields, not
    validated enums — free text is deliberate for the MVP.
    """

    filename: str
    description: str
    user_role: Literal["actuary", "cro", "cfo", "auditor"]
    entity: Optional[str] = None
    period: Optional[str] = None
    currency: Optional[str] = None
    basis: Optional[str] = None
    uploaded_at: datetime


class ReferenceFigureLine(BaseModel):
    """One line from a GL or trial balance extract.

    Sign is carried by `debit_credit`, never by a negative `amount` — the
    `ge=0` constraint makes the ambiguous alternative unrepresentable rather
    than merely discouraged.
    """

    line_id: str
    account_number: Optional[str] = None
    label: str
    entity: str
    period: str
    currency: str
    ledger_source: str
    debit_credit: Literal["debit", "credit"]
    amount: float = Field(ge=0)
    version: Optional[str] = None
    evidence_ref: Optional[str] = None


class ReferenceFigures(BaseModel):
    """A full extract of accounting figures, with the context needed to check
    it refers to the same thing as the workbook before any line-level matching
    is attempted.

    `control_total_confirmed_by_human` defaults to False and is never assumed
    True — a human confirms the extract ties to `control_total`, or nobody does.
    """

    source_label: str
    entity: str
    period: str
    currency: str
    basis: Optional[str] = None
    control_total: Optional[float] = None
    control_total_confirmed_by_human: bool = False
    lines: list[ReferenceFigureLine]
    uploaded_at: datetime


class AccountMapping(BaseModel):
    """The link between one designated Python output and one reference figure line.

    A fuzzy match can propose one of these; only a human can approve one.
    `suggested_confidence` is shown for context and never determines a verdict,
    and a mapping with `is_approved=False` may be displayed as a proposal but
    must never be counted toward a reconciliation verdict.
    """

    mapping_id: str
    python_output_cell_ref: str
    reference_line_id: str
    mapping_type: Literal[
        "one_to_one", "one_to_many", "many_to_one", "elimination", "not_supported"
    ]
    suggested_by: Literal["fuzzy_match", "human_direct"]
    # 0-100, matching what rapidfuzz returns. Context for a human only.
    suggested_confidence: Optional[float] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    # Required when mapping_type != "one_to_one", explaining the manual treatment.
    approval_note: Optional[str] = None
    is_approved: bool = False


class MappingReviewDecision(BaseModel):
    """One human disposition of an accounting mapping proposal at Gate 3.

    ``edit`` means the reviewer selected a different reference line directly;
    the orchestrator preserves the original proposal and creates a separate
    human-direct mapping. Non-one-to-one mappings can only be acknowledged as
    requiring manual reconciliation, never approved as though an aggregate had
    been computed.
    """

    mapping_id: str
    action: Literal["approve", "reject", "edit", "acknowledge_manual"]
    replacement_reference_line_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_replacement(self):
        if self.action == "edit" and not (self.replacement_reference_line_id or "").strip():
            raise ValueError("an edited mapping requires a replacement reference line")
        if self.action != "edit" and self.replacement_reference_line_id is not None:
            raise ValueError("only an edited mapping may name a replacement reference line")
        return self


class CellRecord(BaseModel):
    """Everything known about one cell.

    Formula and cached value are always captured together, never one instead of
    the other. `cached_value` may legitimately be None when the workbook was
    never recalculated after the formula was entered — that is what `is_stale`
    records, and it is not the same thing as the cell having no formula.
    """

    cell_ref: str
    formula: Optional[str] = None
    cached_value: Optional[float | str | bool] = None
    data_type: Literal["number", "text", "date", "boolean", "error", "blank"]
    number_format: str
    is_error: bool
    error_type: Optional[str] = None
    # True when this is a formula cell with cached_value=None, OR the workbook's
    # calc_mode is "manual". Either way the cached value cannot be trusted.
    is_stale: bool


class WorkbookMeta(BaseModel):
    """Facts about the workbook as a whole, not about any one cell.

    `calc_mode` is "unknown" when it cannot be read — never assumed "automatic".
    """

    calc_mode: Literal["automatic", "manual", "unknown"]
    workbook_hash: str
    app_version: Optional[str] = None
    fully_calculated_on_load: Optional[bool] = None


class DerivationStep(BaseModel):
    """One node in a calculation's dependency chain.

    A list of these, walked from an output cell down to its raw inputs, is the
    lineage. This replaces any notion of locating a source cell by matching a
    value. An unsupported node has resolved_value=None and stops the chain.
    """

    cell_ref: str
    formula: Optional[str] = None
    depends_on: list[str]
    resolved_value: Optional[float] = None
    is_supported: bool


class AccountingProvenance(BaseModel):
    """The accounting-side equivalent of a derivation chain.

    Built directly from the `ReferenceFigureLine` and `AccountMapping` that were
    actually used, never from a fresh lookup. `approved_by` is pulled from that
    mapping and is never blank on an entry that reached a report.
    """

    reference_line_id: str
    account_number: Optional[str] = None
    ledger_source: str
    entity: str
    period: str
    currency: str
    evidence_ref: Optional[str] = None
    mapping_id: str
    approved_by: str


class ParsedFile(BaseModel):
    """Output of Agent 1.

    Two dependency graphs, deliberately kept separate:
    `tab_dependency_graph` is a coarse overview and must NEVER be used for
    circular reference detection — a tab referencing another tab twice by two
    different non-circular cell chains is normal, and a tab-level graph cannot
    tell that apart from a genuine cycle. `cell_dependency_graph` is the fine
    graph used for real cycle detection and for building derivation chains.
    """

    tab_names: list[str]
    cells: dict[str, CellRecord]
    named_ranges: dict
    external_links: list[str]
    has_vba: bool
    workbook_meta: WorkbookMeta
    tab_dependency_graph: dict
    cell_dependency_graph: dict
    warnings: list[str]


class AnomalyFinding(BaseModel):
    """One finding from Agent 2.

    All three human decisions are equally valid dispositions. A dismissed
    finding has been reviewed, not approved.
    """

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
    """One row from Agent 3, used for both passes.

    `check_type` keeps the two passes distinguishable so they can never be
    accidentally merged into a single verdict.

    For check_type="excel_vs_python": source_value is the Excel cached value,
    target_value is the Python-reconstructed value (None if completeness is
    "partial"). For check_type="python_vs_accounts": source_value is Python,
    target_value is the accounts figure from the line named by mapping_id.

    "incomplete" is a distinct verdict, not a flavour of "pass" — it applies
    whenever completeness="partial", or when a python_vs_accounts line rests on
    a mapping that is not approved. An unapproved mapping never produces "pass",
    however close the numbers happen to be.
    """

    check_type: Literal["excel_vs_python", "python_vs_accounts"]
    label: str
    source_value: Optional[float] = None
    target_value: Optional[float] = None
    delta: Optional[float] = None
    delta_pct: Optional[float] = None
    verdict: Literal["pass", "warn", "block", "incomplete"]
    # Both thresholds are always evaluated; the verdict is the worse outcome.
    pct_threshold: float
    absolute_threshold: float
    # False when either threshold was changed from the default for this line.
    threshold_is_default: bool
    completeness: Literal["complete", "partial"]
    reconstruction_coverage_pct: float
    unsupported_elements: list[str]
    derivation: list[DerivationStep]
    # Required for check_type="python_vs_accounts" once a line is finalized.
    mapping_id: Optional[str] = None


class ReconciliationResult(BaseModel):
    """The complete return value of Agent 3 — one object holding everything the
    agent produces, rather than a tuple that cannot carry it all.

    `verdicts_are_final` is False on anything straight out of Agent 3, where
    verdicts are a provisional preview computed against default thresholds. Only
    Gate 3, having recomputed every line against the approved thresholds, sets
    it True. A result with verdicts_are_final=False is never ready for report
    assembly.
    """

    lines: list[ReconciliationLine]
    mappings: list[AccountMapping]
    unmatched_reference_items: list[str]
    unmapped_python_outputs: list[str]
    verdicts_are_final: bool = False


class TraceabilityEntry(BaseModel):
    """One row in the traceability index.

    Built from a reconciliation line's derivation chain, or — for a figure that
    came straight from Agent 2 with no reconstruction — from that finding's own
    cell_ref, which is already exact. There is no value-based reverse lookup.

    `trace_status` replaces a bare is_traceable boolean, because a boolean can
    say whether a trace exists but not why one doesn't, and "why" is what an
    auditor reading the gap actually needs.
    """

    report_figure_label: str
    report_value: Optional[float] = None
    derivation: list[DerivationStep]
    accounting_provenance: Optional[AccountingProvenance] = None
    trace_status: Literal[
        "traced",
        "partially_traced",
        "unmapped",
        "mapping_pending_approval",
        "mapping_rejected",
        "not_traceable",
    ]


class TabDocumentation(BaseModel):
    """One block from Agent 4."""

    tab_name: str
    method_summary: str
    assumptions: list[str]
    data_sources: list[str]
    anomalies_noted: list[str]
    role_notes: str


class LLMDataManifestEntry(BaseModel):
    """A record of exactly what was sent to the Anthropic API for one call.

    This records what left the machine, never the content itself — cell
    references and reasons only.
    """

    tab_name: str
    cell_refs_included: list[str]
    cell_refs_excluded: list[str]
    exclusion_reasons: dict[str, str]
    sent_at: datetime
    prompt_char_count: int


class AuditLogRow(BaseModel):
    """One hash-chained entry in the tamper-evident log.

    Tamper-EVIDENT, not tamper-proof. The chain makes after-the-fact
    modification of the log detectable; it does not make the SQLite file
    physically unmodifiable by anyone with write access to it. Keep that
    distinction anywhere this model is used, in code and in user-facing copy.

    event_type uses "report_approved", not "report_signed": Gate 4 produces a
    named approval record, and signature vocabulary is not used anywhere in
    this codebase. This deviates from the literal text of the build document,
    deliberately — see CHANGELOG.md.
    """

    row_id: int
    report_id: str
    event_type: Literal[
        "gate_decision",
        "state_snapshot",
        "llm_call",
        "report_approved",
        "mapping_decision",
    ]
    payload_hash: str
    # The previous row's row_hash, or 64 zeros for the first row in a chain.
    prev_row_hash: str
    # sha256(prev_row_hash + payload_hash + timestamp)
    row_hash: str
    timestamp: datetime
    actor: Optional[str] = None


class StateSnapshot(BaseModel):
    """A full persisted copy of pipeline state at one gate transition.

    In-memory-only orchestrator state cannot support evidence retention: if the
    process restarts, everything since the last snapshot is gone, but everything
    up to it survives on disk — the state itself, not merely the fact that a
    decision was once made about it.
    """

    report_id: str
    gate_name: str
    captured_at: datetime
    state_json: str
    state_hash: str


class AuditReport(BaseModel):
    """The final assembled report.

    The headline field is `translation_and_reconciliation_verdict`, not a
    "validation" verdict. That distinction is enforced here at the model layer
    rather than left to the report template to get right.

    Gate 4's output is `report_approval_name`/`report_approval_at`/
    `report_approval_role` — a typed name checked against
    config/authorized_approvers.json, with a timestamp, and no professional
    claim beyond that. Deliberately not named `approved_by`, which already means
    something narrower on AccountMapping.
    """

    file_context: FileContext
    reference_figures: Optional[ReferenceFigures] = None
    # Cell refs a human explicitly designated at Gate 2. Never inferred by
    # keyword-matching a tab name.
    authoritative_outputs: list[str]
    parsed_file: ParsedFile
    findings: list[AnomalyFinding]
    # Every mapping considered, approved or not. A report that hides its
    # unapproved proposals hides exactly the gap they represent.
    mappings: list[AccountMapping]
    reconciliation: list[ReconciliationLine]
    # Reference lines with no approved mapping to any Python output.
    unmatched_reference_items: list[str]
    # The other direction: designated outputs with no approved mapping to any
    # reference line. Completeness is checked both ways or not at all.
    unmapped_python_outputs: list[str]
    # "not_checked" only when reference_figures is None. "mismatch" caps
    # external_verdict at "block" however well the numbers happen to line up.
    context_match_verdict: Literal["match", "mismatch", "not_checked"]
    traceability_index: list[TraceabilityEntry]
    documentation: list[TabDocumentation]
    llm_data_manifest: list[LLMDataManifestEntry]
    translation_and_reconciliation_verdict: Literal["pass", "warn", "block", "incomplete"]
    internal_verdict: Literal["pass", "warn", "block", "incomplete"]
    external_verdict: Literal["pass", "warn", "block", "incomplete", "not_performed"]
    workbook_hash: str
    code_version: str
    validation_run_id: str
    # Fixed text, never blank, never editable.
    disclaimer: str
    # Fixed text stating whether the same individual performed every gate (the
    # expected solo case) or different individuals did. Never implies an
    # independent review occurred when it didn't.
    independence_disclosure: str
    report_approval_name: Optional[str] = None
    report_approval_at: Optional[datetime] = None
    report_approval_role: Optional[str] = None
    # When report assembly ran. Distinct from report_approval_at: a report can
    # be assembled and ready to review before anyone approves it.
    generated_at: datetime
    report_id: str
    # Fixed text pointing to how this report_id's hash chain can be re-verified.
    audit_log_verification_note: str
