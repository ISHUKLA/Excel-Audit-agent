#!/usr/bin/env python3
"""Create or verify a companion SHA-256 seal for an independent challenge.

The expected-results file cannot contain its own stable hash. The immutable
companion JSON created here records the hash of the exact expected-results
bytes and the exact workbook bytes without opening or interpreting either.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_input(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be an existing regular file, not a symlink: {path}")
    return path.resolve()


def create_seal(
    *, challenge_id: str, workbook: Path, expected_results: Path, output: Path
) -> dict[str, object]:
    if not challenge_id.strip():
        raise ValueError("challenge_id must be non-empty")
    workbook = _require_input(workbook, "workbook")
    expected_results = _require_input(expected_results, "expected-results package")
    output = output.resolve()
    if output in {workbook, expected_results}:
        raise ValueError("seal output must be separate from both sealed inputs")

    payload: dict[str, object] = {
        "schema_version": 1,
        "challenge_id": challenge_id.strip(),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "hash_algorithm": "SHA-256",
        "workbook": {
            "file_name": workbook.name,
            "byte_length": workbook.stat().st_size,
            "sha256": sha256_file(workbook),
        },
        "expected_results": {
            "file_name": expected_results.name,
            "byte_length": expected_results.stat().st_size,
            "sha256": sha256_file(expected_results),
            "hash_location_note": (
                "The expected-results hash is stored in this companion seal because "
                "a file cannot contain its own stable cryptographic hash."
            ),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    return payload


def verify_seal(*, seal: Path, workbook: Path, expected_results: Path) -> list[str]:
    workbook = _require_input(workbook, "workbook")
    expected_results = _require_input(expected_results, "expected-results package")
    payload = json.loads(_require_input(seal, "seal").read_text(encoding="utf-8"))
    failures: list[str] = []
    for label, path, record in (
        ("workbook", workbook, payload.get("workbook", {})),
        ("expected_results", expected_results, payload.get("expected_results", {})),
    ):
        observed_hash = sha256_file(path)
        observed_size = path.stat().st_size
        if record.get("sha256") != observed_hash:
            failures.append(f"{label} SHA-256 mismatch")
        if record.get("byte_length") != observed_size:
            failures.append(f"{label} byte-length mismatch")
        if record.get("file_name") != path.name:
            failures.append(f"{label} filename mismatch")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="write a new, non-overwriting seal")
    create.add_argument("--challenge-id", required=True)
    create.add_argument("--workbook", type=Path, required=True)
    create.add_argument("--expected-results", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)

    verify = subparsers.add_parser("verify", help="verify exact files against a seal")
    verify.add_argument("--seal", type=Path, required=True)
    verify.add_argument("--workbook", type=Path, required=True)
    verify.add_argument("--expected-results", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "create":
        payload = create_seal(
            challenge_id=args.challenge_id,
            workbook=args.workbook,
            expected_results=args.expected_results,
            output=args.output,
        )
        print(json.dumps(payload, indent=2))
        return

    failures = verify_seal(
        seal=args.seal,
        workbook=args.workbook,
        expected_results=args.expected_results,
    )
    if failures:
        raise SystemExit("Seal verification failed: " + "; ".join(failures))
    print("Seal verification passed: workbook and expected-results bytes are unchanged.")


if __name__ == "__main__":
    main()
