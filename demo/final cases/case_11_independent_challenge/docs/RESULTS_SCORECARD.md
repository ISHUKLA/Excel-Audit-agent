# Results scorecard instructions

Complete `results_scorecard_template.yaml` only after the first-run evidence is
preserved and expected outcomes are revealed. Keep raw counts beside every
percentage so a reviewer can reproduce the calculation.

- Supported-formula coverage = supported formula cells / all formula cells.
- Output-chain completeness = completely reconstructed authoritative outputs /
  all authoritative outputs.
- Unsupported-formula recall = expected unsupported constructs reported /
  expected unsupported constructs.
- True positives, false positives and false negatives use only the frozen
  published scope.
- Out-of-scope observations and misses are listed separately. Never combine an
  out-of-scope miss with an in-scope false negative.
- Numeric accuracy applies only where a complete Python target exists. Report
  exact matches, maximum absolute error and maximum relative error; separately
  report any impermissible invented target on a partial output.
- Runtime fields measure parsing, detection and reconstruction separately.
  Record the measurement method and peak-memory method rather than implying
  system-wide memory if only Python allocations were measured.
- Reviewer time runs from first Gate 1 review to the final reachable gate and
  excludes pre-run setup. Count every Gate 2 disposition and every human mapping.
- Workbook-hash and audit-chain verification are explicit booleans backed by
  preserved command output, not inferred from a successful run.

The scorecard is descriptive evidence for this one challenge. It is not an
actuarial validation, certification, assurance conclusion or production-scale
benchmark.
