# Synthetic large-workbook benchmark

Generated: 2026-09-14T18:11:11.459193+00:00

This benchmark measures the deterministic parser, anomaly detector and formula reconstruction engine on one synthetic IFRS 17-related workbook. It does not validate IFRS 17 methodology or establish production suitability.

## Performance

One warm-up pass was followed by 5 measured passes. p95 is not reported because fewer than 20 measured runs were used.

| Metric | Median | Slowest / highest |
|---|---:|---:|
| Parsing time (seconds) | 6.063 | 7.316 |
| Anomaly detection time (seconds) | 2.200 | 2.648 |
| Reconstruction time (seconds) | 4.036 | 5.627 |
| Total deterministic pipeline time (seconds) | 11.817 | 15.590 |
| Peak traced memory (MB) | 56.200 | 63.804 |

## Workbook measured

| Measure | Value |
|---|---:|
| File size (bytes) | 164183 |
| Tabs | 7 |
| Populated cells | 15806 |
| Formula cells | 10847 |
| Dependency edges | 69069 |
| Syntactically supported formula cells | 10847 |
| Syntactic reconstruction coverage | 100.000% |
| Designated-output reconstruction coverage | 100.000% |

Supported function call counts: `{"ABS": 601, "AVERAGEIF": 3, "AVERAGEIFS": 3, "CEILING": 600, "COUNTIF": 3, "COUNTIFS": 3, "FLOOR": 600, "IF": 600, "INDEX": 600, "INT": 600, "MATCH": 600, "MAXIFS": 3, "MINIFS": 3, "ROUND": 4201, "ROUNDDOWN": 600, "ROUNDUP": 600, "SUM": 619, "SUMIF": 6, "SUMIFS": 3, "VLOOKUP": 600}`

Unsupported function counts: `{}`

## Detection and verdict evidence

| Measure | Result |
|---|---|
| Seeded defects | 6 |
| Detected | D1, D2, D3, D4, D5, D6 |
| Missed | None |
| False positives | None |
| Internal preview verdict | pass |
| External preview verdict | pass |
| Mapping status | proposed, not approved |

The external verdict above is an Agent 3 preview. The benchmark never approves a fuzzy mapping and therefore does not present it as a final Gate 3 verdict.

## Environment

| Field | Value |
|---|---|
| Python | 3.12.11 |
| Operating system | macOS-26.6.2-x86_64-i386-64bit |
| CPU | Intel(R) Core(TM) i5-1038NG7 CPU @ 2.00GHz |
| Logical CPU cores | 8 |
| Available RAM (GB) | 16.0 |

## Limitations

- The workbook is entirely synthetic and is not a full IFRS 17 model.
- Only this workbook size and structure were measured. Production-sized workbooks beyond the tested size were not measured.
- Microsoft Excel compatibility was not tested. The fixture was recalculated with LibreOffice, whose formula semantics and file writer are not guaranteed to be identical to Excel.
- No concurrent, multi-user, hosted or sustained-load test was performed.
- `tracemalloc` reports traced Python allocations, not total process resident memory.
- The benchmark excludes Streamlit interaction, PDF rendering, durable gate snapshots and any Anthropic API call.
- No human time saving or commercial impact was measured.
