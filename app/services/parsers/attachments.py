"""Recognising where one uploaded attachment ends and the next begins.

Both ATS formats this app parses end the same way: a generated application
form, followed by whatever the applicant uploaded, with nothing between the
uploads announcing the change. A submitted packet runs them together in one
file; a PageUp bulk compile does the same after each applicant's ``Total
score``. The evidence for a boundary is therefore the same evidence in both
places, and it lives here so the two parsers cannot drift apart on it.

The rules are deliberately asymmetric. A page continues whatever is already
open unless it *affirmatively* looks like the opening of something else, for
one reason: over-splitting and over-merging are not equally bad, but both are
bad in ways the user pays for. Splitting a resume in half produces two
half-documents nobody asked for; merging a cover letter into a resume hides a
document the reviewer needed to read. So the detector answers "is this an
opening?" with structure the file states outright -- an attachment filename, a
document title, a restart of page numbering -- before it falls back to what a
page merely looks like, and the caller is expected to send anything still
unclear to review rather than pick.
"""

from __future__ import annotations

import re

from app.profiles.base import OTHER, DocumentProfile
from app.profiles.recruiting import COVER_LETTER, RESUME, TRANSCRIPT
from app.services.text_features import SALUTATION_RE, PageFeatures, flatten

#: Section headings a generated application form prints. Their presence says
#: "this is still the form", which is what keeps an uploaded resume's
#: employment history from being confused with the form's own.
FORM_SECTION_MARKERS = (
    "biographical",
    "primary phone",
    "primary email",
    "education history",
    "employment history",
    "certificates/licenses",
    "certificates / licenses",
    "additional info",
    "references",
    "eeo",
    "voluntary self-identification",
    "how did you hear",
    "supplemental questions",
)

#: Openings that identify an uploaded cover letter's first page.
COVER_LETTER_OPENINGS = (
    "dear hiring committee",
    "dear hiring manager",
    "dear search committee",
    "dear selection committee",
    "dear committee",
    "to whom it may concern",
)

#: Headings that identify an uploaded resume's first page. Deliberately
#: specific: bare "education" and "experience" also appear on the generated
#: form and would fire on it.
RESUME_HEADINGS = (
    "professional summary",
    "executive profile",
    "professional profile",
    "career summary",
    "summary of qualifications",
    "professional experience",
    "work experience",
    "core competencies",
    "areas of expertise",
    "technical skills",
    "key qualifications",
    "objective",
)

#: Words that title a document as a resume outright.
RESUME_TITLES = ("resume", "résumé", "curriculum vitae")

#: Markers that identify an uploaded transcript's first page.
TRANSCRIPT_MARKERS = (
    "unofficial transcript",
    "official transcript",
    "academic transcript",
    "academic record",
    "transcript of records",
)
TRANSCRIPT_SUPPORTING = ("student id", "course", "credits", "gpa", "grade", "semester", "term")

#: How far into a page an opening marker still counts as an opening.
OPENING_LINES = 16

#: A line that is nothing but an uploaded file's name, optionally introduced by
#: the compiler ("Attachment: Resume - J Smith.pdf"). The strongest evidence
#: there is, because the applicant named the document themselves.
FILENAME_RE = re.compile(
    r"""(?ix)
    ^\s*
    (?:attach(?:ed|ment)s?\s*[:\-]?\s*|document\s*[:\-]?\s*|file\s*(?:name)?\s*[:\-]?\s*)?
    (?P<stem>[^\n\\/:*?"<>|]{1,120}?)
    \s*\.(?:pdf|docx?|rtf|odt|txt|pages)
    \s*$
    """
)

#: Splits a filename stem into the parts a person separates with punctuation,
#: so ``Resume - Jordan Ellery (2026)`` offers ``Resume`` on its own.
_STEM_PARTS_RE = re.compile(r"[-_–—|,()\[\]]+")


class AttachmentDetector:
    """Decides whether a page opens a new uploaded document, and of what type.

    Constructed with a profile so the answer is always a type that profile
    actually supports -- a transcript detected under a profile with no
    transcript type is not a boundary, it is a page of something else.
    """

    def __init__(self, profile: DocumentProfile) -> None:
        self.profile = profile

    # ------------------------------------------------------------------
    def opening(self, features: PageFeatures, open_type: str | None) -> str | None:
        """The type of uploaded document opening on this page, if any.

        Returns ``None`` for every page that merely continues what is already
        open -- including sparse pages, which is the point: a signature-only
        page carries no opening evidence and so stays with its letter.
        """
        if self.is_generated_form_page(features):
            return None
        if self.is_continuation_page(features):
            return None

        head = "\n".join(features.first_lines[:OPENING_LINES])
        flat_head = flatten(head)

        if self.transcript_opening(features, flat_head):
            return self._supported(TRANSCRIPT)

        if self.cover_letter_opening(features, head, flat_head):
            return self._supported(COVER_LETTER)

        if self.resume_opening(features, flat_head, open_type):
            return self._supported(RESUME)

        return None

    # ------------------------------------------------------------------
    def is_generated_form_page(self, features: PageFeatures) -> bool:
        """Still inside the generated application form.

        Either the form names one of its own sections, or the page is laid out
        as labelled fields rather than prose -- both of which an uploaded
        resume or letter does not do.
        """
        if any(marker in features.flat for marker in FORM_SECTION_MARKERS):
            return True
        return features.form_field_ratio >= 0.25 and features.form_field_lines >= 4

    def is_continuation_page(self, features: PageFeatures) -> bool:
        """A page that states it is not the first page of its document.

        An uploaded transcript or resume repeats its title in a running header,
        so without this its second page would re-open as a fresh document.
        """
        marker = features.page_marker
        return marker is not None and marker[0] > 1

    def restarts_numbering(
        self, features: PageFeatures, previous: PageFeatures | None
    ) -> bool:
        """``Page 1 of N`` arriving after a page that was not page 1.

        A document that numbers its own pages has told us where it starts. This
        is structure, not appearance, so it outranks anything the page's words
        suggest -- and it catches an attachment whose first page is otherwise
        unremarkable.
        """
        marker = features.page_marker
        if marker is None or marker[0] != 1:
            return False
        if previous is None:
            return False
        previous_marker = previous.page_marker
        return previous_marker is not None and previous_marker[0] > 1

    # ------------------------------------------------------------------
    def filename_type(self, features: PageFeatures) -> str | None:
        """The document type named by an uploaded file's own name, if printed.

        Read only from the top of the page: a filename in a footer, or cited in
        the body of a letter, describes something other than the page it is on.
        """
        for line in features.first_lines[:6]:
            match = FILENAME_RE.match(line.strip())
            if not match:
                continue
            resolved = self._type_from_stem(match.group("stem"))
            if resolved is not None:
                return resolved
        return None

    def title_type(self, features: PageFeatures) -> str | None:
        """The document type printed as this page's own title, if any.

        Only a line that is *entirely* a type name counts. "Resume" as a
        heading is a title; "resume of my duties" inside a sentence is not.
        """
        for line in features.first_lines[:4]:
            text = line.strip().strip(":").strip()
            if not text or len(text) > 48:
                continue
            resolved = self.profile.normalize_type(text)
            if resolved != OTHER and resolved in self.profile.document_types:
                return resolved
        return None

    # ------------------------------------------------------------------
    def cover_letter_opening(
        self, features: PageFeatures, head: str, flat_head: str
    ) -> bool:
        if any(opening in flat_head for opening in COVER_LETTER_OPENINGS):
            return True
        if "cover letter" in flat_head:
            return True
        # A salutation near the top, with the prose of a letter rather than the
        # bullet lists of a resume.
        return bool(SALUTATION_RE.search(head)) and features.bullet_ratio < 0.15

    def resume_opening(
        self, features: PageFeatures, flat_head: str, open_type: str | None
    ) -> bool:
        titled = any(title in flat_head for title in RESUME_TITLES)
        headings = sum(1 for heading in RESUME_HEADINGS if heading in features.flat)

        if titled and headings:
            return True
        if headings >= 2:
            return True
        # A single strong resume heading counts only when the page also opens
        # with the candidate's own contact block -- the shape of a resume's
        # first page, and not of a letter's continuation.
        if headings == 1 and features.has_contact_block and open_type != RESUME:
            return True
        return False

    def transcript_opening(self, features: PageFeatures, flat_head: str) -> bool:
        if any(marker in flat_head for marker in TRANSCRIPT_MARKERS):
            return True
        if "transcript" in flat_head:
            supporting = sum(
                1 for marker in TRANSCRIPT_SUPPORTING if marker in features.flat
            )
            return supporting >= 2
        return False

    # ------------------------------------------------------------------
    def _supported(self, document_type: str) -> str | None:
        return document_type if document_type in self.profile.document_types else None

    def _type_from_stem(self, stem: str) -> str | None:
        """A document type named anywhere in a filename stem.

        Whole stem first, then its punctuation-separated parts: people name
        files ``Resume.pdf``, ``Resume - Jordan Ellery.pdf`` and
        ``Ellery_CoverLetter.docx``, and all three say the same thing.
        """
        candidates = [stem.strip()]
        candidates += [part.strip() for part in _STEM_PARTS_RE.split(stem) if part.strip()]
        for candidate in candidates:
            if not candidate or len(candidate) > 48:
                continue
            resolved = self.profile.normalize_type(candidate)
            if resolved != OTHER and resolved in self.profile.document_types:
                return resolved
        return None


__all__ = [
    "AttachmentDetector",
    "FORM_SECTION_MARKERS",
    "COVER_LETTER_OPENINGS",
    "RESUME_HEADINGS",
    "RESUME_TITLES",
    "TRANSCRIPT_MARKERS",
    "TRANSCRIPT_SUPPORTING",
    "FILENAME_RE",
    "OPENING_LINES",
]
