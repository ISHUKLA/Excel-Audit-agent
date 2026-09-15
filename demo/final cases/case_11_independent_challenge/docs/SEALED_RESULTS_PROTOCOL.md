# Sealed-results protocol

Roles: the **author** creates the challenge, the **custodian** controls sealed
inputs and expected outcomes, the **operator** performs the recorded run, and
the **developer** observes without inspecting inputs in advance. One person may
not act as both author and developer.

1. Freeze the tool before workbook receipt: clean the worktree, create a release
   commit and tag, and record both with `record_independent_tool_version.py`.
2. Give the author only the public brief, public formula handout and blank
   expected-outcomes template.
3. Do not let the developer help design formulas, planted issues, unusual
   legitimate patterns, outputs, mappings or accounting-context failures.
4. Have the author complete the expected outcomes before the first run and give
   them directly to the custodian.
5. The custodian runs `seal_independent_challenge.py create` against the exact
   workbook and expected-results bytes. The expected-results SHA-256 lives in
   the companion seal because a file cannot contain its own stable hash.
6. Do not let the developer open, preview, parse or manually inspect the
   workbook before the first run. Filename, byte length and cryptographic hash
   may be handled as opaque evidence.
7. Record the first run continuously from pre-run hash verification through the
   final reachable gate. Capture the commands, complete screen, timestamps and
   reviewer actions without editing out errors or pauses.
8. Immediately preserve the original database, audit logs, screenshots,
   console output, generated files and results in a read-only evidence copy.
9. Reveal the expected outcomes only after the first result and its evidence
   copy are preserved; verify the revealed bytes against the original seal.
10. Publish imperfect results honestly, including false positives, false
    negatives, unsupported behaviour and operational failures.
11. Record every later change in the post-run log as either a bug fix or a scope
    expansion. Do not relabel a miss after seeing the workbook.
12. Run any improved version separately with new hashes and a new version
    record, stating prominently that it benefited from seeing the workbook.

No protocol step bypasses or merges the application's four human gates. A
blocked first run ends at the blocking gate; the recording does not force a PDF.
Materiality thresholds remain human inputs and are recorded as chosen, never
supplied by this protocol.
