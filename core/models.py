"""Pydantic models that validate every agent's output before it reaches a human gate.

Data shapes only — no logic lives here. Field-level constraints are limited to
those that prevent a whole class of ambiguity from being representable at all
(see `ReferenceFigureLine.amount`).
"""

from datetime import datetime
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

from core.workbook_identity import validate_hash_format

# Shared, exact wording for AuditReport.ai_documentation_status, so the UI and
# the PDF template never drift from each other or invent their own phrasing.
AI_DOCUMENTATION_STATUS_LABELS: dict[str, str] = {
    "generated": "AI documentation generated.",
    "declined": "AI documentation declined — deterministic results unaffected.",
    "validation_failed": (
        "AI documentation validation failed — manual documentation review is required. "
        "No AI-generated text was accepted; this does not affect any verdict."
    ),
    "unavailable": (
        "AI documentation was unavailable for this report. No AI-generated text was "
        "included; this does not affect any verdict."
    ),
    "not_recorded": (
        "This report predates the explicit per-report AI documentation choice; no "
        "status was recorded."
    ),
}


class FileContext(BaseModel):
    """What the human says this workbook is, including the accounting context
    needed to confirm the workbook and any reference figures describe the same
    entity, period, currency and basis.

    entity/period/currency/basis are human-entered confirmation fields, not
    validated enums — free text is deliberate for the MVP.

    confirmed_workbook_hash binds this context to ONE specific byte sequence.
    A filename is not an identity: two different workbooks can share a name, and
    a reviewer who confirms "provisions.xlsx" has confirmed nothing checkable.
    The field is required and format-validated so that the identity cannot be
    omitted, left blank, or truncated — see core/workbook_identity.py.
    """

    filename: str
    description: str
    user_role: Literal["actuary", "cro", "cfo", "auditor"]
    # The SHA-256 of the workbook bytes the human confirmed at Gate 1.
    confirmed_workbook_hash: str
    entity: Optional[str] = None
    period: Optional[str] = None
    currency: Optional[str] = None
    basis: Optional[str] = None
    uploaded_at: datetime

    @field_validator("confirmed_workbook_hash")
    @classmethod
    def _hash_must_be_a_sha256_digest(cls, value: str) -> str:
        """A blank or truncated hash would make the Gate 1 binding optional in
        practice while still looking present. Rejected at the model boundary so
        no downstream code has to remember to check."""
        return validate_hash_format(value, label="confirmed_workbook_hash")


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
    amount: float = Field(ge=0, allow_inf_nan=False)
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
    # Signed net total: debits are positive and credits are negative.
    control_total: Optional[float] = Field(default=None, allow_inf_nan=False)
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
    cached_value: Optional[Union[float, str, bool]] = None
    data_type: Literal["number", "text", "date", "boolean", "error", "blank"]
    number_format: str
    is_error: bool
    error_type: Optional[str] = None
    # True whenever calculation_freshness != "fresh" — kept for every existing
    # caller (LLM data-minimization manifest, the report's stale-cell list).
    is_stale: bool
    # "fresh": a literal cell, or a formula cell with a cached value under
    # automatic calc mode. "stale": formula cell with no cached value, or
    # workbook calc mode is "manual" — the cached value is known not current.
    # "unknown": workbook calc mode could not be determined — the cached
    # value's freshness cannot be established either way. A literal cell is
    # always "fresh": nothing about it is recomputed, so calc mode cannot make
    # it untrustworthy.
    calculation_freshness: Literal["fresh", "stale", "unknown"]


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
    a mapping that is not approved, or when calculation_evidence_status is not
    "fresh". An unapproved mapping never produces "pass", however close the
    numbers happen to be — and neither does stale or freshness-unknown
    calculation evidence, however close the numbers happen to be.

    calculation_evidence_status is deliberately a field separate from
    completeness: completeness measures how much of a formula chain Python
    could independently reconstruct, while calculation_evidence_status
    measures whether the workbook's OWN cached state for that chain can be
    trusted at all. A chain can be 100% reconstructed (completeness="complete")
    while resting on evidence the workbook itself never confirmed
    (calculation_evidence_status="stale") — collapsing these into one field
    would misrepresent one fact as the other. "not_applicable" is reserved for
    lines with no calculation-freshness question to answer (there is currently
    no such line type; every line traces to at least one CellRecord). There is
    no default: every new ReconciliationLine must state this explicitly, so a
    historical line with no recorded freshness evidence reads as "unknown" —
    never silently as "fresh".
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
    # "fresh": no stale or freshness-unknown formula cell was found anywhere in
    # this line's derivation. "stale"/"unknown": at least one was — see the
    # class docstring. No default; every constructor must state it.
    calculation_evidence_status: Literal["fresh", "stale", "unknown", "not_applicable"]
    # Every distinct stale/unknown cell ref found in the derivation, in the
    # deterministic order _build_derivation visits them, duplicates removed.
    # Empty whenever calculation_evidence_status == "fresh".
    stale_cell_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _fresh_lines_carry_no_stale_refs(self):
        if self.calculation_evidence_status == "fresh" and self.stale_cell_refs:
            raise ValueError(
                "a line reporting calculation_evidence_status='fresh' cannot carry "
                f"stale_cell_refs: {self.stale_cell_refs}"
            )
        return self


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

    "chain_verification" records that the complete global hash chain verified
    immediately before a snapshot was recovered into memory. It is written only
    on success: appending to a chain already known to be broken would commit a
    new row's prev_row_hash to a corrupt predecessor, which is writing fresh
    evidence onto compromised evidence. It attests that no disagreement was
    detected in this file at that moment — not that the evidence is authentic,
    and not that the run is validated.

    "workbook_identity_mismatch" records that a workbook was supplied which did
    not match the one confirmed at Gate 1. Its context carries the CONFIRMED
    hash — the identity a human actually approved — and the rejected hash sits
    in the payload. Recording the rejected hash as the context would assert an
    identity for a workbook nobody confirmed, which is the opposite of what this
    log is for.
    """

    row_id: int
    report_id: str
    event_type: Literal[
        "gate_decision",
        "state_snapshot",
        "llm_call",
        "llm_use_decision",
        "report_approved",
        "mapping_decision",
        "chain_verification",
        "workbook_identity_mismatch",
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


class ControlTotalCheck(BaseModel):
    """Mathematical tie-out of signed reference lines to the declared total.

    ``not_checked`` means no control total was supplied. Debit lines contribute
    positively and credit lines negatively to ``signed_line_total``.
    """

    status: Literal["match", "mismatch", "not_checked"] = "not_checked"
    declared_total: Optional[float] = None
    signed_line_total: Optional[float] = None
    difference: Optional[float] = None


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
    # A mismatch is a hard block on the external reconciliation at Gate 3.
    control_total_check: ControlTotalCheck = Field(default_factory=ControlTotalCheck)
    traceability_index: list[TraceabilityEntry]
    documentation: list[TabDocumentation]
    llm_data_manifest: list[LLMDataManifestEntry]
    # Additive field with a safe historic default so reports/snapshots assembled
    # before this control existed remain readable. "not_recorded" means the
    # report predates the explicit per-report AI choice, not that AI was used.
    ai_documentation_status: Literal[
        "generated", "declined", "validation_failed", "unavailable", "not_recorded"
    ] = "not_recorded"
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


class RecalculationEngineProfile(BaseModel):
    """One candidate or approved calculation engine, with exact version and platform.

    A candidate profile has no approval metadata. An approved profile requires
    approved_by, approved_at, and qualification_reference. A withdrawn profile
    cannot be selected for runtime.
    """

    profile_id: str
    engine_family: Literal["libreoffice", "microsoft_excel"]
    exact_version: str
    operating_system: str
    architecture: str
    supported_extensions: list[str]
    status: Literal["candidate", "approved", "withdrawn"]
    qualification_reference: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    notes: Optional[str] = None

    @field_validator("profile_id")
    @classmethod
    def _profile_id_not_blank(cls, value: str) -> str:
        if not (value or "").strip():
            raise ValueError("profile_id cannot be blank")
        return value

    @field_validator("exact_version")
    @classmethod
    def _exact_version_not_blank(cls, value: str) -> str:
        if not (value or "").strip():
            raise ValueError("exact_version cannot be blank")
        return value

    @field_validator("supported_extensions")
    @classmethod
    def _extensions_not_empty_and_normalized(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("supported_extensions cannot be empty")
        normalized = []
        seen = set()
        for ext in value:
            if not ext.startswith("."):
                raise ValueError(f"extension '{ext}' must begin with a dot")
            lower_ext = ext.lower()
            if lower_ext in seen:
                raise ValueError(f"duplicate extension: {lower_ext}")
            seen.add(lower_ext)
            normalized.append(lower_ext)
        return normalized

    @model_validator(mode="after")
    def _validate_approval_metadata(self):
        if self.status == "candidate":
            if self.approved_by is not None or self.approved_at is not None:
                raise ValueError(
                    "candidate profiles must not contain approved_by or approved_at"
                )
        elif self.status == "approved":
            if not (self.approved_by or "").strip():
                raise ValueError("approved profiles require approved_by")
            if self.approved_at is None:
                raise ValueError("approved profiles require approved_at")
            if not (self.qualification_reference or "").strip():
                raise ValueError("approved profiles require qualification_reference")
        return self


class RecalculationPolicy(BaseModel):
    """A collection of engine profiles available for recalculation.

    Profile IDs must be unique. The list cannot be empty. No profile is
    silently selected as a default.
    """

    policy_id: str
    policy_version: str
    profiles: list[RecalculationEngineProfile]

    @field_validator("profiles")
    @classmethod
    def _profiles_not_empty_and_unique(cls, value: list[RecalculationEngineProfile]) -> list[RecalculationEngineProfile]:
        if not value:
            raise ValueError("profiles list cannot be empty")
        seen_ids = set()
        for profile in value:
            if profile.profile_id in seen_ids:
                raise ValueError(f"duplicate profile_id: {profile.profile_id}")
            seen_ids.add(profile.profile_id)
        return value


class ArtifactReference(BaseModel):
    """A pointer to a workbook artifact with integrity information.

    Both the confirmed source workbook and the recalculated output must be
    referenced this way, with their hashes verified to match.
    """

    artifact_kind: Literal["confirmed_source", "recalculated_output"]
    relative_path: str
    sha256: str
    byte_size: int

    @field_validator("sha256")
    @classmethod
    def _hash_must_be_a_sha256_digest(cls, value: str) -> str:
        return validate_hash_format(value, label="artifact sha256")

    @field_validator("byte_size")
    @classmethod
    def _byte_size_greater_than_zero(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("byte_size must be greater than zero")
        return value

    @field_validator("relative_path")
    @classmethod
    def _path_must_be_relative(cls, value: str) -> str:
        if value.startswith("/") or value.startswith("\\"):
            raise ValueError("relative_path must not be absolute")
        if ".." in value:
            raise ValueError("relative_path must not contain '..'")
        return value


class RecalculationEvidence(BaseModel):
    """A successfully completed and verified recalculation, never a failed attempt.

    Every hash is a canonical SHA-256 digest. The successful recalculation
    requires equal before/after formula counts and equal before/after
    formula-manifest hashes. Identical source and recalculated hashes are
    acceptable — hash inequality is not evidence that recalculation occurred.

    External data refresh must remain "not_performed" for the MVP.
    """

    source_workbook_hash: str
    recalculated_workbook_hash: str
    engine_profile_id: str
    engine_family: Literal["libreoffice", "microsoft_excel"]
    detected_engine_version: str
    policy_id: str
    policy_hash: str
    started_at: datetime
    completed_at: datetime
    formula_count_before: int
    formula_count_after: int
    formula_manifest_hash_before: str
    formula_manifest_hash_after: str
    source_artifact: ArtifactReference
    recalculated_artifact: ArtifactReference
    external_data_refresh_status: Literal["not_performed"]
    warnings: list[str]

    @field_validator("source_workbook_hash")
    @classmethod
    def _source_hash_is_sha256(cls, value: str) -> str:
        return validate_hash_format(value, label="source_workbook_hash")

    @field_validator("recalculated_workbook_hash")
    @classmethod
    def _recalculated_hash_is_sha256(cls, value: str) -> str:
        return validate_hash_format(value, label="recalculated_workbook_hash")

    @field_validator("policy_hash")
    @classmethod
    def _policy_hash_is_sha256(cls, value: str) -> str:
        return validate_hash_format(value, label="policy_hash")

    @field_validator("formula_manifest_hash_before")
    @classmethod
    def _manifest_hash_before_is_sha256(cls, value: str) -> str:
        return validate_hash_format(value, label="formula_manifest_hash_before")

    @field_validator("formula_manifest_hash_after")
    @classmethod
    def _manifest_hash_after_is_sha256(cls, value: str) -> str:
        return validate_hash_format(value, label="formula_manifest_hash_after")

    @field_validator("detected_engine_version")
    @classmethod
    def _detected_version_not_blank(cls, value: str) -> str:
        if not (value or "").strip():
            raise ValueError("detected_engine_version cannot be blank")
        return value

    @field_validator("formula_count_before", "formula_count_after")
    @classmethod
    def _formula_counts_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("formula counts must be non-negative")
        return value

    @model_validator(mode="after")
    def _validate_timestamps_and_formulas(self):
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        if self.formula_count_before != self.formula_count_after:
            raise ValueError(
                "successful evidence requires equal before/after formula counts"
            )
        if self.formula_manifest_hash_before != self.formula_manifest_hash_after:
            raise ValueError(
                "successful evidence requires equal before/after formula-manifest hashes"
            )
        return self

    @model_validator(mode="after")
    def _validate_artifacts(self):
        if self.source_artifact.artifact_kind != "confirmed_source":
            raise ValueError("source_artifact must have artifact_kind=confirmed_source")
        if self.recalculated_artifact.artifact_kind != "recalculated_output":
            raise ValueError(
                "recalculated_artifact must have artifact_kind=recalculated_output"
            )
        if self.source_artifact.sha256 != self.source_workbook_hash:
            raise ValueError(
                "source_artifact.sha256 must agree with source_workbook_hash"
            )
        if self.recalculated_artifact.sha256 != self.recalculated_workbook_hash:
            raise ValueError(
                "recalculated_artifact.sha256 must agree with recalculated_workbook_hash"
            )
        return self
