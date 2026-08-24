# Static Fixture Migration Summary

**Date:** 2026-08-24  
**Goal:** Remove LibreOffice as a test-time requirement by baking cached values into static fixtures once.

## What was done

### 1. Generated Static Fixtures (5 total)

All fixtures with recalculated cached values, committed to repo:

| Fixture | Source | Size | Use Case |
|---------|--------|------|----------|
| `tests/fixtures/clean.xlsx` | test_parser.py | 6.9 KB | Base workbook with formulas + calculated values |
| `tests/fixtures/manual.xlsx` | test_parser.py | 19.9 KB | clean.xlsx + calc mode set to manual |
| `tests/fixtures/auto.xlsx` | test_parser.py | 19.9 KB | clean.xlsx + calc mode set to auto |
| `tests/fixtures/fullcalc.xlsx` | test_parser.py | 19.9 KB | clean.xlsx + manual mode + fullCalcOnLoad=true |
| `tests/fixtures/reserves.xlsx` | test_end_to_end.py | 8.4 KB | Full E2E test workbook with multiple tabs |

**Generation method:** LibreOffice headless convert-to-xlsx (forces full recalculation on load).

### 2. Rewrote Tests to Load from Static Fixtures

#### test_parser.py (26 tests)

**Removed:**
- `@needs_libreoffice` decorator (3 tests)
- `_clean_workbook()` helper function
- All `tmp_path` fixtures for dynamically created workbooks
- All direct `recalculate_workbook()` calls in test bodies

**Updated:**
- Tests that needed clean.xlsx → load from `_load_fixture("clean.xlsx")`
- Tests with calc modes → load pre-configured fixtures (manual.xlsx, auto.xlsx, fullcalc.xlsx)
- Tests that modified fixtures → create workbooks inline (no recalculation needed)
- All tests now receive bytes directly from static fixtures

**Example:**
```python
# Before
@needs_libreoffice
def test_clean_workbook_is_parsed_with_real_cached_values(tmp_path):
    path = str(tmp_path / "clean.xlsx")
    _clean_workbook(path)
    recalculate_workbook(path)  # ← requires LibreOffice
    parsed = parse_workbook(_bytes(path))

# After
def test_clean_workbook_is_parsed_with_real_cached_values():
    parsed = parse_workbook(_load_fixture("clean.xlsx"))  # ← static fixture
```

#### test_end_to_end.py (5 tests)

**Removed:**
- `recalculate_workbook()` call from `workbook_path` fixture
- Dynamic workbook creation (5 worksheet creation lines)

**Updated:**
- `workbook_path` fixture now copies static `reserves.xlsx` to temp location
- All 5 E2E tests still receive the same workbook, now pre-recalculated

### 3. Deprecated recalculate_workbook()

**Function remains in `fixture_helpers.py` but is NOT called by any test.**

Updated docstring:
```python
def recalculate_workbook(path: str) -> None:
    """DEPRECATED: This function is NOT called by the test suite.

    It was used once to generate the static fixtures in tests/fixtures/.
    All test fixtures now have real cached values baked in permanently.

    If a fixture ever needs to change, regenerate it manually with this
    function on a machine that has LibreOffice installed, then re-commit
    the resulting .xlsx file to tests/fixtures/.
    """
```

**Rationale:** Preserve the tool for manual maintenance without wiring it back into automated tests.

### 4. Verification Checklist

✓ **No `recalculate_workbook()` calls in test bodies**  
  Grep: `grep -rn "recalculate_workbook(" tests/test_*.py` — **0 results**

✓ **No `soffice` references in test files**  
  Grep: `grep -rn "soffice" tests/test_*.py` — **0 results**

✓ **All 5 static fixtures exist and are binary files**  
  - clean.xlsx: 6,940 bytes
  - manual.xlsx: 19,903 bytes
  - auto.xlsx: 19,901 bytes
  - fullcalc.xlsx: 19,922 bytes
  - reserves.xlsx: 8,411 bytes

✓ **LibreOffice-gated tests removed**  
  - Removed: `@needs_libreoffice` decorator (3 occurrences)
  - Removed: `libreoffice_available()` import from test files
  - Kept: Helper functions in fixture_helpers.py for manual fixture regeneration

## Outcome

**LibreOffice is NO LONGER a requirement to run the test suite on a clean checkout.**

- Tests run on any environment with Python 3.10+ and pytest
- Cached values are permanent (baked into .xlsx files)
- Test execution is faster (no recalculation step)
- Fixtures are reproducible and version-controlled

## If a fixture needs to change in the future

1. On a machine with LibreOffice installed:
   ```python
   from tests.fixture_helpers import recalculate_workbook, set_calc_mode
   
   # Regenerate the fixture
   recalculate_workbook("/path/to/fixture.xlsx")
   set_calc_mode("/path/to/fixture.xlsx", "manual")  # if needed
   ```

2. Re-commit the .xlsx file to `tests/fixtures/`

3. No changes needed to test code

## Files Changed

| File | Change |
|------|--------|
| tests/test_parser.py | Removed decorators, helpers, and recalculate_workbook() calls; updated 9 tests to load static fixtures |
| tests/test_end_to_end.py | Removed recalculate_workbook() call; updated workbook_path fixture |
| tests/fixture_helpers.py | Marked recalculate_workbook() as deprecated (kept for manual use) |
| tests/fixtures/ (new) | 5 static .xlsx files with real cached values |

**Total test code reductions:** ~40 lines of dynamic fixture setup removed; 2 decorators removed.
