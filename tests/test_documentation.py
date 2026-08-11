"""Step 8 tests: minimized payloads, mocked LLM calls, and safe audit evidence."""

import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agents.documentation import document_tabs
from core.llm_data_policy import minimize_for_llm
from core.models import CellRecord, FileContext, ParsedFile, WorkbookMeta


class _FakeMessagesAPI:
    def __init__(self, responses):
        self._responses = iter(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response = next(self._responses)
        blocks = (
            [SimpleNamespace(type="text", text=response)]
            if isinstance(response, str)
            else response
        )
        return SimpleNamespace(content=blocks)


class _FakeClient:
    def __init__(self, responses):
        self.messages = _FakeMessagesAPI(responses)


class _CapturingAuditLog:
    def __init__(self):
        self.calls: list[dict] = []

    def log_event(self, **kwargs):
        self.calls.append(kwargs)


def _cell(
    cell_ref: str,
    value=None,
    *,
    formula: str | None = None,
    data_type: str = "number",
) -> CellRecord:
    return CellRecord(
        cell_ref=cell_ref,
        formula=formula,
        cached_value=value,
        data_type=data_type,
        number_format="General",
        is_error=data_type == "error",
        error_type=value if data_type == "error" else None,
        is_stale=formula is not None and value is None,
    )


def _parsed_file(tab_names=("Reserves",)) -> ParsedFile:
    cells = {}
    graph = {}
    named_ranges = {}
    for index, tab_name in enumerate(tab_names, start=1):
        input_ref = f"{tab_name}!A1"
        output_ref = f"{tab_name}!B2"
        cells[input_ref] = _cell(input_ref, 1000.0 + index)
        cells[output_ref] = _cell(output_ref, 1250.0 + index, formula="=A1*1.25")
        graph[output_ref] = [input_ref]
        graph[input_ref] = []
        named_ranges[f"{tab_name}::output"] = f"{tab_name}!$B$2"

    return ParsedFile(
        tab_names=list(tab_names),
        cells=cells,
        named_ranges=named_ranges,
        external_links=[],
        has_vba=False,
        workbook_meta=WorkbookMeta(
            calc_mode="automatic",
            workbook_hash="a" * 64,
            app_version=None,
            fully_calculated_on_load=True,
        ),
        tab_dependency_graph={},
        cell_dependency_graph=graph,
        warnings=[],
    )


def _file_context(role="actuary") -> FileContext:
    return FileContext(
        filename="reserves.xlsx",
        description="Q4 reserve calculation",
        user_role=role,
        uploaded_at=datetime.now(timezone.utc),
    )


def _valid_json() -> str:
    return json.dumps(
        {
            "method_summary": "Applies a 25% growth factor to the base reserve.",
            "assumptions": ["25% growth factor"],
            "data_sources": ["Q4 trial balance"],
            "anomalies_noted": [],
            "role_notes": "Confirm the growth factor is current.",
        }
    )


def _run(parsed_file, file_context, client, audit_log=None):
    audit_log = audit_log or _CapturingAuditLog()
    result = document_tabs(
        parsed_file,
        file_context,
        [f"{parsed_file.tab_names[0]}!B2"],
        audit_log=audit_log,
        report_id="report-8",
        audit_context={"workbook_hash": "a" * 64, "code_version": "test"},
        client=client,
    )
    return result, audit_log


def test_valid_json_is_validated_and_manifest_is_returned(monkeypatch):
    monkeypatch.setattr("agents.documentation.time.sleep", lambda _: None)
    parsed = _parsed_file()
    client = _FakeClient([_valid_json()])

    (documents, manifests), audit_log = _run(parsed, _file_context(), client)

    assert documents[0].method_summary == "Applies a 25% growth factor to the base reserve."
    assert documents[0].assumptions == ["25% growth factor"]
    assert manifests[0].tab_name == "Reserves"
    assert manifests[0].cell_refs_included == ["Reserves!A1", "Reserves!B2"]
    assert client.messages.calls[0]["model"] == "claude-sonnet-4-6"
    assert audit_log.calls[0]["event_type"] == "llm_call"
    assert audit_log.calls[0]["payload"]["outcome"] == "success"


def test_extended_thinking_block_before_text_is_ignored(monkeypatch):
    monkeypatch.setattr("agents.documentation.time.sleep", lambda _: None)
    client = _FakeClient(
        [[
            SimpleNamespace(type="thinking", thinking="not evidence"),
            SimpleNamespace(type="text", text=_valid_json()),
        ]]
    )

    (documents, _), _ = _run(_parsed_file(), _file_context(), client)

    assert documents[0].method_summary == "Applies a 25% growth factor to the base reserve."


def test_model_output_cannot_rename_the_workbook_tab(monkeypatch):
    monkeypatch.setattr("agents.documentation.time.sleep", lambda _: None)
    response = json.loads(_valid_json())
    response["tab_name"] = "Invented tab"

    (documents, _), _ = _run(
        _parsed_file(),
        _file_context(),
        _FakeClient([json.dumps(response)]),
    )

    assert documents[0].tab_name == "Reserves"


def test_invalid_json_falls_back_without_logging_raw_response(monkeypatch):
    monkeypatch.setattr("agents.documentation.time.sleep", lambda _: None)
    raw_response = "CONFIDENTIAL-RAW-LLM-RESPONSE is not JSON"
    audit_log = _CapturingAuditLog()

    (documents, manifests), _ = _run(
        _parsed_file(),
        _file_context(),
        _FakeClient([raw_response]),
        audit_log,
    )

    assert documents[0].method_summary == "LLM output invalid — manual review required."
    assert len(manifests) == 1
    logged_payload = audit_log.calls[0]["payload"]
    assert logged_payload["validation_failed"] is True
    assert logged_payload["response_hash"] == hashlib.sha256(raw_response.encode()).hexdigest()
    assert "error_detail" in logged_payload
    assert raw_response not in json.dumps(logged_payload)


def test_pydantic_validation_error_does_not_log_rejected_input(monkeypatch):
    monkeypatch.setattr("agents.documentation.time.sleep", lambda _: None)
    secret_value = "PRIVATE-COMMENTARY-THAT-MUST-NOT-BE-LOGGED"
    invalid = json.dumps(
        {
            "method_summary": secret_value,
            "assumptions": secret_value,
            "data_sources": [],
            "anomalies_noted": [],
            "role_notes": "",
        }
    )
    audit_log = _CapturingAuditLog()

    _run(_parsed_file(), _file_context(), _FakeClient([invalid]), audit_log)

    assert secret_value not in json.dumps(audit_log.calls[0]["payload"])


def test_every_tab_produces_one_document_and_manifest_and_calls_are_delayed(monkeypatch):
    sleeps = []
    monkeypatch.setattr("agents.documentation.time.sleep", sleeps.append)
    parsed = _parsed_file(("Reserves", "Assumptions", "Summary"))
    client = _FakeClient([_valid_json(), _valid_json(), _valid_json()])

    (documents, manifests), audit_log = _run(parsed, _file_context(), client)

    assert [item.tab_name for item in documents] == parsed.tab_names
    assert [item.tab_name for item in manifests] == parsed.tab_names
    assert len(audit_log.calls) == 3
    assert sleeps == [1, 1]


def test_minimization_excludes_long_text_external_links_and_unrelated_cells():
    long_text = "Named customer commentary that is longer than forty characters"
    external_formula = "='[Users/private/source.XLSX]Data'!A1"
    parsed = _parsed_file(("Reserves", "Other"))
    parsed.cells.update(
        {
            "Reserves!C1": _cell("Reserves!C1", "Short label", data_type="text"),
            "Reserves!C2": _cell("Reserves!C2", long_text, data_type="text"),
            "Reserves!C3": _cell(
                "Reserves!C3",
                99.0,
                formula=external_formula,
            ),
            "Reserves!C4": _cell("Reserves!C4", None, data_type="blank"),
        }
    )
    parsed.external_links = [external_formula]

    payload, manifest = minimize_for_llm("Reserves", parsed, ["Reserves!B2"])

    assert "Reserves!C1" in payload["cells"]
    assert "Reserves!C4" in payload["cells"]
    assert "Reserves!C2" not in payload["cells"]
    assert "Reserves!C3" not in payload["cells"]
    assert manifest.exclusion_reasons["Reserves!C2"] == (
        "free text over length threshold, possible PII"
    )
    assert manifest.exclusion_reasons["Reserves!C3"] == "external link path, not sent"
    assert manifest.exclusion_reasons["Other!A1"].startswith("not reachable")
    assert long_text not in json.dumps(payload)
    assert external_formula not in json.dumps(payload)


@pytest.mark.parametrize(
    ("role", "required_text"),
    [
        ("actuary", "formula structure"),
        ("cro", "plain-English risk"),
        ("cfo", "reconcil"),
        ("auditor", "independent"),
    ],
)
def test_each_role_receives_explicit_guidance(monkeypatch, role, required_text):
    monkeypatch.setattr("agents.documentation.time.sleep", lambda _: None)
    client = _FakeClient([_valid_json()])

    _run(_parsed_file(), _file_context(role), client)

    assert required_text.lower() in client.messages.calls[0]["system"].lower()
