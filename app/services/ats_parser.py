"""Backwards-compatible entry point for the separator-page ATS parser.

Structured parsing now lives in :mod:`app.services.parsers`, where each
recognised format is its own parser and a registry picks between them. This
module stays as the original single-parser interface, so existing callers and
tests keep working unchanged; new code should use
:func:`app.services.parsers.build_default_registry` instead, which also reaches
the PageUp and submitted-packet formats.
"""

from __future__ import annotations

from app.profiles.base import DocumentProfile
from app.services.metadata_service import MetadataExtractor
from app.services.parsers.base import DETERMINISTIC_CONFIDENCE
from app.services.parsers.uc_separator import UCSeparatorExportParser
from app.services.text_features import PageFeatures

#: Retained under its original name; the value lives in ``parsers.base`` now.
CONFIDENCE = DETERMINISTIC_CONFIDENCE


class AtsReportParser(UCSeparatorExportParser):
    """The separator-page ATS parser, under its original name and interface."""

    def __init__(
        self,
        profile: DocumentProfile,
        metadata_extractor: MetadataExtractor | None = None,
    ) -> None:
        super().__init__(profile, metadata_extractor)

    def looks_like_ats_export(self, features_list: list[PageFeatures]) -> bool:
        """Whether this file is a separator-page ATS report export."""
        return self.can_parse(features_list).matched


__all__ = ["AtsReportParser", "CONFIDENCE"]
