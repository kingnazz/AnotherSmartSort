"""Document-boundary detection.

This engine answers one question only: *does this page begin a new logical
document, or continue the previous one?* It is deliberately separate from
classification, because a 3-page resume whose second page scores weakly as a
Resume is still one resume.

The engine accumulates signed evidence. Positive scores argue for a new
document, negative scores argue for continuation, and the magnitude drives
:func:`~app.services.confidence.calibrate_boundary`. Crucially, a mere wobble in
classification confidence is worth very little on its own -- splitting requires
positive structural evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.intelligence.base import BoundaryAssessment, PageClassification, PageContext
from app.models.candidate import normalize_person_name
from app.profiles.base import OTHER, DocumentProfile
from app.services.confidence import calibrate_boundary
from app.services.text_features import (
    CLOSING_RE,
    SALUTATION_RE,
    PageFeatures,
    flatten,
    line_length_similarity,
    repeated_line_overlap,
)

#: Confidence at which a page's own type is considered trustworthy evidence.
_CONFIDENT = 0.70
#: How far down the page to look for a letter salutation (past the address block).
_SALUTATION_SCAN_LINES = 14


@dataclass(frozen=True)
class BoundarySignal:
    """One scored boundary observation, kept for explanation in the UI."""

    name: str
    score: float

    @property
    def favours_new_document(self) -> bool:
        return self.score > 0


class BoundaryEngine:
    """Heuristic document-boundary detector driven by structural evidence."""

    def __init__(self, profile: DocumentProfile) -> None:
        self.profile = profile

    # ------------------------------------------------------------------
    def assess(
        self,
        context: PageContext,
        classification: PageClassification,
        *,
        separator_type: str | None = None,
    ) -> BoundaryAssessment:
        """Decide whether ``context``'s page starts a new document."""
        if context.is_first_page:
            return BoundaryAssessment(
                starts_new_document=True,
                confidence=1.0,
                reasons=["First page of the source PDF"],
                score=10.0,
            )

        current = context.features
        previous = context.previous_features
        if current is None or previous is None:
            # Without features we cannot justify a split; continuing is the
            # safe default and the low confidence routes the page to review.
            return BoundaryAssessment(
                starts_new_document=False,
                confidence=0.5,
                reasons=["Not enough page text to judge the boundary"],
                score=0.0,
            )

        signals: list[BoundarySignal] = []

        # A recognised separator page always opens a new document.
        if separator_type:
            signals.append(
                BoundarySignal(f"Separator page announcing “{separator_type}”", 6.0)
            )

        if context.previous_separator_type:
            # A separator announces the document that follows it, so the page
            # after one continues that document rather than starting another.
            signals.append(
                BoundarySignal(
                    f"Follows a “{context.previous_separator_type}” separator page", -6.0
                )
            )

        signals.extend(self._page_numbering_signals(current, previous))
        signals.extend(self._type_change_signals(context, classification))
        signals.extend(self._document_start_signals(current, classification))
        signals.extend(self._previous_page_ending_signals(previous))
        signals.extend(self._identity_signals(context))
        signals.extend(self._layout_signals(current, previous))
        signals.extend(self._continuation_signals(current, context))

        score = round(sum(signal.score for signal in signals), 3)
        starts_new = score > 0.0

        reasons = [
            f"{signal.name} ({signal.score:+.1f})"
            for signal in sorted(signals, key=lambda s: abs(s.score), reverse=True)
            if signal.score
        ]

        return BoundaryAssessment(
            starts_new_document=starts_new,
            confidence=calibrate_boundary(score),
            reasons=reasons[:8],
            score=score,
        )

    # ------------------------------------------------------------------
    # Signal groups
    # ------------------------------------------------------------------
    def _page_numbering_signals(
        self, current: PageFeatures, previous: PageFeatures
    ) -> list[BoundarySignal]:
        """``Page 2 of 3`` following ``Page 1 of 3`` is the strongest continuation cue."""
        signals: list[BoundarySignal] = []
        cur_marker = current.page_marker
        prev_marker = previous.page_marker

        if cur_marker and prev_marker:
            cur_n, cur_total = cur_marker
            prev_n, prev_total = prev_marker
            if cur_total == prev_total and cur_n == prev_n + 1:
                signals.append(
                    BoundarySignal(f"Continues page numbering (page {cur_n} of {cur_total})", -6.0)
                )
            elif cur_n == 1 and prev_n >= 1:
                signals.append(
                    BoundarySignal("Page numbering restarts at page 1", 5.0)
                )
                if prev_n == prev_total:
                    signals.append(
                        BoundarySignal("Previous page was the last of its document", 1.5)
                    )
            elif cur_total != prev_total:
                signals.append(BoundarySignal("Page-count marker changed", 2.0))
        elif cur_marker and cur_marker[0] == 1 and cur_marker[1] > 1:
            signals.append(BoundarySignal("Page marked as page 1 of a document", 3.0))
        elif cur_marker and prev_marker is None and cur_marker[0] > 1:
            signals.append(BoundarySignal("Page marked as a continuation page", -1.5))

        return signals

    def _type_change_signals(
        self, context: PageContext, classification: PageClassification
    ) -> list[BoundarySignal]:
        """Type changes matter -- but only when both classifications are trustworthy."""
        signals: list[BoundarySignal] = []
        previous_type = context.previous_type
        if not previous_type:
            return signals

        current_type = classification.document_type
        current_confident = classification.confidence >= _CONFIDENT
        previous_confident = context.previous_confidence >= _CONFIDENT

        if current_type == previous_type:
            signals.append(BoundarySignal("Same document type as previous page", -2.2))
            return signals

        # An `Other` page is an absence of evidence, not evidence of a new document.
        if current_type == OTHER and not current_confident:
            signals.append(
                BoundarySignal("Unclassified page continues the previous document", -2.0)
            )
            return signals

        if current_confident and previous_confident:
            signals.append(
                BoundarySignal(
                    f"Document type changes from {previous_type} to {current_type}", 4.0
                )
            )
        elif current_confident or previous_confident:
            signals.append(
                BoundarySignal(
                    f"Possible type change from {previous_type} to {current_type}", 2.0
                )
            )
        else:
            # Both uncertain: confidence fluctuation alone must not split a document.
            signals.append(
                BoundarySignal("Uncertain type difference (weak evidence)", 0.6)
            )
        return signals

    def _document_start_signals(
        self, current: PageFeatures, classification: PageClassification
    ) -> list[BoundarySignal]:
        """Markers that a fresh document *opens* on this page."""
        signals: list[BoundarySignal] = []
        # A letter's salutation sits below the sender/recipient address block, so
        # the opening region has to be generous enough to reach it.
        top = "\n".join(current.first_lines[:_SALUTATION_SCAN_LINES])

        if SALUTATION_RE.search(top):
            signals.append(BoundarySignal("Page opens with a letter salutation", 3.2))

        if self._opening_title_line(current):
            signals.append(BoundarySignal("Page opens with a document title", 2.6))

        if current.has_contact_block and current.line_count > 4:
            head = "\n".join(current.first_lines[:6])
            if any(token in head for token in ("@",)) or current.phones:
                signals.append(BoundarySignal("Page opens with a name and contact header", 2.2))

        return signals

    def _previous_page_ending_signals(self, previous: PageFeatures) -> list[BoundarySignal]:
        """Markers that the previous document *closed* on the previous page."""
        signals: list[BoundarySignal] = []
        tail = "\n".join(previous.last_lines[-5:])

        if CLOSING_RE.search(tail):
            signals.append(BoundarySignal("Previous page ends with a letter closing", 2.5))

        if previous.page_marker and previous.page_marker[0] == previous.page_marker[1] > 1:
            signals.append(BoundarySignal("Previous page was marked as the final page", 1.8))

        if previous.ends_mid_sentence and previous.word_count > 25:
            signals.append(BoundarySignal("Previous page ends mid-sentence", -2.4))

        return signals

    def _identity_signals(self, context: PageContext) -> list[BoundarySignal]:
        """Identity continuity is a strong grouping signal in recruiting packets."""
        signals: list[BoundarySignal] = []
        current = context.candidate
        previous = context.previous_candidate

        current_name = normalize_person_name(current.name) if current.name else ""
        previous_name = normalize_person_name(previous.name) if previous.name else ""

        if current_name and previous_name:
            if current_name == previous_name:
                signals.append(BoundarySignal("Same candidate as previous page", -2.0))
            elif not _names_overlap(current_name, previous_name):
                signals.append(BoundarySignal("Different candidate name appears", 3.5))

        if current.email and previous.email:
            if current.email.lower() == previous.email.lower():
                signals.append(BoundarySignal("Same contact email as previous page", -1.2))
            else:
                signals.append(BoundarySignal("Different contact email appears", 2.0))

        if current.applicant_id and previous.applicant_id:
            if current.applicant_id.lower() != previous.applicant_id.lower():
                signals.append(BoundarySignal("Different applicant ID appears", 3.0))
            else:
                signals.append(BoundarySignal("Same applicant ID", -1.5))

        return signals

    def _layout_signals(
        self, current: PageFeatures, previous: PageFeatures
    ) -> list[BoundarySignal]:
        """Running headers/footers and layout similarity betray a shared document."""
        signals: list[BoundarySignal] = []

        header_overlap = repeated_line_overlap(current.first_lines[:3], previous.first_lines[:3])
        if header_overlap >= 0.5:
            signals.append(BoundarySignal("Repeated running header", -1.6))

        footer_overlap = repeated_line_overlap(current.last_lines[-2:], previous.last_lines[-2:])
        if footer_overlap >= 0.5:
            signals.append(BoundarySignal("Repeated running footer", -1.2))

        similarity = line_length_similarity(current, previous)
        if similarity >= 0.85:
            signals.append(BoundarySignal("Very similar page layout", -0.8))
        elif similarity <= 0.35 and current.word_count > 20 and previous.word_count > 20:
            signals.append(BoundarySignal("Abrupt layout change", 1.0))

        return signals

    def _continuation_signals(
        self, current: PageFeatures, context: PageContext
    ) -> list[BoundarySignal]:
        """Content-level cues that this page is simply more of the same document."""
        signals: list[BoundarySignal] = []

        if current.starts_lowercase:
            signals.append(BoundarySignal("Page begins mid-sentence", -2.2))

        if current.is_near_empty and not current.is_label_only:
            signals.append(BoundarySignal("Near-empty trailing page", -1.0))

        if (
            not current.has_salutation
            and not current.has_contact_block
            and not self._opening_title_line(current)
            and current.page_marker is None
            and current.word_count > 15
        ):
            signals.append(BoundarySignal("No document-opening markers on this page", -1.5))

        previous_group_type = context.previous_group_type
        if (
            previous_group_type
            and previous_group_type in self.profile.usually_single_page
            and context.previous_group_page_count >= 1
        ):
            signals.append(
                BoundarySignal(f"{previous_group_type} documents are usually one page", 0.8)
            )

        return signals

    # ------------------------------------------------------------------
    def _opening_title_line(self, features: PageFeatures) -> bool:
        """First non-empty line reads like a document title (``RESUME``, ``References``)."""
        if not features.first_lines:
            return False
        first = features.first_lines[0].strip().rstrip(":")
        if not (2 <= len(first) <= 60):
            return False
        key = re.sub(r"[^a-z0-9 ]+", " ", flatten(first)).strip()
        key = re.sub(r"\s+", " ", key)
        if not key:
            return False
        if key in self.profile.separator_labels:
            return True
        for label in self.profile.separator_labels:
            if re.fullmatch(rf"{re.escape(label)}s?", key):
                return True
        return False


def _names_overlap(a: str, b: str) -> bool:
    """True when two normalized names plausibly refer to the same person.

    Guards against splitting on ``"Benjamin Perez"`` vs ``"Benjamin R. Perez"``
    while still separating genuinely different applicants.
    """
    a_parts = {p for p in a.split() if len(p) > 1}
    b_parts = {p for p in b.split() if len(p) > 1}
    if not a_parts or not b_parts:
        return False
    shared = a_parts & b_parts
    return len(shared) >= 2 or shared == a_parts or shared == b_parts


__all__ = ["BoundaryEngine", "BoundarySignal"]
