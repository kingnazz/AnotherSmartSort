"""Synthetic PageUp People Applicant Bulk Compile fixtures.

Reproduces the *structure* of the real 104-page client bulk compile -- its
cover page, roster, per-applicant application forms and compiled attachments,
and the exact page counts observed.

Everything filled into that structure is invented and must stay invented: the
applicant names, the contact details, the requisition number, and the job title
being recruited for. Only the *shape* is copied from the client's file. A real
value that reaches this module reaches the public repository, and the fact that
it sits in a file named "fixtures" is exactly why nobody would look for it.

The page arithmetic is the point of this module: the real file's expected
ranges (application 2-6, resume 7-8, application 9-12, ...) are reproduced
exactly, so a regression that shifts a boundary by one page fails a test
rather than quietly mis-slicing somebody's resume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.profiles.recruiting import (
    APPLICATION_REPORT,
    COVER_LETTER,
    RESUME,
    TRANSCRIPT,
)
from scripts.sample_data import (
    SampleDocument,
    SamplePage,
    build_pdf,
    bullet,
    paragraph,
)

#: The roster of the real 104-page file, name-for-name replaced with invented
#: people while keeping every awkward shape the real one had: an honorific, a
#: preferred name in brackets, a two-word surname, a diacritic.
DEFAULT_ROSTER: tuple[str, ...] = (
    "Peter Ashfield",
    "Ismael Briseño Cardona (Ezra)",
    "Michael Brownlow",
    "Brenda Cavanaugh",
    "Julio Guzman",
    "Alexis Hollingsworth",
    "Tamiah Ladner",
    "Katelyn Lynwood (Kate)",
    "Dr Tandalea Merriweather (Tandalea)",
    "Taran Prescott (TJ)",
    "Hector Peralta",
    "Rafael Perrone",
    "Francis Ramoso",
    "Terry Strathmore",
)

#: (application pages, resume pages) per applicant, matching the real file's
#: ground-truth table exactly -- 5/2, 4/2, 5/3, 6/3, 5/1, 4/2, 5/2, 5/2, 8/3,
#: 4/2, 5/2, 5/3, 6/3, 4/2 -- which sums with the cover page to 104.
DEFAULT_SHAPE: tuple[tuple[int, int], ...] = (
    (5, 2),
    (4, 2),
    (5, 3),
    (6, 3),
    (5, 1),
    (4, 2),
    (5, 2),
    (5, 2),
    (8, 3),
    (4, 2),
    (5, 2),
    (5, 3),
    (6, 3),
    (4, 2),
)

_FORM_TITLE = "Primary application form - LB-Base Staff/MPP (Long) Application Form"


@dataclass
class PageUpApplicant:
    """One applicant's expected extent inside the compiled file."""

    display_name: str
    #: 1-based inclusive page ranges.
    application_first: int
    application_last: int
    resume_first: int
    resume_last: int

    @property
    def application_pages(self) -> list[int]:
        return list(range(self.application_first, self.application_last + 1))

    @property
    def resume_pages(self) -> list[int]:
        return list(range(self.resume_first, self.resume_last + 1))

    @property
    def application_indexes(self) -> list[int]:
        return [page - 1 for page in self.application_pages]

    @property
    def resume_indexes(self) -> list[int]:
        return [page - 1 for page in self.resume_pages]

    @property
    def all_pages(self) -> list[int]:
        return list(range(self.application_first, self.resume_last + 1))


@dataclass
class PageUpBatch:
    """A synthetic bulk compile and the structure it should parse back into."""

    filename: str
    pages: list[SamplePage]
    applicants: list[PageUpApplicant] = field(default_factory=list)
    declared_types: list[str] = field(default_factory=lambda: [RESUME])

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def write(self, output_dir: str | Path) -> Path:
        document = SampleDocument(
            filename=self.filename,
            description=f"{self.page_count}-page synthetic PageUp bulk compile",
            pages=self.pages,
            expected_candidates=[a.display_name for a in self.applicants],
        )
        return build_pdf(document, Path(output_dir) / self.filename)


@dataclass
class PageUpResumeOnlyApplicant:
    """Expected resume extent for an invented resume-only roster member."""

    display_name: str
    resume_first: int | None
    resume_last: int | None
    has_documents: bool = True

    @property
    def resume_pages(self) -> list[int]:
        if self.resume_first is None or self.resume_last is None:
            return []
        return list(range(self.resume_first, self.resume_last + 1))


@dataclass
class PageUpResumeOnlyBatch:
    """A wholly synthetic PageUp cover followed only by roster-ordered resumes."""

    filename: str
    pages: list[SamplePage]
    applicants: list[PageUpResumeOnlyApplicant] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def write(self, output_dir: str | Path) -> Path:
        document = SampleDocument(
            filename=self.filename,
            description=f"{self.page_count}-page synthetic resume-only bulk compile",
            pages=self.pages,
            expected_candidates=[applicant.display_name for applicant in self.applicants],
        )
        return build_pdf(document, Path(output_dir) / self.filename)


# --------------------------------------------------------------------------
# Page builders
# --------------------------------------------------------------------------


def cover_page(
    roster: tuple[str, ...],
    declared_types: tuple[str, ...] = (RESUME,),
    *,
    where_available: bool = False,
    no_document_indexes: tuple[int, ...] = (),
) -> SamplePage:
    """The bulk compile's metadata cover: declared types, roster, count."""
    types_heading = "The following document types are provided for each applicant"
    if where_available:
        types_heading += " where available"
    lines = [
        "PageUp People Applicant Bulk Compile",
        "",
        types_heading,
    ]
    lines.extend(declared_types)
    lines += ["", "The following applicants are included in this document"]
    no_documents = set(no_document_indexes)
    lines.extend(
        f"{name}*" if index in no_documents else name
        for index, name in enumerate(roster)
    )
    if no_documents:
        lines += ["", "* Applicant has no documents."]
    lines += ["", f"Number of Applicants: {len(roster)}"]
    return SamplePage(lines=lines)


def application_pages(name: str, total: int) -> list[SamplePage]:
    """An applicant's generated application form, ending with ``Total score``."""
    first = [
        name,
        _FORM_TITLE,
        "",
        "Personal details",
        f"Name                     {name}",
        "Email                    applicant@example.edu",
        "Phone                    (555) 010-2200",
        "Address                  118 Rossmore Avenue, Long Beach, CA 90802",
        "",
        "Position applied for     Operations Coordinator",
        "Requisition              900218",
        "Date submitted           4 March 2026",
    ]
    pages = [first]

    for index in range(2, total + 1):
        is_last = index == total
        body = [
            name,
            f"{_FORM_TITLE}  -  page {index} of {total}",
            "",
        ]
        if is_last:
            body += [
                "Selection criteria responses",
                "Criterion 3: Describe your experience coordinating vendors.",
                "Response: Managed twelve vendor contracts across three sites.",
                "",
                "Assessment",
                "Criterion 1 score        4",
                "Criterion 2 score        5",
                "Criterion 3 score        4",
                "Total score              13",
            ]
        else:
            body += [
                "Employment history",
                f"Employer {index}            Cordova Facilities Group",
                "Position                 Operations Coordinator",
                "Dates                    January 2019 - present",
                "",
                "Education history",
                "Institution              Rossmore State College",
                "Qualification            Associate of Applied Science",
                "",
                "Questionnaire",
                "Are you legally entitled to work in the United States?   Yes",
            ]
        pages.append(body)

    return [SamplePage(lines=lines) for lines in pages]


def resume_attachment_pages(
    name: str, total: int, *, numbered: bool = True, letter_page: bool = False
) -> list[SamplePage]:
    """An applicant's uploaded resume, carrying no PageUp form furniture.

    ``numbered=False`` drops the ``Page 2 of 3`` footers and repeats the
    candidate's contact block at the top of every page instead -- the layout a
    great many real resumes use, and the one that makes a continuation page
    look exactly like a first page.

    ``letter_page=True`` opens the second page with a salutation, as happens
    when somebody pastes their covering note into the same file they uploaded
    as their resume. The cover page said this whole region is a resume, and
    that has to beat a page that merely reads like a letter -- otherwise the
    file comes back with a cover letter the compile never promised, and the
    resume it was cut from is short.
    """
    first = [
        name,
        "118 Rossmore Avenue, Long Beach, CA 90802  |  (555) 010-2200",
        "applicant@example.edu",
        "",
        "PROFESSIONAL SUMMARY",
        *paragraph(
            "Facilities coordinator with nine years of experience running "
            "multi-site maintenance programmes, vendor contracts and capital "
            "works scheduling for public sector campuses."
        ),
        "",
        "PROFESSIONAL EXPERIENCE",
        "",
        "Operations Coordinator",
        "Cordova Facilities Group - Long Beach, CA",
        "January 2019 - Present",
        bullet("Coordinated twelve vendor contracts worth $3.4M annually."),
        bullet("Cut average work-order turnaround from nine days to four."),
        bullet("Introduced the preventive maintenance calendar now used site-wide."),
    ]
    pages = [first]

    for index in range(2, total + 1):
        is_last = index == total
        if letter_page and index == 2:
            pages.append(
                [
                    name,
                    "",
                    "Dear Hiring Committee,",
                    "",
                    *paragraph(
                        "Thank you for considering my application for the "
                        "Operations Coordinator position. My experience across "
                        "three campuses is summarised above."
                    ),
                    "",
                    "Sincerely,",
                    name,
                ]
            )
            continue
        if numbered:
            body = [name, f"Page {index} of {total}", ""]
        else:
            body = [
                name,
                "118 Rossmore Avenue, Long Beach, CA 90802  |  (555) 010-2200",
                "applicant@example.edu",
                "",
            ]
        if is_last:
            body += [
                "EDUCATION",
                "Associate of Applied Science, Facilities Management",
                "Rossmore State College - 2016",
                "",
                "CORE COMPETENCIES",
                bullet("Certified Facility Manager (CFM), 2021"),
                bullet("OSHA 30-Hour General Industry, 2019"),
            ]
        else:
            body += [
                "PROFESSIONAL EXPERIENCE (CONTINUED)",
                "",
                "Maintenance Supervisor",
                "Harbour Point Property Services - Long Beach, CA",
                "June 2016 - December 2018",
                bullet("Supervised a team of eight across two shifts."),
                bullet("Managed the annual $600K maintenance operating budget."),
            ]
        pages.append(body)

    return [SamplePage(lines=lines) for lines in pages]


def resume_only_pages(
    header_name: str,
    total: int,
    *,
    include_contact: bool = True,
    opening_kind: str = "resume",
) -> list[SamplePage]:
    """Invented resume pages for the cover-plus-resumes PageUp shape."""
    if opening_kind == "reference":
        first = [
            "REFERENCE LETTER",
            f"RE: Professional reference for {header_name}",
            "To the selection committee:",
            "",
            *paragraph(
                "This invented reference describes dependable programme "
                "coordination and accessible community service."
            ),
        ]
    elif opening_kind == "cover_letter":
        first = [
            "COVER LETTER",
            header_name,
            "Dear Selection Committee:",
            "",
            *paragraph(
                "Please accept this invented application for a programme "
                "coordination role serving the Northwind community."
            ),
        ]
    elif opening_kind == "resume":
        first = [header_name]
        if include_contact:
            first += [
                "invented.candidate@example.com | (555) 010-4400",
                "Northwind, OR",
            ]
        first += [
            "",
            "PROFESSIONAL SUMMARY",
            *paragraph(
                "Program coordinator experienced in community partnerships, "
                "public workshops, scheduling, and accessible service delivery."
            ),
            "",
            "PROFESSIONAL EXPERIENCE",
            "Program Coordinator",
            "Northwind Community Lab",
            "2021 - Present",
            bullet("Coordinated an invented regional workshop programme."),
        ]
    else:
        raise ValueError(f"unknown resume-only opening kind: {opening_kind}")
    pages = [SamplePage(lines=first)]

    for index in range(2, total + 1):
        if index == total:
            lines = [
                "EDUCATION",
                "Bachelor of Arts, Public Administration",
                "Northwind College - 2020",
                "",
                "SKILLS",
                bullet("Programme planning and stakeholder communication"),
            ]
        else:
            lines = [
                "PROFESSIONAL EXPERIENCE (CONTINUED)",
                "Community Liaison",
                "Cedar Valley Learning Cooperative",
                "2017 - 2021",
                bullet("Maintained schedules and prepared public information."),
            ]
        pages.append(SamplePage(lines=lines))
    return pages


def build_resume_only_compile(
    *,
    filename: str = "PageUp_Resume_Only.pdf",
    roster: tuple[str, ...] = (
        "Avery North",
        "Bailey Orchard",
        "Cameron Pine",
        "Devon Quill",
    ),
    lengths: tuple[int, ...] = (1, 2, 4, 7),
    header_names: tuple[str, ...] | None = None,
    contact_flags: tuple[bool, ...] | None = None,
    no_document_indexes: tuple[int, ...] = (),
    where_available: bool = False,
    opening_kinds: tuple[str, ...] | None = None,
) -> PageUpResumeOnlyBatch:
    """Build a roster-ordered resume-only compile with no application forms."""
    if len(roster) != len(lengths):
        raise ValueError("roster and lengths must have the same size")
    headers = header_names or roster
    contacts = contact_flags or tuple(True for _ in roster)
    openings = opening_kinds or tuple("resume" for _ in roster)
    if (
        len(headers) != len(roster)
        or len(contacts) != len(roster)
        or len(openings) != len(roster)
    ):
        raise ValueError("header options must match the roster size")
    no_documents = set(no_document_indexes)
    if any(index < 0 or index >= len(roster) for index in no_documents):
        raise ValueError("no-document indexes must name roster entries")

    pages: list[SamplePage] = [
        cover_page(
            roster,
            (RESUME,),
            where_available=where_available,
            no_document_indexes=no_document_indexes,
        )
    ]
    applicants: list[PageUpResumeOnlyApplicant] = []
    options = zip(roster, headers, lengths, contacts, openings)
    for index, (
        display_name,
        header_name,
        length,
        include_contact,
        opening_kind,
    ) in enumerate(options):
        if index in no_documents:
            applicants.append(
                PageUpResumeOnlyApplicant(
                    display_name=display_name,
                    resume_first=None,
                    resume_last=None,
                    has_documents=False,
                )
            )
            continue
        if length < 1:
            raise ValueError("document-bearing applicants need at least one page")
        resume_first = len(pages) + 1
        pages.extend(
            resume_only_pages(
                header_name,
                length,
                include_contact=include_contact,
                opening_kind=opening_kind,
            )
        )
        applicants.append(
            PageUpResumeOnlyApplicant(
                display_name=display_name,
                resume_first=resume_first,
                resume_last=len(pages),
            )
        )

    return PageUpResumeOnlyBatch(
        filename=filename,
        pages=pages,
        applicants=applicants,
    )


# --------------------------------------------------------------------------
# Batch assembly
# --------------------------------------------------------------------------


def build_bulk_compile(
    *,
    filename: str = "PageUp_Bulk_Compile.pdf",
    roster: tuple[str, ...] = DEFAULT_ROSTER,
    shape: tuple[tuple[int, int], ...] = DEFAULT_SHAPE,
    declared_types: tuple[str, ...] = (RESUME,),
    include_total_score: bool = True,
    numbered_resumes: bool = True,
    letter_inside_resumes: bool = False,
) -> PageUpBatch:
    """Assemble a bulk compile and record every expected page range.

    ``include_total_score=False`` drops the application-ending marker, which
    is the malformed case the parser has to survive without inventing
    boundaries.

    ``numbered_resumes=False`` gives every resume the repeated-contact-block
    layout instead of page footers, so each continuation page reads like the
    opening of a fresh resume. That is the shape a splitter would cut up, and
    the reason the parser refuses to split a region whose type the cover page
    already stated.
    """
    pages: list[SamplePage] = [cover_page(roster, declared_types)]
    applicants: list[PageUpApplicant] = []

    for name, (application_length, resume_length) in zip(roster, shape):
        application_first = len(pages) + 1
        built = application_pages(name, application_length)
        if not include_total_score:
            built = [
                SamplePage(
                    lines=[
                        line
                        for line in page.lines
                        if not line.strip().lower().startswith("total score")
                    ]
                )
                for page in built
            ]
        pages.extend(built)
        application_last = len(pages)

        resume_first = len(pages) + 1
        pages.extend(
            resume_attachment_pages(
                name,
                resume_length,
                numbered=numbered_resumes,
                letter_page=letter_inside_resumes,
            )
        )
        resume_last = len(pages)

        applicants.append(
            PageUpApplicant(
                display_name=name,
                application_first=application_first,
                application_last=application_last,
                resume_first=resume_first,
                resume_last=resume_last,
            )
        )

    return PageUpBatch(
        filename=filename,
        pages=pages,
        applicants=applicants,
        declared_types=list(declared_types),
    )


# --------------------------------------------------------------------------
# Multi-attachment batches
# --------------------------------------------------------------------------
#
# A bulk compile whose cover declares more than one attachment type runs those
# attachments together with nothing between them, so these fixtures exist to
# pin down where each one begins. Every shape below is one the real exports
# produce: a letter that runs onto a signature-only page, a resume that repeats
# its own page numbering, an applicant who uploaded only some of what was
# asked for, and -- the case that must never be guessed -- two documents merged
# into one upload with continuous page numbers.


def cover_letter_attachment_pages(
    name: str, total: int = 1, *, sparse_last: bool = False
) -> list[SamplePage]:
    """An uploaded cover letter, opening with a salutation."""
    first = [
        name,
        "118 Rossmore Avenue, Long Beach, CA 90802",
        "applicant@example.edu  |  (555) 010-2200",
        "",
        "4 March 2026",
        "",
        "Dear Hiring Committee,",
        "",
        *paragraph(
            "I am writing to apply for the Operations Coordinator position "
            "advertised under requisition 900218. Nine years of multi-site "
            "maintenance work have given me the vendor management and capital "
            "planning experience the role calls for."
        ),
        "",
        *paragraph(
            "At Cordova Facilities Group I took responsibility for twelve "
            "vendor contracts and rebuilt the preventive maintenance schedule "
            "that the campus still runs on today."
        ),
    ]
    pages = [first]

    for index in range(2, total + 1):
        if sparse_last and index == total:
            # The page a real letter ends on: a closing and a signature, and
            # nothing else. It must not be read as a document of its own.
            pages.append(["Sincerely,", "", "", name])
            continue
        pages.append(
            [
                *paragraph(
                    "I would welcome the chance to discuss how that experience "
                    "would transfer to your campus, and I have enclosed the "
                    "references named in my application."
                ),
                "",
                "Sincerely,",
                "",
                name,
            ]
        )

    return [SamplePage(lines=lines) for lines in pages]


def transcript_attachment_pages(name: str, total: int = 1) -> list[SamplePage]:
    """An uploaded academic transcript, titled and numbered."""
    first = [
        "Unofficial Transcript",
        "Rossmore State College",
        "",
        f"Student            {name}",
        "Student ID         RSC-4471902",
        f"Page 1 of {total}",
        "",
        "Term               Course                              Credits   Grade",
        "Fall 2014          ENG 101 Composition                 3.0       A-",
        "Fall 2014          MTH 120 College Algebra             3.0       B+",
        "Spring 2015        FAC 210 Building Systems            4.0       A",
        "",
        "Cumulative GPA     3.61",
    ]
    pages = [first]
    for index in range(2, total + 1):
        pages.append(
            [
                "Unofficial Transcript",
                "Rossmore State College",
                f"Page {index} of {total}",
                "",
                "Term               Course                              Credits   Grade",
                "Fall 2015          FAC 315 Capital Works Planning      4.0       A-",
                "Spring 2016        FAC 340 Contract Administration     3.0       B+",
                "",
                "Cumulative GPA     3.61",
            ]
        )
    return [SamplePage(lines=lines) for lines in pages]


def combined_attachment_pages(name: str) -> list[SamplePage]:
    """A cover letter and resume uploaded as one file, numbered straight through.

    The applicant merged two documents before uploading, so the file itself
    says these three pages are one document -- while the text says they are
    two. Nothing here can be trusted to place the seam, which is exactly when
    the parser must hand the pages to a human instead of picking.
    """
    return [
        SamplePage(
            lines=[
                name,
                "Page 1 of 3",
                "",
                "Dear Hiring Committee,",
                "",
                *paragraph(
                    "I am writing to apply for the Operations Coordinator "
                    "position advertised under requisition 900218, and have "
                    "attached my resume below."
                ),
                "",
                "Sincerely,",
                name,
            ]
        ),
        SamplePage(
            lines=[
                name,
                "Page 2 of 3",
                "",
                "PROFESSIONAL SUMMARY",
                *paragraph(
                    "Facilities coordinator with nine years of experience "
                    "running multi-site maintenance programmes and vendor "
                    "contracts for public sector campuses."
                ),
                "",
                "PROFESSIONAL EXPERIENCE",
                "Operations Coordinator",
                "Cordova Facilities Group - Long Beach, CA",
                "January 2019 - Present",
                bullet("Coordinated twelve vendor contracts worth $3.4M annually."),
                bullet("Cut average work-order turnaround from nine days to four."),
            ]
        ),
        SamplePage(
            lines=[
                name,
                "Page 3 of 3",
                "",
                "WORK EXPERIENCE (CONTINUED)",
                "Maintenance Supervisor",
                "Harbour Point Property Services - Long Beach, CA",
                "June 2016 - December 2018",
                bullet("Supervised a team of eight across two shifts."),
                "",
                "EDUCATION",
                "Associate of Applied Science, Facilities Management",
                "Rossmore State College - 2016",
            ]
        ),
    ]


def filename_banner_attachment_pages(
    name: str, total: int = 1, *, document_type: str = RESUME
) -> list[SamplePage]:
    """An upload identified only by the filename the applicant gave it.

    The body is deliberately unremarkable prose, so the filename is the sole
    evidence of what this is and where it starts. Real compiles print these
    banners; a parser that ignores them has to guess instead.
    """
    stem = document_type.replace(" ", "")
    pages = [
        [
            f"{stem} - {name}.pdf",
            "",
            *paragraph(
                "Nine years coordinating facilities for public sector campuses, "
                "with responsibility for vendor contracts, preventive "
                "maintenance scheduling and capital works planning."
            ),
        ]
    ]
    for index in range(2, total + 1):
        pages.append(
            [
                f"Page {index} of {total}",
                "",
                *paragraph(
                    "Further detail on the multi-site programmes described "
                    "above, including the annual operating budget and the "
                    "reporting line into the campus facilities director."
                ),
            ]
        )
    return [SamplePage(lines=lines) for lines in pages]


def unidentifiable_attachment_pages(name: str, total: int = 1) -> list[SamplePage]:
    """An upload that is none of the declared types and says nothing about itself."""
    pages = []
    for index in range(total):
        pages.append(
            SamplePage(
                lines=[
                    *paragraph(
                        "The southern quadrangle drainage survey was carried "
                        "out over four site visits between January and March. "
                        "Readings were taken at each of the eleven inspection "
                        "chambers and compared against the 2019 baseline."
                    ),
                    "",
                    *paragraph(
                        "No further remediation is recommended for the current "
                        "financial year beyond the scheduled clearance of the "
                        "eastern culvert."
                    ),
                ]
            )
        )
    return pages


#: How each attachment type's pages are built. Keyed by canonical type so a
#: batch can be described as ``[(COVER_LETTER, 2), (RESUME, 3)]`` and read the
#: way the cover page reads.
_ATTACHMENT_BUILDERS = {
    COVER_LETTER: cover_letter_attachment_pages,
    RESUME: resume_attachment_pages,
    TRANSCRIPT: transcript_attachment_pages,
}


@dataclass
class ExpectedAttachment:
    """One uploaded document and the pages it should occupy."""

    document_type: str
    first_page: int
    last_page: int
    #: Set when the parser is expected to ask for a human rather than commit.
    needs_review: bool = False

    @property
    def pages(self) -> list[int]:
        return list(range(self.first_page, self.last_page + 1))

    @property
    def indexes(self) -> list[int]:
        return [page - 1 for page in self.pages]


@dataclass
class ExpectedPacket:
    """One applicant's application form and everything they uploaded."""

    display_name: str
    application_first: int
    application_last: int
    attachments: list[ExpectedAttachment] = field(default_factory=list)

    @property
    def application_indexes(self) -> list[int]:
        return [page - 1 for page in range(self.application_first, self.application_last + 1)]


@dataclass
class PageUpMultiBatch:
    """A multi-attachment bulk compile and the structure it should parse into."""

    filename: str
    pages: list[SamplePage]
    packets: list[ExpectedPacket] = field(default_factory=list)
    declared_types: list[str] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def write(self, output_dir: str | Path) -> Path:
        document = SampleDocument(
            filename=self.filename,
            description=f"{self.page_count}-page synthetic multi-attachment bulk compile",
            pages=self.pages,
            expected_candidates=[packet.display_name for packet in self.packets],
        )
        return build_pdf(document, Path(output_dir) / self.filename)


#: ``(name, application page count, [(type, pages), ...])``, where a type is
#: either a canonical document type or one of the special shapes:
#: ``"combined"`` (two documents uploaded as one numbered file),
#: ``"unknown"`` (none of the declared types), and ``"filename:<Type>"`` (a
#: file identified only by the name the applicant gave it).
AttachmentSpec = tuple[str, int]
PacketSpec = tuple[str, int, list[AttachmentSpec]]

_FILENAME_PREFIX = "filename:"
#: Shapes the parser is expected to hand to a reviewer rather than name itself.
_REVIEWABLE = frozenset({"combined", "unknown"})


def build_multi_attachment_compile(
    specs: list[PacketSpec],
    *,
    filename: str = "PageUp_Multi_Attachment.pdf",
    declared_types: tuple[str, ...] = (COVER_LETTER, RESUME),
    sparse_letter_endings: bool = False,
) -> PageUpMultiBatch:
    """Assemble a bulk compile declaring several attachment types."""
    roster = tuple(name for name, _, _ in specs)
    pages: list[SamplePage] = [cover_page(roster, declared_types)]
    packets: list[ExpectedPacket] = []

    for name, application_length, attachments in specs:
        application_first = len(pages) + 1
        pages.extend(application_pages(name, application_length))
        packet = ExpectedPacket(
            display_name=name,
            application_first=application_first,
            application_last=len(pages),
        )

        for shape, length in attachments:
            first = len(pages) + 1
            pages.extend(
                _attachment_pages(
                    shape, name, length, sparse_letter_endings=sparse_letter_endings
                )
            )
            packet.attachments.append(
                ExpectedAttachment(
                    document_type=_expected_type(shape),
                    first_page=first,
                    last_page=len(pages),
                    needs_review=shape in _REVIEWABLE,
                )
            )
        packets.append(packet)

    return PageUpMultiBatch(
        filename=filename,
        pages=pages,
        packets=packets,
        declared_types=list(declared_types),
    )


def _expected_type(shape: str) -> str:
    """The document type a shape should end up classified as."""
    if shape.startswith(_FILENAME_PREFIX):
        return shape[len(_FILENAME_PREFIX) :]
    if shape == "combined":
        return COVER_LETTER
    if shape == "unknown":
        return ""
    return shape


def _attachment_pages(
    shape: str, name: str, length: int, *, sparse_letter_endings: bool
) -> list[SamplePage]:
    if shape.startswith(_FILENAME_PREFIX):
        return filename_banner_attachment_pages(
            name, length, document_type=shape[len(_FILENAME_PREFIX) :]
        )
    if shape == "combined":
        return combined_attachment_pages(name)
    if shape == "unknown":
        return unidentifiable_attachment_pages(name, length)
    builder = _ATTACHMENT_BUILDERS[shape]
    if shape == COVER_LETTER:
        return builder(name, length, sparse_last=sparse_letter_endings)
    return builder(name, length)


def build_roster_mismatch_compile(
    *, filename: str = "PageUp_Roster_Mismatch.pdf"
) -> PageUpBatch:
    """A cover promising more applicants than the file actually contains.

    The parser must extract what is there *and* say the count disagrees,
    rather than silently returning a short batch.
    """
    roster = DEFAULT_ROSTER[:4]
    batch = build_bulk_compile(
        filename=filename, roster=roster, shape=DEFAULT_SHAPE[:4]
    )
    inflated = list(batch.pages[0].lines)
    for index, line in enumerate(inflated):
        if line.lower().startswith("number of applicants"):
            inflated[index] = "Number of Applicants: 9"
            break
    batch.pages[0] = SamplePage(lines=inflated)
    return batch


__all__ = [
    "PageUpApplicant",
    "PageUpBatch",
    "PageUpMultiBatch",
    "ExpectedPacket",
    "ExpectedAttachment",
    "DEFAULT_ROSTER",
    "DEFAULT_SHAPE",
    "cover_page",
    "application_pages",
    "resume_attachment_pages",
    "cover_letter_attachment_pages",
    "transcript_attachment_pages",
    "filename_banner_attachment_pages",
    "combined_attachment_pages",
    "unidentifiable_attachment_pages",
    "build_bulk_compile",
    "build_multi_attachment_compile",
    "build_roster_mismatch_compile",
]
