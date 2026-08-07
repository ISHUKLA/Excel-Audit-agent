"""Uses the Anthropic API to explain already-computed findings in plain English; never computes or certifies figures."""

import json
import logging
import time

import anthropic
from pydantic import ValidationError

from core.models import FileContext, ParsedFile, TabDocumentation

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-5"
_MAX_TOKENS = 1024
_MAX_FORMULAS_PER_TAB = 50
_RATE_LIMIT_DELAY_SECONDS = 1

_SYSTEM_PROMPT = (
    "You are an expert actuary and financial auditor.\n"
    "Analyse this Excel tab and return ONLY valid JSON with these fields:\n"
    "method_summary (str), assumptions (list of str), data_sources (list of str),\n"
    "anomalies_noted (list of str), role_notes (str -- tailored to the user role).\n"
    "Be specific. Reference actual cell values and formula patterns you see.\n"
    "Do not invent data that is not in the payload."
)

_FALLBACK_METHOD_SUMMARY = "LLM output invalid — manual review required."


def document_tabs(
    parsed_file: ParsedFile,
    file_context: FileContext,
    client: anthropic.Anthropic | None = None,
) -> list[TabDocumentation]:
    client = client or anthropic.Anthropic()
    documentation: list[TabDocumentation] = []

    for index, tab in enumerate(parsed_file.tab_names):
        if index > 0:
            time.sleep(_RATE_LIMIT_DELAY_SECONDS)

        payload = _build_payload(parsed_file, file_context, tab)
        response = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(payload)}],
        )
        # response.content[0] isn't reliably the text block -- e.g. extended
        # thinking prepends a ThinkingBlock with no .text attribute at all.
        raw_text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        documentation.append(_parse_response(tab, raw_text))

    return documentation


def _build_payload(parsed_file: ParsedFile, file_context: FileContext, tab: str) -> dict:
    formulas = [
        value
        for key, value in parsed_file.cells.items()
        if key.startswith(f"{tab}!") and isinstance(value, str) and value.startswith("=")
    ][:_MAX_FORMULAS_PER_TAB]

    named_ranges = [
        scoped_key.split("::", 1)[1]
        for scoped_key, definition in parsed_file.named_ranges.items()
        if _named_range_belongs_to_tab(scoped_key, definition, tab)
    ]

    return {
        "tab_name": tab,
        "formulas": formulas,
        "named_ranges": named_ranges,
        "file_description": file_context.description,
        "user_role": file_context.user_role,
    }


def _named_range_belongs_to_tab(scoped_key: str, definition: str, tab: str) -> bool:
    scope, _, _ = scoped_key.partition("::")
    if scope == tab:
        return True
    stripped = definition.replace("$", "")
    return stripped.startswith(f"{tab}!") or stripped.startswith(f"'{tab}'!")


def _parse_response(tab: str, raw_text: str) -> TabDocumentation:
    try:
        data = json.loads(raw_text)
        if not isinstance(data, dict):
            raise TypeError("LLM response was not a JSON object")
        return TabDocumentation(
            tab_name=tab,
            method_summary=data["method_summary"],
            assumptions=data["assumptions"],
            data_sources=data["data_sources"],
            anomalies_noted=data["anomalies_noted"],
            role_notes=data["role_notes"],
        )
    except (json.JSONDecodeError, ValidationError, TypeError, KeyError) as exc:
        logger.warning("LLM documentation output invalid for tab %r: %s (%s)", tab, raw_text, exc)
        return TabDocumentation(
            tab_name=tab,
            method_summary=_FALLBACK_METHOD_SUMMARY,
            assumptions=[],
            data_sources=[],
            anomalies_noted=[],
            role_notes="",
        )
