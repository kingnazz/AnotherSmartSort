"""The update check: version comparison, and every way the network fails.

A version check is a convenience, so the bar it has to clear is that it never
becomes an obstacle: no exception reaches the caller, no failure mode produces
a wrong "you are up to date", and no failure mode nags about an update that
does not exist.
"""

from __future__ import annotations

import pytest
import requests

from app.services.update_service import (
    LATEST_RELEASE_URL,
    RELEASES_PAGE_URL,
    UpdateCheck,
    check_for_updates,
    parse_version,
)


class FakeResponse:
    def __init__(self, status_code: int = 200, payload=None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """Replays one queued response, or raises a queued exception."""

    def __init__(self, item) -> None:
        self._item = item
        self.calls: list[dict] = []

    def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if isinstance(self._item, Exception):
            raise self._item
        return self._item


def release(tag: str, html_url: str = "https://example.com/releases/tag/v9") -> FakeResponse:
    return FakeResponse(200, {"tag_name": tag, "html_url": html_url})


class TestParseVersion:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("1.0.0", (1, 0, 0)),
            ("v1.0.0", (1, 0, 0)),
            ("  v2.11.3  ", (2, 11, 3)),
            ("1.2.3-rc1", (1, 2, 3)),
            ("1.2.3+build7", (1, 2, 3)),
        ],
    )
    def test_recognised_forms(self, text: str, expected) -> None:
        assert parse_version(text) == expected

    @pytest.mark.parametrize("text", ["", "latest", "1.0", "1.0.0.0", "v", "one.two.three", None])
    def test_unrecognised_forms(self, text) -> None:
        assert parse_version(text) is None

    def test_ordering_is_numeric_not_lexical(self) -> None:
        """``1.10.0`` is newer than ``1.9.0``; string comparison says otherwise."""
        assert parse_version("1.10.0") > parse_version("1.9.0")


class TestUpdateAvailable:
    def check(self, current: str, latest: str) -> UpdateCheck:
        return UpdateCheck(current_version=current, latest_version=latest)

    def test_a_newer_release_is_offered(self) -> None:
        assert self.check("1.0.0", "1.1.0").update_available

    def test_the_same_version_is_not(self) -> None:
        assert not self.check("1.0.0", "1.0.0").update_available

    def test_an_older_release_is_not(self) -> None:
        """A development build ahead of the feed must not be told to downgrade."""
        assert not self.check("1.2.0", "1.1.0").update_available

    def test_a_patch_bump_counts(self) -> None:
        assert self.check("1.0.0", "1.0.1").update_available

    def test_double_digit_minors_compare_numerically(self) -> None:
        assert self.check("1.9.0", "1.10.0").update_available

    @pytest.mark.parametrize(
        ("current", "latest", "expected"),
        [
            ("1.0.1", "1.0.1", False),
            ("1.0.1", "1.0.2", True),
            ("1.0.1", "1.0.3", True),
            ("1.0.2", "1.0.3", True),
            ("1.0.3", "1.0.3", False),
            ("1.0.3", "1.0.2", False),
        ],
    )
    def test_published_release_matrix(self, current: str, latest: str, expected: bool) -> None:
        assert self.check(current, latest).update_available is expected

    def test_nothing_is_offered_when_the_check_failed(self) -> None:
        assert not UpdateCheck("1.0.0", error="offline").update_available

    def test_nothing_is_offered_when_there_are_no_releases(self) -> None:
        assert not UpdateCheck("1.0.0").update_available


class TestCheckForUpdates:
    def test_a_newer_release_is_reported(self) -> None:
        session = FakeSession(release("v1.4.0", "https://example.com/r/1.4.0"))
        result = check_for_updates(current_version="1.0.0", session=session)

        assert result.checked
        assert result.update_available
        assert result.latest_version == "1.4.0"
        assert result.release_url == "https://example.com/r/1.4.0"
        assert result.message == (
            "A new version of AS Resume Sorter is available.\n\n"
            "Installed version: 1.0.0\n"
            "Latest version: 1.4.0"
        )

    def test_being_current_is_reported_plainly(self) -> None:
        result = check_for_updates(current_version="1.0.0", session=FakeSession(release("v1.0.0")))

        assert result.checked
        assert not result.update_available
        assert result.message == (
            "AS Resume Sorter is up to date.\n\n"
            "Installed version: 1.0.0\n"
            "Latest published version: 1.0.0"
        )

    def test_it_asks_the_release_feed(self) -> None:
        session = FakeSession(release("v1.0.0"))
        check_for_updates(current_version="1.0.0", session=session)

        assert session.calls[0]["url"] == LATEST_RELEASE_URL
        assert session.calls[0]["timeout"] > 0, "a check with no timeout can hang forever"

    def test_the_feed_is_githubs_latest_published_stable_release(self) -> None:
        """GitHub excludes drafts and prereleases from this endpoint."""
        assert LATEST_RELEASE_URL.endswith("/releases/latest")
        assert "/tags/" not in LATEST_RELEASE_URL

    # -- the ways it fails ---------------------------------------------
    def test_no_releases_yet_is_not_an_error(self) -> None:
        """A freshly published repository has no releases. That is normal."""
        result = check_for_updates(current_version="1.0.0", session=FakeSession(FakeResponse(404)))

        assert result.checked, "an empty release feed was treated as a failure"
        assert not result.update_available
        assert "No releases" in result.message

    def test_being_offline_says_so(self) -> None:
        result = check_for_updates(
            current_version="1.0.0", session=FakeSession(requests.ConnectionError("no route"))
        )
        assert not result.checked
        assert not result.update_available
        assert "connection" in result.message.lower()

    def test_a_timeout_says_so(self) -> None:
        result = check_for_updates(
            current_version="1.0.0", session=FakeSession(requests.Timeout("slow"))
        )
        assert not result.checked
        assert "timed out" in result.message.lower()

    def test_rate_limiting_says_so(self) -> None:
        result = check_for_updates(
            current_version="1.0.0", session=FakeSession(FakeResponse(403))
        )
        assert not result.checked
        assert "rate limit" in result.message.lower()

    def test_an_unexpected_status_is_reported_not_raised(self) -> None:
        result = check_for_updates(
            current_version="1.0.0", session=FakeSession(FakeResponse(500))
        )
        assert not result.checked
        assert "500" in result.message

    def test_a_response_that_is_not_json_is_survived(self) -> None:
        result = check_for_updates(
            current_version="1.0.0", session=FakeSession(FakeResponse(200, None))
        )
        assert not result.checked
        assert not result.update_available

    def test_a_json_response_of_the_wrong_shape_is_survived(self) -> None:
        result = check_for_updates(
            current_version="1.0.0", session=FakeSession(FakeResponse(200, ["not", "a", "dict"]))
        )
        assert not result.checked

    def test_an_unparseable_tag_is_not_offered_as_an_update(self) -> None:
        """A tag like "nightly" must never be compared against a real version."""
        result = check_for_updates(
            current_version="1.0.0", session=FakeSession(release("nightly"))
        )
        assert not result.checked
        assert not result.update_available

    def test_a_missing_html_url_falls_back_to_the_releases_page(self) -> None:
        session = FakeSession(FakeResponse(200, {"tag_name": "v2.0.0"}))
        result = check_for_updates(current_version="1.0.0", session=session)

        assert result.update_available
        assert result.release_url == RELEASES_PAGE_URL

    def test_the_returned_release_url_is_preserved(self) -> None:
        specific = "https://github.com/kingnazz/AnotherSmartSort/releases/tag/v1.0.3"
        result = check_for_updates(
            current_version="1.0.1", session=FakeSession(release("v1.0.3", specific))
        )

        assert result.release_url == specific

    def test_success_logs_only_the_update_versions_and_result(self, monkeypatch) -> None:
        events: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            "app.services.update_service.log_event",
            lambda _logger, event, **fields: events.append((event, fields)),
        )

        check_for_updates(
            current_version="1.0.1", session=FakeSession(release("v1.0.3"))
        )

        assert events == [
            (
                "update_check",
                {
                    "current_version": "1.0.1",
                    "latest_published_version": "1.0.3",
                    "update_available": True,
                },
            )
        ]

    def test_no_failure_mode_raises(self) -> None:
        """The caller is a dialog; an exception here would be a crash on a button."""
        for item in (
            requests.ConnectionError("x"),
            requests.Timeout("x"),
            requests.RequestException("x"),
            FakeResponse(500),
            FakeResponse(404),
            FakeResponse(200, None),
            FakeResponse(200, "string"),
            release("garbage"),
        ):
            result = check_for_updates(current_version="1.0.0", session=FakeSession(item))
            assert isinstance(result, UpdateCheck)
            assert result.message, "every outcome needs something to show the user"
