"""The analysis pipeline.

One PDF at a time, one page at a time, in the order the specification lays out:
open -> validate -> extract text -> OCR if needed -> features -> rules ->
optional AI -> classification confidence -> boundary -> boundary confidence ->
identity -> grouping -> review flags.

Every stage is a separate, individually testable service; this module only
sequences them and keeps a failure in one file from taking down a batch.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from app.intelligence.base import PageContext
from app.models.candidate import Candidate
from app.models.enums import (
    ClassificationSource,
    FileStatus,
    SeparatorPolicy,
    SeparatorState,
    TextSource,
)
from app.models.page import PageAnalysis
from app.models.processing_job import ProcessingJob
from app.models.source_file import SourceFileAnalysis
from app.profiles.base import DocumentProfile
from app.services import pdf_service
from app.services.anchor_scan import AnchorScanner
from app.services.ats_parser import AtsReportParser
from app.services.classification_service import ClassificationService
from app.services.parsers.registry import ATSParserRegistry
from app.services.confidence import ConfidenceThresholds
from app.services.grouping_service import GroupingService
from app.services.metadata_service import MetadataExtractor
from app.services.ocr_service import OCRService
from app.services.packet_service import CandidatePacketService
from app.services.pdf_service import (
    OCR_DPI,
    PdfEncryptedError,
    PdfError,
    extract_page_text,
    open_pdf,
)
from app.services.text_features import PageFeatures, extract_features
from app.utils.hashing import hash_file
from app.utils.logging_setup import get_logger, log_event

logger = get_logger("pipeline")

#: How much neighbouring text an AI provider is given for context.
_CONTEXT_TAIL_CHARS = 600
_CONTEXT_HEAD_CHARS = 600

#: Confidence attached to a boundary the whole-file anchor pass decided. Below
#: a deterministic parser's -- the structure was inferred, not stated -- but
#: well above a page-by-page guess, which is the point of looking at the file
#: as a whole.
_ANCHOR_STRUCTURE_CONFIDENCE = 0.90
#: How decisive the anchor evidence must be to overrule the page-level call.
#: Set so one strong anchor wins and an accumulation of weak hints does not.
_ANCHOR_OVERRIDE_MARGIN = 4.0


class CancellationToken:
    """Cooperative cancellation shared between the UI and worker threads.

    Deliberately does not define ``__bool__``: a truthiness test on a live
    token reads as "is it cancelled?", which would make the common
    ``token or CancellationToken()`` idiom silently throw the caller's token
    away and ignore cancellation entirely.
    """

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled


@dataclass
class ProgressUpdate:
    """A single progress tick, safe to hand straight to the UI."""

    file_path: Path
    file_index: int
    file_total: int
    page_index: int = 0
    page_count: int = 0
    operation: str = ""
    status: FileStatus = FileStatus.ANALYZING

    @property
    def overall_fraction(self) -> float:
        """Progress across the whole batch in [0, 1]."""
        if self.file_total <= 0:
            return 0.0
        page_fraction = (
            (self.page_index / self.page_count) if self.page_count else 0.0
        )
        return min(1.0, (self.file_index + page_fraction) / self.file_total)


ProgressCallback = Callable[[ProgressUpdate], None]
FileCompleteCallback = Callable[[SourceFileAnalysis], None]


@dataclass
class PipelineContext:
    """Rolling state carried from one page to the next within a PDF."""

    previous_features: object | None = None
    previous_type: str | None = None
    previous_confidence: float = 0.0
    previous_candidate: Candidate = field(default_factory=Candidate)
    previous_group_type: str | None = None
    previous_group_page_count: int = 0
    previous_separator_type: str | None = None


class ProcessingPipeline:
    """Analyses PDFs into reviewable document groups."""

    def __init__(
        self,
        profile: DocumentProfile,
        classification_service: ClassificationService,
        ocr_service: OCRService,
        grouping_service: GroupingService,
        *,
        thresholds: ConfidenceThresholds | None = None,
        separator_policy: SeparatorPolicy = SeparatorPolicy.INCLUDE,
        metadata_extractor: MetadataExtractor | None = None,
        compute_hashes: bool = True,
        packet_service: CandidatePacketService | None = None,
        ats_parser: AtsReportParser | None = None,
        parser_registry: ATSParserRegistry | None = None,
        anchor_scanner: AnchorScanner | None = None,
    ) -> None:
        self.profile = profile
        self.classification = classification_service
        self.ocr = ocr_service
        self.grouping = grouping_service
        self.thresholds = thresholds or ConfidenceThresholds()
        self.packets = packet_service or CandidatePacketService(profile, self.thresholds)
        self.separator_policy = separator_policy
        self.metadata = metadata_extractor or MetadataExtractor()
        self.compute_hashes = compute_hashes
        #: Tier A of the classification priority order: a file matching a
        #: recognised format is parsed deterministically, bypassing the rules
        #: classifier and any AI escalation entirely. ``parser_registry``
        #: reaches every known format; ``ats_parser`` is the original
        #: single-parser argument, kept working by wrapping it in a registry
        #: of one. With neither, every page goes through the generic pipeline.
        self.parser_registry = parser_registry or (
            ATSParserRegistry([ats_parser]) if ats_parser is not None else None
        )
        self.ats_parser = ats_parser
        #: Tier B's structural pass: a cheap whole-file scan for the places a
        #: document's start is stated rather than inferred, run before the
        #: page-by-page classifier so a long file's interior pages are judged
        #: against structure instead of appearance.
        self.anchors = anchor_scanner or AnchorScanner(profile)
        #: Set per file by :meth:`_try_structured_parse` so callers can report
        #: which parser handled it and what it warned about.
        self.last_parse_outcome = None
        self.ocr_unavailable_warning: str | None = None

    # ------------------------------------------------------------------
    def analyze_files(
        self,
        paths: Sequence[Path | str],
        *,
        job: ProcessingJob | None = None,
        on_progress: ProgressCallback | None = None,
        on_file_complete: FileCompleteCallback | None = None,
        token: CancellationToken | None = None,
    ) -> list[SourceFileAnalysis]:
        """Analyse a batch. One bad PDF is recorded and the batch continues."""
        token = CancellationToken() if token is None else token
        results: list[SourceFileAnalysis] = []
        total = len(paths)

        for index, path in enumerate(paths):
            if token.is_cancelled:
                break
            analysis = self.analyze_file(
                path,
                file_index=index,
                file_total=total,
                job=job,
                on_progress=on_progress,
                token=token,
            )
            results.append(analysis)
            if on_file_complete is not None:
                on_file_complete(analysis)

        return results

    # ------------------------------------------------------------------
    def analyze_file(
        self,
        path: Path | str,
        *,
        file_index: int = 0,
        file_total: int = 1,
        job: ProcessingJob | None = None,
        on_progress: ProgressCallback | None = None,
        token: CancellationToken | None = None,
        password: str | None = None,
    ) -> SourceFileAnalysis:
        """Analyse one PDF into pages and document groups."""
        token = CancellationToken() if token is None else token
        source = Path(path)
        analysis = SourceFileAnalysis(path=source, status=FileStatus.READING)
        started = time.monotonic()

        def emit(operation: str, page_index: int = 0, status: FileStatus = FileStatus.ANALYZING) -> None:
            if on_progress is None:
                return
            on_progress(
                ProgressUpdate(
                    file_path=source,
                    file_index=file_index,
                    file_total=file_total,
                    page_index=page_index,
                    page_count=analysis.page_count,
                    operation=operation,
                    status=status,
                )
            )

        try:
            emit("Opening", status=FileStatus.READING)
            if self.compute_hashes:
                try:
                    analysis.content_hash = hash_file(source)
                except OSError as exc:
                    logger.warning("Could not hash %s: %s", source.name, exc)

            with open_pdf(source, password) as document:
                analysis.page_count = document.page_count
                log_event(
                    logger,
                    "pdf.opened",
                    file=source.name,
                    pages=analysis.page_count,
                )

                if analysis.page_count == 0:
                    raise PdfError(f"{source.name} contains no pages.", path=source)

                pages = self._analyze_pages(document, analysis, emit, token)
                analysis.pages = pages

            if token.is_cancelled:
                analysis.status = FileStatus.WAITING
                return analysis

            analysis.groups = self.grouping.build_groups(analysis.pages, str(source))
            # Reconstructing applicant packets is a separate pass over the
            # finished documents, never mixed into grouping: one 80-page file
            # holds many applicants and attribution needs the whole picture.
            analysis.packets = self.packets.build_packets(analysis.groups, str(source))
            analysis.status = (
                FileStatus.REVIEW_NEEDED if analysis.review_group_count else FileStatus.READY
            )

        except PdfEncryptedError as exc:
            analysis.encrypted = True
            self._fail(analysis, exc.message, job)
        except PdfError as exc:
            self._fail(analysis, exc.message, job)
        except MemoryError:
            self._fail(
                analysis,
                f"{source.name} is too large to process on this computer.",
                job,
            )
        except OSError as exc:
            self._fail(analysis, f"{source.name} could not be read: {exc.strerror or exc}", job)
        except Exception as exc:  # last-resort guard: never kill the batch
            logger.exception("Unexpected failure analysing %s", source.name)
            self._fail(analysis, f"{source.name} could not be processed ({type(exc).__name__}).", job)

        analysis.analysis_seconds = round(time.monotonic() - started, 3)

        if job is not None and analysis.status is not FileStatus.ERROR:
            job.pdfs_processed += 1
            job.pages_processed += len(analysis.pages)
            job.documents_found += len(analysis.groups)
            job.review_documents += analysis.review_group_count
            job.ocr_pages += analysis.ocr_pages

        log_event(
            logger,
            "pdf.analyzed",
            file=source.name,
            pages=len(analysis.pages),
            documents=len(analysis.groups),
            review=analysis.review_group_count,
            status=analysis.status.value,
            seconds=analysis.analysis_seconds,
        )
        return analysis

    # ------------------------------------------------------------------
    def _analyze_pages(
        self,
        document,
        analysis: SourceFileAnalysis,
        emit: Callable[..., None],
        token: CancellationToken,
    ) -> list[PageAnalysis]:
        """Extract every page's text, then decide how to classify the file.

        Text and OCR extraction happens once, up front, for every page --
        that work is needed regardless of which classification tier handles
        the file. Whether a known ATS export structure applies is a
        whole-file question (a separator on page 40 still has to be seen
        before page 1 can be trusted to be an application report), so it can
        only be answered once every page's features exist.
        """
        pages: list[PageAnalysis] = []
        features_list: list[PageFeatures | None] = []
        page_count = analysis.page_count

        for page_index in range(page_count):
            if token.is_cancelled:
                break

            emit("Reading page", page_index, FileStatus.READING)
            page = PageAnalysis(
                source_pdf=str(analysis.path),
                page_index=page_index,
                page_count=page_count,
            )
            features: PageFeatures | None = None

            try:
                text, source_kind, ocr_used, ocr_failed = self._page_text(
                    document, page_index, emit
                )
                page.extracted_text = text
                page.text_source = source_kind
                page.char_count = len(text.strip())
                page.ocr_used = ocr_used
                page.ocr_failed = ocr_failed
                if ocr_used:
                    analysis.ocr_pages += 1
                else:
                    analysis.native_text_pages += 1
                if ocr_failed:
                    analysis.ocr_failures += 1

                features = extract_features(text)
            except Exception as exc:  # one bad page must not lose the file
                logger.warning("Page %s of %s failed: %s", page_index + 1, analysis.name, exc)
                page.error = f"This page could not be analyzed ({type(exc).__name__})."
                page.add_review_reason(page.error)
                page.starts_new_document = bool(page_index == 0)
                page.boundary_confidence = 0.5

            pages.append(page)
            features_list.append(features)

        if token.is_cancelled:
            return pages

        if not self._try_structured_parse(pages, features_list, analysis, emit):
            self._classify_pages_generically(document, pages, features_list, analysis, emit)

        return pages

    # ------------------------------------------------------------------
    def _try_structured_parse(
        self,
        pages: list[PageAnalysis],
        features_list: list[PageFeatures | None],
        analysis: SourceFileAnalysis,
        emit: Callable[..., None],
    ) -> bool:
        """Tier A: deterministic parse when the file matches a known format.

        Returns whether it applied. When it does not, every page is left
        exactly as it was so the generic pipeline (tiers B/C/D) runs as usual.
        """
        self.last_parse_outcome = None
        if self.parser_registry is None:
            return False
        known_features = [f for f in features_list if f is not None]
        if not known_features:
            return False

        emit("Analyzing page", max(len(pages) - 1, 0), FileStatus.ANALYZING)
        placeholder = extract_features("")
        safe_features = [f if f is not None else placeholder for f in features_list]
        outcome = self.parser_registry.parse(
            pages, safe_features, separator_policy=self.separator_policy
        )
        if outcome is None:
            return False

        self.last_parse_outcome = outcome
        analysis.parser_name = outcome.parser
        analysis.structure_confidence = outcome.structure_confidence
        analysis.parser_warnings = list(outcome.warnings)
        return True

    def _classify_pages_generically(
        self,
        document,
        pages: list[PageAnalysis],
        features_list: list[PageFeatures | None],
        analysis: SourceFileAnalysis,
        emit: Callable[..., None],
    ) -> None:
        """Tiers B/C/D: an anchor-first structural pass, then the classifier.

        The anchor pass runs first and looks at the whole file, so a long
        document's bland interior page is judged against where the file says
        documents begin rather than against how that page happens to look. It
        proposes boundaries only; the classifier still decides types.
        """
        state = PipelineContext()
        needs_ai_context = self.classification.ai_enabled

        known = [f for f in features_list if f is not None]
        scan = self.anchors.scan(known) if (self.anchors and known) else None
        if scan is not None:
            analysis.structure_confidence = max(
                analysis.structure_confidence, _ANCHOR_STRUCTURE_CONFIDENCE
            )

        for page, features in zip(pages, features_list):
            if page.error or features is None:
                continue
            emit("Analyzing page", page.page_index, FileStatus.ANALYZING)
            try:
                self._classify_and_group(
                    document, page, features, state, analysis, needs_ai_context
                )
                if scan is not None:
                    self._apply_anchor(page, scan)
            except Exception as exc:  # one bad page must not lose the file
                logger.warning(
                    "Page %s of %s failed: %s", page.page_index + 1, analysis.name, exc
                )
                page.error = f"This page could not be analyzed ({type(exc).__name__})."
                page.add_review_reason(page.error)
                page.starts_new_document = bool(page.page_index == 0)
                page.boundary_confidence = 0.5

    def _apply_anchor(self, page: PageAnalysis, scan) -> None:
        """Let whole-file structure override a weak page-level boundary call.

        Only where the two disagree *and* the anchor evidence is strong. A page
        that plainly opens a document -- a restarted page count, a title, a
        salutation -- should not be swallowed into the previous one because its
        wording was unremarkable; and a page explicitly marked "page 3 of 5"
        should not start a document because its layout shifted.
        """
        anchors = next(
            (item for item in scan.pages if item.page_index == page.page_index), None
        )
        if anchors is None or page.page_index == 0:
            return

        opens = anchors.opens_document
        if opens == page.starts_new_document:
            # Agreement: the structure corroborates the page, so trust it more.
            if opens or anchors.score <= -_ANCHOR_OVERRIDE_MARGIN:
                page.boundary_confidence = max(
                    page.boundary_confidence, _ANCHOR_STRUCTURE_CONFIDENCE
                )
            return

        if abs(anchors.score) < _ANCHOR_OVERRIDE_MARGIN:
            return

        page.starts_new_document = opens
        page.boundary_confidence = _ANCHOR_STRUCTURE_CONFIDENCE
        page.boundary_reasons = [
            "Whole-file structure: " + reason for reason in anchors.reasons[:4]
        ]

    # ------------------------------------------------------------------
    def _page_text(
        self, document, page_index: int, emit: Callable[..., None]
    ) -> tuple[str, TextSource, bool, bool]:
        """Native text, falling back to OCR only when there is too little."""
        native = extract_page_text(document, page_index)
        if not self.ocr.should_ocr(native):
            return native, TextSource.NATIVE, False, False

        if not pdf_service.page_has_visual_content(document, page_index):
            # A blank page: no text layer, nothing drawn on it. OCR would
            # start a process to read an empty sheet. Common inside generated
            # application forms, and the reason a native-text file could still
            # rack up OCR launches.
            source = TextSource.NATIVE if native.strip() else TextSource.NONE
            return native, source, False, False

        availability = self.ocr.availability()
        if not availability.available:
            if self.ocr_unavailable_warning is None and self.ocr.enabled:
                self.ocr_unavailable_warning = availability.message
            source = TextSource.NATIVE if native.strip() else TextSource.NONE
            return native, source, False, False

        emit("Running OCR", page_index, FileStatus.OCR)
        image = pdf_service.render_page_png(
            document, page_index, dpi=OCR_DPI, grayscale=True
        )
        recognized = self.ocr.recognize(image) if image else ""

        if recognized.strip():
            if not native.strip():
                return recognized, TextSource.OCR, True, False
            # Rendering a page draws its native text too, so OCR output normally
            # already contains whatever little native text there was.
            # Concatenating blindly duplicates it ("RESUME" -> "RESUME RESUME"),
            # which corrupts classification -- separator pages stop being
            # recognised, and keyword counts double.
            if _ocr_supersedes_native(native, recognized):
                return recognized, TextSource.MIXED, True, False
            return f"{native}\n{recognized}", TextSource.MIXED, True, False

        source = TextSource.NATIVE if native.strip() else TextSource.NONE
        return native, source, False, True

    # ------------------------------------------------------------------
    def _classify_and_group(
        self,
        document,
        page: PageAnalysis,
        features,
        state: PipelineContext,
        analysis: SourceFileAnalysis,
        needs_ai_context: bool,
    ) -> None:
        """Run classification, identity extraction and boundary detection."""
        separator_type = self.profile.separator_type_for(features)

        next_head = ""
        if needs_ai_context and page.page_index + 1 < page.page_count:
            next_head = extract_page_text(document, page.page_index + 1)[:_CONTEXT_HEAD_CHARS]

        # A preliminary type lets metadata extraction know whether the contact
        # details on this page belong to the applicant or to their referees.
        preliminary = self.classification.rules.classify_page(
            PageContext(
                source_pdf=str(analysis.path),
                page_index=page.page_index,
                page_count=page.page_count,
                text=page.extracted_text,
                features=features,
            )
        )
        candidate = self.metadata.extract(
            features, document_type=preliminary.document_type
        )

        context = PageContext(
            source_pdf=str(analysis.path),
            page_index=page.page_index,
            page_count=page.page_count,
            text=page.extracted_text,
            features=features,
            previous_type=state.previous_type,
            previous_confidence=state.previous_confidence,
            previous_text_tail=_tail(state.previous_features),
            next_text_head=next_head,
            previous_features=state.previous_features,
            previous_group_type=state.previous_group_type,
            previous_group_page_count=state.previous_group_page_count,
            previous_separator_type=state.previous_separator_type,
            candidate=candidate,
            previous_candidate=state.previous_candidate,
            document_types=tuple(self.profile.document_types),
            profile_name=self.profile.name,
        )

        insight = self.classification.analyze_page(context)

        page.predicted_type = insight.classification.document_type
        page.classification_confidence = insight.classification.confidence
        page.classification_source = (
            ClassificationSource.AI_ASSISTED if insight.used_ai else ClassificationSource.RULES
        )
        page.type_scores = insight.classification.scores
        page.reasoning_summary = insight.reasoning
        page.ai_used = insight.used_ai
        page.starts_new_document = insight.boundary.starts_new_document
        page.boundary_confidence = insight.boundary.confidence
        page.boundary_reasons = insight.boundary.reasons
        page.candidate = insight.candidate.merged_with(candidate) if insight.used_ai else candidate

        if separator_type:
            page.separator_label = separator_type
            page.separator_state = _separator_state(self.separator_policy)
            if page.separator_state is SeparatorState.UNDECIDED:
                page.add_review_reason("Separator page - decide whether to keep it")

        if self.thresholds.requires_review(page.classification_confidence):
            page.add_review_reason("Low document-type confidence")
        if self.thresholds.requires_review(page.boundary_confidence):
            page.add_review_reason("Low page-grouping confidence")
        if page.text_source is TextSource.NONE:
            page.add_review_reason("No readable text on this page")

        # Roll state forward for the next page.
        state.previous_features = features
        state.previous_type = page.predicted_type
        state.previous_confidence = page.classification_confidence
        state.previous_separator_type = separator_type
        state.previous_candidate = (
            page.candidate if not page.candidate.is_empty else state.previous_candidate
        )
        if page.starts_new_document:
            state.previous_group_type = page.predicted_type
            state.previous_group_page_count = 1
        else:
            state.previous_group_page_count += 1

    # ------------------------------------------------------------------
    def _fail(self, analysis: SourceFileAnalysis, message: str, job: ProcessingJob | None) -> None:
        analysis.status = FileStatus.ERROR
        analysis.error = message
        logger.warning("Analysis failed: %s", message)
        if job is not None:
            job.add_error(analysis.path, message)


#: Fraction of native words that must appear in the OCR text for it to be
#: treated as already containing them.
_OCR_COVERAGE_THRESHOLD = 0.8


def _ocr_supersedes_native(native: str, recognized: str) -> bool:
    """True when the OCR result already covers the native text.

    Compared on normalised word sets rather than exact strings, because OCR
    reproduces layout and punctuation differently even when it reads the same
    words correctly.
    """
    native_words = {word for word in _normalise_words(native) if len(word) > 1}
    if not native_words:
        return True
    recognized_words = set(_normalise_words(recognized))
    covered = len(native_words & recognized_words) / len(native_words)
    return covered >= _OCR_COVERAGE_THRESHOLD


def _normalise_words(text: str) -> list[str]:
    return [
        "".join(character for character in word if character.isalnum())
        for word in (text or "").lower().split()
    ]


def _tail(features) -> str:
    if features is None:
        return ""
    return getattr(features, "text", "")[-_CONTEXT_TAIL_CHARS:]


def _separator_state(policy: SeparatorPolicy) -> SeparatorState:
    if policy is SeparatorPolicy.EXCLUDE:
        return SeparatorState.EXCLUDED
    if policy is SeparatorPolicy.ASK:
        return SeparatorState.UNDECIDED
    return SeparatorState.INCLUDED


def mark_duplicates(
    files: Iterable[SourceFileAnalysis], known_hashes: dict[str, str]
) -> list[SourceFileAnalysis]:
    """Flag files whose content hash was seen in a previous job.

    Matching is on content, never on filename, and a duplicate is never blocked
    from processing -- the user is simply told.
    """
    flagged: list[SourceFileAnalysis] = []
    for item in files:
        if item.content_hash and item.content_hash in known_hashes:
            item.duplicate_of = known_hashes[item.content_hash]
            flagged.append(item)
    return flagged


__all__ = [
    "ProcessingPipeline",
    "ProgressUpdate",
    "CancellationToken",
    "mark_duplicates",
]
