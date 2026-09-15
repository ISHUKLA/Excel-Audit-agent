# First-run execution checklist

Complete in order on the continuous recording. Write timestamps in UTC.

- [ ] Identify author, custodian, operator and developer; confirm independence.
- [ ] Show the sealed `tool_version.json`; verify clean commit and release tag.
- [ ] Show the companion seal without revealing expected outcomes.
- [ ] Verify workbook and expected-results hashes with
  `seal_independent_challenge.py verify` while the expected file remains closed.
- [ ] Confirm the developer has not inspected the workbook or expected outcomes.
- [ ] Start continuous screen and terminal recording; record UTC start time.
- [ ] Create a new evidence directory; copy, never move, sealed inputs into it.
- [ ] Start the application from the frozen tag and record environment versions.
- [ ] Gate 1: record exact workbook hash, context and optional reference control
  total; the reviewer confirms the shown information.
- [ ] Gate 2: display every finding, record every disposition and reason, and
  designate two to five authoritative monetary outputs.
- [ ] Gate 3: show internal and external comparisons separately; record the
  reviewer's thresholds and mapping decisions; leave all automatic mapping
  approvals false until a named human acts.
- [ ] If Gate 3 blocks, stop the pipeline there. Do not attempt Gate 4 or create
  a substitute PDF.
- [ ] If Gate 3 permits continuation, record the explicit per-report AI-use
  decision, complete Gate 4's named approval record, and preserve the PDF.
- [ ] Record UTC end time, runtime metrics, peak memory and reviewer time.
- [ ] Verify the audit chain and record the result.
- [ ] Stop recording only after database, logs, screenshots and outputs are
  copied to the evidence directory.
- [ ] Make the first-run evidence copy read-only; record its directory hash or
  archive hash and custody location.
- [ ] Reveal expected outcomes, verify their seal again, and complete the
  scorecard without changing first-run artifacts.
