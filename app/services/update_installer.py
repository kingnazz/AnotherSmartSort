"""Downloading a newer release and handing it to Windows Installer.

:mod:`app.services.update_service` answers *is there a newer version?*. This
module answers *fetch it and start it* -- the step that was deliberately left
out when the check was built, because downloading and running an installer is a
different kind of act from reading a version number.

It is the only place the application executes something it fetched from the
network, so the rules are tighter than anywhere else in the codebase:

**The download URL is checked, not trusted.** It arrives inside a JSON
response. :func:`~app.services.update_service.find_release_assets` discards any
asset not served from this repository's own release path, so a mangled or
redirected feed cannot point this module at an arbitrary host.

**The file is verified before it is run.** Every release publishes
``SHA256SUMS.txt`` beside the MSI. The download is hashed while it streams and
compared against that line; a mismatch is refused outright and never reaches
``msiexec``. This catches a truncated or corrupted transfer -- the realistic
failure -- and it is not a defence against a compromised release, which would
compromise the manual download too. Saying so plainly is better than implying
a guarantee that is not there.

**Only an installed build updates itself.** The MSI upgrades an MSI
installation. Run against the portable EXE it would install a *second* copy
beside it, and against a source checkout it means nothing at all. Both of those
keep the old behaviour of opening the release page.

Everything fails soft, like the check it follows: no exception reaches the UI,
and every failure comes back as a sentence a person can read, with the release
page still one click away.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

from app.utils.external_process import is_windows, start_detached
from app.utils.logging_setup import get_logger
from app.utils.paths import cache_dir

logger = get_logger("updates")

#: Read in chunks so a 90 MB installer reports progress rather than appearing
#: to hang, and so the whole file is never held in memory.
_CHUNK_BYTES = 256 * 1024

#: Generous: this is a large file over a home connection, not an API call.
_DOWNLOAD_TIMEOUT_SECONDS = 60

#: An installer far larger than anything this project produces is a sign the
#: URL is not what we think it is. The MSI is ~93 MB; this is a sanity bound,
#: not a tight limit.
_MAX_INSTALLER_BYTES = 500 * 1024 * 1024


def updates_dir() -> Path:
    """Where a downloaded installer is kept until Windows Installer runs it."""
    path = cache_dir() / "updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def can_self_install() -> bool:
    """Whether this build can replace itself by running an MSI.

    True only for the installed (PyInstaller onedir) build on Windows, which is
    the layout the MSI produces and therefore the only one it can upgrade.

    The three layouts are told apart by where PyInstaller unpacked its bundle.
    A onedir build sets ``sys._MEIPASS`` to the ``_internal`` folder beside the
    executable; a onefile build extracts to a temporary directory somewhere
    else entirely; running from source sets it not at all.
    """
    if not is_windows():
        return False
    if not getattr(sys, "frozen", False):
        return False

    bundle = getattr(sys, "_MEIPASS", None)
    if not bundle:
        return False
    try:
        executable_dir = Path(sys.executable).resolve().parent
        return Path(bundle).resolve().is_relative_to(executable_dir)
    except (OSError, ValueError):  # pragma: no cover - defensive
        return False


@dataclass(frozen=True)
class DownloadOutcome:
    """Where the installer landed, or why it did not."""

    path: Path | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.path is not None and not self.error


def expected_checksum(text: str, filename: str) -> str:
    """Pull one file's SHA-256 out of a ``SHA256SUMS.txt`` body.

    The format is ``<hex>  <name>`` per line. Returns ``""`` when the file is
    not listed, which the caller must treat as "cannot verify" rather than
    "verified".
    """
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == filename and len(parts[0]) == 64:
            try:
                int(parts[0], 16)
            except ValueError:
                continue
            return parts[0].lower()
    return ""


def download_installer(
    check,
    *,
    session: requests.Session | None = None,
    destination: Path | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> DownloadOutcome:
    """Fetch the release's MSI, verify it, and return where it was written.

    Never raises. ``check`` is an
    :class:`~app.services.update_service.UpdateCheck` carrying the asset URLs.
    """
    if not check.installer_url or not check.installer_name:
        return DownloadOutcome(error="This release has no installer to download.")

    http = session or requests.Session()
    folder = destination or updates_dir()

    # The checksum first: without it there is nothing to verify against, and
    # discovering that after a 90 MB download would waste the user's time.
    digest = ""
    if check.checksums_url:
        try:
            response = http.get(check.checksums_url, timeout=_DOWNLOAD_TIMEOUT_SECONDS)
            if getattr(response, "status_code", 0) == 200:
                digest = expected_checksum(response.text, check.installer_name)
        except requests.RequestException as exc:
            logger.info("Could not read the checksum file: %s", exc)

    if not digest:
        # Refusing is the right call: the alternative is running an unverified
        # installer with elevation, and the release page is one click away.
        return DownloadOutcome(
            error=(
                "The published checksum for this update could not be read, so the "
                "download cannot be verified. Use the release page instead."
            )
        )

    target = folder / check.installer_name
    partial = target.with_suffix(target.suffix + ".part")
    hasher = hashlib.sha256()
    written = 0

    try:
        _clear_previous_downloads(folder, keep=partial.name)
        with http.get(
            check.installer_url, stream=True, timeout=_DOWNLOAD_TIMEOUT_SECONDS
        ) as response:
            status = getattr(response, "status_code", 0)
            if status != 200:
                return DownloadOutcome(
                    error=f"The download failed ({status}). Use the release page instead."
                )

            total = check.installer_size or _declared_length(response)
            with partial.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=_CHUNK_BYTES):
                    if should_cancel is not None and should_cancel():
                        partial.unlink(missing_ok=True)
                        return DownloadOutcome(error="")
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > _MAX_INSTALLER_BYTES:
                        partial.unlink(missing_ok=True)
                        return DownloadOutcome(
                            error="The download was unexpectedly large and was stopped."
                        )
                    hasher.update(chunk)
                    handle.write(chunk)
                    if on_progress is not None:
                        on_progress(written, total)
    except requests.Timeout:
        partial.unlink(missing_ok=True)
        return DownloadOutcome(error="The download timed out.")
    except requests.ConnectionError:
        partial.unlink(missing_ok=True)
        return DownloadOutcome(
            error="Could not reach the download server. Check your internet connection."
        )
    except requests.RequestException as exc:
        logger.info("Update download failed: %s", exc)
        partial.unlink(missing_ok=True)
        return DownloadOutcome(error="The download could not be completed.")
    except OSError as exc:
        partial.unlink(missing_ok=True)
        return DownloadOutcome(
            error=f"The update could not be saved: {exc.strerror or exc}"
        )

    if hasher.hexdigest() != digest:
        # Deleted rather than kept: a file that failed verification has no
        # legitimate use, and leaving it on disk invites someone to run it.
        partial.unlink(missing_ok=True)
        logger.warning("Update download failed checksum verification")
        return DownloadOutcome(
            error=(
                "The downloaded update did not match its published checksum and "
                "was discarded. Use the release page instead."
            )
        )

    try:
        partial.replace(target)
    except OSError as exc:  # pragma: no cover - defensive
        return DownloadOutcome(error=f"The update could not be saved: {exc.strerror or exc}")

    logger.info("Update downloaded and verified: %s", target)
    return DownloadOutcome(path=target)


def _declared_length(response) -> int:
    try:
        return int(response.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return 0


def _clear_previous_downloads(folder: Path, *, keep: str) -> None:
    """Remove installers left by earlier runs, so the cache holds one at most."""
    try:
        for stale in folder.iterdir():
            if stale.name != keep and stale.is_file():
                stale.unlink(missing_ok=True)
    except OSError:  # pragma: no cover - defensive
        pass


def launch_installer(path: Path) -> bool:
    """Start Windows Installer on a downloaded MSI and return whether it began.

    Deliberately interactive rather than silent. The user gets the same wizard
    they would get from double-clicking the file, can read what is about to
    happen, and can cancel; a silent install that begins on a single click,
    behind an elevation prompt with no explanation, is the wrong shape for
    replacing the application somebody is using.

    The caller is expected to close the application afterwards so the installer
    is not replacing files that are in use.
    """
    if not path.is_file():
        return False
    return start_detached(["msiexec.exe", "/i", str(path)])


__all__ = [
    "DownloadOutcome",
    "can_self_install",
    "download_installer",
    "expected_checksum",
    "launch_installer",
    "updates_dir",
]
