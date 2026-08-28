"""Synthetic ATS report export fixtures.

Real ATS exports (the PDFs a recruiting system generates when someone
downloads an applicant's report) follow an exact structure: an application
report, a labelled separator page ("Resume"), that attachment's pages, the
next separator ("Cover Letters"), and so on. This module reproduces that
*structure* -- the same page-count pattern observed across real client
exports -- using entirely invented names, contact details and content.

Nothing here is real applicant data, and nothing here is committed as a
binary: every PDF is generated on demand, exactly like the rest of the
project's synthetic fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.profiles.recruiting import APPLICATION_REPORT, COVER_LETTER, RESUME
from scripts.mixed_batch import APPLICANTS, Applicant
from scripts.sample_data import (
    SampleDocument,
    SamplePage,
    build_pdf,
    bullet,
    paragraph,
    separator_page,
)

# --------------------------------------------------------------------------
# The report page builder
# --------------------------------------------------------------------------


def ats_report_pages(
    *,
    name: str,
    applicant_id: str,
    email: str,
    phone: str,
    job_title: str,
    requisition_id: str = "REQ-2026-1001",
    total: int = 3,
) -> list[SamplePage]:
    """A multi-page, machine-generated ATS application report.

    Carries the strong markers a real export prints on every page --
    "Confidential Report", "Application Details for <name>", "Job Opening
    ID:" -- which is exactly what the deterministic parser looks for, and
    what a resume or cover letter never states in passing.
    """
    page1 = [
        "CONFIDENTIAL REPORT",
        f"Application Details for {name}",
        "",
        f"Job Opening ID: {requisition_id}",
        f"Job Posting Title: {job_title}",
        "",
        "APPLICANT INFORMATION",
        f"Name                     {name}",
        f"Applicant ID             {applicant_id}",
        f"Email                    {email}",
        f"Phone                    {phone}",
        "Address                  4127 Windward Lane, Austin, TX 78704",
        "",
        "APPLICATION SUMMARY",
        f"Position Applied For:    {job_title}",
        "Application Date:        March 12, 2026",
        "Source:                  Company Career Site",
        "Status:                  In Review",
    ]

    pages = [page1]
    for index in range(2, total + 1):
        header = [
            "CONFIDENTIAL REPORT",
            f"Application Details for {name}",
            f"Job Opening ID: {requisition_id}                          Page {index} of {total}",
            "",
        ]
        if index == total:
            body = [
                "APPLICATION HISTORY",
                "Date              Stage                       Disposition",
                "March 12, 2026    Application Received        Advanced",
                "March 14, 2026    Recruiter Screen            Advanced",
                "March 18, 2026    Hiring Manager Review        In Progress",
                "",
                "ATTACHMENTS",
                "Attachment 1:            Resume",
                "Attachment 2:            Cover Letter",
            ]
        else:
            body = [
                "APPLICATION QUESTIONS",
                "",
                "Question 1: Are you legally authorized to work in the United States?",
                "Answer: Yes",
                "",
                "Question 2: How many years of relevant experience do you have?",
                "Answer: 6 years",
                "",
                "Question 3: Are you willing to relocate?",
                "Answer: No",
            ]
        pages.append(header + body)

    return [SamplePage(lines=lines) for lines in pages]


def _multi_page_resume(
    *, name: str, email: str, phone: str, total: int
) -> list[SamplePage]:
    """A resume with exactly ``total`` pages, however many that is.

    ``scripts.sample_data.resume_pages`` only has three hand-written page
    templates and silently caps at three pages for any larger ``total`` --
    fine for the rest of the suite, but Trevor Hollands' real six-page resume
    needs to actually be six pages, not three.
    """
    page1 = [
        name,
        f"Austin, TX 78704  |  {phone}  |  {email}",
        "",
        "PROFESSIONAL SUMMARY",
        *paragraph(
            "Experienced professional with a strong record of delivering results "
            "across cross-functional teams, with particular strength in turning "
            "ambiguous problems into structured plans."
        ),
        "",
        "PROFESSIONAL EXPERIENCE",
        "",
        "Senior Analyst",
        "Rivermark Logistics - Austin, TX",
        "June 2021 - Present",
        bullet("Led a cross-functional initiative that improved throughput by 20%."),
        bullet("Managed a team of five analysts across two departments."),
        bullet("Presented quarterly performance reviews to executive leadership."),
    ]
    pages = [page1]
    for index in range(2, total + 1):
        is_last = index == total
        pages.append(
            [
                name,
                f"Page {index} of {total}",
                "",
                "EDUCATION" if is_last else "PROFESSIONAL EXPERIENCE (CONTINUED)",
                "",
                *(
                    [
                        "Bachelor of Science, Business Administration",
                        "State University - Graduated 2016",
                        "",
                        "SKILLS",
                        "Analytics, Project Management, Stakeholder Communication",
                    ]
                    if is_last
                    else [
                        "Operations Coordinator",
                        "Halden Freight Group - San Antonio, TX",
                        f"January {2015 + index} - May {2020 + index}",
                        bullet("Coordinated logistics for a forty-person operations team."),
                        bullet("Reduced processing time 15% through workflow redesign."),
                    ]
                ),
            ]
        )
    return [SamplePage(lines=lines) for lines in pages]


def _multi_page_cover_letter(
    *, name: str, job_title: str, total: int
) -> list[SamplePage]:
    """A cover letter with exactly ``total`` pages.

    ``scripts.sample_data.cover_letter_pages`` only supports one or two pages
    -- Trevor Hollands' real three-page cover letter needs a generator that
    actually honours an arbitrary length.
    """
    opening = [
        name,
        "4127 Windward Lane",
        "Austin, TX 78704",
        "",
        "March 12, 2026",
        "",
        "Hiring Manager",
        "Talent Acquisition",
        "",
        "Dear Hiring Manager,",
        "",
        *paragraph(
            f"I am writing to apply for the {job_title} position. My background "
            "aligns closely with what your posting describes, and I would welcome "
            "the opportunity to discuss it further."
        ),
    ]
    if total <= 1:
        return [
            SamplePage(
                lines=[
                    *opening,
                    "",
                    *paragraph(
                        "Thank you for your time and consideration. I look forward "
                        "to hearing from you."
                    ),
                    "",
                    "Sincerely,",
                    "",
                    name,
                ]
            )
        ]

    pages = [[*opening, "", f"{name} - Page 1 of {total}"]]
    for index in range(2, total + 1):
        is_last = index == total
        body = [
            f"{name} - Cover Letter",
            f"Page {index} of {total}",
            "",
            *paragraph(
                "Beyond the specific responsibilities, I am drawn to organisations "
                "that treat reporting as something people actually rely on, not "
                "just something produced for its own sake."
            ),
        ]
        if is_last:
            body += [
                "",
                *paragraph(
                    "Thank you again for your time and consideration. I would "
                    "welcome the chance to discuss the role further."
                ),
                "",
                "Sincerely,",
                "",
                name,
            ]
        else:
            body += ["", f"{name} - Page {index} of {total}"]
        pages.append(body)
    return [SamplePage(lines=lines) for lines in pages]


# --------------------------------------------------------------------------
# Candidate assembly: report -> "Resume" separator -> resume ->
# "Cover Letters" separator -> cover letter
# --------------------------------------------------------------------------


@dataclass
class AtsSection:
    """One logical document's expected extent within the combined source PDF."""

    document_type: str
    #: 1-based, inclusive -- the pages the *exported* PDF should contain
    #: (never the separator page itself).
    first_page: int
    last_page: int

    @property
    def pages(self) -> list[int]:
        """1-based page numbers."""
        return list(range(self.first_page, self.last_page + 1))

    @property
    def page_indexes(self) -> list[int]:
        """0-based page indexes, directly comparable to ``export_page_indexes``."""
        return [p - 1 for p in self.pages]

    @property
    def page_count(self) -> int:
        return self.last_page - self.first_page + 1


@dataclass
class AtsCandidateResult:
    """One applicant's reconstructed structure within an :class:`AtsBatch`."""

    name: str
    applicant_id: str
    report: AtsSection
    resume: AtsSection
    cover_letter: AtsSection
    #: 1-based page number of each separator page (excluded from export).
    resume_separator_page: int
    cover_letter_separator_page: int

    @property
    def all_pages(self) -> list[int]:
        """Every 1-based page number belonging to this candidate, report through letter."""
        return list(range(self.report.first_page, self.cover_letter.last_page + 1))


@dataclass
class AtsBatch:
    """A synthetic multi-section ATS source PDF and its expected structure."""

    filename: str
    pages: list[SamplePage]
    candidates: list[AtsCandidateResult] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def as_sample_document(self) -> SampleDocument:
        return SampleDocument(
            filename=self.filename,
            description=f"{self.page_count}-page ATS export batch",
            pages=self.pages,
            expected_candidates=[c.name for c in self.candidates],
        )

    def write(self, output_dir: str | Path) -> Path:
        return build_pdf(self.as_sample_document(), Path(output_dir) / self.filename)


class AtsBatchBuilder:
    """Assembles one or more ATS applicant packets, tracking exact page ranges."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.pages: list[SamplePage] = []
        self.candidates: list[AtsCandidateResult] = []

    def add_candidate(
        self,
        *,
        name: str,
        applicant_id: str,
        email: str,
        phone: str,
        job_title: str,
        report_page_count: int,
        resume_page_count: int,
        cover_letter_page_count: int,
        requisition_id: str = "REQ-2026-1001",
    ) -> AtsCandidateResult:
        """Append one applicant's report -> Resume -> Cover Letters packet."""
        report_first = len(self.pages) + 1
        self.pages.extend(
            ats_report_pages(
                name=name,
                applicant_id=applicant_id,
                email=email,
                phone=phone,
                job_title=job_title,
                requisition_id=requisition_id,
                total=report_page_count,
            )
        )
        report_last = len(self.pages)

        self.pages.append(separator_page("Resume"))
        resume_separator_page = len(self.pages)

        resume_first = len(self.pages) + 1
        self.pages.extend(
            _multi_page_resume(name=name, email=email, phone=phone, total=resume_page_count)
        )
        resume_last = len(self.pages)

        self.pages.append(separator_page("Cover Letters"))
        cover_letter_separator_page = len(self.pages)

        cover_letter_first = len(self.pages) + 1
        self.pages.extend(
            _multi_page_cover_letter(name=name, job_title=job_title, total=cover_letter_page_count)
        )
        cover_letter_last = len(self.pages)

        result = AtsCandidateResult(
            name=name,
            applicant_id=applicant_id,
            report=AtsSection(APPLICATION_REPORT, report_first, report_last),
            resume=AtsSection(RESUME, resume_first, resume_last),
            cover_letter=AtsSection(COVER_LETTER, cover_letter_first, cover_letter_last),
            resume_separator_page=resume_separator_page,
            cover_letter_separator_page=cover_letter_separator_page,
        )
        self.candidates.append(result)
        return result

    def build(self) -> AtsBatch:
        return AtsBatch(filename=self.filename, pages=self.pages, candidates=self.candidates)


# --------------------------------------------------------------------------
# The four real-file-derived, single-candidate fixtures
# --------------------------------------------------------------------------
#
# Page ranges reproduce exactly what was observed in the real client exports:
# an application report, a "Resume" separator, the resume, a "Cover Letters"
# separator, the cover letter.
#
# Every applicant here is invented, and must stay that way. Only the *shape* of
# a real export -- its page counts and section order -- is reproduced; never a
# real person's name, email, phone or applicant ID. An earlier version of this
# file claimed the names were invented while actually carrying the names of the
# five real applicants in the git-ignored qa/input, which is how they ended up
# quoted across the docs and tests as well. Use example.com/.net/.org and the
# reserved 555 phone prefix so a real detail is obvious on sight.


def marcus_delgado_batch() -> AtsBatch:
    """Report 1-5, separator p6, resume 7-10, separator p11, cover letter p12."""
    builder = AtsBatchBuilder("MarcusDelgado_Application_Report.pdf")
    builder.add_candidate(
        name="Marcus Delgado",
        applicant_id="A-30001",
        email="marcus.delgado@example.com",
        phone="(555) 201-4477",
        job_title="Field Service Technician",
        report_page_count=5,
        resume_page_count=4,
        cover_letter_page_count=1,
    )
    return builder.build()


def nathan_whitfield_batch() -> AtsBatch:
    """Report 1-4, separator p5, resume 6-7, separator p8, cover letter p9."""
    builder = AtsBatchBuilder("NathanWhitfield_Application_Report.pdf")
    builder.add_candidate(
        name="Nathan Whitfield",
        applicant_id="A-30002",
        email="nathan.whitfield@example.com",
        phone="(555) 442-1190",
        job_title="Warehouse Supervisor",
        report_page_count=4,
        resume_page_count=2,
        cover_letter_page_count=1,
    )
    return builder.build()


def trevor_hollands_batch() -> AtsBatch:
    """Report 1-10, separator p11, resume 12-17, separator p18, cover letter 19-21."""
    builder = AtsBatchBuilder("TrevorHollands_Application_Report.pdf")
    builder.add_candidate(
        name="Trevor Hollands",
        applicant_id="A-30003",
        email="trevor.hollands@example.com",
        phone="(555) 663-8820",
        job_title="Senior Project Manager",
        report_page_count=10,
        resume_page_count=6,
        cover_letter_page_count=3,
    )
    return builder.build()


def sofia_brennan_batch() -> AtsBatch:
    """Report 1-3, separator p4, resume 5-7, separator p8, cover letter 9-10."""
    builder = AtsBatchBuilder("SofiaBrennan_Application_Report.pdf")
    builder.add_candidate(
        name="Sofia Brennan",
        applicant_id="A-30004",
        email="sofia.brennan@example.com",
        phone="(555) 774-2036",
        job_title="Marketing Coordinator",
        report_page_count=3,
        resume_page_count=3,
        cover_letter_page_count=2,
    )
    return builder.build()


NAMED_BATCHES: tuple = (
    marcus_delgado_batch,
    nathan_whitfield_batch,
    trevor_hollands_batch,
    sofia_brennan_batch,
)


# --------------------------------------------------------------------------
# Multi-applicant batch (~80 pages), for the concatenated-file test
# --------------------------------------------------------------------------

#: (report pages, resume pages, cover-letter pages) per applicant, cycled.
_SECTION_VARIANTS: tuple[tuple[int, int, int], ...] = (
    (3, 1, 1),
    (2, 2, 1),
    (4, 1, 2),
    (2, 1, 1),
    (5, 2, 1),
    (3, 2, 2),
)


def build_multi_applicant_batch(
    *,
    filename: str = "MultiApplicant_2026.pdf",
    applicants: tuple[Applicant, ...] = APPLICANTS,
    target_pages: int = 80,
) -> AtsBatch:
    """Many ATS applicant packets concatenated into one source PDF.

    Mirrors the real client workflow: one export holding many applicants back
    to back, each with its own report -> Resume -> Cover Letters structure.
    Every section length is fixed (not randomised), so the exact page ranges
    are reproducible and assertable.
    """
    builder = AtsBatchBuilder(filename)
    for index, applicant in enumerate(applicants):
        if builder.pages and len(builder.pages) >= target_pages:
            break
        report_n, resume_n, cover_n = _SECTION_VARIANTS[index % len(_SECTION_VARIANTS)]
        builder.add_candidate(
            name=applicant.name,
            applicant_id=applicant.applicant_id,
            email=applicant.email,
            phone=applicant.phone,
            job_title=applicant.job_title,
            report_page_count=report_n,
            resume_page_count=resume_n,
            cover_letter_page_count=cover_n,
            requisition_id=f"REQ-2026-{1000 + index}",
        )
    return builder.build()


__all__ = [
    "ats_report_pages",
    "AtsSection",
    "AtsCandidateResult",
    "AtsBatch",
    "AtsBatchBuilder",
    "marcus_delgado_batch",
    "nathan_whitfield_batch",
    "trevor_hollands_batch",
    "sofia_brennan_batch",
    "NAMED_BATCHES",
    "build_multi_applicant_batch",
]
