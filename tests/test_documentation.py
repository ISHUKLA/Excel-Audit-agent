"""Tests for agents/documentation.py: mocked Anthropic API, JSON validation, and the invalid-output fallback."""

import json
from datetime import datetime
from types import SimpleNamespace

from agents.documentation import _build_payload, document_tabs
from core.models import FileContext, ParsedFile


class _FakeMessagesAPI:
    def __init__(self, responses: list[str]):
        self._responses = iter(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(text=next(self._responses))])


class _FakeClient:
    def __init__(self, responses: list[str]):
        self.messages = _FakeMessagesAPI(responses)


def _parsed_file(tab_names=("Reserves",)) -> ParsedFile:
    return ParsedFile(
        tab_names=list(tab_names),
        cells={f"{tab_names[0]}!B2": "=A1*1.25"} if tab_names else {},
        cached_values={},
        named_ranges={f"{tab_names[0]}::taux_technique": f"{tab_names[0]}!$B$5"} if tab_names else {},
        external_links=[],
        has_vba=False,
        dependency_graph={},
        warnings=[],
    )


def _file_context() -> FileContext:
    return FileContext(
        filename="reserves.xlsx",
        description="Q4 reserve calculation",
        user_role="actuary",
        uploaded_at=datetime.now(),
    )


def _valid_json():
    return json.dumps(
        {
            "method_summary": "Applies a 25% growth factor to the base reserve.",
            "assumptions": ["25% growth factor"],
            "data_sources": ["Q4 trial balance"],
            "anomalies_noted": [],
            "role_notes": "Actuary should confirm the growth factor is current.",
        }
    )


def test_valid_json_parses_into_tab_documentation(monkeypatch):
    monkeypatch.setattr("agents.documentation.time.sleep", lambda seconds: None)
    client = _FakeClient([_valid_json()])

    result = document_tabs(_parsed_file(), _file_context(), client=client)

    assert len(result) == 1
    doc = result[0]
    assert doc.tab_name == "Reserves"
    assert doc.method_summary == "Applies a 25% growth factor to the base reserve."
    assert doc.assumptions == ["25% growth factor"]
    assert doc.role_notes == "Actuary should confirm the growth factor is current."


def test_invalid_json_triggers_fallback_message(monkeypatch):
    monkeypatch.setattr("agents.documentation.time.sleep", lambda seconds: None)
    client = _FakeClient(["this is not valid JSON at all"])

    result = document_tabs(_parsed_file(), _file_context(), client=client)

    assert len(result) == 1
    assert result[0].tab_name == "Reserves"
    assert result[0].method_summary == "LLM output invalid — manual review required."
    assert result[0].assumptions == []


def test_response_missing_required_field_also_triggers_fallback(monkeypatch):
    monkeypatch.setattr("agents.documentation.time.sleep", lambda seconds: None)
    incomplete = json.dumps({"method_summary": "Missing the other fields"})
    client = _FakeClient([incomplete])

    result = document_tabs(_parsed_file(), _file_context(), client=client)

    assert result[0].method_summary == "LLM output invalid — manual review required."


def test_all_tabs_produce_exactly_one_documentation_block(monkeypatch):
    monkeypatch.setattr("agents.documentation.time.sleep", lambda seconds: None)
    parsed = _parsed_file(tab_names=("Reserves", "Assumptions", "Summary"))
    client = _FakeClient([_valid_json(), _valid_json(), _valid_json()])

    result = document_tabs(parsed, _file_context(), client=client)

    assert [doc.tab_name for doc in result] == ["Reserves", "Assumptions", "Summary"]
    assert len(client.messages.calls) == 3


def test_sleeps_between_calls_but_not_before_the_first(monkeypatch):
    sleep_calls = []
    monkeypatch.setattr("agents.documentation.time.sleep", lambda seconds: sleep_calls.append(seconds))
    parsed = _parsed_file(tab_names=("Reserves", "Assumptions", "Summary"))
    client = _FakeClient([_valid_json(), _valid_json(), _valid_json()])

    document_tabs(parsed, _file_context(), client=client)

    assert sleep_calls == [1, 1]


def test_build_payload_scopes_formulas_and_named_ranges_to_the_tab():
    parsed = ParsedFile(
        tab_names=["TabA", "TabB"],
        cells={
            "TabA!B1": "=A1*2",
            "TabB!B1": "=A1*3",
        },
        cached_values={},
        named_ranges={
            "TabA::taux_technique": "TabA!$B$5",
            "workbook::global_range": "TabB!$C$1",
        },
        external_links=[],
        has_vba=False,
        dependency_graph={},
        warnings=[],
    )

    payload = _build_payload(parsed, _file_context(), "TabA")

    assert payload["formulas"] == ["=A1*2"]
    assert payload["named_ranges"] == ["taux_technique"]
    assert payload["tab_name"] == "TabA"
