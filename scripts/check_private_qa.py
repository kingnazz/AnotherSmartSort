#!/usr/bin/env python3
"""Fail when confidential QA locations or PDFs are tracked by Git."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path, PurePosixPath


FAILURE_MESSAGE = "Confidential QA files must not be tracked."


def is_forbidden_qa_path(path: str) -> bool:
    """Return whether a repository-relative path violates the QA policy."""
    normalized = path.replace("\\", "/").removeprefix("./")
    parts = PurePosixPath(normalized).parts
    if len(parts) < 2 or parts[0] != "qa":
        return False
    return parts[1] in {"input", "output"} or normalized.lower().endswith(".pdf")


def find_forbidden_tracked_paths(repo_root: Path) -> list[str]:
    """Read tracked QA paths without inspecting any file contents."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "qa"],
        cwd=repo_root,
        check=True,
        stdout=subprocess.PIPE,
    )
    paths = result.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    return [path for path in paths if path and is_forbidden_qa_path(path)]


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    try:
        forbidden = find_forbidden_tracked_paths(repo_root)
    except (OSError, subprocess.CalledProcessError):
        print("Unable to verify the confidential QA file policy.", file=sys.stderr)
        return 2
    if forbidden:
        print(FAILURE_MESSAGE, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
