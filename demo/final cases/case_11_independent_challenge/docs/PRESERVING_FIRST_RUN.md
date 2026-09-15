# Preserving the first run unchanged

Treat the first run as evidence, not a working directory. Before revealing the
expected outcomes, the custodian should copy the database, audit log export,
terminal transcript, continuous recording, screenshots, result JSON, formula
inventory, report if one was legitimately available, input hashes and tool
version record into a timestamped `first_run_original/` directory. Do not run
formatters, metadata cleaners or report regeneration inside that directory.

Make the copy read-only using the storage system's ordinary access controls and
record a SHA-256 for either every file or one lossless archive. Keep the mutable
working directory separately. A cryptographic hash is tamper-evident, not
tamper-proof: it makes later modification detectable if the original hash is
held independently; it does not prevent someone with file access from editing
the evidence.

If a command crashes, a screen is embarrassing, a detection is wrong or the
pipeline blocks, preserve that state. Add explanatory material beside the
original evidence; never edit the original transcript, database or result.

After expected outcomes are revealed, create `assessment/` for the completed
scorecard and `later_runs/<tool-version>/` for improvements. Every later run
gets its own workbook hash verification, tool commit/tag record, database and
results. Label it as having benefited from seeing the workbook.
