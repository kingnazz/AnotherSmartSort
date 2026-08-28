"""Boundary engine: when a page starts a new document, and when it must not."""

from __future__ import annotations

import pytest

from app.intelligence.base import PageClassification, PageContext
from app.models.candidate import Candidate
from app.profiles.base import OTHER
from app.profiles.recruiting import COVER_LETTER, RESUME
from app.services.boundary_engine import BoundaryEngine
from app.services.confidence import calibrate_boundary
from app.services.text_features import extract_features
from scripts import sample_data


@pytest.fixture
def engine(profile) -> BoundaryEngine:
    return BoundaryEngine(profile)


def make_context(
    *,
    text: str,
    previous_text: str = "",
    page_index: int = 1,
    page_count: int = 3,
    previous_type: str | None = RESUME,
    previous_confidence: float = 0.95,
    candidate: Candidate | None = None,
    previous_candidate: Candidate | None = None,
    previous_separator_type: str | None = None,
) -> PageContext:
    return PageContext(
        source_pdf="test.pdf",
        page_index=page_index,
        page_count=page_count,
        text=text,
        features=extract_features(text),
        previous_features=extract_features(previous_text) if previous_text else None,
        previous_type=previous_type,
        previous_confidence=previous_confidence,
        candidate=candidate or Candidate(),
        previous_candidate=previous_candidate or Candidate(),
        previous_separator_type=previous_separator_type,
    )


def lines(block) -> str:
    return "\n".join(block.lines)


class TestFirstPage:
    def test_first_page_always_starts_a_document(self, engine) -> None:
        context = make_context(text="anything", page_index=0)
        result = engine.assess(context, PageClassification(RESUME, 0.9))
        assert result.starts_new_document
        assert result.confidence == 1.0


class TestContinuation:
    def test_page_numbering_holds_a_resume_together(self, engine) -> None:
        pages = sample_data.resume_pages(total=3)
        context = make_context(text=lines(pages[1]), previous_text=lines(pages[0]))
        result = engine.assess(context, PageClassification(RESUME, 0.85))
        assert not result.starts_new_document
        assert result.confidence >= 0.9

    def test_multi_page_cover_letter_stays_together(self, engine) -> None:
        pages = sample_data.cover_letter_pages(total=2)
        context = make_context(
            text=lines(pages[1]),
            previous_text=lines(pages[0]),
            previous_type=COVER_LETTER,
        )
        result = engine.assess(context, PageClassification(COVER_LETTER, 0.9))
        assert not result.starts_new_document

    def test_confidence_wobble_alone_never_splits(self, engine) -> None:
        """Two uncertain pages of the same kind are still one document."""
        pages = sample_data.resume_pages(total=3)
        context = make_context(
            text=lines(pages[1]),
            previous_text=lines(pages[0]),
            previous_confidence=0.55,
        )
        result = engine.assess(context, PageClassification(RESUME, 0.52))
        assert not result.starts_new_document

    def test_unclassified_page_continues_the_previous_document(self, engine) -> None:
        pages = sample_data.resume_pages(total=2)
        context = make_context(
            text="Some trailing content with no recognisable structure at all.",
            previous_text=lines(pages[0]),
        )
        result = engine.assess(context, PageClassification(OTHER, 0.35))
        assert not result.starts_new_document

    def test_page_after_a_separator_continues_that_document(self, engine) -> None:
        pages = sample_data.resume_pages(total=2)
        context = make_context(
            text=lines(pages[0]),
            previous_text="RESUME",
            previous_type=RESUME,
            previous_separator_type=RESUME,
        )
        result = engine.assess(context, PageClassification(RESUME, 0.95))
        assert not result.starts_new_document


class TestNewDocument:
    def test_type_change_between_confident_pages_splits(self, engine) -> None:
        resume = sample_data.resume_pages(total=3)[2]
        letter = sample_data.cover_letter_pages(total=1)[0]
        context = make_context(text=lines(letter), previous_text=lines(resume))
        result = engine.assess(context, PageClassification(COVER_LETTER, 0.95))
        assert result.starts_new_document
        assert result.confidence >= 0.8

    def test_letter_salutation_opens_a_document(self, engine) -> None:
        letter = sample_data.cover_letter_pages(total=1)[0]
        context = make_context(text=lines(letter), previous_text="Some previous page text.")
        result = engine.assess(context, PageClassification(COVER_LETTER, 0.95))
        assert result.starts_new_document
        assert any("salutation" in reason.lower() for reason in result.reasons)

    def test_different_candidate_splits_two_documents_of_the_same_type(self, engine) -> None:
        first = sample_data.resume_pages(total=2)[1]
        second = sample_data.resume_pages(
            name="Jane Smith", email="jane.smith@example.com", total=2
        )[0]
        context = make_context(
            text=lines(second),
            previous_text=lines(first),
            candidate=Candidate(name="Jane Smith"),
            previous_candidate=Candidate(name="Benjamin Perez"),
        )
        result = engine.assess(context, PageClassification(RESUME, 0.95))
        assert result.starts_new_document

    def test_similar_names_do_not_split(self, engine) -> None:
        """`Benjamin Perez` and `Benjamin R. Perez` are the same person."""
        pages = sample_data.resume_pages(total=3)
        context = make_context(
            text=lines(pages[1]),
            previous_text=lines(pages[0]),
            candidate=Candidate(name="Benjamin R. Perez"),
            previous_candidate=Candidate(name="Benjamin Perez"),
        )
        result = engine.assess(context, PageClassification(RESUME, 0.9))
        assert not result.starts_new_document

    def test_separator_page_starts_a_document(self, engine, profile) -> None:
        separator = sample_data.separator_page("Cover Letter")
        context = make_context(text=lines(separator), previous_text="Resume content here.")
        result = engine.assess(
            context, PageClassification(COVER_LETTER, 0.92), separator_type=COVER_LETTER
        )
        assert result.starts_new_document

    def test_restarted_page_numbering_starts_a_document(self, engine) -> None:
        context = make_context(
            text="Jane Smith\nPage 1 of 2\nPROFESSIONAL EXPERIENCE\nSenior Analyst",
            previous_text="Benjamin Perez\nPage 3 of 3\nSKILLS\nSQL, Python",
        )
        result = engine.assess(context, PageClassification(RESUME, 0.9))
        assert result.starts_new_document
        assert any("numbering" in reason.lower() for reason in result.reasons)


class TestBoundaryConfidence:
    def test_strong_evidence_is_confident(self) -> None:
        assert calibrate_boundary(8.0) >= 0.9

    def test_no_evidence_sits_at_the_midpoint(self) -> None:
        assert calibrate_boundary(0.0) == pytest.approx(0.5)

    def test_confidence_is_symmetric_in_sign(self) -> None:
        assert calibrate_boundary(4.0) == calibrate_boundary(-4.0)

    def test_missing_features_are_reported_as_uncertain(self, engine) -> None:
        context = PageContext(
            source_pdf="t.pdf", page_index=1, page_count=2, text="", features=None
        )
        result = engine.assess(context, PageClassification(OTHER, 0.3))
        assert not result.starts_new_document
        assert result.confidence == 0.5

    def test_reasons_are_populated_for_explanation(self, engine) -> None:
        pages = sample_data.resume_pages(total=3)
        context = make_context(text=lines(pages[1]), previous_text=lines(pages[0]))
        result = engine.assess(context, PageClassification(RESUME, 0.9))
        assert result.reasons
        assert all(isinstance(reason, str) for reason in result.reasons)


class TestConfidenceReflectsRealDecisions:
    """Review should flag what a human would change, and nothing else.

    On real applicant tracking exports almost every document was flagged while
    being entirely correct. Two causes, both measured against the client's own
    files before and after.
    """

    def make_pages(self, boundary_confidences: list[float]):
        """Pages forming one document, with the given boundary confidences."""
        from app.models.page import PageAnalysis

        pages = []
        for index, confidence in enumerate(boundary_confidences):
            page = PageAnalysis(source_pdf="memory.pdf", page_index=index)
            page.starts_new_document = index == 0
            page.boundary_confidence = confidence
            page.predicted_type = "Resume"
            page.classification_confidence = 0.95
            pages.append(page)
        return pages

    def test_a_confident_middle_page_does_not_drag_the_document_down(
        self, profile, thresholds
    ) -> None:
        """A page 85% sure it continues is a confident continuation.

        Taking the minimum across every internal page meant one ordinary page in
        a ten-page report put the whole document in the queue.
        """
        from app.services.grouping_service import GroupingService

        grouping = GroupingService(profile, thresholds)
        pages = self.make_pages([0.98, 0.85, 0.90, 0.88])
        groups = grouping.build_groups(pages, "memory.pdf")

        assert len(groups) == 1
        assert groups[0].boundary_confidence >= 0.90, (
            "an ordinary continuation page was treated as doubt"
        )

    def test_a_genuine_near_miss_is_still_counted(self, profile, thresholds) -> None:
        """A page that nearly started a new document is worth a glance."""
        from app.services.grouping_service import GroupingService

        grouping = GroupingService(profile, thresholds)
        pages = self.make_pages([0.98, 0.55, 0.95, 0.95])
        groups = grouping.build_groups(pages, "memory.pdf")

        assert groups[0].boundary_confidence < thresholds.high, (
            "a near-miss split was hidden from review"
        )

    def test_the_opening_decision_always_counts(self, profile, thresholds) -> None:
        from app.services.grouping_service import GroupingService

        grouping = GroupingService(profile, thresholds)
        pages = self.make_pages([0.55, 0.98, 0.98])
        groups = grouping.build_groups(pages, "memory.pdf")

        assert groups[0].boundary_confidence < thresholds.high
