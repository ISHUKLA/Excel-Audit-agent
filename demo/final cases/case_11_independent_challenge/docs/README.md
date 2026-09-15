# Case 11 — independent challenge protocol

Status: **not performed**. This repository contains no Case 11 workbook and no
completed expected outcomes. The developer must not author, inspect or help
design the independent workbook before the recorded first run.

Public material for the independent author:

- `INDEPENDENT_AUTHOR_BRIEF.md`
- `PUBLIC_FORMULA_SUPPORT.md`
- `../expected_results/expected_outcomes_template.yaml`

Custodian and first-run material:

- `SEALED_RESULTS_PROTOCOL.md`
- `FIRST_RUN_CHECKLIST.md`
- `PRESERVING_FIRST_RUN.md`
- `../expected_results/results_scorecard_template.yaml`
- `POST_RUN_CHANGE_LOG_TEMPLATE.md`

Evidence commands:

```bash
python scripts/seal_independent_challenge.py create \
  --challenge-id CHALLENGE_ID \
  --workbook /sealed/input/challenge.xlsx \
  --expected-results /sealed/input/expected_outcomes.yaml \
  --output /sealed/evidence/challenge_seal.json

python scripts/record_independent_tool_version.py \
  --release-tag independent-challenge-v1 \
  --output /sealed/evidence/tool_version.json
```

Both commands create new files and refuse to overwrite existing evidence. The
version command also refuses a dirty worktree or a tag that does not resolve to
the tested `HEAD`. It records evidence only; it does not create a commit or tag.

Verify the protocol tooling with
`pytest tests/test_competition_case_11_protocol.py -q`.
