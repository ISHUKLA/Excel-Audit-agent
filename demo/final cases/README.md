# Competition use-case evidence pack

This directory contains four generated synthetic demonstrations and the protocol
for a fifth, independently authored challenge. The cases exercise the real
parser, anomaly detector, two-pass reconciliation, four human gates and report
guardrails. They do not validate actuarial methodology, replace professional
judgement, provide an audit opinion, or certify any number.

All cases appear in Streamlit's **Load a demonstration case** dropdown. Cases
7a–10 and 14 load their synthetic workbooks and applicable reference figures
while preserving every gate. Case 11 appears as a protocol-only option and
deliberately does not load a workbook.

| Case | Purpose | Expected internal / external endpoint |
|---|---|---|
| 7a | Clean IFRS 17-style aggregation | `pass` / `pass`; PDF only after Gate 4 |
| 7b | Final populated cohort omitted | `pass` / `block`; EUR 6.2m difference |
| 8 | Hardcoded Solvency II diversification amount | `pass` / `block`; EUR 12m difference |
| 9 | Unsupported pricing functions | `incomplete` / `not_performed`; default demonstration stops at Gate 3 |
| 10 | Matching numbers from an incompatible ledger | `pass` / `block`; zero mapped delta does not cure context |
| 11 | Independent challenge protocol only | Not yet performed; no workbook authored here |
| 14 | Manual calculation preserves the pre-change board pack | `incomplete` / `incomplete`; EUR 9.3m stale-cache difference and zero-delta control |

## Rebuild implemented cases

Install the pinned Python dependencies, make `@oai/artifact-tool` available to
Node.js, and install LibreOffice. Then run, for example:

```bash
python scripts/generate_competition_cases.py --cases 7 8 9 10 \
  --node /path/to/node \
  --soffice /path/to/soffice \
  --artifact-tool-module /path/to/artifact_tool.mjs
```

The JavaScript builder authors workbook content with Artifact Tool. The Python
finaliser then recalculates with LibreOffice, verifies formula caches through
the production code, inventories every formula and writes SHA-256 manifests.
It never manufactures cached values by editing worksheet OOXML.

## Verify

```bash
pytest tests/test_competition_cases_7_8.py \
       tests/test_competition_cases_9_10.py \
       tests/test_competition_case_11_protocol.py -q
```

The tests use clearly labelled technical fixture thresholds only to exercise
gate behaviour. They are not defaults or recommendations. Microsoft Excel
compatibility has not been tested; each manifest identifies the LibreOffice
build actually used.
