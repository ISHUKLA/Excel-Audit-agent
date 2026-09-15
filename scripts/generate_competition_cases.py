#!/usr/bin/env python3
"""Build, recalculate and qualify the requested competition cases."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", nargs="+", type=int, choices=(7, 8, 9, 10), required=True)
    parser.add_argument("--node", default=os.environ.get("NODE_BIN") or shutil.which("node"))
    parser.add_argument("--soffice", default=os.environ.get("SOFFICE_BIN"))
    parser.add_argument(
        "--artifact-tool-module",
        default=os.environ.get("ARTIFACT_TOOL_MODULE"),
        help="Optional file URL or path to artifact_tool.mjs when the package is unavailable",
    )
    parser.add_argument("--build-dir", type=Path)
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
                str(ROOT / "scripts" / "build_competition_cases.mjs"),
                "--output-dir",
                str(build_dir),
                "--cases",
                ",".join(str(case) for case in args.cases),
            ],
            check=True,
            cwd=ROOT,
            env=environment,
        )
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "finalize_competition_cases.py"),
                "--build-dir",
                str(build_dir),
                "--cases",
                *(str(case) for case in args.cases),
            ],
            check=True,
            cwd=ROOT,
            env=environment,
        )

    if args.build_dir:
        args.build_dir.mkdir(parents=True, exist_ok=True)
        generate(args.build_dir.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix="excel-audit-competition-") as temp:
            generate(Path(temp))


if __name__ == "__main__":
    main()
