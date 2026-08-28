"""Candidate / applicant metadata extraction.

Identity is recovered from labelled form fields, resume headers and letter
signatures. Extraction is deliberately conservative: a wrong name propagates
into folder names and filenames, so ambiguous evidence is recorded as a
conflict for review rather than guessed at.
"""

from __future__ import annotations

import re

from app.models.candidate import Candidate, normalize_person_name, strip_credentials
from app.services.text_features import (
    CLOSING_RE,
    EMAIL_RE,
    LINKEDIN_RE,
    PAGE_MARKER_RE,
    PHONE_RE,
    PageFeatures,
)

# Labels commonly used by applicant tracking systems and forms.
_NAME_LABELS = (
    "name", "full name", "applicant name", "candidate name", "student name",
    "employee name", "applicant", "candidate", "submitted by", "prepared by",
    "legal name", "printed name",
)
_FIRST_NAME_LABELS = ("first name", "given name", "forename")
_LAST_NAME_LABELS = ("last name", "surname", "family name")
_EMAIL_LABELS = ("email", "e-mail", "email address", "e-mail address", "contact email")
_PHONE_LABELS = (
    "phone", "telephone", "mobile", "cell", "cell phone", "mobile phone",
    "phone number", "primary phone", "home phone", "contact number", "contact phone",
)
_LINKEDIN_LABELS = ("linkedin", "linkedin profile", "linkedin url")
#: Labels that unambiguously introduce a job title -- trusted as written.
_JOB_LABELS_EXPLICIT = (
    "position applied for", "job title", "applied for", "applying for",
    "requisition title", "posting title", "position title", "job posting",
    "position", "vacancy",
)
#: Labels that could introduce something else, so the value is sanity-checked.
_JOB_LABELS_GENERIC = ("job", "role", "title")
_JOB_LABELS = _JOB_LABELS_EXPLICIT + _JOB_LABELS_GENERIC
_APPLICANT_ID_LABELS = (
    "applicant id", "candidate id", "application id", "applicant number",
    "applicant #", "candidate number", "reference number", "application number",
    "applicant reference",
)

_LABEL_SEGMENT_RE = re.compile(r"^\s*([A-Za-z][A-Za-z /&'()#.\-]{1,40}?)\s*[:：]\s*(.+?)\s*$")
_SEGMENT_SPLIT_RE = re.compile(r"\s{2,}|\t+")

#: Words that disqualify a line from being read as a person's name.
_NAME_STOPWORDS = {
    "resume", "resumé", "cv", "curriculum", "vitae", "cover", "letter", "references",
    "reference", "transcript", "application", "report", "page", "confidential",
    "address", "phone", "telephone", "email", "objective", "summary", "experience",
    "education", "skills", "certifications", "employment", "history", "profile",
    "university", "college", "school", "department", "company", "inc", "llc",
    "portfolio", "sample", "writing", "professional", "personal", "contact",
    "information", "details", "candidate", "applicant", "position", "job", "title",
    "hiring", "manager", "dear", "sincerely", "regards", "attn", "attention",
    "official", "unofficial", "academic", "record", "student", "gpa", "credits",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    # Table headers and section labels that otherwise read as Title Case names.
    "date", "stage", "disposition", "status", "notes", "internal", "attachment",
    "attachments", "questions", "question", "answer", "screening", "voluntary",
    "category", "submission", "submitted", "generated", "issued", "term", "course",
    "title", "credit", "grade", "grades", "total", "organization", "relationship",
    "years", "known", "semester", "quarter", "degree", "awarded", "cumulative",
    "list", "sheet", "overview", "summary", "table", "contents", "section",
}

#: Job-title words that would otherwise pass as Title Case personal names.
#: Deliberately excludes words that are also common surnames (Baker, Mason,
#: Marshall, Associate-style words), so real people are not rejected.
_JOB_TITLE_WORDS = {
    "analyst", "manager", "engineer", "director", "coordinator", "specialist",
    "supervisor", "administrator", "consultant", "intern", "technician",
    "developer", "designer", "representative", "senior", "junior", "chief",
    "officer", "president", "executive", "recruiter", "accountant", "attorney",
    "paralegal", "nurse", "physician", "operations", "logistics", "marketing",
    "sales", "finance", "accounting", "engineering",
}

_NAME_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z'\-]*\.?$")
_JOB_PHRASE_RE = re.compile(
    r"(?i)\b(?:apply|applying|application)\s+for\s+(?:the\s+)?"
    r"(?P<title>[A-Z][A-Za-z0-9 /&,'\-]{2,60}?)\s*(?:position|role|opening|job|vacancy|\.|,|$)"
)
_JOB_INTEREST_RE = re.compile(
    r"(?i)\binterest(?:ed)?\s+in\s+(?:the\s+)?(?P<title>[A-Z][A-Za-z0-9 /&,'\-]{2,60}?)\s*"
    r"(?:position|role|opening|job|vacancy)\b"
)

_NULL_VALUES = {"", "-", "--", "n/a", "na", "none", "not provided", "unknown", "tbd"}

#: Applicant tracking systems often state outright whose record this is, in a
#: generated line no human wrote. That is the strongest identity evidence a page
#: can carry -- better than a resume header, which is only a name in large type.
_REPORT_SUBJECT_RE = re.compile(
    r"(?im)^\s*(?:application|candidate|applicant)\s+(?:details|report|summary|profile)\s+"
    r"for\s+(?P<name>[^\n]{3,60}?)\s*$"
)

#: A letter of recommendation is written *about* the applicant by somebody else.
#: Its signature is the referee, so the only name that identifies the candidate
#: is the one being recommended. Without this the packet is filed under whoever
#: wrote it -- which is how a real applicant's reference letter ended up as its
#: own separate person.
#: The introducing phrase is case-insensitive; the name is emphatically not.
#: Capitalisation is the only thing stopping the capture running on past the
#: name into the rest of the sentence ("Amara Okonjo as she applies...").
_RECOMMENDATION_SUBJECT_RE = re.compile(
    r"(?i:\b(?:recommendation|reference|support)\s+(?:letter\s+)?for\s+)"
    r"(?P<name>[A-Z][a-z'\-]+(?:\s+[A-Z][a-z'\-.]*)+)"
)
_RECOMMENDING_RE = re.compile(
    r"(?i:\b(?:i|we)\s+(?:am|are)\s+(?:writing|pleased|delighted|happy)\s+"
    r"(?:to\s+)?(?:offer|provide|give|write|recommend|support)[^.]{0,60}?\s+"
    r"(?:for|of)\s+)"
    r"(?P<name>[A-Z][a-z'\-]+(?:\s+[A-Z][a-z'\-.]*)+)"
)
#: Phrases that mark a page as a third-party endorsement rather than the
#: applicant's own writing.
_RECOMMENDATION_MARKERS = (
    "letter of recommendation",
    "letter of reference",
    "i am writing to recommend",
    "i am writing to offer my recommendation",
    "i am pleased to recommend",
    "i highly recommend",
    "it is my pleasure to recommend",
    "i recommend",
    "i offer my recommendation",
    "my recommendation for",
)


def name_from_report_subject(features: PageFeatures) -> str | None:
    """The applicant an ATS report states it is about (``"Application Details
    for Sofia Brennan"``). Module-level so the deterministic ATS parser can
    reuse the same extraction :class:`MetadataExtractor` uses internally,
    without needing an instance.
    """
    match = _REPORT_SUBJECT_RE.search(features.text)
    if not match:
        return None
    name = match.group("name").strip(" ,.-")
    return _tidy_name(name) if looks_like_person_name(name) else None


class MetadataExtractor:
    """Extracts candidate identity from a single page's features."""

    #: Document types whose contact details belong to other people.
    THIRD_PARTY_CONTACT_TYPES = ("References",)

    def extract(self, features: PageFeatures, *, document_type: str | None = None) -> Candidate:
        """Build a :class:`Candidate` from one page.

        ``document_type`` matters: on a References page the phone numbers and
        emails belong to the referees, not to the applicant, so they are not
        harvested as candidate contact details.
        """
        labels = _labelled_values(features.text)
        third_party_contacts = document_type in self.THIRD_PARTY_CONTACT_TYPES

        name = self._extract_name(features, labels, third_party=third_party_contacts)
        email = None if third_party_contacts else self._extract_email(features, labels)
        phone = None if third_party_contacts else self._extract_phone(features, labels)
        linkedin = self._extract_linkedin(features, labels)
        job_title = self._extract_job_title(features, labels)
        applicant_id = _first_label(labels, _APPLICANT_ID_LABELS)

        return Candidate(
            name=name,
            email=email,
            phone=phone,
            linkedin=linkedin,
            job_title=job_title,
            applicant_id=applicant_id,
        )

    # ------------------------------------------------------------------
    def _extract_name(
        self, features: PageFeatures, labels: dict[str, str], *, third_party: bool = False
    ) -> str | None:
        # A system-generated "Application Details for X" outranks everything
        # else on the page: it is the report stating its own subject, rather
        # than a name that happens to appear somewhere.
        subject = self._name_from_report_subject(features)
        if subject:
            return subject

        # A recommendation letter is written about the applicant by somebody
        # else. Reading its signature would file the packet under the referee.
        if is_recommendation_letter(features):
            recommended = self._name_from_recommendation(features)
            return recommended  # never fall through to the signature

        # On a reference sheet every ``Name:`` field names a referee, so only the
        # page header can identify the applicant the sheet belongs to.
        if third_party:
            return self._name_from_header(features)

        labelled = _first_label(labels, _NAME_LABELS)
        if labelled and looks_like_person_name(labelled):
            return _tidy_name(labelled)

        first = _first_label(labels, _FIRST_NAME_LABELS)
        last = _first_label(labels, _LAST_NAME_LABELS)
        if first and last:
            combined = f"{first} {last}"
            if looks_like_person_name(combined):
                return _tidy_name(combined)

        signature = self._name_from_signature(features)
        if signature:
            return signature

        return self._name_from_header(features)

    def _name_from_report_subject(self, features: PageFeatures) -> str | None:
        """The applicant an ATS report says it is about."""
        return name_from_report_subject(features)

    def _name_from_recommendation(self, features: PageFeatures) -> str | None:
        """The person a recommendation letter is written *about*."""
        for pattern in (_RECOMMENDING_RE, _RECOMMENDATION_SUBJECT_RE):
            for match in pattern.finditer(features.text):
                name = match.group("name").strip(" ,.-")
                if looks_like_person_name(name):
                    return _tidy_name(name)
        return None

    def _name_from_signature(self, features: PageFeatures) -> str | None:
        """The line after ``Sincerely,`` in a letter is almost always the writer."""
        lines = features.lines
        for index, line in enumerate(lines):
            if not CLOSING_RE.search(line):
                continue
            # A closing may share its line with the name ("Sincerely, Jane Doe").
            remainder = CLOSING_RE.sub("", line).strip(" ,.-")
            if remainder and looks_like_person_name(remainder):
                return _tidy_name(remainder)
            for candidate_line in lines[index + 1 : index + 4]:
                stripped = candidate_line.strip(" ,.-")
                if looks_like_person_name(stripped):
                    return _tidy_name(stripped)
        return None

    #: Only the top few lines are trusted as a header name.
    HEADER_SCAN_LINES = 3

    def _name_from_header(self, features: PageFeatures) -> str | None:
        """A person's name at the top of a page, usually above the contact block.

        Beyond the very first line a name is only accepted when the page also
        carries contact details, which keeps Title Case table headers such as
        ``Date  Stage  Disposition`` from being read as people.
        """
        has_contact_context = bool(features.emails or features.phones)
        for index, line in enumerate(features.first_lines[: self.HEADER_SCAN_LINES]):
            stripped = line.strip(" ,.-|")
            if "@" in stripped or PHONE_RE.search(stripped):
                continue
            if index > 0 and not has_contact_context:
                continue
            if looks_like_person_name(stripped):
                return _tidy_name(stripped)
        return None

    def _extract_email(self, features: PageFeatures, labels: dict[str, str]) -> str | None:
        labelled = _first_label(labels, _EMAIL_LABELS)
        if labelled:
            match = EMAIL_RE.search(labelled)
            if match:
                return match.group(0)
        return features.emails[0] if features.emails else None

    def _extract_phone(self, features: PageFeatures, labels: dict[str, str]) -> str | None:
        labelled = _first_label(labels, _PHONE_LABELS)
        if labelled:
            match = PHONE_RE.search(labelled)
            if match:
                return match.group(0).strip()
        return features.phones[0] if features.phones else None

    def _extract_linkedin(self, features: PageFeatures, labels: dict[str, str]) -> str | None:
        labelled = _first_label(labels, _LINKEDIN_LABELS)
        if labelled:
            match = LINKEDIN_RE.search(labelled)
            if match:
                return match.group(0)
            if labelled and "/" in labelled:
                return labelled
        return features.linkedin[0] if features.linkedin else None

    def _extract_job_title(self, features: PageFeatures, labels: dict[str, str]) -> str | None:
        # An explicit label such as "Position Applied For" is taken at face
        # value; only vague labels ("Title", "Role") need a sanity check.
        explicit = _first_label(labels, _JOB_LABELS_EXPLICIT)
        if explicit and 2 < len(explicit) <= 80:
            return explicit

        generic = _first_label(labels, _JOB_LABELS_GENERIC)
        if generic and 2 < len(generic) <= 80 and not looks_like_person_name(generic):
            return generic

        for pattern in (_JOB_PHRASE_RE, _JOB_INTEREST_RE):
            match = pattern.search(features.text)
            if match:
                title = " ".join(match.group("title").split()).strip(" ,.-")
                if 2 < len(title) <= 80:
                    return title
        return None


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

#: Every label the extractor understands, for recognising a bare label line.
_ALL_LABELS: frozenset[str] = frozenset(
    _NAME_LABELS
    + _FIRST_NAME_LABELS
    + _LAST_NAME_LABELS
    + _EMAIL_LABELS
    + _PHONE_LABELS
    + _LINKEDIN_LABELS
    + _JOB_LABELS
    + _APPLICANT_ID_LABELS
    + (
        # Neighbouring labels in the same form block. Listed so a value is never
        # read from the line that actually holds the *next* field's label -- an
        # empty "Middle Name" must not swallow "Last Name".
        "middle name", "address", "applicant type", "applicant status",
        "preferred contact", "city", "state", "country", "postal code", "zip",
        "county", "home phone", "work phone", "other phone", "date of birth",
        "gender", "ethnicity", "veteran status", "disability status",
        "job opening id", "job posting title", "department", "location",
        "recruiter", "hiring manager", "status", "disposition", "stage",
        "source", "referral source", "application date", "date applied",
        "resume", "cover letter", "attachments", "notes",
    )
)


def _normalize_label(text: str) -> str:
    return " ".join(str(text).split()).strip(" :•-").lower()


def _labelled_values(text: str) -> dict[str, str]:
    """Parse form fields, in both layouts real systems produce.

    Two layouts occur, and applicant tracking systems overwhelmingly use the
    second:

    * ``Name: Sofia Brennan`` -- label and value on one line.
    * ``Name`` / ``Sofia Brennan`` -- label on its own line, value beneath it.

    Only recognised labels are read vertically, and a value is refused if it is
    itself a label, so an empty ``Middle Name`` field cannot swallow the
    ``Last Name`` heading that follows it.
    """
    values: dict[str, str] = {}

    for line in text.splitlines():
        if ":" not in line:
            continue
        for segment in _SEGMENT_SPLIT_RE.split(line):
            match = _LABEL_SEGMENT_RE.match(segment)
            if not match:
                continue
            label = " ".join(match.group(1).split()).lower()
            value = _strip_trailing_footer(" ".join(match.group(2).split()).strip())
            if not value or value.lower() in _NULL_VALUES:
                continue
            values.setdefault(label, value)

    lines = [line.strip() for line in text.splitlines()]
    for index, line in enumerate(lines):
        label = _normalize_label(line)
        if label not in _ALL_LABELS:
            continue
        for candidate_line in lines[index + 1 : index + 3]:
            if not candidate_line:
                continue
            if _normalize_label(candidate_line) in _ALL_LABELS:
                break  # the field was blank; the next label follows immediately
            value = _strip_trailing_footer(" ".join(candidate_line.split()).strip())
            if value and value.lower() not in _NULL_VALUES:
                values.setdefault(label, value)
            break

    return values


def _strip_trailing_footer(value: str) -> str:
    """Drop a running footer that PDF extraction glued onto a field value.

    ``"Applicant ID: A-10482        Page 2 of 4"`` collapses to one line when the
    text layer loses its column spacing; without this the ID would read as
    ``"A-10482 Page 2 of 4"`` and look different on every page.
    """
    cleaned = PAGE_MARKER_RE.sub(" ", value)
    return " ".join(cleaned.split()).strip(" .,-|")


def _first_label(labels: dict[str, str], wanted: tuple[str, ...]) -> str | None:
    for key in wanted:
        value = labels.get(key)
        if value:
            return value
    return None


def is_recommendation_letter(features: PageFeatures) -> bool:
    """True when a page endorses somebody rather than speaking for itself."""
    return any(marker in features.flat for marker in _RECOMMENDATION_MARKERS)


def looks_like_person_name(text: str) -> bool:
    """Heuristic test for ``Benjamin Perez`` / ``PEREZ, BENJAMIN`` style names."""
    if not text:
        return False
    stripped = " ".join(strip_credentials(text).split()).strip(" ,.-")
    if not (4 <= len(stripped) <= 60):
        return False
    if any(ch.isdigit() for ch in stripped) or "@" in stripped:
        return False

    working = stripped
    if working.count(",") == 1:
        last, _, first = working.partition(",")
        working = f"{first.strip()} {last.strip()}".strip()
    elif "," in working:
        return False

    tokens = working.split()
    if not (2 <= len(tokens) <= 4):
        return False
    if not all(_NAME_TOKEN_RE.match(token) for token in tokens):
        return False
    lowered = [token.lower().strip(".") for token in tokens]
    if any(token in _NAME_STOPWORDS for token in lowered):
        return False
    if any(token in _JOB_TITLE_WORDS for token in lowered):
        return False

    # Require name-like capitalisation (Title Case or ALL CAPS).
    title_case = all(token[0].isupper() for token in tokens)
    all_caps = all(token.isupper() for token in tokens if token.isalpha())
    return title_case or all_caps


def _tidy_name(text: str) -> str:
    """Normalise a detected name to ``First Last`` display form."""
    stripped = " ".join(strip_credentials(text).split()).strip(" ,.-")
    if stripped.count(",") == 1:
        last, _, first = stripped.partition(",")
        stripped = f"{first.strip()} {last.strip()}".strip()
    tokens = stripped.split()
    tidied = []
    for token in tokens:
        if token.isupper() and len(token) > 1:
            tidied.append(token.capitalize() if token.isalpha() else token)
        else:
            tidied.append(token)
    return " ".join(tidied)


def merge_candidates(candidates: list[Candidate]) -> Candidate:
    """Merge page-level identities into one, recording disagreeing names.

    The most frequently seen name wins; every other distinct name is preserved
    in :attr:`Candidate.conflicting_names` so the UI can flag the ambiguity.
    """
    merged = Candidate()
    name_counts: dict[str, tuple[str, int]] = {}

    for candidate in candidates:
        if candidate.is_empty:
            continue
        merged = merged.merged_with(
            Candidate(
                email=candidate.email,
                phone=candidate.phone,
                linkedin=candidate.linkedin,
                job_title=candidate.job_title,
                applicant_id=candidate.applicant_id,
            )
        )
        if candidate.name:
            key = normalize_person_name(candidate.name)
            display, count = name_counts.get(key, (candidate.name, 0))
            name_counts[key] = (display, count + 1)

    if name_counts:
        ordered = sorted(name_counts.items(), key=lambda item: item[1][1], reverse=True)
        merged.name = ordered[0][1][0]
        merged.conflicting_names = [display for _, (display, _count) in ordered[1:]]

    return merged


__all__ = [
    "MetadataExtractor",
    "merge_candidates",
    "looks_like_person_name",
    "is_recommendation_letter",
    "name_from_report_subject",
]
