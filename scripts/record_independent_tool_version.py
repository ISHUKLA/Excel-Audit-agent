#!/usr/bin/env python3
"""Record the clean Git commit and existing release tag used for a first run.

This script records evidence only. It deliberately does not create a commit or
tag; freezing and tagging the tool is a separately authorised release action.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

_SAFE_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def record_tool_version(*, repo: Path, release_tag: str, output: Path) -> dict[str, object]:
    repo = repo.resolve()
    if not _SAFE_TAG.fullmatch(release_tag) or ".." in release_tag or "@{" in release_tag:
        raise ValueError("release_tag contains unsafe or invalid characters")
    if _git(repo, "status", "--porcelain"):
        raise RuntimeError("the tool worktree is dirty; freeze it before recording a first-run version")

    commit = _git(repo, "rev-parse", "HEAD")
    tagged_commit = _git(repo, "rev-parse", f"refs/tags/{release_tag}^{{commit}}")
    if tagged_commit != commit:
        raise RuntimeError(
            f"release tag {release_tag!r} points to {tagged_commit}, not tested HEAD {commit}"
        )

    payload: dict[str, object] = {
        "schema_version": 1,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": commit,
        "release_tag": release_tag,
        "commit_timestamp": _git(repo, "log", "-1", "--format=%cI", "HEAD"),
        "worktree_clean": True,
    }
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = record_tool_version(
        repo=args.repo,
        release_tag=args.release_tag,
        output=args.output,
    )
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
