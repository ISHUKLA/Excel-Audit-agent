# Excluded from Release v1.0.0

Modules moved to `excluded_from_release/` before competition release. None are deleted; all are recoverable with `git checkout HEAD -- excluded_from_release/`.

| Module | Lines | Reason | Future Release |
|--------|-------|--------|-----------------|
| `core/recalculation.py` | 509 | Fully implemented, zero pipeline callers. Requires LibreOffice system dependency. Cutting it removes the dependency rather than solving it. | Candidate for post-competition integration as Recommendation 3, Phase E3 |
| `core/recalculation_policy.py` | 130 | Config/policy loader for `recalculation.py`. Only consumer is the excluded module; orphaned by that cut. | Tied to recalculation.py; included if recalculation.py is re-integrated |
| `core/artifact_store.py` | 262 | Fully implemented, zero pipeline callers. No added value articulated for this release. | Candidate for post-competition integration as Recommendation 3, Phase E2 |
| `config/recalculation_engines.json` | — | Configuration file for `recalculation.py` only. Moved with it. | Tied to recalculation.py |
| `tests/test_artifact_store.py` | — | Unit tests for `artifact_store.py`. Excluded with the module. | Tied to artifact_store.py |
| `tests/test_recalculation.py` | — | Unit tests for `recalculation.py`. Excluded with the module. | Tied to recalculation.py |
| `tests/test_recalculation_qualification.py` | — | Qualification/preflight tests for recalculation candidates. Excluded with the module. | Tied to recalculation.py |
| `tests/test_recalculation_policy.py` | — | Unit tests for `recalculation_policy.py`. Excluded with the module. | Tied to recalculation_policy.py |

## Shipping test files that use LibreOffice (fixture generation only)

- `tests/test_parser.py` — uses `fixture_helpers.recalculate_workbook()` to bake static cached values into test fixtures. LibreOffice is a dev-time fixture tool, not a runtime dependency.
- `tests/test_end_to_end.py` — uses `fixture_helpers.recalculate_workbook()` for the same purpose. Verified to import nothing from the excluded modules.

Both depend on `tests/fixture_helpers.py`, which ships. LibreOffice is documented in README as a development dependency for fixture creation only.

## Recovery

To restore all excluded modules (e.g., to re-integrate recalculation in a future release):

```bash
git checkout HEAD -- excluded_from_release/
```

This will restore the directory tree and all files to the state at the decision commit.
