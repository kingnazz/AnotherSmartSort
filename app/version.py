"""Single source of truth for the application version.

Everything downstream reads from here -- the Python package, the About dialog,
PyInstaller's Windows metadata, the MSI ProductVersion, artifact filenames and
the CI workflows. Change the version in this one file and nowhere else.

    python -m app.version          # prints 1.0.1
    python -m app.version --windows  # prints 1.0.1.0
"""

from __future__ import annotations

import re
import sys

#: The application version. This is the only place it is written down.
__version__ = "1.0.1"

_SEMVER_RE = re.compile(
    r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:[-+](?P<label>[0-9A-Za-z.\-]+))?$"
)


def version_tuple() -> tuple[int, int, int]:
    """``1.0.1`` -> ``(1, 0, 1)`` -- the numeric parts, label ignored."""
    match = _SEMVER_RE.match(__version__)
    if not match:  # pragma: no cover - guarded by tests
        raise ValueError(f"Malformed application version: {__version__!r}")
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )


def windows_version(build: int = 0) -> str:
    """``1.0.1`` -> ``1.0.1.0`` -- the four-part form Windows requires.

    Windows Installer compares only the first three fields for major upgrades,
    so the fourth is a build counter and must not carry meaning.
    """
    major, minor, patch = version_tuple()
    return f"{major}.{minor}.{patch}.{int(build)}"


def windows_version_tuple(build: int = 0) -> tuple[int, int, int, int]:
    """The four-part version as integers, for PyInstaller's version resource."""
    major, minor, patch = version_tuple()
    return (major, minor, patch, int(build))


def is_prerelease() -> bool:
    match = _SEMVER_RE.match(__version__)
    return bool(match and match.group("label"))


def _main(argv: list[str]) -> int:
    if "--windows" in argv:
        print(windows_version())
    else:
        print(__version__)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
