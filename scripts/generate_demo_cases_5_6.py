#!/usr/bin/env python3
"""Rebuild the two user-facing demonstration workbooks from the live catalogue.

Python only orchestrates the generation. Workbook authoring remains in
``build_demo_cases_5_6.mjs`` and uses ``@oai/artifact-tool`` exclusively.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.formula_catalogue import SUPPORTED_FUNCTIONS


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", default=os.environ.get("NODE_BIN") or shutil.which("node"))
    parser.add_argument("--soffice", default=os.environ.get("SOFFICE_BIN"))
    parser.add_argument(
        "--artifact-tool-module",
        default=os.environ.get("ARTIFACT_TOOL_MODULE"),
        help="Optional file URL or path to artifact_tool.mjs when the package is not installed",
    )
    parser.add_argument(
        "--build-dir",
        type=Path,
        help="Optional retained intermediate directory; otherwise a temporary directory is used",
    )
    args = parser.parse_args()
    if not args.node:
        parser.error("Node.js was not found; pass --node or set NODE_BIN")

    environment = os.environ.copy()
    if args.soffice:
        environment["SOFFICE_BIN"] = args.soffice
    if args.artifact_tool_module:
        module = args.artifact_tool_module
        if "://" not in module:
            module = Path(module).expanduser().resolve().as_uri()
        environment["ARTIFACT_TOOL_MODULE"] = module

    def generate(build_dir: Path) -> None:
        subprocess.run(
            [
                args.node,
                str(ROOT / "scripts" / "build_demo_cases_5_6.mjs"),
                "--output-dir",
                str(build_dir),
                "--supported-functions",
                json.dumps(sorted(SUPPORTED_FUNCTIONS)),
            ],
            check=True,
            cwd=ROOT,
            env=environment,
        )
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "finalize_demo_cases_5_6.py"),
                "--build-dir",
                str(build_dir),
            ],
            check=True,
            cwd=ROOT,
            env=environment,
        )

    if args.build_dir:
        args.build_dir.mkdir(parents=True, exist_ok=True)
        generate(args.build_dir.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix="excel-audit-demo-cases-5-6-") as temp:
            generate(Path(temp))


if __name__ == "__main__":
    main()
