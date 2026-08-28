"""Synthetic submitted-applicant-packet fixtures.

Reproduces the *structure* of the real 17-file client corpus -- a generated
application form followed by uploaded attachments with no separator pages
between them -- using invented applicants and content. No real applicant data
is ever committed to this repository.

The shapes here are chosen to cover the cases that actually broke, not just
the easy ones: a cover letter whose second page holds only a signature, an
application form containing a blank page, a packet with no attachments at all,
and names carrying suffixes, hyphens and credentials that vary between the
form and the attachment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.profiles.recruiting import APPLICATION_REPORT, COVER_LETTER, RESUME, TRANSCRIPT
from scripts.sample_data import SampleDocument, SamplePage, build_pdf, bullet, paragraph


@dataclass
class ExpectedDocument:
    """One logical document's expected extent, 1-based and inclusive."""

    document_type: str
    first_page: int
    last_page: int

    @property
    def pages(self) -> list[int]:
        return list(range(self.first_page, self.last_page + 1))

    @property
    def page_indexes(self) -> list[int]:
        return [page - 1 for page in self.pages]

    @property
    def page_count(self) -> int:
        return self.last_page - self.first_page + 1


@dataclass
class PacketFixture:
    """One applicant packet PDF and the documents it should parse back into."""

    filename: str
    display_name: str
    pages: list[SamplePage]
    expected: list[ExpectedDocument] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def expect(self, document_type: str) -> ExpectedDocument | None:
        return next((d for d in self.expected if d.document_type == document_type), None)

    def write(self, output_dir: str | Path) -> Path:
        document = SampleDocument(
            filename=self.filename,
            description=f"{self.page_count}-page synthetic applicant packet",
            pages=self.pages,
            expected_candidates=[self.display_name],
        )
        return build_pdf(document, Path(output_dir) / self.filename)


# --------------------------------------------------------------------------
# Page builders
# --------------------------------------------------------------------------


def application_form_pages(
    *,
    display_name: str,
    surname: str,
    given: str,
    submitted: str = "8/5/2026",
    total: int = 5,
    blank_page: int | None = None,
) -> list[SamplePage]:
    """A generated application form.

    Every page carries at least one of the form's own section headings, which
    is what tells the parser the form is still running -- and what stops an
    uploaded resume's employment history from being confused with the form's.

    ``blank_page`` makes that 1-based page within the form effectively empty,
    reproducing the blank sheet real exports sometimes embed.
    """
    first = [
        f"{surname}, {given} (Submitted on: {submitted})",
        "",
        "Biographical",
        "",
        "Name",
        display_name,
        "",
        "Primary Phone",
        "(555) 0142-8890",
        "",
        "Primary Email",
        "applicant@example.edu",
        "",
        "Address",
        "4127 Windward Lane, Long Beach, CA 90802",
    ]
    pages = [first]

    sections = [
        (
            "General",
            [
                "Position applied for",
                "Executive Director of Housing",
                "",
                "Are you legally authorized to work in the United States?",
                "Yes",
            ],
        ),
        (
            "Education History",
            [
                "Institution",
                "Rossmore State University",
                "",
                "Degree",
                "Master of Public Administration",
                "",
                "Graduated",
                "2014",
            ],
        ),
        (
            "Employment History",
            [
                "Employer",
                "Harbour Point Housing Authority",
                "",
                "Title",
                "Associate Director of Residential Life",
                "",
                "Dates",
                "June 2018 - present",
            ],
        ),
        (
            "Certificates/Licenses",
            [
                "Certificate",
                "Certified Housing Professional",
                "",
                "Issued",
                "2019",
            ],
        ),
        (
            "Additional Info",
            [
                "How did you hear about this position?",
                "Institutional careers site",
            ],
        ),
    ]

    for index in range(2, total + 1):
        if blank_page is not None and index == blank_page:
            # A genuinely near-empty sheet: no heading, no fields, nothing to
            # classify. It must stay inside the form rather than becoming its
            # own document.
            pages.append([""])
            continue
        heading, body = sections[(index - 2) % len(sections)]
        pages.append(
            [
                f"{surname}, {given} (Submitted on: {submitted})",
                f"Page {index} of {total}",
                "",
                heading,
                "",
                *body,
            ]
        )

    return [SamplePage(lines=lines) for lines in pages]


def cover_letter_pages(*, display_name: str, total: int = 1) -> list[SamplePage]:
    """An uploaded cover letter. A two-page one closes with a sparse signature page."""
    first = [
        display_name,
        "4127 Windward Lane, Long Beach, CA 90802",
        "(555) 0142-8890",
        "",
        "August 5, 2026",
        "",
        "Dear Hiring Committee,",
        "",
        *paragraph(
            "I am writing to apply for the Executive Director of Housing position. "
            "For the past eight years I have led residential life operations across "
            "a portfolio of nine buildings housing roughly four thousand students."
        ),
        "",
        *paragraph(
            "My work has centred on making housing operations legible to the people "
            "who depend on them: rebuilding the maintenance request system, "
            "publishing occupancy and turnaround metrics, and bringing residents "
            "into the annual planning cycle rather than briefing them afterwards."
        ),
    ]
    if total == 1:
        return [
            SamplePage(
                lines=[
                    *first,
                    "",
                    *paragraph(
                        "I would welcome the opportunity to discuss the role further. "
                        "Thank you for your time and consideration."
                    ),
                    "",
                    "Sincerely,",
                    "",
                    display_name,
                ]
            )
        ]

    # The real corpus's important regression: a second page holding only the
    # closing and signature, with almost nothing to classify on its own.
    return [
        SamplePage(lines=first),
        SamplePage(
            lines=[
                "",
                "Sincerely,",
                "",
                "",
                display_name,
            ]
        ),
    ]


def resume_pages(*, display_name: str, total: int = 2) -> list[SamplePage]:
    """An uploaded resume, opening with a contact block and resume headings."""
    first = [
        display_name,
        "4127 Windward Lane, Long Beach, CA 90802  |  (555) 0142-8890",
        "applicant@example.edu",
        "",
        "PROFESSIONAL SUMMARY",
        *paragraph(
            "Housing administrator with twelve years of progressive responsibility "
            "across residential life, facilities and student services."
        ),
        "",
        "PROFESSIONAL EXPERIENCE",
        "",
        "Associate Director of Residential Life",
        "Harbour Point Housing Authority - Long Beach, CA",
        "June 2018 - Present",
        bullet("Directed housing operations for 4,000 residents across nine buildings."),
        bullet("Reduced unit turnaround time from eleven days to six."),
        bullet("Managed a $12M annual operating budget."),
    ]
    pages = [first]

    for index in range(2, total + 1):
        is_last = index == total
        body = [display_name, f"Page {index} of {total}", ""]
        if is_last:
            body += [
                "EDUCATION",
                "Master of Public Administration",
                "Rossmore State University - 2014",
                "",
                "CERTIFICATIONS",
                bullet("Certified Housing Professional, 2019"),
            ]
        else:
            body += [
                "PROFESSIONAL EXPERIENCE (CONTINUED)",
                "",
                "Residence Life Coordinator",
                "Cedar Point University - Round Rock, TX",
                "August 2014 - May 2018",
                bullet("Supervised twenty-two resident advisors across four halls."),
                bullet("Launched the peer mentoring programme retained campus-wide."),
            ]
        pages.append(body)

    return [SamplePage(lines=lines) for lines in pages]


def transcript_pages(*, display_name: str, total: int = 2) -> list[SamplePage]:
    """An uploaded academic transcript."""
    first = [
        "ROSSMORE STATE UNIVERSITY",
        "Office of the Registrar",
        "Unofficial Transcript",
        "",
        f"Student Name:    {display_name}",
        "Student ID:      RSU-2012-88431",
        "Program:         Master of Public Administration",
        "",
        "Course      Description                            Credits   Grade",
        "PAD 501     Foundations of Public Administration      3.0       A",
        "PAD 512     Public Budgeting and Finance              3.0       A",
        "PAD 530     Organizational Behavior                   3.0       B",
        "",
        f"Page 1 of {total}",
    ]
    pages = [first]
    for index in range(2, total + 1):
        pages.append(
            [
                "ROSSMORE STATE UNIVERSITY - Unofficial Transcript",
                f"{display_name} - Student ID: RSU-2012-88431",
                f"Page {index} of {total}",
                "",
                "Course      Description                            Credits   Grade",
                "PAD 545     Housing Policy and Planning              3.0       A",
                "PAD 560     Capstone in Public Administration        3.0       A",
                "",
                "Cumulative GPA: 3.78          Total Credits Earned: 36.0",
                "",
                "End of unofficial academic record.",
            ]
        )
    return [SamplePage(lines=lines) for lines in pages]


# --------------------------------------------------------------------------
# Packet assembly
# --------------------------------------------------------------------------


def build_packet(
    *,
    filename: str,
    display_name: str,
    surname: str,
    given: str,
    application: int,
    cover_letter: int = 0,
    resume: int = 0,
    transcript: int = 0,
    blank_page: int | None = None,
) -> PacketFixture:
    """One applicant packet: form, then whichever attachments they uploaded."""
    pages: list[SamplePage] = []
    expected: list[ExpectedDocument] = []

    start = len(pages) + 1
    pages.extend(
        application_form_pages(
            display_name=display_name,
            surname=surname,
            given=given,
            total=application,
            blank_page=blank_page,
        )
    )
    expected.append(ExpectedDocument(APPLICATION_REPORT, start, len(pages)))

    if cover_letter:
        start = len(pages) + 1
        pages.extend(cover_letter_pages(display_name=display_name, total=cover_letter))
        expected.append(ExpectedDocument(COVER_LETTER, start, len(pages)))

    if resume:
        start = len(pages) + 1
        pages.extend(resume_pages(display_name=display_name, total=resume))
        expected.append(ExpectedDocument(RESUME, start, len(pages)))

    if transcript:
        start = len(pages) + 1
        pages.extend(transcript_pages(display_name=display_name, total=transcript))
        expected.append(ExpectedDocument(TRANSCRIPT, start, len(pages)))

    return PacketFixture(
        filename=filename, display_name=display_name, pages=pages, expected=expected
    )


#: The 17-file corpus, shape for shape. Page counts, attachment presence and
#: the awkward names all mirror the real corpus; the people are invented.
#: Totals: 17 application reports, 16 resumes, 10 cover letters, 1 transcript
#: -- 44 logical documents across 161 pages.
CORPUS_SHAPES: tuple[dict, ...] = (
    dict(filename="Carlos Paredez.pdf", display_name="Carlos Paredez",
         surname="Paredez", given="Carlos", application=8, resume=2),
    dict(filename="DeLoyd Grayson Jr..pdf", display_name="DeLoyd Grayson Jr.",
         surname="Grayson", given="DeLoyd", application=6, resume=2, transcript=2),
    dict(filename="Demetrius Brownlee.pdf", display_name="Demetrius Brownlee",
         surname="Brownlee", given="Demetrius", application=7, cover_letter=1, resume=3),
    dict(filename="Edgar Rodrigo.pdf", display_name="Edgar Rodrigo",
         surname="Rodrigo", given="Edgar", application=10, cover_letter=1, resume=2),
    dict(filename="Gema Trujilla.pdf", display_name="Gema Trujilla",
         surname="Trujilla", given="Gema", application=5, resume=1),
    dict(filename="Genia Bakerly.pdf", display_name="Genia Bakerly",
         surname="Bakerly", given="Genia", application=5, resume=2),
    dict(filename="Kimberly Bolinski.pdf", display_name="Kimberly Bolinski",
         surname="Bolinski", given="Kimberly", application=6, cover_letter=1, resume=2),
    # No attachments at all, and a blank sheet inside the form: nothing here
    # may invent a resume.
    dict(filename="Lunye Shepperd.pdf", display_name="Lunye Shepperd",
         surname="Shepperd", given="Lunye", application=7, blank_page=5),
    dict(filename="Marion Sandoval.pdf", display_name="Marion Sandoval",
         surname="Sandoval", given="Marion", application=5, resume=4),
    dict(filename="Matthew Troutner.pdf", display_name="Matthew Troutner",
         surname="Troutner", given="Matthew", application=7, cover_letter=1, resume=4),
    dict(filename="Mayra Galvez.pdf", display_name="Mayra Galvez",
         surname="Galvez", given="Mayra", application=6, cover_letter=1, resume=2),
    dict(filename="Michael Kitchens.pdf", display_name="Michael Kitchens",
         surname="Kitchens", given="Michael", application=4, cover_letter=1, resume=1),
    # Hyphenated surname, plus the two-page cover letter whose second page is
    # only a signature.
    dict(filename="Nicole Stevens-Bothwell.pdf", display_name="Nicole Stevens-Bothwell",
         surname="Stevens-Bothwell", given="Nicole", application=5, cover_letter=2, resume=2),
    dict(filename="Omar Qureshy.pdf", display_name="Omar Qureshy",
         surname="Qureshy", given="Omar", application=6, resume=1),
    dict(filename="Ramon Borunde.pdf", display_name="Ramon Borunde",
         surname="Borunde", given="Ramon", application=11, cover_letter=1, resume=4),
    dict(filename="Richard Saldano.pdf", display_name="Richard Saldano",
         surname="Saldano", given="Richard", application=6, cover_letter=2, resume=2),
    dict(filename="Saul Leale.pdf", display_name="Saul Leale",
         surname="Leale", given="Saul", application=5, cover_letter=2, resume=3),
)


def build_corpus() -> list[PacketFixture]:
    """Every packet in the synthetic corpus."""
    return [build_packet(**shape) for shape in CORPUS_SHAPES]


def build_concatenated_corpus(
    *, filename: str = "Concatenated_Applicants.pdf", count: int = 4
) -> PacketFixture:
    """Several packets joined into one PDF, with nothing between them.

    A new generated first page is the only thing marking the boundary, which
    is exactly the case the parser has to handle for a batched export.
    """
    pages: list[SamplePage] = []
    expected: list[ExpectedDocument] = []
    names: list[str] = []

    for shape in CORPUS_SHAPES[:count]:
        packet = build_packet(**shape)
        offset = len(pages)
        pages.extend(packet.pages)
        names.append(packet.display_name)
        for document in packet.expected:
            expected.append(
                ExpectedDocument(
                    document.document_type,
                    document.first_page + offset,
                    document.last_page + offset,
                )
            )

    fixture = PacketFixture(
        filename=filename,
        display_name=names[0] if names else "",
        pages=pages,
        expected=expected,
    )
    fixture.candidate_names = names  # type: ignore[attr-defined]
    return fixture


__all__ = [
    "ExpectedDocument",
    "PacketFixture",
    "application_form_pages",
    "cover_letter_pages",
    "resume_pages",
    "transcript_pages",
    "build_packet",
    "build_corpus",
    "build_concatenated_corpus",
    "CORPUS_SHAPES",
]
