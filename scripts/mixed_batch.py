"""Synthetic multi-applicant source PDFs.

The real client workflow is one large PDF holding a mixed run of documents for
many different applicants. This module builds such files, together with the
ground truth for what should be recovered from them, so the packet
reconstruction path can be tested against the shape of the actual problem
rather than against tidy one-candidate files.

Nothing here touches real applicant data: every name, address and email is
invented, and the PDFs are generated on demand rather than committed.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

from app.profiles.recruiting import (
    APPLICATION_REPORT,
    COVER_LETTER,
    REFERENCES,
    RESUME,
    TRANSCRIPT,
)
from scripts.sample_data import (
    SampleDocument,
    SamplePage,
    ambiguous_page,
    application_report_pages,
    build_pdf,
    cover_letter_pages,
    references_pages,
    resume_pages,
    separator_page,
    transcript_pages,
)


@dataclass
class ExpectedDocument:
    """One logical document the pipeline is expected to recover."""

    document_type: str
    #: 1-based, inclusive.
    first_page: int
    last_page: int

    @property
    def pages(self) -> list[int]:
        return list(range(self.first_page, self.last_page + 1))


@dataclass
class ExpectedCandidate:
    """One applicant packet the pipeline is expected to reconstruct."""

    name: str
    documents: list[ExpectedDocument] = field(default_factory=list)

    @property
    def pages(self) -> list[int]:
        pages: list[int] = []
        for document in self.documents:
            pages.extend(document.pages)
        return sorted(pages)


@dataclass
class MixedBatch:
    """A synthetic multi-applicant PDF and everything it should produce."""

    filename: str
    pages: list[SamplePage]
    candidates: list[ExpectedCandidate] = field(default_factory=list)
    #: Pages carrying no identity that the system must attribute by context.
    anonymous_pages: list[int] = field(default_factory=list)
    #: Separator pages, which are not part of any expected document.
    separator_pages: list[int] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def document_count(self) -> int:
        return sum(len(candidate.documents) for candidate in self.candidates)

    def as_sample_document(self) -> SampleDocument:
        return SampleDocument(
            filename=self.filename,
            description=f"{self.page_count}-page mixed applicant batch",
            pages=self.pages,
            expected_candidates=[c.name for c in self.candidates],
        )

    def write(self, output_dir: str | Path) -> Path:
        return build_pdf(self.as_sample_document(), Path(output_dir) / self.filename)

    def ground_truth(self) -> dict:
        """Ground truth in the QA harness's format."""
        return {
            "candidates": [
                {
                    "name": candidate.name,
                    "documents": [
                        {"type": document.document_type, "pages": document.pages}
                        for document in candidate.documents
                    ],
                }
                for candidate in self.candidates
            ]
        }


# --------------------------------------------------------------------------
# Applicant identities
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Applicant:
    """The details that appear on one invented person's documents."""

    name: str
    applicant_id: str
    email: str
    phone: str
    job_title: str = "Senior Operations Analyst"
    linkedin: str = ""

    def with_name(self, name: str) -> "Applicant":
        return Applicant(
            name=name,
            applicant_id=self.applicant_id,
            email=self.email,
            phone=self.phone,
            job_title=self.job_title,
            linkedin=self.linkedin,
        )


#: Deliberately varied: different surname lengths, some hyphenated, some with
#: middle initials, so name normalisation is exercised by ordinary cases.
APPLICANTS: tuple[Applicant, ...] = (
    Applicant("Jane Smith", "A-20001", "jane.smith@example.com", "(206) 555-1234",
              "Operations Coordinator", "linkedin.com/in/janesmith"),
    Applicant("Robert Jones", "A-20002", "robert.jones@example.com", "(415) 555-2288",
              "Logistics Planner"),
    Applicant("Sarah Lee", "A-20003", "sarah.lee@example.com", "(312) 555-9911",
              "Supply Chain Analyst"),
    Applicant("Mark Davis", "A-20004", "mark.davis@example.com", "(646) 555-4402",
              "Warehouse Supervisor"),
    Applicant("Priya Raman", "A-20005", "priya.raman@example.com", "(503) 555-7710",
              "Inventory Analyst"),
    Applicant("Daniel O'Connor", "A-20006", "daniel.oconnor@example.com", "(617) 555-3345",
              "Transportation Manager"),
    Applicant("Amara Okafor", "A-20007", "amara.okafor@example.com", "(404) 555-8820",
              "Procurement Specialist"),
    Applicant("Thomas Nguyen", "A-20008", "thomas.nguyen@example.com", "(714) 555-6677",
              "Operations Analyst"),
    Applicant("Elena Vasquez", "A-20009", "elena.vasquez@example.com", "(305) 555-1120",
              "Distribution Lead"),
    Applicant("Michael Brennan", "A-20010", "michael.brennan@example.com", "(773) 555-4491",
              "Fleet Coordinator"),
    Applicant("Aisha Karim", "A-20011", "aisha.karim@example.com", "(206) 555-3312",
              "Demand Planner"),
    Applicant("Jonathan Pike", "A-20012", "jonathan.pike@example.com", "(919) 555-7788",
              "Operations Manager"),
    Applicant("Rebecca Stone", "A-20013", "rebecca.stone@example.com", "(602) 555-2231",
              "Logistics Analyst"),
    Applicant("Victor Alvarez", "A-20014", "victor.alvarez@example.com", "(858) 555-5540",
              "Freight Coordinator"),
    Applicant("Hannah Whitfield", "A-20015", "hannah.whitfield@example.com", "(781) 555-9903",
              "Supply Planner"),
    Applicant("Omar Haddad", "A-20016", "omar.haddad@example.com", "(469) 555-6614",
              "Network Analyst"),
    Applicant("Grace Lindqvist", "A-20017", "grace.lindqvist@example.com", "(360) 555-2277",
              "Operations Specialist"),
    Applicant("Nathan Cole", "A-20018", "nathan.cole@example.com", "(425) 555-8845",
              "Routing Analyst"),
)


def _application_report(applicant: Applicant, total: int) -> list[SamplePage]:
    return application_report_pages(
        name=applicant.name,
        applicant_id=applicant.applicant_id,
        email=applicant.email,
        phone=applicant.phone,
        job_title=applicant.job_title,
        total=total,
    )


def _resume(applicant: Applicant, total: int) -> list[SamplePage]:
    return resume_pages(
        name=applicant.name,
        email=applicant.email,
        phone=applicant.phone,
        linkedin=applicant.linkedin or f"linkedin.com/in/{applicant.name.split()[0].lower()}",
        total=total,
    )


def _cover_letter(applicant: Applicant, total: int = 1) -> list[SamplePage]:
    return cover_letter_pages(name=applicant.name, job_title=applicant.job_title, total=total)


def _references(applicant: Applicant, total: int = 2) -> list[SamplePage]:
    return references_pages(name=applicant.name, total=total)


def _transcript(applicant: Applicant, total: int = 2) -> list[SamplePage]:
    return transcript_pages(name=applicant.name, total=total)


_BUILDERS = {
    APPLICATION_REPORT: _application_report,
    RESUME: _resume,
    COVER_LETTER: _cover_letter,
    REFERENCES: _references,
    TRANSCRIPT: _transcript,
}


class MixedBatchBuilder:
    """Assembles a mixed batch page by page, tracking the expected structure."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.pages: list[SamplePage] = []
        self.candidates: list[ExpectedCandidate] = []
        self.anonymous_pages: list[int] = []
        self.separator_pages: list[int] = []
        self._current: ExpectedCandidate | None = None

    # -- structure -----------------------------------------------------
    def start_candidate(self, name: str) -> ExpectedCandidate:
        candidate = ExpectedCandidate(name=name)
        self.candidates.append(candidate)
        self._current = candidate
        return candidate

    def add_document(
        self,
        document_type: str,
        pages: list[SamplePage],
        *,
        candidate: ExpectedCandidate | None = None,
    ) -> ExpectedDocument:
        owner = candidate or self._current
        if owner is None:
            raise ValueError("A document needs a candidate; call start_candidate first.")
        first = len(self.pages) + 1
        self.pages.extend(pages)
        document = ExpectedDocument(document_type, first, len(self.pages))
        owner.documents.append(document)
        return document

    def add_separator(self, label: str) -> None:
        self.pages.append(separator_page(label))
        self.separator_pages.append(len(self.pages))

    def add_ambiguous(self) -> None:
        """A page with genuinely mixed signals, owned by nobody."""
        self.pages.append(ambiguous_page())
        self.anonymous_pages.append(len(self.pages))

    def build(self) -> MixedBatch:
        return MixedBatch(
            filename=self.filename,
            pages=self.pages,
            candidates=self.candidates,
            anonymous_pages=self.anonymous_pages,
            separator_pages=self.separator_pages,
        )


def build_mixed_batch(
    *,
    filename: str = "Applicants_2026.pdf",
    applicants: tuple[Applicant, ...] = APPLICANTS,
    seed: int = 20260814,
    target_pages: int = 85,
) -> MixedBatch:
    """Build a realistic mixed applicant batch of roughly ``target_pages`` pages.

    The composition mirrors what the client actually receives: most applicants
    have an application report and a resume, many have a cover letter, some have
    references or a transcript, a few are missing the report entirely, resumes
    run from one to three pages, and a handful of separator and ambiguous pages
    are scattered through. The layout is randomised but seeded, so the file is
    identical on every run and a failure can be reproduced exactly.
    """
    random.seed(seed)
    builder = MixedBatchBuilder(filename)

    for index, applicant in enumerate(applicants):
        if len(builder.pages) >= target_pages:
            break
        builder.start_candidate(applicant.name)

        # Most applicants lead with an ATS report; some packets arrive without one.
        if index % 5 != 3:
            builder.add_document(
                APPLICATION_REPORT, _application_report(applicant, random.choice((3, 3, 4)))
            )

        resume_length = random.choice((1, 2, 2, 3))
        builder.add_document(RESUME, _resume(applicant, resume_length))

        if index % 3 != 2:
            builder.add_document(COVER_LETTER, _cover_letter(applicant))

        if index % 6 == 4:
            builder.add_document(REFERENCES, _references(applicant))
        if index % 7 == 5:
            builder.add_document(TRANSCRIPT, _transcript(applicant))

        if index % 8 == 6:
            builder.add_separator("RESUME")

    return builder.build()


def build_ambiguity_batch(filename: str = "Ambiguous_2026.pdf") -> MixedBatch:
    """The specification's worked examples, as one file.

    Jane's cover letter carries no name and must be inferred from context;
    Sarah's resume follows Jane's documents with no application report in
    between and must *not* be swallowed into Jane's packet.
    """
    builder = MixedBatchBuilder(filename)
    jane, robert, sarah = APPLICANTS[0], APPLICANTS[1], APPLICANTS[2]

    jane_packet = builder.start_candidate(jane.name)
    builder.add_document(APPLICATION_REPORT, _application_report(jane, 3))
    builder.add_document(RESUME, _resume(jane, 2))
    # A cover letter with no identifying details at all.
    builder.add_document(COVER_LETTER, _anonymous_cover_letter(), candidate=jane_packet)

    builder.start_candidate(sarah.name)
    builder.add_document(RESUME, _resume(sarah, 3))
    builder.add_document(COVER_LETTER, _cover_letter(sarah))

    builder.start_candidate(robert.name)
    builder.add_document(APPLICATION_REPORT, _application_report(robert, 3))
    builder.add_document(RESUME, _resume(robert, 2))

    return builder.build()


def _anonymous_cover_letter() -> list[SamplePage]:
    """A cover letter that never names the writer or the reader."""
    from scripts.sample_data import paragraph

    return [
        SamplePage(
            lines=[
                "March 12, 2024",
                "",
                "Dear Hiring Committee,",
                "",
                *paragraph(
                    "I am writing to express my interest in the operations role described in "
                    "your recent posting. The position aligns closely with the work I have "
                    "done over the past several years, and I would welcome the chance to "
                    "discuss it further."
                ),
                "",
                *paragraph(
                    "In my current role I rebuilt a weekly capacity forecast that leadership "
                    "had stopped trusting, and I reduced variance from eighteen percent to "
                    "six. I care a great deal about reporting that people actually read."
                ),
                "",
                *paragraph(
                    "Thank you for your time and consideration. I have enclosed the "
                    "materials requested in the posting and am happy to provide anything "
                    "further that would be useful."
                ),
                "",
                "Sincerely,",
                "",
                "",
            ]
        )
    ]


__all__ = [
    "Applicant",
    "APPLICANTS",
    "ExpectedCandidate",
    "ExpectedDocument",
    "MixedBatch",
    "MixedBatchBuilder",
    "build_mixed_batch",
    "build_ambiguity_batch",
]
