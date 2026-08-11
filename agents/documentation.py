"""Generate minimized, role-specific tab documentation through Anthropic.

This is the only module that calls an LLM. Workbook content reaches the API
only through ``minimize_for_llm``. Audit events contain cell references and
hashes, never workbook cell content or raw LLM responses.
"""

import hashlib
import json
import time

import anthropic
from pydantic import ValidationError

from core.audit_log import AuditLog
from core.llm_data_policy import minimize_for_llm
from core.models import (
    FileContext,
    LLMDataManifestEntry,
    ParsedFile,
    TabDocumentation,
)

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 1024
_RATE_LIMIT_DELAY_SECONDS = 1
_FALLBACK_METHOD_SUMMARY = "LLM output invalid — manual review required."

_BASE_SYSTEM_PROMPT = (
    "You are an expert actuary and financial auditor.\n"
    "Analyse this Excel tab and return ONLY valid JSON with these fields:\n"
    "method_summary (str), assumptions (list of str), data_sources (list of str),\n"
    "anomalies_noted (list of str), role_notes (str).\n"
    "Be specific. Reference actual cell values and formula patterns you see.\n"
    "Do not invent data that is not in the payload.\n"
    "Some cells were withheld from this payload for data-minimization reasons; "
    "do not speculate about their content.\n"
)

_ROLE_GUIDANCE = {
    "actuary": (
        "For role='actuary': provide technical detail about formula structure, "
        "assumption sourcing, and cell-level specifics."
    ),
    "cro": (
        "For role='cro': use plain-English risk framing—what could go wrong and "
        "how material it may be, without formula syntax."
    ),
    "cfo": (
        "For role='cfo': use accounting and reconciliation framing. Explain "
        "whether the tab output appears suitable to tie to a GL account."
    ),
    "auditor": (
        "For role='auditor': use independent-verification framing. Explain what "
        "someone without the preparer's access would need to check the figures."
    ),
}


def document_tabs(
    parsed_file: ParsedFile,
    file_context: FileContext,
    authoritative_outputs: list[str],
    *,
    audit_log: AuditLog,
    report_id: str,
    audit_context: dict,
    client: anthropic.Anthropic | None = None,
) -> tuple[list[TabDocumentation], list[LLMDataManifestEntry]]:
    """Document every tab and return its non-sensitive transmission manifests.

    Audit parameters are explicit because every API call must be tied to the
    workbook and code version in effect. No default ``audit.db`` is created as
    a hidden side effect.
    """
    client = client or anthropic.Anthropic()
    documentation: list[TabDocumentation] = []
    manifests: list[LLMDataManifestEntry] = []
    system_prompt = _system_prompt_for_role(file_context.user_role)

    for index, tab_name in enumerate(parsed_file.tab_names):
        if index:
            time.sleep(_RATE_LIMIT_DELAY_SECONDS)

        payload, manifest = minimize_for_llm(
            tab_name,
            parsed_file,
            authoritative_outputs,
        )
        manifests.append(manifest)
        request_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))

        try:
            response = client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": request_json}],
            )
        except Exception as exc:
            _log_llm_call(
                audit_log,
                report_id,
                audit_context,
                manifest,
                request_json,
                raw_response=None,
                outcome="api_error",
                error_detail=type(exc).__name__,
            )
            raise

        # Extended-thinking responses may put a non-text block first. Only text
        # blocks form the JSON response; reasoning blocks are neither parsed nor
        # retained.
        raw_response = "".join(
            block.text
            for block in response.content
            if getattr(block, "type", None) == "text"
        )
        tab_documentation, validation_error = _parse_response(tab_name, raw_response)
        documentation.append(tab_documentation)

        _log_llm_call(
            audit_log,
            report_id,
            audit_context,
            manifest,
            request_json,
            raw_response=raw_response,
            outcome="validation_failed" if validation_error else "success",
            error_detail=validation_error,
        )

    return documentation, manifests


def _system_prompt_for_role(role: str) -> str:
    """Return the fixed prompt plus explicit guidance for the selected role."""
    return f"{_BASE_SYSTEM_PROMPT}{_ROLE_GUIDANCE[role]}"


def _parse_response(tab_name: str, raw_response: str) -> tuple[TabDocumentation, str | None]:
    """Validate one response without retaining invalid raw content."""
    try:
        data = json.loads(raw_response)
        # The workbook's tab name is authoritative; model output cannot rename
        # the block by returning its own tab_name field.
        document = TabDocumentation.model_validate({**data, "tab_name": tab_name})
        return document, None
    except json.JSONDecodeError as exc:
        error_detail = f"JSONDecodeError: {exc.msg} at line {exc.lineno} column {exc.colno}"
    except (TypeError, ValidationError) as exc:
        error_detail = _safe_validation_error(exc)

    return (
        TabDocumentation(
            tab_name=tab_name,
            method_summary=_FALLBACK_METHOD_SUMMARY,
            assumptions=[],
            data_sources=[],
            anomalies_noted=[],
            role_notes="",
        ),
        error_detail,
    )


def _safe_validation_error(exc: TypeError | ValidationError) -> str:
    """Describe validation failure while omitting rejected input values."""
    if isinstance(exc, ValidationError):
        return json.dumps(exc.errors(include_input=False), sort_keys=True, default=str)
    return f"{type(exc).__name__}: response JSON must be an object"


def _log_llm_call(
    audit_log: AuditLog,
    report_id: str,
    audit_context: dict,
    manifest: LLMDataManifestEntry,
    request_json: str,
    *,
    raw_response: str | None,
    outcome: str,
    error_detail: str | None,
) -> None:
    """Log a non-evidentiary call summary, never request/response content."""
    event_payload = {
        "manifest": manifest.model_dump(mode="json"),
        "request_payload_hash": _sha256(request_json),
        "response_hash": _sha256(raw_response) if raw_response is not None else None,
        "outcome": outcome,
        "validation_failed": outcome == "validation_failed",
    }
    if error_detail is not None:
        event_payload["error_detail"] = error_detail

    audit_log.log_event(
        report_id=report_id,
        event_type="llm_call",
        payload=event_payload,
        actor=None,
        context=audit_context,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
