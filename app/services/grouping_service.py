"""Grouping: turning per-page decisions into logical documents.

The boundary engine decides *where* documents break. This service assembles
those decisions into :class:`~app.models.document.DocumentGroup` objects,
derives each group's type and identity from its member pages, and applies the
review policy.

It also owns every *correction* the user can make in the review workspace
(retype, split, merge, exclude). Keeping those operations here rather than in
the UI means the same rules re-derive group state whichever way it was changed.
"""

from __future__ import annotations

from collections import defaultdict

from app.models.candidate import Candidate, normalize_person_name
from app.models.document import DocumentGroup
from app.models.enums import ClassificationSource, SeparatorPolicy, SeparatorState
from app.models.page import PageAnalysis
from app.models.source_file import SourceFileAnalysis
from app.profiles.base import OTHER, DocumentProfile
from app.services.confidence import ConfidenceThresholds
from app.services.metadata_service import merge_candidates
from app.utils.logging_setup import get_logger

logger = get_logger("grouping")

#: How much each additional agreeing page contributes to group confidence.
#: Below 1.0 so corroboration strengthens a conclusion without letting a pile of
#: individually weak pages masquerade as certainty.
_CORROBORATION_WEIGHT = 0.5


def _corroborated_confidence(confidences: list[float]) -> float:
    """Combine agreeing per-page confidences into one group confidence.

    Pages that independently reach the same conclusion are corroborating
    evidence, so a 3-page resume should be *more* certain of its type than any
    single page -- averaging would wrongly let one weak continuation page drag a
    well-evidenced document below the review threshold. Extra pages shrink the
    remaining doubt rather than adding full weight of their own.
    """
    if not confidences:
        return 0.0
    ordered = sorted(confidences, reverse=True)
    doubt = 1.0 - ordered[0]
    for extra in ordered[1:]:
        doubt *= 1.0 - _CORROBORATION_WEIGHT * extra
    return round(min(0.99, 1.0 - doubt), 4)


class GroupingService:
    """Builds and maintains document groups for a source PDF."""

    def __init__(
        self,
        profile: DocumentProfile,
        thresholds: ConfidenceThresholds | None = None,
        separator_policy: SeparatorPolicy = SeparatorPolicy.INCLUDE,
    ) -> None:
        self.profile = profile
        self.thresholds = thresholds or ConfidenceThresholds()
        self.separator_policy = separator_policy

    # ------------------------------------------------------------------
    # Building
    # ------------------------------------------------------------------
    def build_groups(self, pages: list[PageAnalysis], source_pdf: str) -> list[DocumentGroup]:
        """Assemble contiguous pages into logical documents."""
        groups: list[DocumentGroup] = []
        current: DocumentGroup | None = None

        for page in sorted(pages, key=lambda p: p.page_index):
            if current is None or page.starts_new_document:
                current = DocumentGroup(source_pdf=source_pdf, page_indexes=[page.page_index])
                groups.append(current)
            else:
                current.page_indexes.append(page.page_index)

        for group in groups:
            self.refresh_group(group, pages)

        self._propagate_identity(groups, pages)
        self._apply_review_policy(groups, pages)
        return groups

    # ------------------------------------------------------------------
    def refresh_group(self, group: DocumentGroup, pages: list[PageAnalysis]) -> None:
        """Recompute a group's derived type, confidence, identity and separators."""
        members = self._members(group, pages)
        if not members:
            group.classification_confidence = 0.0
            group.boundary_confidence = 0.0
            return

        if not group.type_manually_set:
            document_type, confidence, source = self._aggregate_type(members)
            group.document_type = document_type
            group.classification_confidence = confidence
            group.classification_source = source

        group.boundary_confidence = self._aggregate_boundary(group, members, pages)
        group.candidate = self._aggregate_candidate(members, group.document_type)
        self._apply_separator_policy(group, members)

    def _members(self, group: DocumentGroup, pages: list[PageAnalysis]) -> list[PageAnalysis]:
        by_index = {page.page_index: page for page in pages}
        return [by_index[i] for i in group.page_indexes if i in by_index]

    def _aggregate_type(
        self, members: list[PageAnalysis]
    ) -> tuple[str, float, ClassificationSource]:
        """Confidence-weighted vote across the group's pages.

        A weak second page never outvotes a decisive first page, which is why a
        3-page resume whose middle page scores poorly is still a Resume.
        """
        weights: dict[str, float] = defaultdict(float)
        sources: dict[str, int] = defaultdict(int)

        for position, page in enumerate(members):
            if page.predicted_type == OTHER and page.classification_confidence < 0.5:
                continue
            # The opening page defines a document's identity more than later pages.
            positional = 1.35 if position == 0 else 1.0
            weights[page.predicted_type] += page.classification_confidence * positional
            sources[page.classification_source.value] += 1

        if not weights:
            return OTHER, min((p.classification_confidence for p in members), default=0.35), (
                ClassificationSource.RULES
            )

        best_type = max(weights, key=lambda key: weights[key])
        supporting = [
            page.classification_confidence
            for page in members
            if page.predicted_type == best_type
        ]
        confidence = _corroborated_confidence(supporting)

        # Disagreement inside a group is itself evidence of uncertainty.
        distinct_types = {
            page.predicted_type
            for page in members
            if page.predicted_type != OTHER or page.classification_confidence >= 0.5
        }
        if len(distinct_types) > 1:
            agreement = len(supporting) / len(members)
            confidence *= 0.6 + 0.4 * agreement

        source = ClassificationSource.RULES
        if sources.get(ClassificationSource.DETERMINISTIC.value):
            # A group the deterministic ATS parser handled never mixes with
            # rules/AI-classified pages, so this is decisive whenever present.
            source = ClassificationSource.DETERMINISTIC
        elif sources.get(ClassificationSource.AI.value):
            source = ClassificationSource.AI
        elif sources.get(ClassificationSource.AI_ASSISTED.value):
            source = ClassificationSource.AI_ASSISTED

        return best_type, round(min(confidence, 0.99), 4), source

    def _aggregate_boundary(
        self, group: DocumentGroup, members: list[PageAnalysis], pages: list[PageAnalysis]
    ) -> float:
        """How sure we are the group's *extent* is right.

        Only the decisions that could change the answer count: where the
        document opened, and where the next one did. A page in the middle
        continuing is not a decision anybody would revisit -- a ten-page ATS
        report always contains some page whose continuation evidence is
        thinner than its neighbours', and taking the minimum over all of them
        dragged perfectly correct documents into the review queue.

        Internal pages are counted only when the engine came genuinely close to
        splitting there -- below the review threshold, not merely short of
        certainty. A page that is 85% sure it continues is a confident
        continuation, and treating that as doubt is how every long document
        ended up in the queue.
        """
        confidences = [members[0].boundary_confidence]

        next_index = group.end_page_index + 1
        following = next((p for p in pages if p.page_index == next_index), None)
        if following is not None:
            confidences.append(following.boundary_confidence)

        confidences.extend(
            page.boundary_confidence
            for page in members[1:]
            if page.boundary_confidence < self.thresholds.review
        )

        valid = [c for c in confidences if c > 0]
        return round(min(valid), 4) if valid else 0.5

    def _aggregate_candidate(self, members: list[PageAnalysis], document_type: str) -> Candidate:
        """Merge page identities, preferring pages that reliably carry identity."""
        preferred = [
            page.candidate
            for page in members
            if page.predicted_type in self.profile.identity_types and not page.candidate.is_empty
        ]
        pool = preferred or [page.candidate for page in members if not page.candidate.is_empty]
        return merge_candidates(pool)

    def _apply_separator_policy(self, group: DocumentGroup, members: list[PageAnalysis]) -> None:
        """Honour the separator policy, respecting any per-page user override."""
        excluded: list[int] = []
        for page in members:
            if page.separator_state is SeparatorState.NOT_SEPARATOR:
                continue
            if page.separator_state is SeparatorState.EXCLUDED:
                excluded.append(page.page_index)
        group.excluded_separator_pages = sorted(excluded)

    # ------------------------------------------------------------------
    def _propagate_identity(
        self, groups: list[DocumentGroup], pages: list[PageAnalysis]
    ) -> None:
        """Share one unambiguous identity across a single-applicant PDF.

        This is the narrow case: a file that names exactly one person, where a
        References page that never repeats the name still obviously belongs to
        them. When more than one name appears the file is a multi-applicant
        batch, and attributing documents is
        :class:`~app.services.packet_service.CandidatePacketService`'s job --
        nothing is guessed here.
        """
        names: dict[str, str] = {}
        for group in groups:
            if group.candidate.name:
                names.setdefault(normalize_person_name(group.candidate.name), group.candidate.name)

        if len(names) != 1:
            return

        only_name = next(iter(names.values()))
        donor = next(
            (g.candidate for g in groups if g.candidate.name and not g.candidate.is_empty),
            Candidate(),
        )

        for group in groups:
            if group.candidate.name:
                continue
            inherited = Candidate(
                name=only_name,
                email=group.candidate.email or donor.email,
                phone=group.candidate.phone or donor.phone,
                linkedin=group.candidate.linkedin or donor.linkedin,
                job_title=group.candidate.job_title or donor.job_title,
                applicant_id=group.candidate.applicant_id or donor.applicant_id,
            )
            group.candidate = inherited

    # ------------------------------------------------------------------
    def _apply_review_policy(
        self, groups: list[DocumentGroup], pages: list[PageAnalysis]
    ) -> None:
        for group in groups:
            self.evaluate_review(group, pages)

    def evaluate_review(self, group: DocumentGroup, pages: list[PageAnalysis]) -> None:
        """Decide whether a group needs human attention, and say why."""
        group.clear_review()
        if group.type_manually_set:
            return

        if not group.export_page_indexes:
            # Every page here is an excluded separator or divider -- a bulk
            # compile's cover sheet, a page reading only "RESUME". Nothing will
            # be written, so there is no decision to make, and asking for one
            # is the kind of noise that makes a reviewer stop reading them.
            return

        members = self._members(group, pages)

        if self.thresholds.requires_review(group.classification_confidence):
            band = self.thresholds.band(group.classification_confidence)
            group.add_review_reason(
                f"Document type confidence is "
                f"{group.classification_confidence * 100:.0f}% ({band.label.lower()})"
            )

        if self.thresholds.requires_review(group.boundary_confidence):
            group.add_review_reason(
                f"Page grouping confidence is {group.boundary_confidence * 100:.0f}%"
            )

        if group.document_type == OTHER:
            group.add_review_reason("Document type could not be determined")

        if group.candidate.has_conflict:
            others = ", ".join(group.candidate.conflicting_names[:3])
            group.add_review_reason(f"More than one name appears on these pages ({others})")

        # Whether this document has an owner is not a question about its type or
        # its extent. CandidatePacketService answers it and flags it there.

        for page in members:
            # A structured parser flags the page that opens a document it could
            # not resolve -- an attachment it cannot name, two uploads it cannot
            # separate. Nothing else carries that doubt upward, so without this
            # the parser's own warning dies on the page and the document is
            # exported looking certain. Only deterministic pages are read this
            # way: the generic pipeline's page notes ("Low document-type
            # confidence") already have group-level equivalents above, and
            # repeating them would bury the reasons a reviewer can act on.
            if page.classification_source is ClassificationSource.DETERMINISTIC:
                for reason in page.review_reasons:
                    group.add_review_reason(reason)

            if page.error:
                group.add_review_reason(f"Page {page.page_number}: {page.error}")
            elif page.ocr_failed:
                group.add_review_reason(f"Page {page.page_number} could not be read by OCR")
            elif (
                page.predicted_type == OTHER
                and group.document_type != OTHER
                and self.thresholds.requires_review(page.classification_confidence)
            ):
                # Naming the page is far more actionable than a bare percentage.
                group.add_review_reason(
                    f"Page {page.page_number} could not be identified - check it belongs here"
                )
            elif page.separator_state is SeparatorState.UNDECIDED:
                group.add_review_reason(
                    f"Page {page.page_number} looks like a separator page - keep or remove it?"
                )

    # ------------------------------------------------------------------
    # Corrections (used by the review workspace)
    # ------------------------------------------------------------------
    def set_document_type(
        self, file: SourceFileAnalysis, group: DocumentGroup, document_type: str
    ) -> None:
        """Apply a manual type correction to a group and its pages."""
        normalized = self.profile.normalize_type(document_type)
        group.set_type(normalized)
        for page in self._members(group, file.pages):
            page.predicted_type = normalized
            page.classification_source = ClassificationSource.MANUAL
            page.requires_review = False
            page.review_reasons.clear()
        file.refresh_status()

    def split_before(
        self, file: SourceFileAnalysis, page_index: int
    ) -> tuple[DocumentGroup, DocumentGroup] | None:
        """Split the group containing ``page_index`` so a new document starts there."""
        group = file.group_for_page(page_index)
        if group is None or page_index == group.start_page_index:
            return None

        tail_indexes = [i for i in group.page_indexes if i >= page_index]
        head_indexes = [i for i in group.page_indexes if i < page_index]
        if not head_indexes or not tail_indexes:
            return None

        group.page_indexes = head_indexes
        new_group = DocumentGroup(source_pdf=str(file.path), page_indexes=tail_indexes)

        page = file.page(page_index)
        if page is not None:
            page.starts_new_document = True
            page.boundary_confidence = 1.0
            page.boundary_reasons = ["Split requested by reviewer"]

        position = file.groups.index(group)
        file.groups.insert(position + 1, new_group)

        self.refresh_group(group, file.pages)
        self.refresh_group(new_group, file.pages)
        self.evaluate_review(group, file.pages)
        self.evaluate_review(new_group, file.pages)
        file.refresh_status()
        return group, new_group

    def merge_with_previous(
        self, file: SourceFileAnalysis, group: DocumentGroup
    ) -> DocumentGroup | None:
        """Merge ``group`` into the document immediately before it."""
        position = file.groups.index(group)
        if position == 0:
            return None
        previous = file.groups[position - 1]
        return self._merge(file, previous, group)

    def merge_with_next(
        self, file: SourceFileAnalysis, group: DocumentGroup
    ) -> DocumentGroup | None:
        """Merge the document immediately after ``group`` into it."""
        position = file.groups.index(group)
        if position >= len(file.groups) - 1:
            return None
        following = file.groups[position + 1]
        return self._merge(file, group, following)

    def _merge(
        self, file: SourceFileAnalysis, keep: DocumentGroup, absorb: DocumentGroup
    ) -> DocumentGroup:
        first_absorbed = absorb.start_page_index
        keep.page_indexes = sorted(set(keep.page_indexes) | set(absorb.page_indexes))
        keep.excluded_separator_pages = sorted(
            set(keep.excluded_separator_pages) | set(absorb.excluded_separator_pages)
        )
        file.groups.remove(absorb)

        page = file.page(first_absorbed)
        if page is not None:
            page.starts_new_document = False
            page.boundary_confidence = 1.0
            page.boundary_reasons = ["Merged by reviewer"]

        # A merge is an explicit human decision about extent; if the user also
        # fixed the type earlier, keep it.
        self.refresh_group(keep, file.pages)
        self.evaluate_review(keep, file.pages)
        file.refresh_status()
        return keep

    def set_group_excluded(
        self, file: SourceFileAnalysis, group: DocumentGroup, excluded: bool
    ) -> None:
        """Include or exclude a whole document from export."""
        group.excluded = excluded
        for page in self._members(group, file.pages):
            page.excluded = excluded
        file.refresh_status()

    def set_separator_included(
        self, file: SourceFileAnalysis, group: DocumentGroup, page_index: int, included: bool
    ) -> None:
        """Keep or drop an individual separator page from the exported PDF."""
        page = file.page(page_index)
        if page is None or page.separator_state is SeparatorState.NOT_SEPARATOR:
            return
        page.separator_state = SeparatorState.INCLUDED if included else SeparatorState.EXCLUDED
        self.refresh_group(group, file.pages)
        self.evaluate_review(group, file.pages)
        file.refresh_status()

    def mark_reviewed(self, file: SourceFileAnalysis, group: DocumentGroup) -> None:
        """Accept a group as-is, clearing its review flags."""
        group.clear_review()
        group.type_manually_set = True
        file.refresh_status()

    # ------------------------------------------------------------------
    # Page-level corrections
    # ------------------------------------------------------------------
    def can_move_pages(
        self,
        file: SourceFileAnalysis,
        page_indexes: list[int],
        target: DocumentGroup,
    ) -> tuple[bool, str]:
        """Whether moving these pages into ``target`` is safe, and why not.

        Refusing an unsafe move is the point. A document is a contiguous run of
        pages in one PDF; an operation that would leave a group with a hole in
        the middle, or mix two source files together, produces an export whose
        pages are not the pages the user saw. Better to decline the drop than
        to write that file.
        """
        if not page_indexes:
            return False, "No pages were selected."

        wanted = sorted(set(page_indexes))
        if wanted != list(range(wanted[0], wanted[-1] + 1)):
            return False, "Only a run of pages next to each other can be moved together."

        if str(target.source_pdf) != str(file.path):
            return False, "Pages can only be moved within the same PDF."

        for index in wanted:
            source = file.group_for_page(index)
            if source is None:
                return False, "Those pages are not part of a document."
            if source is target:
                return False, "Those pages are already in this document."

        # The pages must join the target at one end, or the result would not be
        # one continuous document.
        merged = sorted(set(target.page_indexes) | set(wanted))
        if merged != list(range(merged[0], merged[-1] + 1)):
            return False, "That would leave a gap in the document."

        # And whatever they came from must survive as a continuous document.
        for source in _source_groups(file, wanted, target):
            remaining = [i for i in source.page_indexes if i not in set(wanted)]
            if remaining and remaining != list(range(remaining[0], remaining[-1] + 1)):
                return False, "That would split the other document into two pieces."

        return True, ""

    def move_pages(
        self,
        file: SourceFileAnalysis,
        page_indexes: list[int],
        target: DocumentGroup,
    ) -> bool:
        """Move pages into ``target``, keeping every document contiguous.

        Source order is never rewritten: pages keep their original positions in
        the PDF, so a document always exports in the order it was scanned.
        """
        allowed, _reason = self.can_move_pages(file, page_indexes, target)
        if not allowed:
            return False

        wanted = sorted(set(page_indexes))
        sources = _source_groups(file, wanted, target)

        for source in sources:
            source.page_indexes = [i for i in source.page_indexes if i not in set(wanted)]
            source.excluded_separator_pages = [
                i for i in source.excluded_separator_pages if i not in set(wanted)
            ]

        target.page_indexes = sorted(set(target.page_indexes) | set(wanted))

        for page in self._members(target, file.pages):
            page.predicted_type = target.document_type
            page.classification_source = ClassificationSource.MANUAL

        # A group emptied by the move is no longer a document.
        for source in sources:
            if not source.page_indexes:
                file.groups.remove(source)

        file.groups.sort(key=lambda g: g.start_page_index)

        for group in file.groups:
            self.refresh_group(group, file.pages)
            self.evaluate_review(group, file.pages)
        file.refresh_status()
        return True

    def split_into_new_document(
        self, file: SourceFileAnalysis, page_indexes: list[int]
    ) -> DocumentGroup | None:
        """Pull a trailing run of pages out into a document of their own.

        The drag equivalent of "these last two pages are actually a separate
        letter". Only a run at one end can be taken, because removing pages
        from the middle would leave the original in two pieces.
        """
        wanted = sorted(set(page_indexes))
        if not wanted:
            return None
        if wanted != list(range(wanted[0], wanted[-1] + 1)):
            return None

        source = file.group_for_page(wanted[0])
        if source is None or not set(wanted).issubset(set(source.page_indexes)):
            return None

        remaining = [i for i in source.page_indexes if i not in set(wanted)]
        if not remaining:
            return None
        if remaining != list(range(remaining[0], remaining[-1] + 1)):
            return None

        source.page_indexes = remaining
        source.excluded_separator_pages = [
            i for i in source.excluded_separator_pages if i in set(remaining)
        ]

        new_group = DocumentGroup(source_pdf=str(file.path), page_indexes=wanted)
        file.groups.append(new_group)
        file.groups.sort(key=lambda g: g.start_page_index)

        first = file.page(wanted[0])
        if first is not None:
            first.starts_new_document = True
            first.boundary_confidence = 1.0
            first.boundary_reasons = ["Split requested by reviewer"]

        self.refresh_group(source, file.pages)
        self.refresh_group(new_group, file.pages)
        self.evaluate_review(source, file.pages)
        self.evaluate_review(new_group, file.pages)
        file.refresh_status()
        return new_group


def _source_groups(
    file: SourceFileAnalysis, page_indexes: list[int], target: DocumentGroup
) -> list[DocumentGroup]:
    """The distinct groups these pages currently belong to, excluding ``target``.

    Collected by identity rather than in a set: :class:`DocumentGroup` is a
    mutable dataclass and so unhashable, and comparing by value would merge two
    genuinely different documents that happen to look alike.
    """
    found: list[DocumentGroup] = []
    seen: set[int] = set()
    for index in page_indexes:
        source = file.group_for_page(index)
        if source is None or source is target:
            continue
        if id(source) not in seen:
            seen.add(id(source))
            found.append(source)
    return found


__all__ = ["GroupingService"]
