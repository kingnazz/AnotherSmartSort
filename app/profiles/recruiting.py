"""Built-in ``Recruiting`` document profile.

Every signal here is *evidence*, never an absolute rule: no single keyword
decides a type. Scores combine so a page missing one cue can still be
classified confidently from the others.
"""

from __future__ import annotations

import re

from app.profiles.base import OTHER, DocumentProfile, Signal
from app.services.text_features import PageFeatures

# Canonical recruiting document types.
APPLICATION_REPORT = "Application Report"
RESUME = "Resume"
COVER_LETTER = "Cover Letter"
REFERENCES = "References"
TRANSCRIPT = "Transcript"
WRITING_SAMPLE = "Writing Sample"
PORTFOLIO = "Portfolio"

RECRUITING_TYPES = [
    APPLICATION_REPORT,
    RESUME,
    COVER_LETTER,
    REFERENCES,
    TRANSCRIPT,
    WRITING_SAMPLE,
    PORTFOLIO,
    OTHER,
]

#: Order documents appear inside a combined candidate packet. A reviewer opening
#: one packet PDF wants the ATS report first for context, then the resume, then
#: the letter -- not whatever order the source PDF happened to use.
DEFAULT_PACKET_ORDER: tuple[str, ...] = (
    APPLICATION_REPORT,
    RESUME,
    COVER_LETTER,
    REFERENCES,
    TRANSCRIPT,
    WRITING_SAMPLE,
    PORTFOLIO,
    OTHER,
)

COURSE_CODE_RE = re.compile(r"\b[A-Z]{2,4}\s?-?\s?\d{3}[A-Z]?\b")
GRADE_ROW_RE = re.compile(r"(?m)^.*\b[A-D][+\-]?\b\s*$")
FIRST_PERSON_RE = re.compile(r"\bi\b")
QA_RE = re.compile(r"(?im)^\s*(?:q(?:uestion)?\s*\d*\s*[:.]|answer\s*[:.])")
#: Markers an applicant tracking system prints on every page of a report it
#: generates. No resume, letter or transcript carries them, because a person did
#: not write them -- which makes their presence close to conclusive.
ATS_HEADER_MARKERS = (
    "confidential report",
    "application details for",
    "job opening id",
    "job posting title",
    "candidate details for",
    "applicant details for",
    "requisition id",
    "req id",
)

LETTER_DATE_RE = re.compile(
    r"(?im)^\s*(?:january|february|march|april|may|june|july|august|september|october|"
    r"november|december)\s+\d{1,2},\s+(?:19|20)\d{2}\s*$"
)


# --------------------------------------------------------------------------
# Structural predicates
# --------------------------------------------------------------------------

def _ats_generated_header(features: PageFeatures) -> bool:
    """A running header written by the tracking system, not by a person.

    Real exports repeat it on every page of the report and on none of the
    attachments, which makes it both a decisive type signal and an honest one:
    two independent markers are required so a resume mentioning a requisition
    number in passing cannot trip it.
    """
    head = "\n".join(features.first_lines[:12]).lower()
    return sum(1 for marker in ATS_HEADER_MARKERS if marker in head) >= 2


def is_ats_generated_page(features: PageFeatures) -> bool:
    """Public entry point for :func:`_ats_generated_header`.

    Used by the deterministic ATS report parser to decide whether a whole file
    matches a known, machine-generated export -- the same signal the rules
    classifier uses to keep report pages from reading as resumes.
    """
    return _ats_generated_header(features)


def _bullet_heavy(features: PageFeatures) -> bool:
    return features.bullet_ratio >= 0.20 and features.bullet_lines >= 3


def _paragraph_heavy(features: PageFeatures) -> bool:
    return features.paragraph_ratio >= 0.30 and features.bullet_ratio < 0.15


def _form_like(features: PageFeatures) -> bool:
    return features.form_field_ratio >= 0.22 and features.form_field_lines >= 4


def _tabular(features: PageFeatures) -> bool:
    return features.table_like_lines >= 4


def _has_contact_block(features: PageFeatures) -> bool:
    return features.has_contact_block


def _dated_experience(features: PageFeatures) -> bool:
    return features.date_range_count >= 2


def _many_dated_experience(features: PageFeatures) -> bool:
    return features.date_range_count >= 4


def _first_person_voice(features: PageFeatures) -> bool:
    return len(FIRST_PERSON_RE.findall(features.flat)) >= 5


def _multiple_people_contacts(features: PageFeatures) -> bool:
    """Several distinct contact blocks -- typical of a reference sheet."""
    return len(features.phones) >= 2 and (len(features.emails) >= 2 or len(features.phones) >= 3)


def _course_codes(features: PageFeatures) -> bool:
    return len(COURSE_CODE_RE.findall(features.text)) >= 3


def _grade_rows(features: PageFeatures) -> bool:
    return len(GRADE_ROW_RE.findall(features.text)) >= 3


def _question_answer_pairs(features: PageFeatures) -> bool:
    return len(QA_RE.findall(features.text)) >= 3


def _letter_date_header(features: PageFeatures) -> bool:
    return bool(LETTER_DATE_RE.search("\n".join(features.first_lines)))


def _no_identity_markers(features: PageFeatures) -> bool:
    return not features.emails and not features.phones and features.date_range_count == 0


#: Layout alone never identifies a document. These predicates require at least
#: one vocabulary hit so that a short or wordy page with no recognisable content
#: stays `Other` (and therefore goes to review) instead of being guessed at.
_PORTFOLIO_WORDS = ("portfolio", "case study", "project", "exhibition", "selected works")
_WRITING_SAMPLE_WORDS = (
    "abstract", "introduction", "conclusion", "methodology", "bibliography",
    "works cited", "appendix", "thesis", "essay", "memorandum",
)
#: Minimum length before a page counts as a "sustained" piece of writing.
_SUSTAINED_PROSE_WORDS = 120


def _mentions_any(features: PageFeatures, words: tuple[str, ...]) -> bool:
    return any(word in features.flat for word in words)


def _sparse_portfolio_layout(features: PageFeatures) -> bool:
    """A sparse, image-led page *that also mentions portfolio work*."""
    sparse = features.word_count < 90 and features.line_count <= 14 and not features.emails
    return sparse and _mentions_any(features, _PORTFOLIO_WORDS)


def _sustained_prose(features: PageFeatures) -> bool:
    """Long-form prose with academic apparatus and no contact details."""
    if features.word_count < _SUSTAINED_PROSE_WORDS:
        return False
    return (
        _paragraph_heavy(features)
        and _no_identity_markers(features)
        and _mentions_any(features, _WRITING_SAMPLE_WORDS)
    )


def _resume_heading_stack(features: PageFeatures) -> bool:
    """Two or more classic resume section headings present as headings."""
    wanted = {
        "experience", "work experience", "professional experience", "employment",
        "employment history", "education", "skills", "technical skills",
        "certifications", "summary", "professional summary", "objective",
        "achievements", "accomplishments", "awards", "projects", "core competencies",
        "qualifications", "activities", "volunteer experience", "publications",
    }
    seen = {h.lower().strip() for h in features.heading_lines}
    return len(seen & wanted) >= 2


def build_recruiting_profile() -> DocumentProfile:
    """Construct the built-in recruiting profile."""

    signals: dict[str, list[Signal]] = {
        # ------------------------------------------------------------------
        APPLICATION_REPORT: [
            Signal(
                "System-generated report header",
                weight=6.5,
                predicate=_ats_generated_header,
            ),
            Signal(
                "Applicant tracking vocabulary",
                weight=3.0,
                per_hit_weight=1.6,
                keywords=[
                    "applicant information", "application report", "candidate profile",
                    "application summary", "applicant profile", "application details",
                    "submission details", "application questions", "screening questions",
                    "prescreen questions", "candidate summary", "applicant record",
                ],
            ),
            Signal(
                "Requisition / applicant identifiers",
                weight=2.6,
                per_hit_weight=1.3,
                keywords=[
                    "requisition", "requisition id", "req id", "req number", "applicant id",
                    "candidate id", "application id", "job id", "posting id", "job code",
                ],
            ),
            Signal(
                "Application metadata fields",
                weight=1.6,
                per_hit_weight=0.9,
                keywords=[
                    "application date", "date applied", "submitted on", "date submitted",
                    "source", "referral source", "status", "disposition", "stage",
                    "position applied for", "applied for", "job title", "department",
                    "location", "employment type", "recruiter",
                ],
            ),
            Signal(
                "Compliance / EEO section",
                weight=1.8,
                per_hit_weight=1.0,
                keywords=[
                    "eeo", "equal employment opportunity", "veteran status", "disability status",
                    "ethnicity", "gender identity", "voluntary self-identification",
                    "work authorization", "authorized to work", "sponsorship",
                ],
            ),
            Signal("System-generated form layout", weight=3.2, predicate=_form_like),
            Signal("Tabular field layout", weight=1.2, predicate=_tabular),
            Signal("Question / answer blocks", weight=2.0, predicate=_question_answer_pairs),
            Signal("Letter salutation (not an application form)", weight=-2.5,
                   predicate=lambda f: f.has_salutation),
            Signal("Bullet-heavy resume layout", weight=-1.2, predicate=_bullet_heavy),
        ],
        # ------------------------------------------------------------------
        RESUME: [
            # A page of a system-generated report is part of that report, however
            # much its content resembles something else. Page 2 of an ATS export
            # lists employment history and read as a resume without this.
            Signal(
                "Inside a system-generated report",
                weight=-5.0,
                predicate=_ats_generated_header,
            ),
            Signal(
                "Resume section headings",
                weight=2.6,
                per_hit_weight=1.5,
                keywords=[
                    "professional experience", "work experience", "employment history",
                    "work history", "professional summary", "summary of qualifications",
                    "core competencies", "technical skills", "key skills", "areas of expertise",
                    "professional affiliations", "relevant experience", "career summary",
                ],
            ),
            Signal(
                "Common resume sections",
                weight=1.5,
                per_hit_weight=1.1,
                keywords=[
                    "experience", "education", "skills", "certifications", "achievements",
                    "accomplishments", "awards", "publications", "projects", "objective",
                    "volunteer", "activities", "languages", "references available",
                ],
            ),
            Signal("Resume heading stack", weight=2.8, predicate=_resume_heading_stack),
            Signal("Document titled resume / CV", weight=3.0,
                   keywords=["resume", "curriculum vitae", "cv"]),
            Signal("Employment date ranges", weight=2.6, predicate=_dated_experience),
            Signal("Extensive employment history", weight=1.4, predicate=_many_dated_experience),
            Signal("Bullet-heavy accomplishment formatting", weight=2.0, predicate=_bullet_heavy),
            Signal("Contact block near top of page", weight=1.8, predicate=_has_contact_block),
            Signal(
                "Role titles and employers",
                weight=0.9,
                per_hit_weight=0.45,
                keywords=[
                    "manager", "engineer", "analyst", "director", "coordinator", "specialist",
                    "supervisor", "administrator", "consultant", "associate", "intern",
                    "assistant", "technician", "developer", "designer", "representative",
                    "llc", "inc", "corporation", "company",
                ],
            ),
            Signal(
                "Degrees and academic credentials",
                weight=1.0,
                per_hit_weight=0.6,
                keywords=[
                    "bachelor", "bachelors", "master", "masters", "associate degree",
                    "mba", "b.s.", "b.a.", "m.s.", "m.a.", "ph.d", "phd", "doctorate",
                ],
            ),
            Signal("Letter salutation (cover letter cue)", weight=-3.2,
                   predicate=lambda f: f.has_salutation),
            Signal("Letter closing (cover letter cue)", weight=-1.4,
                   predicate=lambda f: f.has_closing and f.bullet_ratio < 0.1),
            Signal("System form layout (application form cue)", weight=-1.6, predicate=_form_like),
            Signal("Course codes (transcript cue)", weight=-1.8, predicate=_course_codes),
        ],
        # ------------------------------------------------------------------
        COVER_LETTER: [
            # A page of a system-generated report is part of that report, however
            # much its content resembles something else. Page 2 of an ATS export
            # lists employment history and read as a resume without this.
            Signal(
                "Inside a system-generated report",
                weight=-5.0,
                predicate=_ats_generated_header,
            ),
            Signal("Salutation", weight=4.4, predicate=lambda f: f.has_salutation),
            Signal("Letter closing", weight=3.0, predicate=lambda f: f.has_closing),
            Signal(
                "Application intent phrasing",
                weight=2.4,
                per_hit_weight=1.3,
                keywords=[
                    "i am applying", "i am writing to apply", "i am writing to express",
                    "i am excited to apply", "i would like to apply", "i wish to apply",
                    "please accept my application", "please consider my application",
                    "in response to your posting", "i am submitting my application",
                    "my application for", "i am interested in the",
                ],
            ),
            Signal(
                "Cover letter courtesy phrasing",
                weight=1.5,
                per_hit_weight=0.9,
                keywords=[
                    "thank you for your consideration", "thank you for your time",
                    "look forward to hearing from you", "look forward to discussing",
                    "at your convenience", "attached resume", "enclosed resume",
                    "attached is my resume", "my qualifications", "i believe my",
                    "hiring manager", "hiring committee", "your organization",
                    "your company", "your team", "the position",
                ],
            ),
            Signal("Paragraph-driven letter structure", weight=2.4, predicate=_paragraph_heavy),
            Signal("First-person voice", weight=1.8, predicate=_first_person_voice),
            Signal("Letter date header", weight=1.4, predicate=_letter_date_header),
            Signal("Bullet-heavy resume layout", weight=-2.0, predicate=_bullet_heavy),
            Signal("Many employment date ranges", weight=-1.8, predicate=_many_dated_experience),
            Signal("System form layout", weight=-1.8, predicate=_form_like),
            Signal("Course codes (transcript cue)", weight=-1.5, predicate=_course_codes),
        ],
        # ------------------------------------------------------------------
        REFERENCES: [
            # A page of a system-generated report is part of that report, however
            # much its content resembles something else. Page 2 of an ATS export
            # lists employment history and read as a resume without this.
            Signal(
                "Inside a system-generated report",
                weight=-5.0,
                predicate=_ats_generated_header,
            ),
            Signal(
                "References heading",
                weight=4.0,
                per_hit_weight=1.5,
                keywords=[
                    "references", "professional references", "personal references",
                    "reference list", "list of references", "reference sheet",
                    "character references", "employment references",
                ],
            ),
            Signal(
                "Reference relationship fields",
                weight=2.2,
                per_hit_weight=1.2,
                keywords=[
                    "relationship", "years known", "how do you know", "may we contact",
                    "reference name", "reference contact", "former supervisor",
                    "former manager", "colleague", "mentor", "supervisor at",
                ],
            ),
            Signal("Multiple contact blocks", weight=2.6, predicate=_multiple_people_contacts),
            Signal("Salutation (letter cue)", weight=-2.0, predicate=lambda f: f.has_salutation),
            Signal("Extensive employment history (resume cue)", weight=-1.5,
                   predicate=_many_dated_experience),
            Signal("Resume heading stack", weight=-2.0, predicate=_resume_heading_stack),
        ],
        # ------------------------------------------------------------------
        TRANSCRIPT: [
            # A page of a system-generated report is part of that report, however
            # much its content resembles something else. Page 2 of an ATS export
            # lists employment history and read as a resume without this.
            Signal(
                "Inside a system-generated report",
                weight=-5.0,
                predicate=_ats_generated_header,
            ),
            Signal(
                "Transcript vocabulary",
                weight=3.6,
                per_hit_weight=1.5,
                keywords=[
                    "transcript", "official transcript", "unofficial transcript",
                    "academic record", "academic history", "record of study",
                    "student copy", "degree audit",
                ],
            ),
            Signal(
                "Academic registry fields",
                weight=1.7,
                per_hit_weight=0.9,
                keywords=[
                    "registrar", "student id", "credit hours", "credits earned", "credits",
                    "semester", "quarter", "term", "gpa", "grade point average",
                    "cumulative gpa", "course title", "course number", "grade",
                    "degree awarded", "major", "minor", "academic standing", "dean's list",
                ],
            ),
            Signal(
                "Institution names",
                weight=1.4,
                per_hit_weight=0.8,
                keywords=["university", "college", "institute of technology", "school of"],
            ),
            Signal("Course codes", weight=3.0, predicate=_course_codes),
            Signal("Grade rows", weight=2.0, predicate=_grade_rows),
            Signal("Tabular course layout", weight=1.6, predicate=_tabular),
            Signal("Salutation (letter cue)", weight=-2.5, predicate=lambda f: f.has_salutation),
            Signal("Bullet-heavy resume layout", weight=-1.5, predicate=_bullet_heavy),
        ],
        # ------------------------------------------------------------------
        WRITING_SAMPLE: [
            Signal(
                "Writing sample vocabulary",
                weight=3.4,
                per_hit_weight=1.4,
                keywords=[
                    "writing sample", "work sample", "sample essay", "research paper",
                    "white paper", "case analysis", "memorandum", "abstract",
                    "literature review", "thesis statement",
                ],
            ),
            Signal(
                "Academic apparatus",
                weight=1.6,
                per_hit_weight=0.9,
                keywords=[
                    "introduction", "conclusion", "methodology", "discussion",
                    "bibliography", "works cited", "references cited", "footnotes",
                    "endnotes", "appendix", "citation",
                ],
            ),
            Signal("Sustained prose with no contact data", weight=1.6,
                   predicate=_sustained_prose),
            Signal("Salutation (letter cue)", weight=-2.2, predicate=lambda f: f.has_salutation),
            Signal("Resume heading stack", weight=-2.2, predicate=_resume_heading_stack),
            Signal("System form layout", weight=-1.5, predicate=_form_like),
        ],
        # ------------------------------------------------------------------
        PORTFOLIO: [
            Signal(
                "Portfolio vocabulary",
                weight=3.6,
                per_hit_weight=1.5,
                keywords=[
                    "portfolio", "selected works", "selected projects", "case study",
                    "project overview", "design work", "work samples", "gallery",
                    "exhibition", "creative brief", "before and after",
                ],
            ),
            Signal(
                "Project presentation fields",
                weight=1.4,
                per_hit_weight=0.8,
                keywords=[
                    "client", "role", "tools", "medium", "year completed", "project",
                    "challenge", "solution", "outcome", "deliverables",
                ],
            ),
            Signal("Sparse visual page layout", weight=1.2,
                   predicate=_sparse_portfolio_layout),
            Signal("Salutation (letter cue)", weight=-2.0, predicate=lambda f: f.has_salutation),
            Signal("Resume heading stack", weight=-1.8, predicate=_resume_heading_stack),
        ],
        # ``Other`` intentionally has no positive signals: it is the fallback
        # when no type accumulates meaningful evidence.
        OTHER: [],
    }

    separator_labels = {
        "resume": RESUME,
        "resumes": RESUME,
        "cv": RESUME,
        "curriculum vitae": RESUME,
        "cover letter": COVER_LETTER,
        "cover letters": COVER_LETTER,
        "letter": COVER_LETTER,
        "references": REFERENCES,
        "reference": REFERENCES,
        "professional references": REFERENCES,
        "transcript": TRANSCRIPT,
        "transcripts": TRANSCRIPT,
        "academic transcript": TRANSCRIPT,
        "application": APPLICATION_REPORT,
        "application report": APPLICATION_REPORT,
        "applicant information": APPLICATION_REPORT,
        "writing sample": WRITING_SAMPLE,
        "writing samples": WRITING_SAMPLE,
        "portfolio": PORTFOLIO,
    }

    type_aliases = {
        "cv": RESUME,
        "curriculum vitae": RESUME,
        "resum": RESUME,
        "resume cv": RESUME,
        "coverletter": COVER_LETTER,
        "cover letter application": COVER_LETTER,
        "letter": COVER_LETTER,
        "letter of interest": COVER_LETTER,
        "letter of application": COVER_LETTER,
        "motivation letter": COVER_LETTER,
        "reference": REFERENCES,
        "reference list": REFERENCES,
        "reference letter": REFERENCES,
        "recommendation": REFERENCES,
        "letter of recommendation": REFERENCES,
        "academic transcript": TRANSCRIPT,
        "official transcript": TRANSCRIPT,
        "unofficial transcript": TRANSCRIPT,
        "grades": TRANSCRIPT,
        "application": APPLICATION_REPORT,
        "job application": APPLICATION_REPORT,
        "applicant report": APPLICATION_REPORT,
        "application form": APPLICATION_REPORT,
        "candidate profile": APPLICATION_REPORT,
        "essay": WRITING_SAMPLE,
        "work sample": WRITING_SAMPLE,
        "writing": WRITING_SAMPLE,
        "sample": WRITING_SAMPLE,
        "unknown": OTHER,
        "misc": OTHER,
        "miscellaneous": OTHER,
    }

    return DocumentProfile(
        name="Recruiting",
        description="Applicant and candidate document packets: applications, resumes, "
        "cover letters, references, transcripts, writing samples and portfolios.",
        document_types=list(RECRUITING_TYPES),
        signals=signals,
        identity_types=(APPLICATION_REPORT, RESUME, COVER_LETTER),
        # An ATS report or a resume without a name anywhere is odd. A cover
        # letter without one is ordinary -- plenty are scanned, or sign off
        # with an image -- so an anonymous letter is not treated as suspect.
        identity_expected_types=(APPLICATION_REPORT, RESUME),
        separator_labels=separator_labels,
        type_aliases=type_aliases,
        usually_single_page=(COVER_LETTER,),
        usually_multi_page=(RESUME, APPLICATION_REPORT, TRANSCRIPT, WRITING_SAMPLE),
        packet_order=DEFAULT_PACKET_ORDER,
        # The three documents an ATS export actually attaches. References,
        # transcripts, writing samples and portfolios are real and fully
        # supported, just less common -- reachable under "more types" rather
        # than crowding the everyday choice.
        primary_document_types=(RESUME, COVER_LETTER, APPLICATION_REPORT),
        default_type=OTHER,
    )


RECRUITING_PROFILE = build_recruiting_profile()

__all__ = [
    "RECRUITING_PROFILE",
    "build_recruiting_profile",
    "RECRUITING_TYPES",
    "DEFAULT_PACKET_ORDER",
    "APPLICATION_REPORT",
    "RESUME",
    "COVER_LETTER",
    "REFERENCES",
    "TRANSCRIPT",
    "WRITING_SAMPLE",
    "PORTFOLIO",
    "ATS_HEADER_MARKERS",
    "is_ats_generated_page",
]
