"""Why a document needs review, in words a person can act on.

The models already record *that* something needs review, and mostly *why*, but
the reasons are spread across three places: the document's own
``review_reasons``, the separate ``association_review`` flag covering "we could
not work out whose this is", and per-page ``review_reasons`` for problems that
belong to one page rather than the whole document. Before this, a card showed
one of those, the inspector showed another, and a document flagged only by
association showed nothing at all -- so the queue could say "1 to review" while
the workspace looked entirely normal.

This is the one place that answers the question, so every surface gives the
same answer.

Nothing here invents a reason from a confidence score. If the pipeline recorded
why, that wording is used as written; ``FALLBACK_REASON`` covers the case where
something is flagged but nothing said why, which is a real state and better
admitted than papered over with a guess.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from app.models.document import DocumentGroup
from app.models.page import PageAnalysis
from app.models.source_file import SourceFileAnalysis

#: Shown when a document is flagged but carries no recorded reason. It says what
#: to check rather than restating that something is wrong, because "this needs
#: review" on its own leaves the reviewer exactly where they started.
FALLBACK_REASON = (
    "The app could not confidently confirm this document. Check its type, "
    "pages, and candidate before saving."
)

#: Used when only the association flag is set. The pipeline records association
#: *reasons* explaining why it matched, which are not the same thing as saying
#: the match is unconfirmed.
UNCONFIRMED_CANDIDATE_REASON = "Candidate assignment could not be confirmed"


def review_reasons_for(
    group: DocumentGroup, pages: Sequence[PageAnalysis] | None = None
) -> list[str]:
    """Every reason this document is flagged, de-duplicated, order preserved.

    Empty when the document is not flagged at all -- callers use that to decide
    whether to show anything, rather than having to ask twice.
    """
    if not group.needs_attention:
        return []

    reasons: list[str] = []

    def add(reason: str) -> None:
        cleaned = (reason or "").strip()
        if cleaned and cleaned not in reasons:
            reasons.append(cleaned)

    for reason in group.review_reasons:
        add(reason)

    if group.association_review:
        add(UNCONFIRMED_CANDIDATE_REASON)

    # Page-level problems last: they are narrower than the document-level ones
    # and read better as detail underneath them.
    for page in pages or ():
        if not page.requires_review:
            continue
        for reason in page.review_reasons:
            add(reason)

    return reasons or [FALLBACK_REASON]


def flagged_groups(analysis: SourceFileAnalysis) -> list[DocumentGroup]:
    """The documents in a file that need review, in the order they appear.

    Matches :attr:`SourceFileAnalysis.review_group_count` exactly -- excluded
    documents are not counted there and are not offered here, so the number the
    queue shows is the number the workspace can actually walk through.
    """
    return [
        group
        for group in analysis.groups
        if group.needs_attention and not group.excluded
    ]


def flagged_pages(group: DocumentGroup, pages: Iterable[PageAnalysis]) -> list[int]:
    """Page indexes inside ``group`` that carry their own review flag.

    Used to point at the specific page when the problem belongs to one, and
    empty when the flag is document-wide -- in which case the caller should
    stay on the document rather than guess at a page.
    """
    return [
        page.page_index
        for page in pages
        if page.requires_review and group.contains(page.page_index)
    ]


def review_summary(count: int, file_name: str) -> str:
    """The workspace banner's headline, pluralised."""
    if count == 1:
        return f"1 item needs review in {file_name}"
    return f"{count} items need review in {file_name}"


__all__ = [
    "FALLBACK_REASON",
    "UNCONFIRMED_CANDIDATE_REASON",
    "flagged_groups",
    "flagged_pages",
    "review_reasons_for",
    "review_summary",
]
