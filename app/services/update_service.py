"""Checking whether a newer release exists.

The application is distributed as an MSI and a portable EXE published to
GitHub Releases. This module answers one question -- *is there a newer version
than the one running?* -- and nothing else. It does not download, install, or
touch the running installation.

That restraint is deliberate. The MSI already performs a correct in-place
upgrade when a user runs a newer one, so the hard part is solved; what was
missing was any way to find out a newer one exists. Downloading and launching
an installer is a separate decision with its own consequences (elevation
prompts, a SmartScreen warning on an unsigned build, a half-applied upgrade if
it fails) and belongs in its own change, on top of this one.

Everything here fails soft. A version check is a convenience: if the network
is down, the release feed has moved, or the machine is offline behind a proxy,
the right outcome is a quiet "could not check", never an error the user has to
dismiss before getting on with their work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import requests

from app import APP_NAME
from app.utils.logging_setup import get_logger, log_event
from app.version import __version__

logger = get_logger("updates")

#: Where releases are published. One place, so pointing the application at a
#: different feed -- a fork, a mirror, a staging repo -- is a single edit.
RELEASE_OWNER = "kingnazz"
RELEASE_REPO = "AnotherSmartSort"

#: GitHub's "newest published release" endpoint. It excludes drafts and
#: pre-releases, which is the behaviour we want: a draft is not released, and
#: a pre-release is not for everybody.
LATEST_RELEASE_URL = (
    f"https://api.github.com/repos/{RELEASE_OWNER}/{RELEASE_REPO}/releases/latest"
)

#: The page a user is sent to in order to actually get the new version.
RELEASES_PAGE_URL = f"https://github.com/{RELEASE_OWNER}/{RELEASE_REPO}/releases"

#: Short enough that a hung network never makes the dialog feel broken.
DEFAULT_TIMEOUT_SECONDS = 10

_VERSION_RE = re.compile(
    r"^\s*v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:[-+](?P<label>[0-9A-Za-z.\-]+))?\s*$"
)


def parse_version(text: str) -> tuple[int, int, int] | None:
    """``v1.2.3`` or ``1.2.3-beta`` -> ``(1, 2, 3)``; anything else -> ``None``.

    The leading ``v`` is optional because release tags conventionally carry one
    and ``app/version.py`` does not. A pre-release label is parsed but ignored
    for ordering: this only ever compares released versions, and treating
    ``1.1.0-rc1`` as equal to ``1.1.0`` is the conservative reading -- it will
    not tell somebody an update exists when it might not.
    """
    match = _VERSION_RE.match(text or "")
    if not match:
        return None
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )


@dataclass(frozen=True)
class UpdateCheck:
    """The outcome of one check, including the ways it can fail."""

    #: Version currently running.
    current_version: str
    #: Version published, when the feed could be read and understood.
    latest_version: str = ""
    #: Where to go and get it.
    release_url: str = RELEASES_PAGE_URL
    #: Set when the check could not be completed. Written for a user to read.
    error: str = ""

    @property
    def checked(self) -> bool:
        """Whether the feed was successfully read."""
        return not self.error

    @property
    def update_available(self) -> bool:
        """Whether the published version is genuinely newer than this one."""
        if self.error or not self.latest_version:
            return False
        latest = parse_version(self.latest_version)
        current = parse_version(self.current_version)
        if latest is None or current is None:
            return False
        return latest > current

    @property
    def message(self) -> str:
        """A concise result the Settings dialog can show verbatim."""
        if self.error:
            return self.error
        if not self.latest_version:
            return "No releases have been published yet."
        if self.update_available:
            return (
                f"A new version of {APP_NAME} is available.\n\n"
                f"Installed version: {self.current_version}\n"
                f"Latest version: {self.latest_version}"
            )
        return (
            f"{APP_NAME} is up to date.\n\n"
            f"Installed version: {self.current_version}\n"
            f"Latest published version: {self.latest_version}"
        )


def _log_result(result: UpdateCheck) -> UpdateCheck:
    """Record a privacy-safe diagnostic summary and return *result*."""
    log_event(
        logger,
        "update_check",
        current_version=result.current_version,
        latest_published_version=result.latest_version or None,
        update_available=result.update_available,
    )
    return result


def check_for_updates(
    *,
    current_version: str = __version__,
    session: requests.Session | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    url: str = LATEST_RELEASE_URL,
) -> UpdateCheck:
    """Ask the release feed what the newest published version is.

    Never raises. Every failure -- offline, timeout, rate limit, a feed that
    moved, a response that is not the JSON we expect -- comes back as an
    ``UpdateCheck`` carrying a message rather than an exception, because there
    is no failure here worth interrupting somebody's work over.
    """
    http = session or requests.Session()
    try:
        response = http.get(
            url,
            timeout=timeout,
            headers={"Accept": "application/vnd.github+json"},
        )
    except requests.Timeout:
        return _log_result(UpdateCheck(current_version, error="The update check timed out."))
    except requests.ConnectionError:
        return _log_result(
            UpdateCheck(
                current_version,
                error="Could not reach the update server. Check your internet connection.",
            )
        )
    except requests.RequestException as exc:
        logger.info("Update check failed: %s", type(exc).__name__)
        return _log_result(
            UpdateCheck(current_version, error="The update check could not be completed.")
        )

    status = getattr(response, "status_code", 0)
    if status == 404:
        # An empty release feed is not an error: a freshly published
        # repository has no releases, and neither does one that only ever
        # ships builds by hand.
        return _log_result(UpdateCheck(current_version))
    if status == 403:
        return _log_result(
            UpdateCheck(
                current_version,
                error="The update server is rate limiting requests. Try again later.",
            )
        )
    if status != 200:
        logger.info("Update check returned HTTP %s", status)
        return _log_result(
            UpdateCheck(
                current_version,
                error=f"The update server returned an unexpected response ({status}).",
            )
        )

    try:
        payload = response.json()
    except ValueError:
        return _log_result(
            UpdateCheck(
                current_version, error="The update server's response could not be read."
            )
        )
    if not isinstance(payload, dict):
        return _log_result(
            UpdateCheck(
                current_version, error="The update server's response could not be read."
            )
        )

    tag = str(payload.get("tag_name") or payload.get("name") or "").strip()
    if parse_version(tag) is None:
        logger.info("Update check got an unrecognised published version tag")
        return _log_result(
            UpdateCheck(
                current_version, error="The published version could not be understood."
            )
        )

    return _log_result(
        UpdateCheck(
            current_version=current_version,
            latest_version=tag.lstrip("v").strip(),
            release_url=str(payload.get("html_url") or RELEASES_PAGE_URL),
        )
    )


__all__ = [
    "UpdateCheck",
    "check_for_updates",
    "parse_version",
    "LATEST_RELEASE_URL",
    "RELEASES_PAGE_URL",
    "RELEASE_OWNER",
    "RELEASE_REPO",
]
