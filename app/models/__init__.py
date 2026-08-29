"""Typed domain models for AS Resume Sorter."""

from .candidate import Candidate, normalize_person_name
from .document import DocumentGroup, apply_separator_policy_to_group
from .enums import (
    ClassificationSource,
    ConfidenceBand,
    FileStatus,
    JobStatus,
    ProviderKind,
    SeparatorPolicy,
    SeparatorState,
    TextSource,
)
from .page import PageAnalysis
from .processing_job import JobError, ProcessingJob
from .source_file import SourceFileAnalysis, unique_display_names

__all__ = [
    "Candidate",
    "normalize_person_name",
    "DocumentGroup",
    "apply_separator_policy_to_group",
    "PageAnalysis",
    "SourceFileAnalysis",
    "unique_display_names",
    "ProcessingJob",
    "JobError",
    "ClassificationSource",
    "ConfidenceBand",
    "FileStatus",
    "JobStatus",
    "ProviderKind",
    "SeparatorPolicy",
    "SeparatorState",
    "TextSource",
]
