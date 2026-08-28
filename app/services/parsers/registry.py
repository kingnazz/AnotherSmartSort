"""The structured-parser registry.

Holds every parser that recognises a specific document format, asks each one
how strongly it claims a file, and hands the file to the single strongest
claimant -- or to nobody, which is the safe and common outcome.

Two rules matter more than the mechanism:

*One parser, once.* Every ``can_parse`` runs read-only, and only the winner's
``parse`` is ever called. A file is never partly parsed by a loser and then
re-parsed by someone else, so a parser that half-recognises a format cannot
leave damage behind.

*Falling through is a valid answer.* Below :data:`MIN_MATCH_CONFIDENCE`
nothing is chosen and the generic pipeline handles the file exactly as it did
before any of these parsers existed. Forcing the closest parser onto an
unfamiliar file would produce confident, wrong extractions -- much worse than
the generic path's honest uncertainty.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import SeparatorPolicy
from app.models.page import PageAnalysis
from app.profiles.base import DocumentProfile
from app.services.metadata_service import MetadataExtractor
from app.services.parsers.base import (
    MIN_MATCH_CONFIDENCE,
    ParseOutcome,
    ParserMatch,
    StructuredParser,
)
from app.services.parsers.pageup import PageUpBulkCompileParser
from app.services.parsers.submitted_packet import SubmittedApplicantPacketParser
from app.services.parsers.uc_separator import UCSeparatorExportParser
from app.services.text_features import PageFeatures
from app.utils.logging_setup import get_logger, log_event

logger = get_logger("parsers")


@dataclass(frozen=True)
class ParserSelection:
    """Which parser claimed a file, and how strongly."""

    parser: StructuredParser | None
    match: ParserMatch

    @property
    def matched(self) -> bool:
        return self.parser is not None and self.match.matched


class ATSParserRegistry:
    """Chooses and runs the one structured parser that fits a file."""

    def __init__(self, parsers: list[StructuredParser] | None = None) -> None:
        self._parsers: list[StructuredParser] = list(parsers or [])

    # ------------------------------------------------------------------
    @property
    def parsers(self) -> list[StructuredParser]:
        return list(self._parsers)

    def register(self, parser: StructuredParser) -> None:
        self._parsers.append(parser)

    def select(self, features_list: list[PageFeatures]) -> ParserSelection:
        """The strongest parser claiming this file, if any.

        Every parser is asked, not just until the first match: a file can look
        weakly like one format and strongly like another, and the strongest
        claim should win rather than whichever happened to be registered
        first.
        """
        best: StructuredParser | None = None
        best_match = ParserMatch.no()

        for parser in self._parsers:
            try:
                match = parser.can_parse(features_list)
            except Exception:  # a broken parser must never break analysis
                logger.exception("Parser %s failed while matching", parser.name)
                continue
            if match.confidence > best_match.confidence:
                best, best_match = parser, match

        if best is None or not best_match.matched:
            return ParserSelection(None, ParserMatch.no())
        return ParserSelection(best, best_match)

    def parse(
        self,
        pages: list[PageAnalysis],
        features_list: list[PageFeatures],
        *,
        separator_policy: SeparatorPolicy,
    ) -> ParseOutcome | None:
        """Run the winning parser, or return ``None`` to fall through."""
        selection = self.select(features_list)
        if not selection.matched or selection.parser is None:
            return None

        parser = selection.parser
        try:
            outcome = parser.parse(
                pages, features_list, separator_policy=separator_policy
            )
        except Exception:
            # A parser that throws mid-file may have left pages half-assigned.
            # Reset them so the generic pipeline starts from a clean slate
            # rather than inheriting a partial, misleading structure.
            logger.exception("Parser %s failed while parsing", parser.name)
            _reset(pages)
            return None

        outcome.structure_confidence = max(
            outcome.structure_confidence, selection.match.confidence
        )
        log_event(
            logger,
            "parser.selected",
            parser=parser.name,
            confidence=round(selection.match.confidence, 3),
            reason=selection.match.reason,
            documents=outcome.documents_found,
            warnings=len(outcome.warnings),
        )
        return outcome


def _reset(pages: list[PageAnalysis]) -> None:
    """Undo a failed parser's partial assignments."""
    from app.models.enums import ClassificationSource, SeparatorState

    for page in pages:
        if page.classification_source is not ClassificationSource.DETERMINISTIC:
            continue
        page.predicted_type = "Other"
        page.classification_confidence = 0.0
        page.classification_source = ClassificationSource.RULES
        page.boundary_confidence = 0.0
        page.boundary_reasons = []
        page.reasoning_summary = None
        page.separator_label = None
        page.separator_state = SeparatorState.NOT_SEPARATOR


def build_default_registry(
    profile: DocumentProfile,
    metadata_extractor: MetadataExtractor | None = None,
) -> ATSParserRegistry:
    """The registry the application ships with.

    Order here is only a tiebreak for equal confidence -- :meth:`select` asks
    every parser regardless -- but it is written strongest-signature-first so
    the list reads as the priority it represents.
    """
    metadata = metadata_extractor or MetadataExtractor()
    return ATSParserRegistry(
        [
            PageUpBulkCompileParser(profile),
            SubmittedApplicantPacketParser(profile, metadata),
            UCSeparatorExportParser(profile, metadata),
        ]
    )


__all__ = [
    "ATSParserRegistry",
    "ParserSelection",
    "build_default_registry",
    "MIN_MATCH_CONFIDENCE",
]
