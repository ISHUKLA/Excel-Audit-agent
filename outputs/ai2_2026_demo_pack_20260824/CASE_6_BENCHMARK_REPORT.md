# Case 6 synthetic business-impact benchmark

Generated: 2026-09-15T05:03:34.911850+00:00

This benchmark measures the deterministic parser, anomaly detector and formula reconstruction engine on the user-facing Case 6 workbook. The workbook is synthetic, the calculation is illustrative, and these timings do not establish production scalability.

## Measured result

One warm-up pass was followed by 5 measured passes. p95 is not reported because fewer than 20 measured runs were used.

| Metric | Median | Highest |
|---|---:|---:|
| Parsing time (seconds) | 2.925 | 3.010 |
| Anomaly detection time (seconds) | 1.269 | 1.390 |
| Reconciliation time (seconds) | 2.814 | 2.923 |
| Total deterministic time (seconds) | 6.990 | 7.158 |
| Peak traced memory (MB) | 41.634 | 46.077 |

## Workbook measured

| Measure | Value |
|---|---:|
| File size (bytes) | 141179 |
| Tabs | 9 |
| Populated cells | 11792 |
| Formula cells | 8453 |
| Dependency edges | 45402 |

The two designated accounting outputs reconstructed completely. The run found 0 anomalies, proposed 2 mappings, approved none automatically, and left no unmatched reference lines or unmapped designated outputs.

## Demonstrable synthetic business impact

| Measure | Baseline / change |
|---|---:|
| Baseline technical provisions | EUR 11,411,087.59 |
| Adverse technical provisions | EUR 13,741,241.69 |
| Increase in technical provisions | EUR 2,330,154.10 |
| Reduction in available own funds | EUR 2,330,154.10 |
| Baseline solvency ratio | 181.68% |
| Adverse solvency ratio | 175.03% |
| Deterioration | 6.65 percentage points |

## Environment

| Field | Value |
|---|---|
| Python | 3.12.11 |
| Operating system | macOS-26.6.2-x86_64-i386-64bit |
| CPU | Intel(R) Core(TM) i5-1038NG7 CPU @ 2.00GHz |
| Logical CPU cores | 8 |
| Available RAM (GB) | 16.0 |

## Limitations

- The workbook and accounting extract are entirely synthetic.
- The calculation is an illustrative reserve stress, not certified Solvency II or IFRS 17 methodology.
- Only this workbook size and structure were measured; production scalability is not established.
- Microsoft Excel compatibility was not tested; the committed file was recalculated with LibreOffice.
- Peak memory is Python tracemalloc output, not total process resident memory.
- Streamlit, PDF rendering, durable gate snapshots, concurrent users and LLM latency are excluded.
