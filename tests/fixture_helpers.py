"""Helpers for building .xlsx test fixtures that behave like real workbooks.

The problem these exist to solve: openpyxl has no formula engine. Writing
`ws["C5"] = "=A1*2"` and saving produces a file whose C5 has a formula and no
cached value — forever, until something that can actually calculate opens it.
A test built on such a fixture can assert "the parser read the formula" but can
never assert "the parser read the formula AND its value", which is the single
property the CellRecord model exists to guarantee.

`recalculate_workbook` fixes that by running the file through headless
LibreOffice, which does have a formula engine. Converting a file to its own
format forces a full recalculation on load.
"""

import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Optional

# macOS installs the binary inside the app bundle rather than on PATH.
_CANDIDATE_BINARIES = (
    "soffice",
    "libreoffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/bin/soffice",
    "/usr/bin/libreoffice",
)


def soffice_path() -> Optional[str]:
    """The LibreOffice binary, or None if it isn't installed."""
    for candidate in _CANDIDATE_BINARIES:
        resolved = shutil.which(candidate) if "/" not in candidate else candidate
        if resolved and Path(resolved).exists():
            return resolved
    return None


def libreoffice_available() -> bool:
    return soffice_path() is not None


def recalculate_workbook(path: str) -> None:
    """DEPRECATED: This function is NOT called by the test suite.

    It was used once to generate the static fixtures in tests/fixtures/.
    All test fixtures now have real cached values baked in permanently.

    If a fixture ever needs to change, regenerate it manually with this
    function on a machine that has LibreOffice installed, then re-commit
    the resulting .xlsx file to tests/fixtures/ — do not wire this back
    into the automated test run.

    Original docstring:
    ----
    Recalculate an .xlsx in place, so its formula cells get real cached values.

    Converts through a scratch directory rather than in place, because
    LibreOffice will not reliably overwrite its own input file. Raises if
    LibreOffice is missing — silently skipping the recalculation would leave a
    test that passes while asserting nothing.
    """
    binary = soffice_path()
    if binary is None:
        raise RuntimeError(
            "LibreOffice is not installed, so this fixture cannot be given real "
            "cached formula values. Install it with `brew install --cask "
            "libreoffice` (macOS) or `apt install libreoffice-calc` (Debian). "
            "See README.md — it is a test-only dependency."
        )

    source = Path(path)
    scratch = source.parent / "_recalc"
    scratch.mkdir(exist_ok=True)

    subprocess.run(
        [binary, "--headless", "--convert-to", "xlsx", "--outdir", str(scratch), str(source)],
        check=True,
        timeout=120,
        capture_output=True,
    )

    produced = scratch / f"{source.stem}.xlsx"
    if not produced.exists():
        raise RuntimeError(f"LibreOffice produced no output for {source}")
    shutil.move(str(produced), str(source))
    shutil.rmtree(scratch, ignore_errors=True)


def set_calc_mode(path: str, mode: str, full_calc_on_load: Optional[bool] = None) -> None:
    """Rewrite a workbook's calcPr calcMode directly in the .xlsx zip.

    Used to test the manual-calculation path in isolation: the fixture is
    recalculated first so cached values exist, then switched to manual, so that
    is_stale=True can only be caused by the calc mode and not by a missing value.
    """
    source = Path(path)
    with zipfile.ZipFile(source) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}

    xml = entries["xl/workbook.xml"].decode("utf-8")
    attrs = f'calcMode="{mode}"'
    if full_calc_on_load is not None:
        attrs += f' fullCalcOnLoad="{"1" if full_calc_on_load else "0"}"'

    if "<calcPr" in xml:
        start = xml.index("<calcPr")
        end = xml.index(">", start) + 1
        xml = xml[:start] + f"<calcPr calcId=\"191029\" {attrs}/>" + xml[end:]
    else:
        xml = xml.replace("</workbook>", f"<calcPr calcId=\"191029\" {attrs}/></workbook>")

    entries["xl/workbook.xml"] = xml.encode("utf-8")

    with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)


def strip_calc_pr(path: str) -> None:
    """Remove calcPr entirely, so calc_mode has nothing to read.

    The parser must report "unknown" here rather than assuming "automatic".
    """
    source = Path(path)
    with zipfile.ZipFile(source) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}

    xml = entries["xl/workbook.xml"].decode("utf-8")
    while "<calcPr" in xml:
        start = xml.index("<calcPr")
        end = xml.index(">", start) + 1
        xml = xml[:start] + xml[end:]
    entries["xl/workbook.xml"] = xml.encode("utf-8")

    with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
