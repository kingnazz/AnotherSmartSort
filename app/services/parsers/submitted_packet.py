"""Submitted applicant packet parser.

One applicant's complete submission, exported as a single PDF: a generated
application form followed by whatever they uploaded, with **no separator
pages** between them::

    Stevens-Bothman, Nicole (Submitted on: 8/5/2026)
    Biographical
    Name
    Nicole Stevens-Bothman
    ...                          <- generated application form
    Dear Hiring Committee, ...   <- cover letter (uploaded)
    Nicole Stevens-Bothman       <- resume (uploaded)
    Unofficial Transcript        <- transcript (uploaded)

Nothing announces where the form stops and the uploads begin, which is why
this needs structure rather than page-by-page scoring. Two failure modes
matter, and the design targets both:

*Do not mistake the form for a resume.* Later pages of a generated
application form list employment and education history, which reads exactly
like a resume to a keyword classifier. The form's own vocabulary
(``Biographical``, ``Employment History``, ``Certificates/Licenses``) and its
field layout suppress attachment detection while the form is still running.

*Do not invent a document out of a sparse page.* A cover letter's second page
often holds only a signature; an application form can contain a blank page.
Neither is evidence of a new document, so a page only ever starts one by
affirmatively looking like the *opening* of a cover letter, resume or
transcript. Everything else continues whatever is already open.

The same parser handles one applicant per file and many packets concatenated
into one PDF: a new generated first page definitively closes the previous
applicant's last attachment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.candidate import Candidate, normalize_person_name
from app.models.enums import SeparatorPolicy
from app.models.page import PageAnalysis
from app.profiles.base import DocumentProfile
from app.profiles.recruiting import APPLICATION_REPORT
from app.services.metadata_service import MetadataExtractor
from app.services.parsers.attachments import (
    COVER_LETTER_OPENINGS,
    FORM_SECTION_MARKERS,
    RESUME_HEADINGS,
    RESUME_TITLES,
    TRANSCRIPT_MARKERS,
    TRANSCRIPT_SUPPORTING,
    AttachmentDetector,
)
from app.services.parsers.base import ParseOutcome, ParserMatch, assign_page
from app.services.text_features import PageFeatures

#: ``Stevens-Bothman, Nicole (Submitted on: 8/5/2026)`` -- the generated
#: header that opens an applicant's packet. Distinctive enough on its own to
#: identify the format.
PACKET_HEADER_RE = re.compile(
    r"(?im)^\s*(?P<last>[A-Za-z][\w'’\-]*(?:\s+[A-Za-z][\w'’\-]*)*)\s*,\s*"
    r"(?P<first>[A-Za-z][\w'’.\-]*(?:\s+[A-Za-z][\w'’.\-]*)*)\s*"
    r"\(\s*submitted\s+on\s*:\s*(?P<date>[^)]{1,40})\)"
)


@dataclass
class _Segment:
    """One contiguous run of pages forming one logical document."""

    document_type: str
    first_page: int
    last_page: int
    review_reason: str = ""


class SubmittedApplicantPacketParser:
    """Deterministic parser for submitted applicant packets."""

    name = "Submitted applicant packet"

    def __init__(
        self,
        profile: DocumentProfile,
        metadata_extractor: MetadataExtractor | None = None,
    ) -> None:
        self.profile = profile
        self.metadata = metadata_extractor or MetadataExtractor()
        self.attachments = AttachmentDetector(profile)

    # ------------------------------------------------------------------
    def can_parse(self, features_list: list[PageFeatures]) -> ParserMatch:
        """Match on the generated packet header, corroborated by form sections.

        The header pattern is the signature; requiring at least one of the
        form's own section headings alongside it stops an unrelated document
        that happens to contain a ``Surname, Given (Submitted on: …)`` line
        from being claimed.
        """
        if not features_list:
            return ParserMatch.no()

        header_pages = [
            index
            for index, features in enumerate(features_list)
            if PACKET_HEADER_RE.search(features.text)
        ]
        if not header_pages:
            return ParserMatch.no()

        first = features_list[header_pages[0]]
        sections = sum(1 for marker in FORM_SECTION_MARKERS if marker in first.flat)
        if sections >= 2:
            return ParserMatch(0.95, "generated applicant packet form header")
        if sections == 1:
            return ParserMatch(0.80, "applicant packet header with form section")
        return ParserMatch(0.65, "applicant packet header")

    # ------------------------------------------------------------------
    def parse(
        self,
        pages: list[PageAnalysis],
        features_list: list[PageFeatures],
        *,
        separator_policy: SeparatorPolicy,
    ) -> ParseOutcome:
        outcome = ParseOutcome(parser=self.name)
        packets = self._split_packets(features_list)
        outcome.metadata["packets"] = len(packets)

        for start, end in packets:
            candidate = self._identity(features_list[start])
            segments = self._segment(features_list, start, end)
            for segment in segments:
                outcome.documents_found += 1
                for index in range(segment.first_page, segment.last_page + 1):
                    if index >= len(pages) or pages[index].error:
                        continue
                    assign_page(
                        pages[index],
                        segment.document_type,
                        candidate,
                        starts_new_document=index == segment.first_page,
                        parser_name=self.name,
                        reason=(
                            f"{segment.document_type.lower()} starts here"
                            if index == segment.first_page
                            else "continues open section"
                        ),
                    )
                if segment.review_reason:
                    pages[segment.first_page].add_review_reason(segment.review_reason)
        return outcome

    # ------------------------------------------------------------------
    def _split_packets(self, features_list: list[PageFeatures]) -> list[tuple[int, int]]:
        """Page ranges of each applicant packet in the file.

        A generated first page closes whatever the previous applicant had
        open, which is what makes concatenated batches work without any
        separator between them.
        """
        starts: list[int] = []
        current_key = ""

        for index, features in enumerate(features_list):
            key = self._packet_header_key(features)
            if not key:
                continue
            # The generated header repeats as a running header on every
            # continuation page, so its presence alone cannot open a packet.
            # A *change of applicant* can, which is what makes concatenated
            # batches split correctly while a single applicant's form stays
            # whole however many pages it runs to.
            if key != current_key:
                starts.append(index)
                current_key = key

        if not starts:
            return []
        ends = starts[1:] + [len(features_list)]
        return [(start, end - 1) for start, end in zip(starts, ends)]

    def _packet_header_key(self, features: PageFeatures) -> str:
        """Normalized applicant identity from the generated header, if present."""
        match = PACKET_HEADER_RE.search("\n".join(features.first_lines[:6]))
        if not match:
            return ""
        if not any(marker in features.flat for marker in FORM_SECTION_MARKERS):
            return ""
        return normalize_person_name(
            f"{match.group('first').strip()} {match.group('last').strip()}"
        )

    # ------------------------------------------------------------------
    def _segment(
        self, features_list: list[PageFeatures], start: int, end: int
    ) -> list[_Segment]:
        """Split one packet into its application form and uploaded attachments."""
        segments = [_Segment(APPLICATION_REPORT, start, start)]

        for index in range(start + 1, end + 1):
            features = features_list[index]
            current = segments[-1]
            opening = self.attachments.opening(features, current.document_type)

            if opening is None:
                current.last_page = index
                continue

            segments.append(_Segment(opening, index, index))

        return segments

    # ------------------------------------------------------------------
    def _identity(self, features: PageFeatures) -> Candidate:
        """Who the packet belongs to, from the generated form.

        The form's own ``Name`` field is preferred over inverting the
        ``Surname, Given`` header, because it prints the name the way the
        applicant writes it. The header is the fallback when the field is
        missing.
        """
        candidate = self.metadata.extract(features, document_type=APPLICATION_REPORT)
        if candidate.name:
            return candidate

        match = PACKET_HEADER_RE.search(features.text)
        if match:
            candidate.name = f"{match.group('first').strip()} {match.group('last').strip()}"
        return candidate


__all__ = [
    "SubmittedApplicantPacketParser",
    "PACKET_HEADER_RE",
    "FORM_SECTION_MARKERS",
    "COVER_LETTER_OPENINGS",
    "RESUME_HEADINGS",
    "TRANSCRIPT_MARKERS",
]
