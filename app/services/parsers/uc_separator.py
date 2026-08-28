"""Separator-page ATS export parser (the original UC-style format).

The format this handles prints a machine-generated application report, then
introduces each attachment with a page carrying nothing but a label --
``Resume``, ``Cover Letters``, ``References``. The separator names the section
that follows, which makes extraction exact: no page's own appearance needs to
be judged, because the file already says what each run of pages is.

This is the parser verified against the real client ATS PDFs in the previous
phase; its behaviour is deliberately unchanged by the move into the registry.
"""

from __future__ import annotations

from app.models.candidate import Candidate, normalize_person_name
from app.models.enums import SeparatorPolicy, SeparatorState
from app.models.page import PageAnalysis
from app.profiles.base import DocumentProfile
from app.profiles.recruiting import APPLICATION_REPORT, is_ats_generated_page
from app.services.metadata_service import MetadataExtractor, name_from_report_subject
from app.services.parsers.base import ParseOutcome, ParserMatch, assign_page
from app.services.text_features import PageFeatures


class UCSeparatorExportParser:
    """Deterministic parser for separator-page ATS report exports."""

    name = "Separator-page ATS export"

    def __init__(
        self,
        profile: DocumentProfile,
        metadata_extractor: MetadataExtractor | None = None,
    ) -> None:
        self.profile = profile
        self.metadata = metadata_extractor or MetadataExtractor()

    # ------------------------------------------------------------------
    def can_parse(self, features_list: list[PageFeatures]) -> ParserMatch:
        """Match on the machine-generated report header this format prints.

        Two independent markers are required (``is_ats_generated_page``), so a
        resume that mentions a requisition number in passing cannot trip it.
        """
        if APPLICATION_REPORT not in self.profile.document_types:
            return ParserMatch.no()
        if not any(is_ats_generated_page(features) for features in features_list):
            return ParserMatch.no()
        return ParserMatch(0.90, "system-generated application report header")

    # ------------------------------------------------------------------
    def parse(
        self,
        pages: list[PageAnalysis],
        features_list: list[PageFeatures],
        *,
        separator_policy: SeparatorPolicy,
    ) -> ParseOutcome:
        """Assign type, boundary, identity and separator state to every page.

        Once a section is open -- whether the application report itself or a
        section a separator announced -- every following page belongs to it
        until another known separator, a new applicant, or the end of the
        file.
        """
        outcome = ParseOutcome(parser=self.name)
        current_candidate = Candidate()
        current_type: str | None = None
        opened_first_applicant = False

        for page, features in zip(pages, features_list):
            if page.error:
                continue

            subject_name = name_from_report_subject(features)
            separator_type = self.profile.separator_type_for(features)
            new_applicant = self._is_new_applicant(
                features, subject_name, current_candidate, opened_first_applicant
            )

            if new_applicant:
                current_candidate = self._extract_report_candidate(features, subject_name)
                current_type = APPLICATION_REPORT
                opened_first_applicant = True
                outcome.documents_found += 1
                assign_page(
                    page,
                    current_type,
                    current_candidate,
                    starts_new_document=True,
                    parser_name=self.name,
                )
            elif separator_type:
                current_type = separator_type
                outcome.documents_found += 1
                assign_page(
                    page,
                    current_type,
                    current_candidate,
                    starts_new_document=True,
                    parser_name=self.name,
                    reason=f"“{separator_type}” separator page",
                )
                self._mark_separator(page, current_type, separator_policy)
            elif current_type is not None:
                if current_type == APPLICATION_REPORT:
                    # A later report page can carry a field the subject-line
                    # page did not (a phone number, an applicant ID further
                    # down) -- folded in, but the name itself is never
                    # revisited, so an incidental Title Case phrase elsewhere
                    # in the report can never look like a second candidate.
                    current_candidate = self._absorb_contact_fields(
                        current_candidate, features
                    )
                assign_page(
                    page,
                    current_type,
                    current_candidate,
                    starts_new_document=False,
                    parser_name=self.name,
                )
            # else: nothing has opened a section yet (a cover page before the
            # first applicant). Left alone, so it falls back to whatever
            # default review flagging an unclassified page already gets.

        return outcome

    # ------------------------------------------------------------------
    def _is_new_applicant(
        self,
        features: PageFeatures,
        subject_name: str | None,
        current_candidate: Candidate,
        opened_first_applicant: bool,
    ) -> bool:
        if subject_name:
            if not current_candidate.name:
                return True
            return normalize_person_name(subject_name) != normalize_person_name(
                current_candidate.name
            )
        # No explicit subject line on this page. A system-generated header
        # with no name yet known still opens the very first packet -- a
        # report whose applicant line falls on a later page must not be left
        # to the generic fallback -- but only ever the first packet: an
        # applicant whose own report carried no identity at all must not
        # re-trigger "new applicant" on its own later pages just because
        # `current_candidate` is still empty.
        return not opened_first_applicant and is_ats_generated_page(features)

    def _extract_report_candidate(
        self, features: PageFeatures, subject_name: str | None
    ) -> Candidate:
        candidate = self.metadata.extract(features, document_type=APPLICATION_REPORT)
        if subject_name and not candidate.name:
            candidate.name = subject_name
        return candidate

    def _absorb_contact_fields(
        self, current: Candidate, features: PageFeatures
    ) -> Candidate:
        """Fill in contact details a later report page reveals.

        Deliberately never touches ``name``: the subject line is the
        authority for identity, and letting a later page's own name guess
        compete with it risks flagging a false conflict on a page that is, in
        fact, unambiguous.
        """
        extra = self.metadata.extract(features, document_type=APPLICATION_REPORT)
        return Candidate(
            name=current.name,
            email=current.email or extra.email,
            phone=current.phone or extra.phone,
            linkedin=current.linkedin or extra.linkedin,
            job_title=current.job_title or extra.job_title,
            applicant_id=current.applicant_id or extra.applicant_id,
            conflicting_names=list(current.conflicting_names),
        )

    def _mark_separator(
        self, page: PageAnalysis, document_type: str, separator_policy: SeparatorPolicy
    ) -> None:
        page.separator_label = document_type
        page.separator_state = _separator_state(separator_policy)
        if page.separator_state is SeparatorState.UNDECIDED:
            page.add_review_reason("Separator page - decide whether to keep it")


def _separator_state(policy: SeparatorPolicy) -> SeparatorState:
    if policy is SeparatorPolicy.EXCLUDE:
        return SeparatorState.EXCLUDED
    if policy is SeparatorPolicy.ASK:
        return SeparatorState.UNDECIDED
    return SeparatorState.INCLUDED


__all__ = ["UCSeparatorExportParser"]
