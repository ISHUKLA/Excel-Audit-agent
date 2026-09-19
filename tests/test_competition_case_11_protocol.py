"""Acceptance tests for the independent-challenge protocol and evidence seals."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
import yaml

from core.formula_catalogue import SUPPORTED_FUNCTIONS
from scripts import record_independent_tool_version as version_recorder
from scripts.seal_independent_challenge import create_seal, verify_seal

ROOT = Path(__file__).resolve().parents[1]
COMPETITION = ROOT / "demo" / "final cases"
CASE11 = COMPETITION / "case_11_independent_challenge"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_case_11_is_protocol_only_and_has_every_required_artifact_class():
    assert not list(CASE11.rglob("*.xlsx"))
    required = [
        "docs/INDEPENDENT_AUTHOR_BRIEF.md",
        "docs/PUBLIC_FORMULA_SUPPORT.md",
        "expected_results/expected_outcomes_template.yaml",
        "docs/SEALED_RESULTS_PROTOCOL.md",
        "docs/FIRST_RUN_CHECKLIST.md",
        "expected_results/results_scorecard_template.yaml",
        "docs/POST_RUN_CHANGE_LOG_TEMPLATE.md",
        "docs/PRESERVING_FIRST_RUN.md",
        "workbooks/README.md",
        "reference_figures/README.md",
        "formula_inventories/README.md",
        "manifests/README.md",
    ]
    assert all((CASE11 / relative).is_file() for relative in required)
    readme = (CASE11 / "docs/README.md").read_text(encoding="utf-8")
    assert "not performed" in readme.lower()
    assert "no Case 11 workbook" in readme


def test_independent_author_brief_contains_the_public_challenge_constraints():
    brief = (CASE11 / "docs/INDEPENDENT_AUTHOR_BRIEF.md").read_text(encoding="utf-8")
    compact_brief = " ".join(brief.split())
    for requirement in (
        "at least five substantive tabs",
        "at least 500 formula cells",
        "two to five authoritative monetary outputs",
        "cross-tab calculations",
        "at least one planted control problem inside the published scope",
        "at least one formula outside the published support catalogue",
        "at least one mapping, population-completeness or accounting-context issue",
        "at least one unusual but legitimate pattern",
        "quantifiable financial, capital or management consequence",
    ):
        assert requirement in compact_brief
    assert "developer may provide only" in compact_brief
    assert "does not validate actuarial methodology" in compact_brief


def test_public_formula_handout_matches_the_live_function_catalogue():
    handout = (CASE11 / "docs/PUBLIC_FORMULA_SUPPORT.md").read_text(encoding="utf-8")
    supported_section = handout.split("## Explicitly outside", 1)[0]
    listed = set(re.findall(r"`([A-Z]+)`", supported_section))
    assert listed == set(SUPPORTED_FUNCTIONS)
    assert len(listed) == len(SUPPORTED_FUNCTIONS)
    for unsupported in ("IRR", "XIRR", "OFFSET"):
        assert f"`{unsupported}`" in handout
    assert "partial/incomplete" in handout
    assert "target remains empty" in handout


def test_expected_outcomes_template_captures_required_fields_without_a_self_hash():
    template = yaml.safe_load(
        (CASE11 / "expected_results/expected_outcomes_template.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert template["challenge"]["challenge_id"] == "FILL_ME"
    assert set(template["workbook_context"]) >= {"entity", "period", "currency"}
    assert template["sealed_identity"]["workbook_sha256"]
    assert template["sealed_identity"]["expected_results_sha256"] == "COMPANION_SEAL_MANIFEST"
    assert "cannot be embedded" in template["sealed_identity"]["expected_results_hash_note"]
    assert 2 <= len(template["authoritative_outputs"]) <= 5
    assert template["planted_issues"][0]["inside_published_scope"] is True
    assert template["planted_issues"][0]["quantified_consequence"]
    assert template["unsupported_formulas"]
    assert template["legitimate_unusual_patterns"]
    assert set(template["accounting_context"]) >= {"entity", "period", "currency"}
    assert template["expected_mappings"]
    assert "expected_unmatched_reference_ids" in template
    assert "expected_unmapped_output_cells" in template
    assert set(template["expected_verdicts"]) >= {
        "internal", "external", "expected_gate_block", "pdf_available"
    }


def test_protocol_has_all_twelve_controls_and_preservation_is_tamper_evident():
    protocol = (CASE11 / "docs/SEALED_RESULTS_PROTOCOL.md").read_text(encoding="utf-8")
    compact_protocol = " ".join(protocol.split())
    for number in range(1, 13):
        assert re.search(rf"^{number}\. ", protocol, flags=re.MULTILINE)
    for phrase in (
        "Freeze the tool before workbook receipt",
        "Do not let the developer help design",
        "before the first run",
        "Do not let the developer open, preview, parse or manually inspect",
        "Record the first run continuously",
        "preserve the original database",
        "Reveal the expected outcomes only after",
        "Publish imperfect results honestly",
        "bug fix or a scope expansion",
        "benefited from seeing the workbook",
    ):
        assert phrase in compact_protocol
    preservation = (CASE11 / "docs/PRESERVING_FIRST_RUN.md").read_text(encoding="utf-8")
    assert "tamper-evident, not" in preservation
    assert "never edit the original" in preservation


def test_scorecard_keeps_out_of_scope_misses_separate():
    scorecard = yaml.safe_load(
        (CASE11 / "expected_results/results_scorecard_template.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert scorecard["scope_counts"]["supported_formula_coverage"]["calculation"]
    assert scorecard["scope_counts"]["output_chain_completeness"]["calculation"]
    detection = scorecard["detection"]
    assert detection["unsupported_formula_recall"]["calculation"]
    assert set(detection) >= {
        "true_positives", "false_positives", "false_negatives", "out_of_scope_observations"
    }
    assert "Do not add" in detection["out_of_scope_observations"]["note"]
    assert set(scorecard["performance"]) >= {
        "parse_runtime_seconds",
        "detection_runtime_seconds",
        "reconstruction_runtime_seconds",
        "peak_memory_megabytes",
    }
    assert set(scorecard["human_review"]) >= {
        "reviewer_time_minutes", "number_of_dispositions", "number_of_manual_mappings"
    }
    assert set(scorecard["assessed_first_run"]) >= {
        "workbook_sha256_verified", "audit_chain_verified"
    }


def test_companion_seal_records_exact_bytes_refuses_overwrite_and_detects_change(tmp_path):
    workbook = tmp_path / "challenge.xlsx"
    expected = tmp_path / "expected.yaml"
    seal = tmp_path / "challenge_seal.json"
    workbook.write_bytes(b"opaque-workbook-bytes")
    expected.write_text("challenge_id: independent-001\n", encoding="utf-8")

    payload = create_seal(
        challenge_id="independent-001",
        workbook=workbook,
        expected_results=expected,
        output=seal,
    )
    assert payload["workbook"]["sha256"] == _sha256(workbook)
    assert payload["expected_results"]["sha256"] == _sha256(expected)
    assert verify_seal(seal=seal, workbook=workbook, expected_results=expected) == []
    with pytest.raises(FileExistsError):
        create_seal(
            challenge_id="independent-001",
            workbook=workbook,
            expected_results=expected,
            output=seal,
        )

    expected.write_text("challenge_id: changed-after-seal\n", encoding="utf-8")
    failures = verify_seal(seal=seal, workbook=workbook, expected_results=expected)
    assert "expected_results SHA-256 mismatch" in failures


def test_tool_version_record_requires_clean_head_and_matching_existing_tag(tmp_path, monkeypatch):
    commit = "a" * 40
    dirty = False

    def fake_git(_repo, *args):
        if args == ("status", "--porcelain"):
            return " M changed.py" if dirty else ""
        if args == ("rev-parse", "HEAD"):
            return commit
        if args == ("rev-parse", "refs/tags/independent-challenge-v1^{commit}"):
            return commit
        if args == ("log", "-1", "--format=%cI", "HEAD"):
            return "2026-09-15T12:00:00+00:00"
        raise AssertionError(args)

    monkeypatch.setattr(version_recorder, "_git", fake_git)
    output = tmp_path / "tool_version.json"
    payload = version_recorder.record_tool_version(
        repo=tmp_path,
        release_tag="independent-challenge-v1",
        output=output,
    )
    assert payload["git_commit"] == commit
    assert payload["release_tag"] == "independent-challenge-v1"
    assert payload["worktree_clean"] is True

    dirty = True
    with pytest.raises(RuntimeError, match="worktree is dirty"):
        version_recorder.record_tool_version(
            repo=tmp_path,
            release_tag="independent-challenge-v1",
            output=tmp_path / "must_not_exist.json",
        )


def test_implemented_case_manifests_verify_every_recorded_artifact_hash():
    manifests = sorted(COMPETITION.glob("case_*/manifests/*_manifest.json"))
    assert len(manifests) == 6
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert _sha256(ROOT / manifest["workbook_file"]) == manifest["workbook_sha256"]
        assert _sha256(ROOT / manifest["expected_results_file"]) == manifest["expected_results_sha256"]
        assert _sha256(ROOT / manifest["formula_inventory_file"]) == manifest["formula_inventory_sha256"]
        if manifest["reference_file"] is None:
            assert manifest["reference_sha256"] is None
        else:
            assert _sha256(ROOT / manifest["reference_file"]) == manifest["reference_sha256"]
        assert re.fullmatch(r"[0-9a-f]{40}", manifest["tool_git_commit"])
        assert manifest["recalculation_engine"] == "LibreOffice"
        assert manifest["synthetic_data"] is True
