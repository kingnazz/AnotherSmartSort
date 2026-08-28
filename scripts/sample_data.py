"""Synthetic recruiting documents used by the tests and for manual QA.

Everything here is invented. No real applicant data is ever committed to this
repository. The same definitions drive the automated tests and
``scripts/generate_samples.py`` so what developers eyeball is exactly what CI
asserts on.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

# --------------------------------------------------------------------------
# Page geometry / rendering
# --------------------------------------------------------------------------

_PAGE_WIDTH, _PAGE_HEIGHT = 612.0, 792.0  # US Letter, 72 dpi units
_MARGIN_X, _MARGIN_TOP = 62.0, 72.0
_LINE_HEIGHT = 13.0
_FONT_SIZE = 9.5
_FONT = "helv"
_WRAP_WIDTH = 104
_SCAN_DPI = 150


@dataclass
class SamplePage:
    """One page of a synthetic document."""

    lines: list[str]
    #: When True the page is rendered as a flat image with no text layer,
    #: simulating a scan so the OCR path can be exercised.
    scanned: bool = False


@dataclass
class SampleDocument:
    """A synthetic source PDF plus the grouping the pipeline should recover."""

    filename: str
    description: str
    pages: list[SamplePage]
    #: Expected ``(document_type, first_page, last_page)`` using 1-based pages.
    expected_groups: list[tuple[str, int, int]] = field(default_factory=list)
    expected_candidates: list[str] = field(default_factory=list)
    #: Pages (1-based) that must be surfaced to the user as needing review.
    expected_review_pages: list[int] = field(default_factory=list)
    #: When False the sample's groups are not asserted exhaustively.
    groups_are_exhaustive: bool = True

    @property
    def page_count(self) -> int:
        return len(self.pages)


def _wrap(text: str, width: int = _WRAP_WIDTH) -> list[str]:
    """Wrap a paragraph into lines long enough to read as prose."""
    return textwrap.wrap(text, width=width) or [""]


def paragraph(text: str) -> list[str]:
    return _wrap(" ".join(text.split()))


def bullet(text: str) -> str:
    return f"• {text}"


def _draw_lines(page: pymupdf.Page, lines: list[str]) -> None:
    y = _MARGIN_TOP
    for line in lines:
        if y > _PAGE_HEIGHT - 54:
            break
        if line.strip():
            page.insert_text(
                (_MARGIN_X, y),
                line,
                fontsize=_FONT_SIZE,
                fontname=_FONT,
                encoding=pymupdf.TEXT_ENCODING_LATIN,
            )
        y += _LINE_HEIGHT


def build_pdf(document: SampleDocument, output_path: str | Path) -> Path:
    """Write a :class:`SampleDocument` to disk as a real PDF."""
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    out = pymupdf.open()
    try:
        for sample_page in document.pages:
            if sample_page.scanned:
                _append_scanned_page(out, sample_page.lines)
            else:
                page = out.new_page(width=_PAGE_WIDTH, height=_PAGE_HEIGHT)
                _draw_lines(page, sample_page.lines)
        out.save(str(destination), garbage=3, deflate=True)
    finally:
        out.close()
    return destination


def _append_scanned_page(out: pymupdf.Document, lines: list[str]) -> None:
    """Render text to an image and place it as a flat page (no text layer)."""
    scratch = pymupdf.open()
    try:
        temp_page = scratch.new_page(width=_PAGE_WIDTH, height=_PAGE_HEIGHT)
        _draw_lines(temp_page, lines)
        pixmap = temp_page.get_pixmap(dpi=_SCAN_DPI, colorspace=pymupdf.csGRAY, alpha=False)
    finally:
        scratch.close()

    page = out.new_page(width=_PAGE_WIDTH, height=_PAGE_HEIGHT)
    page.insert_image(pymupdf.Rect(0, 0, _PAGE_WIDTH, _PAGE_HEIGHT), pixmap=pixmap)


# --------------------------------------------------------------------------
# Content building blocks
# --------------------------------------------------------------------------

def application_report_pages(
    *,
    name: str = "Benjamin Perez",
    applicant_id: str = "A-10482",
    email: str = "benjamin.perez@example.com",
    phone: str = "(555) 214-8890",
    job_title: str = "Senior Operations Analyst",
    total: int = 4,
) -> list[SamplePage]:
    """A four-page applicant tracking system export."""
    page1 = [
        "CANDIDATE APPLICATION REPORT",
        "Rivermark Logistics - Talent Acquisition",
        f"Generated: March 14, 2024                                        Page 1 of {total}",
        "",
        "APPLICANT INFORMATION",
        f"Name:                    {name}",
        f"Applicant ID:            {applicant_id}",
        f"Email:                   {email}",
        f"Phone:                   {phone}",
        "Address:                 4127 Windward Lane, Austin, TX 78704",
        "Work Authorization:      Authorized to work in the United States",
        "Sponsorship Required:    No",
        "",
        "POSITION DETAILS",
        f"Position Applied For:    {job_title}",
        "Requisition ID:          REQ-2024-3391",
        "Department:              Business Operations",
        "Location:                Austin, TX",
        "Employment Type:         Full Time",
        "Application Date:        March 12, 2024",
        "Source:                  Company Career Site",
        "Status:                  In Review",
        "Recruiter:               Danielle Ortiz",
        "",
        "SUBMISSION DETAILS",
        "Submitted On:            March 12, 2024 09:14 CDT",
        "Submission Method:       Online Application",
        "Resume Attached:         Yes",
        "Cover Letter Attached:   Yes",
    ]

    page2 = [
        f"CANDIDATE APPLICATION REPORT - {name}",
        f"Applicant ID: {applicant_id}                                     Page 2 of {total}",
        "",
        "APPLICATION QUESTIONS",
        "",
        "Question 1: Are you legally authorized to work in the United States?",
        "Answer: Yes",
        "",
        "Question 2: Will you now or in the future require sponsorship for employment?",
        "Answer: No",
        "",
        "Question 3: How many years of operations analysis experience do you have?",
        "Answer: 9 years",
        "",
        "Question 4: Are you willing to travel up to 20% of the time?",
        "Answer: Yes",
        "",
        "Question 5: What is your desired compensation range?",
        "Answer: Commensurate with experience",
        "",
        "SCREENING QUESTIONS",
        "Minimum Qualifications Met:   Yes",
        "Preferred Qualifications Met: Yes",
        "Screening Score:              92 / 100",
    ]

    page3 = [
        f"CANDIDATE APPLICATION REPORT - {name}",
        f"Applicant ID: {applicant_id}                                     Page 3 of {total}",
        "",
        "VOLUNTARY SELF-IDENTIFICATION",
        "",
        "The following information is requested for equal employment opportunity",
        "reporting. Providing this information is voluntary.",
        "",
        "Gender Identity:         Declined to self-identify",
        "Ethnicity:               Declined to self-identify",
        "Veteran Status:          I am not a protected veteran",
        "Disability Status:       Declined to self-identify",
        "",
        "EEO CATEGORY",
        "Job Category:            Professionals",
        "EEO-1 Classification:    2 - Professionals",
        "",
        "This employer is an Equal Employment Opportunity employer. All qualified",
        "applicants receive consideration without regard to protected status.",
    ]

    page4 = [
        f"CANDIDATE APPLICATION REPORT - {name}",
        f"Applicant ID: {applicant_id}                                     Page 4 of {total}",
        "",
        "APPLICATION HISTORY",
        "",
        "Date              Stage                       Disposition",
        "March 12, 2024    Application Received        Advanced",
        "March 13, 2024    Recruiter Screen           Advanced",
        "March 14, 2024    Hiring Manager Review      In Progress",
        "",
        "ATTACHMENTS",
        "Attachment 1:            Resume (3 pages)",
        "Attachment 2:            Cover Letter (1 page)",
        "Attachment 3:            Professional References (2 pages)",
        "",
        "INTERNAL NOTES",
        "Recruiter Notes:         Strong operations background. Schedule panel interview.",
    ]

    pages = [page1, page2, page3, page4][:total]
    return [SamplePage(lines=lines) for lines in pages]


def resume_pages(
    *,
    name: str = "Benjamin Perez",
    email: str = "benjamin.perez@example.com",
    phone: str = "(555) 214-8890",
    linkedin: str = "linkedin.com/in/benjaminperez",
    total: int = 3,
) -> list[SamplePage]:
    """A three-page resume with running headers and page numbering."""
    page1 = [
        name,
        f"Austin, TX 78704  |  {phone}  |  {email}",
        linkedin,
        "",
        "PROFESSIONAL SUMMARY",
        *paragraph(
            "Operations analyst with nine years of experience turning messy logistics data "
            "into forecasts leadership actually trusts. Specializes in capacity planning, "
            "vendor scorecards, and rebuilding reporting that people had quietly stopped "
            "reading."
        ),
        "",
        "PROFESSIONAL EXPERIENCE",
        "",
        "Senior Operations Analyst",
        "Rivermark Logistics - Austin, TX",
        "June 2020 - Present",
        bullet("Rebuilt the weekly capacity forecast, cutting variance from 18% to 6%."),
        bullet("Consolidated 14 regional spreadsheets into one governed reporting model."),
        bullet("Led vendor scorecard reviews covering $42M of annual freight spend."),
        bullet("Mentored four junior analysts through the analytics onboarding program."),
        "",
        "Operations Analyst",
        "Halden Freight Group - San Antonio, TX",
        "March 2017 - May 2020",
        bullet("Automated daily exception reporting, saving roughly 11 hours per week."),
        bullet("Partnered with warehouse leads to redesign the inbound receiving workflow."),
        bullet("Built the carrier on-time dashboard adopted across three distribution centers."),
        "",
        f"{name} - Page 1 of {total}",
    ]

    page2 = [
        name,
        f"Page 2 of {total}",
        "",
        "PROFESSIONAL EXPERIENCE (CONTINUED)",
        "",
        "Logistics Coordinator",
        "Halden Freight Group - San Antonio, TX",
        "January 2015 - February 2017",
        bullet("Coordinated inbound scheduling for 60+ carriers across two facilities."),
        bullet("Reduced detention charges 23% by renegotiating appointment windows."),
        bullet("Standardized the damage claim process adopted company wide in 2016."),
        "",
        "Operations Associate",
        "Cedar Point Distribution - Round Rock, TX",
        "August 2013 - December 2014",
        bullet("Maintained cycle count accuracy above 99.2% across a 240,000 sq ft facility."),
        bullet("Trained seasonal staff on safety procedures and inventory systems."),
        "",
        "SELECTED PROJECTS",
        "",
        "Capacity Forecast Rebuild - 2022",
        *paragraph(
            "Replaced a manual spreadsheet forecast with a governed model sourced directly "
            "from the transportation management system, reviewed weekly with regional "
            "operations directors."
        ),
        "",
        f"{name} - Page 2 of {total}",
    ]

    page3 = [
        name,
        f"Page 3 of {total}",
        "",
        "EDUCATION",
        "",
        "Bachelor of Science, Supply Chain Management",
        "The University of Texas at Austin - Austin, TX",
        "Graduated May 2013",
        "",
        "CERTIFICATIONS",
        bullet("APICS Certified in Planning and Inventory Management (CPIM), 2019"),
        bullet("Lean Six Sigma Green Belt, 2018"),
        bullet("Advanced SQL for Analysts, 2021"),
        "",
        "TECHNICAL SKILLS",
        "Analytics:      SQL, Python (pandas), Power BI, Tableau",
        "Systems:        SAP ERP, Oracle TMS, Manhattan WMS",
        "Productivity:   Advanced Excel, Smartsheet, Confluence",
        "",
        "ACHIEVEMENTS",
        bullet("Operations Excellence Award, Rivermark Logistics, 2022"),
        bullet("Presented capacity planning case study at the 2023 regional supply chain summit"),
        "",
        "LANGUAGES",
        "English (native), Spanish (professional working proficiency)",
        "",
        f"{name} - Page 3 of {total}",
    ]

    pages = [page1, page2, page3][:total]
    return [SamplePage(lines=lines) for lines in pages]


def cover_letter_pages(
    *,
    name: str = "Benjamin Perez",
    job_title: str = "Senior Operations Analyst",
    total: int = 1,
) -> list[SamplePage]:
    """A one- or two-page cover letter."""
    opening = [
        name,
        "4127 Windward Lane",
        "Austin, TX 78704",
        "",
        "March 12, 2024",
        "",
        "Danielle Ortiz",
        "Talent Acquisition",
        "Rivermark Logistics",
        "900 Congress Avenue",
        "Austin, TX 78701",
        "",
        "Dear Ms. Ortiz,",
        "",
        *paragraph(
            f"I am writing to apply for the {job_title} position at Rivermark Logistics. "
            "I have spent the last nine years in operations analysis, and the work described "
            "in your posting lines up closely with what I have been doing since 2020."
        ),
        "",
        *paragraph(
            "In my current role I rebuilt the weekly capacity forecast that regional directors "
            "rely on, cutting forecast variance from eighteen percent to six. That work "
            "required more than modeling. It meant sitting with dispatchers who had good "
            "reasons to distrust the previous numbers and rebuilding the inputs with them."
        ),
    ]

    if total == 1:
        return [
            SamplePage(
                lines=[
                    *opening,
                    "",
                    *paragraph(
                        "I would welcome the chance to discuss how that experience can support "
                        "your capacity planning goals. Thank you for your consideration."
                    ),
                    "",
                    "Sincerely,",
                    "",
                    name,
                ]
            )
        ]

    page1 = [
        *opening,
        "",
        *paragraph(
            "Your posting also mentions vendor performance management. At Rivermark I own the "
            "quarterly vendor scorecard review covering roughly forty-two million dollars of "
            "annual freight spend, which has become the forum where service issues actually "
            "get resolved rather than escalated."
        ),
        "",
        f"{name} - Page 1 of {total}",
    ]

    page2 = [
        f"{name} - Cover Letter",
        f"Page 2 of {total}",
        "",
        *paragraph(
            "Beyond the analytics, I care about the reporting being usable. Early in my career "
            "I built a dashboard that was technically correct and entirely ignored. The lesson "
            "stuck: I now start by asking which decision a report is meant to support, and I "
            "retire reports that no longer support one."
        ),
        "",
        *paragraph(
            "I am drawn to Rivermark because your operations team publishes its service metrics "
            "internally rather than burying them. That kind of transparency makes analysis "
            "worth doing."
        ),
        "",
        *paragraph(
            "I would welcome the chance to discuss the role further and am happy to walk "
            "through the capacity forecast rebuild in detail. Thank you for your time and "
            "consideration."
        ),
        "",
        "Sincerely,",
        "",
        name,
    ]

    return [SamplePage(lines=page1), SamplePage(lines=page2)]


def references_pages(*, name: str = "Benjamin Perez", total: int = 2) -> list[SamplePage]:
    """A two-page professional reference sheet."""
    page1 = [
        "PROFESSIONAL REFERENCES",
        name,
        "Austin, TX 78704",
        "",
        "Reference 1",
        "Name:            Danielle Ortiz",
        "Title:           Director of Operations",
        "Organization:    Rivermark Logistics",
        "Relationship:    Direct supervisor, 2020 - present",
        "Years Known:     4",
        "Phone:           (555) 771-2214",
        "Email:           dortiz@example.com",
        "",
        "Reference 2",
        "Name:            Marcus Whitfield",
        "Title:           Senior Manager, Transportation",
        "Organization:    Halden Freight Group",
        "Relationship:    Former manager, 2017 - 2020",
        "Years Known:     7",
        "Phone:           (555) 408-6621",
        "Email:           mwhitfield@example.com",
        "",
        f"References - {name} - Page 1 of {total}",
    ]

    page2 = [
        f"PROFESSIONAL REFERENCES - {name}",
        f"Page 2 of {total}",
        "",
        "Reference 3",
        "Name:            Priya Raman",
        "Title:           Continuous Improvement Lead",
        "Organization:    Cedar Point Distribution",
        "Relationship:    Cross-functional project partner, 2014 - 2016",
        "Years Known:     9",
        "Phone:           (555) 233-0147",
        "Email:           praman@example.com",
        "",
        "Reference 4",
        "Name:            Alan Vestergaard",
        "Title:           Warehouse Operations Manager",
        "Organization:    Cedar Point Distribution",
        "Relationship:    Former colleague, 2013 - 2014",
        "Years Known:     10",
        "Phone:           (555) 902-4418",
        "Email:           avestergaard@example.com",
        "",
        "May we contact these references?  Yes, please contact after an initial interview.",
        "",
        f"References - {name} - Page 2 of {total}",
    ]

    pages = [page1, page2][:total]
    return [SamplePage(lines=lines) for lines in pages]


def transcript_pages(*, name: str = "Benjamin Perez", total: int = 2) -> list[SamplePage]:
    """A two-page academic transcript."""
    page1 = [
        "THE UNIVERSITY OF TEXAS AT AUSTIN",
        "Office of the Registrar",
        "OFFICIAL ACADEMIC TRANSCRIPT",
        "",
        f"Student Name:    {name}",
        "Student ID:      UT-2009-44817",
        "Program:         Bachelor of Science, Supply Chain Management",
        "Date Issued:     June 2, 2013",
        "",
        "FALL 2011 SEMESTER",
        "Course      Course Title                          Credits   Grade",
        "SCM 301     Introduction to Supply Chains            3.0       A",
        "STA 309     Statistics for Business                  3.0       B",
        "ECO 304     Principles of Economics                  3.0       A",
        "MAN 320     Foundations of Management                3.0       B",
        "Term GPA: 3.65                                   Credits Earned: 12.0",
        "",
        "SPRING 2012 SEMESTER",
        "Course      Course Title                          Credits   Grade",
        "SCM 320     Operations Management                    3.0       A",
        "SCM 335     Transportation and Logistics             3.0       A",
        "FIN 357     Business Finance                         3.0       B",
        "MKT 337     Principles of Marketing                  3.0       B",
        "Term GPA: 3.50                                   Credits Earned: 12.0",
        "",
        f"Page 1 of {total}",
    ]

    page2 = [
        "THE UNIVERSITY OF TEXAS AT AUSTIN - OFFICIAL ACADEMIC TRANSCRIPT",
        f"{name} - Student ID: UT-2009-44817",
        f"Page 2 of {total}",
        "",
        "FALL 2012 SEMESTER",
        "Course      Course Title                          Credits   Grade",
        "SCM 350     Inventory and Warehousing                3.0       A",
        "SCM 361     Procurement Strategy                     3.0       A",
        "ACC 311     Financial Accounting                     3.0       B",
        "Term GPA: 3.67                                   Credits Earned: 9.0",
        "",
        "SPRING 2013 SEMESTER",
        "Course      Course Title                          Credits   Grade",
        "SCM 375     Supply Chain Analytics                   3.0       A",
        "SCM 380     Global Sourcing Capstone                 3.0       A",
        "BUS 340     Business Communication                   3.0       B",
        "Term GPA: 3.67                                   Credits Earned: 9.0",
        "",
        "DEGREE AWARDED",
        "Bachelor of Science, Supply Chain Management - May 2013",
        "Cumulative GPA: 3.62          Total Credits Earned: 122.0",
        "Academic Standing: Good Standing. Dean's List: Fall 2012, Spring 2013.",
        "",
        "End of official academic record.",
    ]

    pages = [page1, page2][:total]
    return [SamplePage(lines=lines) for lines in pages]


def ambiguous_page() -> SamplePage:
    """A page with genuinely mixed signals that should land in review."""
    return SamplePage(
        lines=[
            "Additional Information",
            "",
            *paragraph(
                "The attached materials were provided in response to the request from the "
                "committee. Items are arranged in the order they were received and have not "
                "been altered."
            ),
            "",
            "Prepared March 2024",
            "Reference: 2024-0311",
        ]
    )


def separator_page(label: str) -> SamplePage:
    """A title/divider page carrying only a document label."""
    return SamplePage(lines=["", "", "", label.upper()])


# --------------------------------------------------------------------------
# The sample corpus
# --------------------------------------------------------------------------

def sample_a() -> SampleDocument:
    """10 pages: application report, resume, cover letter, references."""
    return SampleDocument(
        filename="SampleA_BenjaminPerezApplication.pdf",
        description="Full applicant packet: 4-page application report, 3-page resume, "
        "1-page cover letter, 2-page references.",
        pages=[
            *application_report_pages(total=4),
            *resume_pages(total=3),
            *cover_letter_pages(total=1),
            *references_pages(total=2),
        ],
        expected_groups=[
            ("Application Report", 1, 4),
            ("Resume", 5, 7),
            ("Cover Letter", 8, 8),
            ("References", 9, 10),
        ],
        expected_candidates=["Benjamin Perez"],
    )


def sample_b() -> SampleDocument:
    """A standalone 3-page resume that must stay one document."""
    return SampleDocument(
        filename="SampleB_Resume_ThreePages.pdf",
        description="Resume only, three pages, must remain a single document.",
        pages=resume_pages(total=3),
        expected_groups=[("Resume", 1, 3)],
        expected_candidates=["Benjamin Perez"],
    )


def sample_c() -> SampleDocument:
    """A 2-page cover letter that must stay one document."""
    return SampleDocument(
        filename="SampleC_CoverLetter_TwoPages.pdf",
        description="Cover letter only, two pages, must remain a single document.",
        pages=cover_letter_pages(total=2),
        expected_groups=[("Cover Letter", 1, 2)],
        expected_candidates=["Benjamin Perez"],
    )


def sample_d() -> SampleDocument:
    """A scanned-style document with no text layer, requiring OCR."""
    scanned = [SamplePage(lines=page.lines, scanned=True) for page in resume_pages(total=2)]
    return SampleDocument(
        filename="SampleD_Scanned_NoTextLayer.pdf",
        description="Image-only pages with no text layer; exercises the OCR path.",
        pages=scanned,
        expected_groups=[],
        expected_candidates=[],
    )


def sample_e() -> SampleDocument:
    """A packet containing an ambiguous page that should be flagged for review."""
    return SampleDocument(
        filename="SampleE_AmbiguousPage.pdf",
        description="Resume followed by an ambiguous page that should require review.",
        pages=[*resume_pages(total=2), ambiguous_page()],
        # The trailing page carries no usable signals. Inventing a document for
        # it would be worse than keeping it with the resume, so the pipeline
        # keeps it attached and flags the group for review instead.
        expected_groups=[("Resume", 1, 3)],
        expected_candidates=["Benjamin Perez"],
        expected_review_pages=[3],
    )


def sample_f() -> SampleDocument:
    """A packet using separator/title pages between documents."""
    return SampleDocument(
        filename="SampleF_SeparatorPages.pdf",
        description="Separator title pages introducing a resume and a cover letter.",
        pages=[
            separator_page("Resume"),
            *resume_pages(total=2),
            separator_page("Cover Letter"),
            *cover_letter_pages(total=1),
        ],
        expected_groups=[("Resume", 1, 3), ("Cover Letter", 4, 5)],
        expected_candidates=["Benjamin Perez"],
    )


def sample_g() -> SampleDocument:
    """Two different applicants combined into one source PDF."""
    return SampleDocument(
        filename="SampleG_TwoCandidates.pdf",
        description="Two applicants in one PDF; identity change must split the documents.",
        pages=[
            *resume_pages(total=2),
            *resume_pages(
                name="Jane Smith",
                email="jane.smith@example.com",
                phone="(555) 640-1187",
                linkedin="linkedin.com/in/janesmithops",
                total=2,
            ),
        ],
        expected_groups=[("Resume", 1, 2), ("Resume", 3, 4)],
        expected_candidates=["Benjamin Perez", "Jane Smith"],
    )


def sample_h() -> SampleDocument:
    """An academic packet: transcript plus references."""
    return SampleDocument(
        filename="SampleH_TranscriptAndReferences.pdf",
        description="Two-page transcript followed by a two-page reference sheet.",
        pages=[*transcript_pages(total=2), *references_pages(total=2)],
        expected_groups=[("Transcript", 1, 2), ("References", 3, 4)],
        expected_candidates=["Benjamin Perez"],
    )


ALL_SAMPLES: tuple = (
    sample_a,
    sample_b,
    sample_c,
    sample_d,
    sample_e,
    sample_f,
    sample_g,
    sample_h,
)


def build_all(output_dir: str | Path) -> list[Path]:
    """Generate every sample PDF into ``output_dir``."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for factory in ALL_SAMPLES:
        document = factory()
        written.append(build_pdf(document, directory / document.filename))
    return written


__all__ = [
    "SamplePage",
    "SampleDocument",
    "build_pdf",
    "build_all",
    "ALL_SAMPLES",
    "application_report_pages",
    "resume_pages",
    "cover_letter_pages",
    "references_pages",
    "transcript_pages",
    "ambiguous_page",
    "separator_page",
    "sample_a",
    "sample_b",
    "sample_c",
    "sample_d",
    "sample_e",
    "sample_f",
    "sample_g",
    "sample_h",
    "paragraph",
    "bullet",
]
