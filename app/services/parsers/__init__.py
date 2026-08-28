"""Structured parsers for recognised, machine-generated document formats.

Each parser here handles one format whose structure is stated by the file
itself, so extraction is exact rather than inferred. The registry picks the
single strongest claimant per file, and falls through to the generic pipeline
when nothing matches confidently.

    ATSParserRegistry
    ├── PageUpBulkCompileParser        many applicants, one bulk compile
    ├── SubmittedApplicantPacketParser one applicant, form plus uploads
    ├── UCSeparatorExportParser        separator-page ATS report export
    └── (falls through to the generic structural pipeline)
"""

from app.services.parsers.base import (
    DETERMINISTIC_CONFIDENCE,
    MIN_MATCH_CONFIDENCE,
    ParseOutcome,
    ParserMatch,
    StructuredParser,
    assign_page,
)
from app.services.parsers.pageup import PageUpBulkCompileParser
from app.services.parsers.registry import (
    ATSParserRegistry,
    ParserSelection,
    build_default_registry,
)
from app.services.parsers.submitted_packet import SubmittedApplicantPacketParser
from app.services.parsers.uc_separator import UCSeparatorExportParser

__all__ = [
    "ATSParserRegistry",
    "ParserSelection",
    "build_default_registry",
    "StructuredParser",
    "ParserMatch",
    "ParseOutcome",
    "assign_page",
    "DETERMINISTIC_CONFIDENCE",
    "MIN_MATCH_CONFIDENCE",
    "PageUpBulkCompileParser",
    "SubmittedApplicantPacketParser",
    "UCSeparatorExportParser",
]
