#!/usr/bin/env python3
"""Reproducible benchmark for the user-facing Case 6 demonstration workbook.

Measures one warm-up plus at least five deterministic production-path passes.
It does not measure Streamlit, human review, PDF rendering or LLM latency, and
it does not establish production scalability.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import statistics
import subprocess
import sys
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.anomaly_detector import detect_anomalies
from agents.parser import parse_workbook
from agents.reconciliation import run_reconciliation
from core.accounting import signed_reference_amount
from core.ui_inputs import build_reference_figures

WORKBOOK = ROOT / "demo" / "workbooks" / "case_6_reserve_stress_business_impact.xlsx"
REFERENCE = ROOT / "demo" / "reference_figures" / "case_6_reference_figures.csv"
EXPECTED = ROOT / "demo" / "expected_results" / "case_6_expected.json"
BENCHMARK_DIR = ROOT / "benchmark"
SUMMARY_PATH = BENCHMARK_DIR / "case_6_summary.json"
REPORT_PATH = BENCHMARK_DIR / "CASE_6_BENCHMARK_REPORT.md"
OUTPUT_PACK = ROOT / "outputs" / "ai2_2026_demo_pack_20260824"


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


def _reference_figures(expected: dict):
    with REFERENCE.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    context = expected["gate1_context"]
    return build_reference_figures(
        source_label="Synthetic Q4 2025 trial balance",
        entity=context["entity"],
        period=context["period"],
        currency=context["currency"],
        basis=context["basis"],
        control_total=expected["reference_control_total"],
        control_total_confirmed_by_human=True,
        rows=rows,
        require_account_number=True,
        uploaded_at=datetime.now(timezone.utc),
    )


def _run_once(workbook_bytes: bytes, outputs: list[str], reference):
    tracemalloc.start()
    total_start = time.perf_counter()
    parse_start = time.perf_counter()
    parsed = parse_workbook(workbook_bytes)
    parse_seconds = time.perf_counter() - parse_start
    anomaly_start = time.perf_counter()
    findings = detect_anomalies(parsed)
    anomaly_seconds = time.perf_counter() - anomaly_start
    reconciliation_start = time.perf_counter()
    result = run_reconciliation(parsed, outputs, reference)
    reconciliation_seconds = time.perf_counter() - reconciliation_start
    total_seconds = time.perf_counter() - total_start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "parse_seconds": parse_seconds,
        "anomaly_detection_seconds": anomaly_seconds,
        "reconciliation_seconds": reconciliation_seconds,
        "total_seconds": total_seconds,
        "peak_traced_memory_mb": peak / (1024 * 1024),
    }, parsed, findings, result


def _stat(values: list[float]) -> dict:
    return {"median": statistics.median(values), "highest": max(values)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    if args.runs < 5:
        parser.error("at least five measured runs are required")

    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    workbook_bytes = WORKBOOK.read_bytes()
    reference = _reference_figures(expected)
    outputs = expected["authoritative_output_cells"]

    print("Warm-up pass")
    _run_once(workbook_bytes, outputs, reference)
    rows = []
    latest_parsed = latest_findings = latest_result = None
    for index in range(1, args.runs + 1):
        metrics, latest_parsed, latest_findings, latest_result = _run_once(
            workbook_bytes, outputs, reference
        )
        rows.append(metrics)
        print(f"Measured pass {index}/{args.runs}: {metrics['total_seconds']:.3f}s")

    internal = [line for line in latest_result.lines if line.check_type == "excel_vs_python"]
    external = [line for line in latest_result.lines if line.check_type == "python_vs_accounts"]
    formula_cells = [cell for cell in latest_parsed.cells.values() if cell.formula]
    metrics_summary = {
        name: _stat([row[name] for row in rows])
        for name in rows[0]
    }
    summary = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "warm_up_runs": 1,
        "measured_runs": args.runs,
        "workbook": {
            "path": str(WORKBOOK.relative_to(ROOT)),
            "file_size_bytes": len(workbook_bytes),
            "tab_count": len(latest_parsed.tab_names),
            "populated_cell_count": len(latest_parsed.cells),
            "formula_cell_count": len(formula_cells),
            "dependency_edges": sum(
                len(dependencies)
                for dependencies in latest_parsed.cell_dependency_graph.values()
            ),
        },
        "metrics": metrics_summary,
        "observed": {
            "finding_count": len(latest_findings),
            "internal_lines": len(internal),
            "external_lines": len(external),
            "internal_complete": all(line.completeness == "complete" for line in internal),
            "internal_preview_verdicts": [line.verdict for line in internal],
            "external_preview_verdicts": [line.verdict for line in external],
            "mappings_all_unapproved": bool(latest_result.mappings)
            and all(not mapping.is_approved for mapping in latest_result.mappings),
            "unmatched_reference_items": latest_result.unmatched_reference_items,
            "unmapped_python_outputs": latest_result.unmapped_python_outputs,
            "signed_reference_values": [
                signed_reference_amount(line) for line in reference.lines
            ],
        },
        "business_impact": expected["business_impact"],
        "environment": _environment(),
        "limitations": [
            "The workbook and accounting extract are entirely synthetic.",
            "The calculation is an illustrative reserve stress, not certified Solvency II or IFRS 17 methodology.",
            "Only this workbook size and structure were measured; production scalability is not established.",
            "Microsoft Excel compatibility was not tested; the committed file was recalculated with LibreOffice.",
            "Peak memory is Python tracemalloc output, not total process resident memory.",
            "Streamlit, PDF rendering, durable gate snapshots, concurrent users and LLM latency are excluded.",
        ],
    }

    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    metric = summary["metrics"]
    impact = expected["business_impact"]
    environment = summary["environment"]
    report = f"""# Case 6 synthetic business-impact benchmark

Generated: {summary['generated_at']}

This benchmark measures the deterministic parser, anomaly detector and formula reconstruction engine on the user-facing Case 6 workbook. The workbook is synthetic, the calculation is illustrative, and these timings do not establish production scalability.

## Measured result

One warm-up pass was followed by {args.runs} measured passes. p95 is not reported because fewer than 20 measured runs were used.

| Metric | Median | Highest |
|---|---:|---:|
| Parsing time (seconds) | {metric['parse_seconds']['median']:.3f} | {metric['parse_seconds']['highest']:.3f} |
| Anomaly detection time (seconds) | {metric['anomaly_detection_seconds']['median']:.3f} | {metric['anomaly_detection_seconds']['highest']:.3f} |
| Reconciliation time (seconds) | {metric['reconciliation_seconds']['median']:.3f} | {metric['reconciliation_seconds']['highest']:.3f} |
| Total deterministic time (seconds) | {metric['total_seconds']['median']:.3f} | {metric['total_seconds']['highest']:.3f} |
| Peak traced memory (MB) | {metric['peak_traced_memory_mb']['median']:.3f} | {metric['peak_traced_memory_mb']['highest']:.3f} |

## Workbook measured

| Measure | Value |
|---|---:|
| File size (bytes) | {summary['workbook']['file_size_bytes']} |
| Tabs | {summary['workbook']['tab_count']} |
| Populated cells | {summary['workbook']['populated_cell_count']} |
| Formula cells | {summary['workbook']['formula_cell_count']} |
| Dependency edges | {summary['workbook']['dependency_edges']} |

The two designated accounting outputs reconstructed completely. The run found {len(latest_findings)} anomalies, proposed {len(latest_result.mappings)} mappings, approved none automatically, and left no unmatched reference lines or unmapped designated outputs.

## Demonstrable synthetic business impact

| Measure | Baseline / change |
|---|---:|
| Baseline technical provisions | EUR {impact['baseline_technical_provisions']['expected_value']:,.2f} |
| Adverse technical provisions | EUR {impact['adverse_technical_provisions']['expected_value']:,.2f} |
| Increase in technical provisions | EUR {impact['technical_provisions_increase']['expected_value']:,.2f} |
| Reduction in available own funds | EUR {impact['available_own_funds_reduction']['expected_value']:,.2f} |
| Baseline solvency ratio | {impact['baseline_solvency_ratio']['expected_value']:.2%} |
| Adverse solvency ratio | {impact['adverse_solvency_ratio']['expected_value']:.2%} |
| Deterioration | {impact['solvency_ratio_deterioration']['expected_value'] * 100:.2f} percentage points |

## Environment

| Field | Value |
|---|---|
| Python | {environment['python_version']} |
| Operating system | {environment['operating_system']} |
| CPU | {environment['cpu_model']} |
| Logical CPU cores | {environment['cpu_core_count']} |
| Available RAM (GB) | {environment['available_ram_gb']} |

## Limitations

""" + "\n".join(f"- {item}" for item in summary["limitations"]) + "\n"
    REPORT_PATH.write_text(report, encoding="utf-8")
    OUTPUT_PACK.mkdir(parents=True, exist_ok=True)
    for path in (SUMMARY_PATH, REPORT_PATH):
        (OUTPUT_PACK / path.name).write_bytes(path.read_bytes())
    print(f"Summary: {SUMMARY_PATH.relative_to(ROOT)}")
    print(f"Report: {REPORT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
