"""Downloading an update and handing it to Windows Installer.

This is the only code path that fetches a file and then *executes* it, so the
tests here are weighted towards the ways that could go wrong rather than the
happy path: an asset URL pointing somewhere it should not, a download that does
not match its published checksum, a build that has no business installing an
MSI over itself.

The check that finds the update is covered in tests/test_updates.py.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import requests

from app.services.update_installer import (
    DownloadOutcome,
    can_self_install,
    download_installer,
    expected_checksum,
    launch_installer,
)
from app.services.update_service import ASSET_URL_PREFIX, UpdateCheck, find_release_assets

INSTALLER = "SmartPDFSorter-Setup-1.0.1.msi"
GOOD_MSI_URL = f"{ASSET_URL_PREFIX}v1.0.1/{INSTALLER}"
GOOD_SUMS_URL = f"{ASSET_URL_PREFIX}v1.0.1/SHA256SUMS.txt"


def release_payload(assets):
    return {"tag_name": "v1.0.1", "html_url": "https://example.com/r", "assets": assets}


def asset(name, url, size=0):
    return {"name": name, "browser_download_url": url, "size": size}


class FakeResponse:
    """Enough of a requests response for both the plain and streaming paths."""

    def __init__(self, status_code=200, text="", chunks=(), headers=None):
        self.status_code = status_code
        self.text = text
        self._chunks = list(chunks)
        self.headers = headers or {}

    def iter_content(self, chunk_size=None):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    """Serves queued responses by URL, or raises a queued exception."""

    def __init__(self, by_url):
        self._by_url = by_url
        self.requested: list[str] = []

    def get(self, url, **kwargs):
        self.requested.append(url)
        item = self._by_url[url]
        if isinstance(item, Exception):
            raise item
        return item


def sums_body(payload: bytes, name: str = INSTALLER) -> str:
    return f"{hashlib.sha256(payload).hexdigest()}  {name}\n"


def working_session(payload: bytes, *, chunk: int = 7):
    chunks = [payload[i : i + chunk] for i in range(0, len(payload), chunk)]
    return FakeSession(
        {
            GOOD_SUMS_URL: FakeResponse(200, text=sums_body(payload)),
            GOOD_MSI_URL: FakeResponse(200, chunks=chunks),
        }
    )


def check_for(payload_size: int = 0) -> UpdateCheck:
    return UpdateCheck(
        current_version="1.0.0",
        latest_version="1.0.1",
        installer_url=GOOD_MSI_URL,
        installer_name=INSTALLER,
        installer_size=payload_size,
        checksums_url=GOOD_SUMS_URL,
    )


class TestFindingTheInstaller:
    def test_it_picks_the_msi_and_its_checksums(self) -> None:
        found = find_release_assets(
            release_payload(
                [
                    asset(INSTALLER, GOOD_MSI_URL, 97844224),
                    asset("SHA256SUMS.txt", GOOD_SUMS_URL),
                    asset("SmartPDFSorter-Portable-1.0.1.exe", f"{ASSET_URL_PREFIX}v1.0.1/p.exe"),
                ]
            )
        )
        assert found == (GOOD_MSI_URL, INSTALLER, 97844224, GOOD_SUMS_URL)

    def test_an_asset_served_from_elsewhere_is_refused(self) -> None:
        """The URL comes out of a JSON response and ends up being executed.

        Anything not on this repository's own release path is discarded, so a
        mangled or redirected feed cannot point the installer at another host.
        """
        found = find_release_assets(
            release_payload([asset(INSTALLER, "https://evil.example.com/payload.msi")])
        )
        assert found == ("", "", 0, "")

    def test_a_lookalike_host_is_refused(self) -> None:
        found = find_release_assets(
            release_payload(
                [asset(INSTALLER, "https://github.com.evil.example.com/x/releases/download/v1/a.msi")]
            )
        )
        assert found[0] == ""

    def test_another_repositorys_release_is_refused(self) -> None:
        found = find_release_assets(
            release_payload(
                [asset(INSTALLER, "https://github.com/someone/else/releases/download/v1/a.msi")]
            )
        )
        assert found[0] == ""

    def test_the_portable_exe_is_never_offered_as_an_installer(self) -> None:
        """It installs nothing, so it cannot upgrade an installation."""
        found = find_release_assets(
            release_payload(
                [
                    asset(
                        "SmartPDFSorter-Portable-1.0.1.exe",
                        f"{ASSET_URL_PREFIX}v1.0.1/SmartPDFSorter-Portable-1.0.1.exe",
                    )
                ]
            )
        )
        assert found[0] == ""

    def test_a_release_with_no_assets_is_not_an_error(self) -> None:
        assert find_release_assets(release_payload([])) == ("", "", 0, "")
        assert find_release_assets({"tag_name": "v1.0.1"}) == ("", "", 0, "")


class TestWhetherAnUpdateCanBeDownloaded:
    def test_it_needs_both_a_newer_version_and_an_installer(self) -> None:
        assert check_for().can_download

    def test_no_installer_means_no_download(self) -> None:
        assert not UpdateCheck("1.0.0", latest_version="1.0.1").can_download

    def test_the_same_version_is_never_downloaded(self) -> None:
        same = UpdateCheck(
            "1.0.1", latest_version="1.0.1", installer_url=GOOD_MSI_URL, installer_name=INSTALLER
        )
        assert not same.can_download


class TestTheChecksumFile:
    def test_it_finds_the_line_for_one_file(self) -> None:
        body = "aa\n" + "b" * 64 + f"  {INSTALLER}\n" + "c" * 64 + "  other.exe\n"
        assert expected_checksum(body, INSTALLER) == "b" * 64

    def test_a_file_that_is_not_listed_has_no_checksum(self) -> None:
        assert expected_checksum("d" * 64 + "  other.exe", INSTALLER) == ""

    def test_malformed_lines_are_ignored(self) -> None:
        for body in ("", "garbage", "short  " + INSTALLER, "z" * 64 + f"  {INSTALLER}"):
            assert expected_checksum(body, INSTALLER) == ""


class TestWhichBuildsMayInstallUpdates:
    """Only the installed build. The MSI upgrades an MSI installation; run
    against the portable EXE it would install a second copy beside it."""

    def frozen_as(self, monkeypatch, *, executable, bundle, windows=True):
        from app.services import update_installer

        monkeypatch.setattr(update_installer, "is_windows", lambda: windows)
        monkeypatch.setattr(update_installer.sys, "frozen", True, raising=False)
        monkeypatch.setattr(update_installer.sys, "executable", executable, raising=False)
        monkeypatch.setattr(update_installer.sys, "_MEIPASS", bundle, raising=False)

    def test_the_installed_onedir_build_may(self, monkeypatch, tmp_path: Path) -> None:
        app_dir = tmp_path / "AS Resume Sorter"
        (app_dir / "_internal").mkdir(parents=True)
        self.frozen_as(
            monkeypatch,
            executable=str(app_dir / "SmartPDFSorter.exe"),
            bundle=str(app_dir / "_internal"),
        )
        assert can_self_install()

    def test_the_portable_onefile_build_may_not(self, monkeypatch, tmp_path: Path) -> None:
        """A onefile build unpacks to a temp directory, nowhere near its EXE."""
        (tmp_path / "extracted").mkdir()
        (tmp_path / "portable").mkdir()
        self.frozen_as(
            monkeypatch,
            executable=str(tmp_path / "portable" / "SmartPDFSorter-Portable.exe"),
            bundle=str(tmp_path / "extracted"),
        )
        assert not can_self_install()

    def test_a_source_checkout_may_not(self, monkeypatch) -> None:
        from app.services import update_installer

        monkeypatch.setattr(update_installer, "is_windows", lambda: True)
        monkeypatch.setattr(update_installer.sys, "frozen", False, raising=False)
        assert not can_self_install()

    def test_other_platforms_may_not(self, monkeypatch, tmp_path: Path) -> None:
        app_dir = tmp_path / "app"
        (app_dir / "_internal").mkdir(parents=True)
        self.frozen_as(
            monkeypatch,
            executable=str(app_dir / "run"),
            bundle=str(app_dir / "_internal"),
            windows=False,
        )
        assert not can_self_install()


class TestDownloading:
    PAYLOAD = b"pretend this is ninety megabytes of installer" * 3

    def test_a_verified_download_is_written(self, tmp_path: Path) -> None:
        outcome = download_installer(
            check_for(len(self.PAYLOAD)),
            session=working_session(self.PAYLOAD),
            destination=tmp_path,
        )
        assert outcome.ok, outcome.error
        assert outcome.path.name == INSTALLER
        assert outcome.path.read_bytes() == self.PAYLOAD

    def test_progress_is_reported_while_it_streams(self, tmp_path: Path) -> None:
        seen: list[tuple[int, int]] = []
        download_installer(
            check_for(len(self.PAYLOAD)),
            session=working_session(self.PAYLOAD),
            destination=tmp_path,
            on_progress=lambda done, total: seen.append((done, total)),
        )
        assert seen, "a 90 MB download that reports nothing looks like a hang"
        assert seen[-1][0] == len(self.PAYLOAD)
        assert [done for done, _ in seen] == sorted(done for done, _ in seen)

    def test_a_download_that_fails_verification_is_refused_and_deleted(
        self, tmp_path: Path
    ) -> None:
        """The whole point of the checksum. A file that failed it must not
        survive on disk, where somebody could run it by hand."""
        session = FakeSession(
            {
                GOOD_SUMS_URL: FakeResponse(200, text=sums_body(b"a different file")),
                GOOD_MSI_URL: FakeResponse(200, chunks=[self.PAYLOAD]),
            }
        )
        outcome = download_installer(check_for(), session=session, destination=tmp_path)

        assert not outcome.ok
        assert "checksum" in outcome.error.lower()
        assert list(tmp_path.iterdir()) == [], "the rejected installer was left on disk"

    def test_an_unreadable_checksum_file_stops_it_before_downloading(
        self, tmp_path: Path
    ) -> None:
        """Running an unverified installer with elevation is not an acceptable
        fallback, so this refuses rather than proceeding."""
        session = FakeSession(
            {
                GOOD_SUMS_URL: FakeResponse(404),
                GOOD_MSI_URL: FakeResponse(200, chunks=[self.PAYLOAD]),
            }
        )
        outcome = download_installer(check_for(), session=session, destination=tmp_path)

        assert not outcome.ok
        assert GOOD_MSI_URL not in session.requested, "it downloaded before checking"
        assert list(tmp_path.iterdir()) == []

    def test_a_release_with_no_installer_is_declined_politely(self, tmp_path: Path) -> None:
        outcome = download_installer(
            UpdateCheck("1.0.0", latest_version="1.0.1"),
            session=FakeSession({}),
            destination=tmp_path,
        )
        assert not outcome.ok
        assert "no installer" in outcome.error.lower()

    def test_an_http_error_on_the_installer_is_reported(self, tmp_path: Path) -> None:
        session = FakeSession(
            {
                GOOD_SUMS_URL: FakeResponse(200, text=sums_body(self.PAYLOAD)),
                GOOD_MSI_URL: FakeResponse(503),
            }
        )
        outcome = download_installer(check_for(), session=session, destination=tmp_path)
        assert not outcome.ok
        assert "503" in outcome.error

    def test_cancelling_leaves_no_file_and_no_complaint(self, tmp_path: Path) -> None:
        outcome = download_installer(
            check_for(),
            session=working_session(self.PAYLOAD),
            destination=tmp_path,
            should_cancel=lambda: True,
        )
        assert not outcome.ok
        assert outcome.error == "", "a cancellation is not a failure to announce"
        assert list(tmp_path.iterdir()) == []

    def test_an_absurdly_large_download_is_stopped(self, tmp_path: Path, monkeypatch) -> None:
        from app.services import update_installer

        monkeypatch.setattr(update_installer, "_MAX_INSTALLER_BYTES", 10)
        outcome = download_installer(
            check_for(), session=working_session(self.PAYLOAD), destination=tmp_path
        )
        assert not outcome.ok
        assert "large" in outcome.error.lower()
        assert list(tmp_path.iterdir()) == []

    @pytest.mark.parametrize(
        ("raised", "expected"),
        [
            (requests.Timeout("slow"), "timed out"),
            (requests.ConnectionError("no route"), "connection"),
            (requests.RequestException("odd"), "could not be completed"),
        ],
    )
    def test_network_failures_come_back_as_sentences(
        self, tmp_path: Path, raised, expected: str
    ) -> None:
        session = FakeSession(
            {GOOD_SUMS_URL: FakeResponse(200, text=sums_body(self.PAYLOAD)), GOOD_MSI_URL: raised}
        )
        outcome = download_installer(check_for(), session=session, destination=tmp_path)
        assert not outcome.ok
        assert expected in outcome.error.lower()

    def test_no_failure_mode_raises(self, tmp_path: Path) -> None:
        """The caller is a button. An exception here is a crash on a click."""
        for session in (
            FakeSession({GOOD_SUMS_URL: requests.ConnectionError("x")}),
            FakeSession(
                {GOOD_SUMS_URL: FakeResponse(200, text="nonsense"), GOOD_MSI_URL: FakeResponse(500)}
            ),
            working_session(self.PAYLOAD),
        ):
            outcome = download_installer(check_for(), session=session, destination=tmp_path)
            assert isinstance(outcome, DownloadOutcome)

    def test_an_earlier_download_is_cleared_out(self, tmp_path: Path) -> None:
        """One installer in the cache at a time, not one per release ever seen."""
        (tmp_path / "SmartPDFSorter-Setup-0.9.0.msi").write_bytes(b"old")
        download_installer(
            check_for(), session=working_session(self.PAYLOAD), destination=tmp_path
        )
        assert [p.name for p in tmp_path.iterdir()] == [INSTALLER]


class TestLaunchingTheInstaller:
    def test_it_hands_the_file_to_windows_installer(self, tmp_path: Path, monkeypatch) -> None:
        from app.services import update_installer

        started: list[list[str]] = []
        monkeypatch.setattr(
            update_installer, "start_detached", lambda cmd: started.append(list(cmd)) or True
        )
        msi = tmp_path / INSTALLER
        msi.write_bytes(b"x")

        assert launch_installer(msi)
        assert started == [["msiexec.exe", "/i", str(msi)]]

    def test_it_is_interactive_not_silent(self, tmp_path: Path, monkeypatch) -> None:
        """A silent install behind an unexplained elevation prompt is the wrong
        shape for replacing the application somebody is using."""
        from app.services import update_installer

        started: list[list[str]] = []
        monkeypatch.setattr(
            update_installer, "start_detached", lambda cmd: started.append(list(cmd)) or True
        )
        msi = tmp_path / INSTALLER
        msi.write_bytes(b"x")
        launch_installer(msi)

        assert "/qn" not in started[0], "the installer must show its window"

    def test_a_missing_file_is_not_launched(self, tmp_path: Path) -> None:
        assert not launch_installer(tmp_path / "gone.msi")
