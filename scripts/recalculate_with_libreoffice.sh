#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$ROOT/tests/fixtures/qualification_workbook_unrecalculated.xlsx"
FINAL="$ROOT/tests/fixtures/qualification_workbook.xlsx"
SOFFICE_BIN="${SOFFICE_BIN:-soffice}"
PYTHON_BIN="${PYTHON_BIN:-python}"

if ! command -v "$SOFFICE_BIN" >/dev/null 2>&1; then
  echo "LibreOffice is unavailable. qualification_workbook.xlsx was not produced." >&2
  exit 1
fi

ENGINE_VERSION="$($SOFFICE_BIN --version)"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT

"$SOFFICE_BIN" --headless --convert-to xlsx --outdir "$TEMP_DIR" "$SOURCE" >/dev/null
mv "$TEMP_DIR/$(basename "$SOURCE")" "$FINAL"

"$PYTHON_BIN" "$ROOT/scripts/generate_qualification_workbook.py" \
  --finalize-manifest "$FINAL" \
  --engine-version "$ENGINE_VERSION"
