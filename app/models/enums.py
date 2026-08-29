"""Enumerations shared across the AS Resume Sorter domain model.

Document *types* deliberately are not an enum: they are declared by the active
:class:`~app.profiles.base.DocumentProfile` so new profiles (Accounting, Legal,
Medical...) can introduce their own types without touching the core models.
"""

from __future__ import annotations

from enum import Enum


class TextSource(str, Enum):
    """Where the text of a page came from."""

    NATIVE = "native"
    OCR = "ocr"
    MIXED = "mixed"
    NONE = "none"


class ConfidenceBand(str, Enum):
    """Review band a confidence value falls into."""

    HIGH = "high"
    REVIEW_SUGGESTED = "review_suggested"
    REVIEW_REQUIRED = "review_required"

    @property
    def label(self) -> str:
        return {
            ConfidenceBand.HIGH: "High confidence",
            ConfidenceBand.REVIEW_SUGGESTED: "Review suggested",
            ConfidenceBand.REVIEW_REQUIRED: "Review required",
        }[self]


class SeparatorPolicy(str, Enum):
    """What to do with pages that only carry a document label."""

    INCLUDE = "include"
    EXCLUDE = "exclude"
    ASK = "ask"

    @property
    def label(self) -> str:
        return {
            SeparatorPolicy.INCLUDE: "Include separator page in the document",
            SeparatorPolicy.EXCLUDE: "Exclude separator page from output",
            SeparatorPolicy.ASK: "Ask me during review",
        }[self]


class SeparatorState(str, Enum):
    """Per-page separator decision, overridable by the user in Review."""

    NOT_SEPARATOR = "not_separator"
    INCLUDED = "included"
    EXCLUDED = "excluded"
    UNDECIDED = "undecided"


class FileStatus(str, Enum):
    """Lifecycle of a single source PDF inside the queue."""

    WAITING = "Waiting"
    READING = "Reading"
    OCR = "OCR"
    ANALYZING = "Analyzing"
    REVIEW_NEEDED = "Review Needed"
    READY = "Ready"
    EXPORTING = "Exporting"
    COMPLETED = "Completed"
    ERROR = "Error"

    @property
    def is_terminal(self) -> bool:
        return self in (FileStatus.COMPLETED, FileStatus.ERROR)


class ClassificationSource(str, Enum):
    """Which subsystem produced a classification decision."""

    RULES = "rules"
    AI = "ai"
    AI_ASSISTED = "ai_assisted"
    MANUAL = "manual"
    INHERITED = "inherited"
    #: Assigned by the deterministic ATS report parser (Tier A) rather than
    #: scored page by page -- exact by construction, not a probabilistic guess.
    DETERMINISTIC = "deterministic"


class ProviderKind(str, Enum):
    """Available document-intelligence providers."""

    RULES = "rules"
    OPENAI = "openai"
    OLLAMA = "ollama"

    @property
    def label(self) -> str:
        return {
            ProviderKind.RULES: "Rules Only",
            ProviderKind.OPENAI: "OpenAI",
            ProviderKind.OLLAMA: "Ollama",
        }[self]

    @property
    def is_external(self) -> bool:
        """True when using the provider transmits text off the machine."""
        return self is ProviderKind.OPENAI


class JobStatus(str, Enum):
    """Lifecycle of a processing job."""

    PENDING = "pending"
    ANALYZING = "analyzing"
    ANALYZED = "analyzed"
    EXPORTING = "exporting"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


__all__ = [
    "TextSource",
    "ConfidenceBand",
    "SeparatorPolicy",
    "SeparatorState",
    "FileStatus",
    "ClassificationSource",
    "ProviderKind",
    "JobStatus",
]
