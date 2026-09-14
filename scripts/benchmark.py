#!/usr/bin/env python3
"""Reproducible deterministic benchmark for the large synthetic workbook.

This script measures only what it runs: one warm-up followed by at least five
parser, anomaly-detector and reconstruction passes.  It does not establish
production suitability or estimate human time savings.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import time
import tracemalloc
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.anomaly_detector import detect_anomalies
from agents.parser import parse_workbook
from agents.reconciliation import (
    _normalize_formula_tokens,
    _unsupported_reason,
    run_reconciliation,
)
from core.formula_catalogue import SUPPORTED_FUNCTIONS
from core.models import ReferenceFigureLine, ReferenceFigures
from core.ui_inputs import build_reference_figures

FIXTURES = ROOT / "tests" / "fixtures"
CLEAN_WORKBOOK = FIXTURES / "ifrs17_workbook_clean.xlsx"
DEFECTIVE_WORKBOOK = FIXTURES / "ifrs17_workbook_defective.xlsx"
CLEAN_REFERENCE = FIXTURES / "accounting_reference.csv"
DEFECTIVE_REFERENCE = FIXTURES / "accounting_reference_defective.csv"
EXPECTED_CLEAN = FIXTURES / "ifrs17_expected.json"
EXPECTED_DEFECTS = FIXTURES / "expected_defects.json"
BENCHMARK_DIR = ROOT / "benchmark"
RUNS_DIR = BENCHMARK_DIR / "runs"
SUMMARY_PATH = BENCHMARK_DIR / "summary.csv"
REPORT_PATH = BENCHMARK_DIR / "BENCHMARK_REPORT.md"

FUNCTION_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_.]*)\s*\(")


def _load_reference(path: Path) -> ReferenceFigures:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    first = rows[0]
    return build_reference_figures(
        source_label=first["ledger_source"],
        entity=first["entity"],
        period=first["period"],
        currency=first["currency"],
        basis=first["basis"],
        control_total=float(first["control_total"]),
        control_total_confirmed_by_human=True,
        rows=rows,
        require_account_number=True,
        uploaded_at=datetime.now(timezone.utc),
    )


def _timed_pipeline(workbook_bytes: bytes, authoritative_outputs: list[str], reference):
    tracemalloc.start()
    total_start = time.perf_counter()
    parse_start = total_start
    parsed = parse_workbook(workbook_bytes)
    parse_seconds = time.perf_counter() - parse_start

    anomaly_start = time.perf_counter()
    findings = detect_anomalies(parsed)
    anomaly_seconds = time.perf_counter() - anomaly_start

    reconstruction_start = time.perf_counter()
    result = run_reconciliation(parsed, authoritative_outputs, reference)
    reconstruction_seconds = time.perf_counter() - reconstruction_start
    total_seconds = time.perf_counter() - total_start
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "parse_seconds": parse_seconds,
        "anomaly_detection_seconds": anomaly_seconds,
        "reconstruction_seconds": reconstruction_seconds,
        "total_seconds": total_seconds,
        "peak_memory_mb": peak_bytes / (1024 * 1024),
    }, parsed, findings, result


def _warm_up(workbook_bytes: bytes, authoritative_outputs: list[str], reference) -> None:
    parsed = parse_workbook(workbook_bytes)
    detect_anomalies(parsed)
    run_reconciliation(parsed, authoritative_outputs, reference)


def _environment() -> dict:
    cpu_model = platform.processor() or platform.machine()
    if platform.system() == "Darwin":
        try:
            cpu_model = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip() or cpu_model
        except (OSError, subprocess.SubprocessError):
            try:
                hardware = subprocess.run(
                    ["system_profiler", "SPHardwareDataType"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=15,
                ).stdout
                name = re.search(r"Model Name:\s*(.+)", hardware)
                processor = re.search(r"Processor Name:\s*(.+)", hardware)
                speed = re.search(r"Processor Speed:\s*(.+)", hardware)
                parts = []
                for match in (name, processor, speed):
                    if match is None:
                        continue
                    value = match.group(1).strip()
                    if value and value.lower() != "unknown":
                        parts.append(value)
                if parts:
                    cpu_model = ", ".join(parts)
            except (OSError, subprocess.SubprocessError):
                pass
    available_ram = None
    try:
        available_ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        pass
    return {
        "python_version": platform.python_version(),
        "operating_system": platform.platform(),
        "cpu_model": cpu_model,
        "cpu_core_count": os.cpu_count(),
        "available_ram_gb": round(available_ram / (1024**3), 2) if available_ram else None,
    }


def _formula_inventory(parsed) -> dict:
    supported_by_name: Counter = Counter()
    unsupported_by_name: Counter = Counter()
    supported_formula_cells = 0
    total_formula_cells = 0
    for record in parsed.cells.values():
        if not record.formula:
            continue
        total_formula_cells += 1
        normalized = _normalize_formula_tokens(record.formula)
        called = {
            name.upper().removeprefix("_XLFN.")
            for name in FUNCTION_PATTERN.findall(normalized)
        }
        reason = _unsupported_reason(record.formula)
        if reason is None:
            supported_formula_cells += 1
            for name in called & SUPPORTED_FUNCTIONS:
                supported_by_name[name] += 1
        else:
            outside = called - SUPPORTED_FUNCTIONS
            if outside:
                for name in outside:
                    unsupported_by_name[name] += 1
            else:
                unsupported_by_name[reason] += 1
    return {
        "formula_cell_count": total_formula_cells,
        "supported_formula_cell_count": supported_formula_cells,
        "supported_formula_count_by_name": dict(sorted(supported_by_name.items())),
        "unsupported_formula_count_by_name": dict(sorted(unsupported_by_name.items())),
        "syntactic_reconstruction_coverage_pct": round(
            supported_formula_cells / total_formula_cells * 100, 6
        ) if total_formula_cells else 100.0,
    }


def _find_seeded_defects() -> dict:
    expected = json.loads(EXPECTED_DEFECTS.read_text(encoding="utf-8"))
    parsed = parse_workbook(DEFECTIVE_WORKBOOK.read_bytes())
    findings = detect_anomalies(parsed)
    result = run_reconciliation(
        parsed,
        expected["authoritative_outputs"],
        _load_reference(DEFECTIVE_REFERENCE),
    )
    detected = []
    matched_finding_ids = set()
    for defect_id, defect in expected["defects"].items():
        kind = defect["evidence_kind"]
        is_detected = False
        if kind == "anomaly_finding":
            tab, cell_ref = defect["cell_address"].split("!", 1)
            finding = next(
                (
                    item
                    for item in findings
                    if item.tab == tab
                    and item.cell_ref == cell_ref
                    and defect["expected_description_contains"] in item.description
                ),
                None,
            )
            is_detected = finding is not None
            if finding:
                matched_finding_ids.add(finding.finding_id)
        elif kind == "internal_reconciliation":
            line = next(
                (
                    item
                    for item in result.lines
                    if item.check_type == "excel_vs_python"
                    and any(step.cell_ref == defect["cell_address"] for step in item.derivation)
                ),
                None,
            )
            is_detected = bool(
                line
                and line.completeness == defect["expected_completeness"]
                and any(
                    defect["expected_unsupported_contains"] in item
                    for item in line.unsupported_elements
                )
            )
        elif defect_id == "D4":
            is_detected = defect["reference_line_id"] in result.unmatched_reference_items
        elif defect_id == "D5":
            is_detected = defect["cell_address"] in result.unmapped_python_outputs
        elif kind == "context_match":
            is_detected = (
                expected["workbook_context"]["currency"]
                != expected["reference_context"]["currency"]
            )
        if is_detected:
            detected.append(defect_id)
    all_ids = list(expected["defects"])
    false_positives = [
        f"{finding.tab}!{finding.cell_ref}: {finding.description}"
        for finding in findings
        if finding.finding_id not in matched_finding_ids
    ]
    return {
        "seeded_defects_total": len(all_ids),
        "defects_detected": detected,
        "defects_missed": [item for item in all_ids if item not in detected],
        "false_positives": false_positives,
    }


def _aggregate_verdict(lines, check_type: str) -> str:
    verdicts = [line.verdict for line in lines if line.check_type == check_type]
    for verdict in ("block", "incomplete", "warn", "pass"):
        if verdict in verdicts:
            return verdict
    return "not_performed"


def _write_summary(rows: list[dict]) -> dict:
    metrics = (
        "parse_seconds",
        "anomaly_detection_seconds",
        "reconstruction_seconds",
        "total_seconds",
        "peak_memory_mb",
    )
    summary = {
        name: {
            "median": statistics.median(row[name] for row in rows),
            "worst": max(row[name] for row in rows),
        }
        for name in metrics
    }
    with SUMMARY_PATH.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = ["row_type", "run"] + list(metrics)
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({"row_type": "measured", **row})
        writer.writerow(
            {
                "row_type": "median",
                "run": "median",
                **{name: summary[name]["median"] for name in metrics},
            }
        )
        writer.writerow(
            {
                "row_type": "worst",
                "run": "worst",
                **{name: summary[name]["worst"] for name in metrics},
            }
        )
    return summary


def _write_report(
    run_count: int,
    summary: dict,
    workbook_meta: dict,
    environment: dict,
    defects: dict,
    verdicts: dict,
) -> None:
    def value(metric: str, statistic: str) -> str:
        return f"{summary[metric][statistic]:.3f}"

    report = f"""# Synthetic large-workbook benchmark

Generated: {datetime.now(timezone.utc).isoformat()}

This benchmark measures the deterministic parser, anomaly detector and formula reconstruction engine on one synthetic IFRS 17-related workbook. It does not validate IFRS 17 methodology or establish production suitability.

## Performance

One warm-up pass was followed by {run_count} measured passes. p95 is not reported because fewer than 20 measured runs were used.

| Metric | Median | Slowest / highest |
|---|---:|---:|
| Parsing time (seconds) | {value('parse_seconds', 'median')} | {value('parse_seconds', 'worst')} |
| Anomaly detection time (seconds) | {value('anomaly_detection_seconds', 'median')} | {value('anomaly_detection_seconds', 'worst')} |
| Reconstruction time (seconds) | {value('reconstruction_seconds', 'median')} | {value('reconstruction_seconds', 'worst')} |
| Total deterministic pipeline time (seconds) | {value('total_seconds', 'median')} | {value('total_seconds', 'worst')} |
| Peak traced memory (MB) | {value('peak_memory_mb', 'median')} | {value('peak_memory_mb', 'worst')} |

## Workbook measured

| Measure | Value |
|---|---:|
| File size (bytes) | {workbook_meta['file_size_bytes']} |
| Tabs | {workbook_meta['tab_count']} |
| Populated cells | {workbook_meta['populated_cell_count']} |
| Formula cells | {workbook_meta['formula_cell_count']} |
| Dependency edges | {workbook_meta['dependency_edges']} |
| Syntactically supported formula cells | {workbook_meta['supported_formula_cell_count']} |
| Syntactic reconstruction coverage | {workbook_meta['syntactic_reconstruction_coverage_pct']:.3f}% |
| Designated-output reconstruction coverage | {workbook_meta['designated_output_reconstruction_coverage_pct']:.3f}% |

Supported function call counts: `{json.dumps(workbook_meta['supported_formula_count_by_name'], sort_keys=True)}`

Unsupported function counts: `{json.dumps(workbook_meta['unsupported_formula_count_by_name'], sort_keys=True)}`

## Detection and verdict evidence

| Measure | Result |
|---|---|
| Seeded defects | {defects['seeded_defects_total']} |
| Detected | {', '.join(defects['defects_detected']) or 'None'} |
| Missed | {', '.join(defects['defects_missed']) or 'None'} |
| False positives | {'; '.join(defects['false_positives']) or 'None'} |
| Internal preview verdict | {verdicts['internal_preview_verdict']} |
| External preview verdict | {verdicts['external_preview_verdict']} |
| Mapping status | {verdicts['mapping_status']} |

The external verdict above is an Agent 3 preview. The benchmark never approves a fuzzy mapping and therefore does not present it as a final Gate 3 verdict.

## Environment

| Field | Value |
|---|---|
| Python | {environment['python_version']} |
| Operating system | {environment['operating_system']} |
| CPU | {environment['cpu_model']} |
| Logical CPU cores | {environment['cpu_core_count']} |
| Available RAM (GB) | {environment['available_ram_gb']} |

## Limitations

- The workbook is entirely synthetic and is not a full IFRS 17 model.
- Only this workbook size and structure were measured. Production-sized workbooks beyond the tested size were not measured.
- Microsoft Excel compatibility was not tested. The fixture was recalculated with LibreOffice, whose formula semantics and file writer are not guaranteed to be identical to Excel.
- No concurrent, multi-user, hosted or sustained-load test was performed.
- `tracemalloc` reports traced Python allocations, not total process resident memory.
- The benchmark excludes Streamlit interaction, PDF rendering, durable gate snapshots and any Anthropic API call.
- No human time saving or commercial impact was measured.
"""
    REPORT_PATH.write_text(report, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    if args.runs < 5:
        parser.error("at least five measured runs are required")

    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    clean_expected = json.loads(EXPECTED_CLEAN.read_text(encoding="utf-8"))
    workbook_bytes = CLEAN_WORKBOOK.read_bytes()
    reference = _load_reference(CLEAN_REFERENCE)
    authoritative_outputs = clean_expected["authoritative_outputs"]

    print("Warm-up pass")
    _warm_up(workbook_bytes, authoritative_outputs, reference)
    rows = []
    latest_parsed = latest_result = None
    run_group = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    environment = _environment()
    for index in range(1, args.runs + 1):
        metrics, latest_parsed, _, latest_result = _timed_pipeline(
            workbook_bytes, authoritative_outputs, reference
        )
        row = {"run": index, **metrics}
        rows.append(row)
        raw = {
            "schema_version": 1,
            "run_group": run_group,
            "run": index,
            "measured_at": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics,
            "environment": environment,
        }
        (RUNS_DIR / f"run_{run_group}_{index:02d}.json").write_text(
            json.dumps(raw, indent=2) + "\n", encoding="utf-8"
        )
        print(f"Measured pass {index}/{args.runs}: {metrics['total_seconds']:.3f}s")

    formula_meta = _formula_inventory(latest_parsed)
    internal_lines = [
        line for line in latest_result.lines if line.check_type == "excel_vs_python"
    ]
    workbook_meta = {
        "file_size_bytes": len(workbook_bytes),
        "tab_count": len(latest_parsed.tab_names),
        "populated_cell_count": len(latest_parsed.cells),
        "dependency_edges": sum(len(items) for items in latest_parsed.cell_dependency_graph.values()),
        "designated_output_reconstruction_coverage_pct": statistics.mean(
            line.reconstruction_coverage_pct for line in internal_lines
        ),
        **formula_meta,
    }
    defects = _find_seeded_defects()
    verdicts = {
        "internal_preview_verdict": _aggregate_verdict(latest_result.lines, "excel_vs_python"),
        "external_preview_verdict": _aggregate_verdict(latest_result.lines, "python_vs_accounts"),
        "mapping_status": "proposed, not approved",
    }
    summary = _write_summary(rows)
    _write_report(args.runs, summary, workbook_meta, environment, defects, verdicts)
    print(f"Summary: {SUMMARY_PATH.relative_to(ROOT)}")
    print(f"Report: {REPORT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
