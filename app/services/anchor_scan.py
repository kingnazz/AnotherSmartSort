"""Whole-file anchor scan for PDFs no structured parser recognises.

The generic pipeline decides page by page, carrying a little context forward.
That works for short files and struggles on long ones: a resume's fourth page
looks like nothing much on its own, and by the time enough pages have wobbled,
a document has been split in the middle.

This pass looks at the whole file first, cheaply, for the handful of places
where a document's *start* is stated rather than inferred -- a document title,
a letter's salutation, page numbering restarting at one, a different
applicant's name -- and uses those to propose boundaries before any page is
classified. A strong anchor outranks a weak page-level opinion, which is the
right way round: the anchor is evidence about structure, and the page score is
evidence about appearance.

The output is deliberately *provisional*. This does not assign types or
identity; it proposes where documents begin, and the existing classifier and
grouping engine do the rest with better information than they had.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models.candidate import normalize_person_name
from app.profiles.base import DocumentProfile
from app.services.text_features import SALUTATION_RE, PageFeatures, flatten

#: How much evidence a page needs before it is treated as opening a document.
#: Tuned so one strong anchor (a title, a restarted page count) is enough, and
#: a lone weak hint is not.
ANCHOR_THRESHOLD = 3.0

#: Vendor/system banners that mark the top of a generated report.
VENDOR_MARKERS = (
    "confidential report",
    "application details for",
    "applicant bulk compile",
    "primary application form",
    "job opening id",
    "requisition id",
)

_TOTAL_SCORE_RE = re.compile(r"(?i)\btotal\s+score\b")
_ATTACHMENT_FILENAME_RE = re.compile(r"(?i)\b[\w \-]{1,60}\.(pdf|docx?|rtf|txt)\b")


@dataclass(frozen=True)
class Anchor:
    """One piece of evidence that a document begins on a page."""

    name: str
    weight: float


@dataclass
class PageAnchors:
    """Everything the scan noticed about one page."""

    page_index: int
    anchors: list[Anchor] = field(default_factory=list)

    @property
    def score(self) -> float:
        return round(sum(anchor.weight for anchor in self.anchors), 3)

    @property
    def opens_document(self) -> bool:
        return self.score >= ANCHOR_THRESHOLD

    @property
    def reasons(self) -> list[str]:
        return [f"{a.name} ({a.weight:+.1f})" for a in self.anchors]


@dataclass
class Segment:
    """A provisional logical document: a contiguous run of pages."""

    first_page: int
    last_page: int
    reasons: list[str] = field(default_factory=list)

    @property
    def page_indexes(self) -> list[int]:
        return list(range(self.first_page, self.last_page + 1))

    @property
    def page_count(self) -> int:
        return self.last_page - self.first_page + 1


@dataclass
class AnchorScan:
    """The whole-file view: per-page anchors, and the segments they imply."""

    pages: list[PageAnchors] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)

    def opening_pages(self) -> list[int]:
        return [page.page_index for page in self.pages if page.opens_document]

    def segment_for(self, page_index: int) -> Segment | None:
        for segment in self.segments:
            if segment.first_page <= page_index <= segment.last_page:
                return segment
        return None


class AnchorScanner:
    """Finds document-opening anchors across a whole file."""

    def __init__(self, profile: DocumentProfile) -> None:
        self.profile = profile

    # ------------------------------------------------------------------
    def scan(self, features_list: list[PageFeatures]) -> AnchorScan:
        """Score every page for document-opening evidence, then segment."""
        pages = [
            self._scan_page(index, features, features_list)
            for index, features in enumerate(features_list)
        ]
        return AnchorScan(pages=pages, segments=_segments(pages))

    # ------------------------------------------------------------------
    def _scan_page(
        self, index: int, features: PageFeatures, all_features: list[PageFeatures]
    ) -> PageAnchors:
        found = PageAnchors(page_index=index)
        previous = all_features[index - 1] if index > 0 else None

        if index == 0:
            found.anchors.append(Anchor("First page of the file", 10.0))
            return found

        flat_head = flatten("\n".join(features.first_lines[:12]))

        # -- the file says what this is ---------------------------------
        separator_type = self.profile.separator_type_for(features)
        if separator_type:
            found.anchors.append(Anchor(f"Separator page for {separator_type}", 8.0))

        if previous is not None and self.profile.separator_type_for(previous):
            # A separator announces the document that follows it, so this page
            # continues what the separator opened. Decisive, because such a
            # page usually *also* looks like an opening -- it is one -- and
            # without this the separator would be stranded as a document of
            # its own and the real first page would start a second one.
            found.anchors.append(Anchor("Follows a separator page", -12.0))

        if self._document_title(features):
            found.anchors.append(Anchor("Opens with a document title", 4.0))

        if any(marker in flat_head for marker in VENDOR_MARKERS):
            found.anchors.append(Anchor("System-generated report header", 5.0))

        # -- page numbering ---------------------------------------------
        marker = features.page_marker
        if marker is not None and marker[0] == 1 and marker[1] > 1:
            found.anchors.append(Anchor("Page numbering restarts at 1", 5.0))
        elif marker is not None and marker[0] > 1:
            # Explicitly a continuation page. Strong evidence *against*.
            found.anchors.append(Anchor("Marked as a continuation page", -6.0))

        if previous is not None:
            previous_marker = previous.page_marker
            if (
                previous_marker is not None
                and previous_marker[0] == previous_marker[1] > 1
            ):
                found.anchors.append(Anchor("Previous page was the last of its document", 2.0))

        # -- letters -----------------------------------------------------
        if SALUTATION_RE.search("\n".join(features.first_lines[:14])):
            found.anchors.append(Anchor("Opens with a letter salutation", 4.0))

        # -- generated form endings --------------------------------------
        if previous is not None and _TOTAL_SCORE_RE.search(previous.text):
            found.anchors.append(Anchor("Previous page ended an application form", 5.0))

        # -- uploaded attachment names -----------------------------------
        if index > 0 and _ATTACHMENT_FILENAME_RE.search(
            "\n".join(features.first_lines[:4])
        ):
            found.anchors.append(Anchor("Names an uploaded attachment", 2.5))

        # -- identity ----------------------------------------------------
        if previous is not None:
            change = _identity_change(features, previous)
            if change:
                found.anchors.append(Anchor(change, 4.0))

        # -- contact header ----------------------------------------------
        if features.has_contact_block and previous is not None:
            if not previous.has_contact_block:
                found.anchors.append(Anchor("Opens with a name and contact header", 2.5))

        # -- running headers argue for continuation -----------------------
        if previous is not None and _shares_running_header(features, previous):
            found.anchors.append(Anchor("Repeats the previous page's header", -3.0))

        if features.starts_lowercase:
            found.anchors.append(Anchor("Begins mid-sentence", -4.0))

        return found

    def _document_title(self, features: PageFeatures) -> bool:
        """First line reads as a document's own title (``RESUME``, ``References``)."""
        if not features.first_lines:
            return False
        first = features.first_lines[0].strip().rstrip(":")
        if not (2 <= len(first) <= 60):
            return False
        key = re.sub(r"[^a-z0-9 ]+", " ", flatten(first)).strip()
        key = re.sub(r"\s+", " ", key)
        if not key:
            return False
        if key in self.profile.separator_labels:
            return True
        return any(
            re.fullmatch(rf"{re.escape(label)}s?", key)
            for label in self.profile.separator_labels
        )


# ----------------------------------------------------------------------
def _segments(pages: list[PageAnchors]) -> list[Segment]:
    """Turn per-page opening evidence into contiguous provisional documents."""
    if not pages:
        return []

    segments: list[Segment] = []
    current: Segment | None = None

    for page in pages:
        if current is None or page.opens_document:
            current = Segment(
                first_page=page.page_index,
                last_page=page.page_index,
                reasons=list(page.reasons),
            )
            segments.append(current)
        else:
            current.last_page = page.page_index

    return segments


def _identity_change(current: PageFeatures, previous: PageFeatures) -> str | None:
    """A different person's contact details appearing."""
    current_emails = {email.lower() for email in current.emails}
    previous_emails = {email.lower() for email in previous.emails}
    if current_emails and previous_emails and not (current_emails & previous_emails):
        return "A different email address appears"
    return None


def _shares_running_header(current: PageFeatures, previous: PageFeatures) -> bool:
    if not current.first_lines or not previous.first_lines:
        return False
    return flatten(current.first_lines[0]) == flatten(previous.first_lines[0])


__all__ = [
    "AnchorScanner",
    "AnchorScan",
    "PageAnchors",
    "Segment",
    "Anchor",
    "ANCHOR_THRESHOLD",
]
