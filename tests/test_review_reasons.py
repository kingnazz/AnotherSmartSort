"""Why a document needs review, and which documents those are.

These are the words the user reads when the queue says "Review Needed", so they
are worth pinning: an empty explanation, or one that quietly disagrees with the
count the queue showed, sends somebody hunting through a board for an item that
looks exactly like every other item.

No Qt here -- this is the plain-Python half, so it runs everywhere and fails
for reasons about the content rather than the widgets.
"""

from __future__ import annotations

from app.models.candidate import Candidate
from app.models.document import DocumentGroup
from app.models.page import PageAnalysis
from app.models.source_file import SourceFileAnalysis
from app.services.review_reasons import (
    FALLBACK_REASON,
    UNCONFIRMED_CANDIDATE_REASON,
    flagged_groups,
    flagged_pages,
    review_reasons_for,
    review_summary,
)


def group(**kwargs) -> DocumentGroup:
    base = {
        "source_pdf": "/tmp/sample.pdf",
        "document_type": "Resume",
        "page_indexes": [0, 1],
        "candidate": Candidate(name="Dana Whitfield"),
    }
    base.update(kwargs)
    return DocumentGroup(**base)


def page(index: int, **kwargs) -> PageAnalysis:
    return PageAnalysis(source_pdf="/tmp/sample.pdf", page_index=index, **kwargs)


class TestWhyADocumentNeedsReview:
    def test_a_clean_document_has_nothing_to_explain(self) -> None:
        assert review_reasons_for(group()) == []

    def test_recorded_reasons_are_used_as_written(self) -> None:
        """The pipeline already phrases these for a person; rewording them here
        would mean two places to keep honest."""
        flagged = group()
        flagged.add_review_reason("Document type could not be determined")
        flagged.add_review_reason("Low page-grouping confidence")

        assert review_reasons_for(flagged) == [
            "Document type could not be determined",
            "Low page-grouping confidence",
        ]

    def test_an_unconfirmed_candidate_is_explained(self) -> None:
        """This is the case that used to show nothing at all: the file counted
        it, and the document looked ordinary."""
        flagged = group(association_review=True)

        assert review_reasons_for(flagged) == [UNCONFIRMED_CANDIDATE_REASON]

    def test_page_problems_are_listed_under_the_document(self) -> None:
        flagged = group()
        flagged.add_review_reason("Low document-type confidence")
        bad_page = page(1)
        bad_page.add_review_reason("Page 2 could not be read by OCR")

        assert review_reasons_for(flagged, [page(0), bad_page]) == [
            "Low document-type confidence",
            "Page 2 could not be read by OCR",
        ]

    def test_a_page_that_is_fine_contributes_nothing(self) -> None:
        flagged = group(association_review=True)
        quiet = page(0)
        quiet.review_reasons.append("stale text that was never flagged")

        assert review_reasons_for(flagged, [quiet]) == [UNCONFIRMED_CANDIDATE_REASON]

    def test_reasons_are_not_repeated(self) -> None:
        flagged = group()
        flagged.add_review_reason("Low document-type confidence")
        duplicated = page(0)
        duplicated.add_review_reason("Low document-type confidence")

        assert review_reasons_for(flagged, [duplicated]) == [
            "Low document-type confidence"
        ]

    def test_a_flagged_document_with_no_recorded_reason_still_says_something(
        self,
    ) -> None:
        """"Needs review" with no reason is where the user gets stuck, so the
        fallback names what to check instead of restating the problem."""
        flagged = group(requires_review=True)

        assert review_reasons_for(flagged) == [FALLBACK_REASON]
        assert "type, pages, and candidate" in FALLBACK_REASON

    def test_the_fallback_never_masks_a_real_reason(self) -> None:
        flagged = group()
        flagged.add_review_reason("Separator page - decide whether to keep it")

        assert FALLBACK_REASON not in review_reasons_for(flagged)


class TestWhichDocumentsAreFlagged:
    def file_with(self, groups: list[DocumentGroup]) -> SourceFileAnalysis:
        analysis = SourceFileAnalysis(path="/tmp/sample.pdf", page_count=8)
        analysis.groups = groups
        return analysis

    def test_it_matches_the_count_the_queue_shows(self) -> None:
        """If these two disagree, the queue promises an item the workspace
        cannot walk the user to."""
        first = group(requires_review=True)
        analysis = self.file_with([group(), first, group(association_review=True)])

        assert len(flagged_groups(analysis)) == analysis.review_group_count == 2

    def test_excluded_documents_are_not_offered(self) -> None:
        analysis = self.file_with([group(requires_review=True, excluded=True)])

        assert flagged_groups(analysis) == []
        assert analysis.review_group_count == 0

    def test_order_follows_the_document_order(self) -> None:
        first = group(page_indexes=[0], requires_review=True)
        second = group(page_indexes=[4], association_review=True)
        analysis = self.file_with([first, group(page_indexes=[2]), second])

        assert [g.id for g in flagged_groups(analysis)] == [first.id, second.id]


class TestPointingAtAPage:
    def test_a_page_level_flag_is_reported(self) -> None:
        owner = group(page_indexes=[0, 1])
        bad = page(1)
        bad.add_review_reason("Page 2 could not be read by OCR")

        assert flagged_pages(owner, [page(0), bad]) == [1]

    def test_a_document_wide_flag_points_at_no_page(self) -> None:
        """Better to leave the reviewer on the document than guess a page."""
        owner = group(page_indexes=[0, 1], requires_review=True)

        assert flagged_pages(owner, [page(0), page(1)]) == []

    def test_pages_outside_the_document_are_ignored(self) -> None:
        owner = group(page_indexes=[0, 1])
        elsewhere = page(7)
        elsewhere.add_review_reason("Page 8 could not be read by OCR")

        assert flagged_pages(owner, [elsewhere]) == []


class TestTheHeadline:
    def test_one_item_reads_as_one(self) -> None:
        assert review_summary(1, "Applications.pdf") == (
            "1 item needs review in Applications.pdf"
        )

    def test_several_items_agree_with_themselves(self) -> None:
        assert review_summary(3, "Applications.pdf") == (
            "3 items need review in Applications.pdf"
        )
