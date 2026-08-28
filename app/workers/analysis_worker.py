"""Background analysis worker.

PDF parsing, OCR and AI calls all happen here, on a :class:`QThread`, so the UI
thread only ever receives signals. Cancellation is cooperative and leaves
already-analysed files intact.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QThread, Signal

from app.models.processing_job import ProcessingJob
from app.models.source_file import SourceFileAnalysis
from app.services.app_services import AnalysisServices
from app.services.processing_service import CancellationToken, ProgressUpdate
from app.utils.logging_setup import get_logger

logger = get_logger("worker.analysis")


class AnalysisWorker(QThread):
    """Analyses a batch of PDFs off the UI thread."""

    #: (file path, page number, page count, operation, overall fraction 0-1)
    progressed = Signal(str, int, int, str, float)
    #: One file finished (successfully or not).
    file_completed = Signal(object)
    #: The whole batch finished: (list[SourceFileAnalysis], ProcessingJob)
    finished_batch = Signal(object, object)
    #: A non-fatal problem worth surfacing (missing OCR, provider trouble).
    warned = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        services: AnalysisServices,
        paths: Sequence[Path],
        job: ProcessingJob | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._services = services
        self._paths = list(paths)
        self._job = job or ProcessingJob(inputs=[str(p) for p in paths])
        self._token = CancellationToken()

    # ------------------------------------------------------------------
    @property
    def job(self) -> ProcessingJob:
        return self._job

    def cancel(self) -> None:
        """Ask the worker to stop at the next page boundary."""
        self._token.cancel()

    @property
    def is_cancelled(self) -> bool:
        return self._token.is_cancelled

    # ------------------------------------------------------------------
    def run(self) -> None:  # noqa: D102 - QThread entry point
        try:
            results = self._services.pipeline.analyze_files(
                self._paths,
                job=self._job,
                on_progress=self._emit_progress,
                on_file_complete=self.file_completed.emit,
                token=self._token,
            )
        except Exception as exc:  # pragma: no cover - defensive backstop
            logger.exception("Analysis worker crashed")
            self.failed.emit(
                f"Analysis stopped unexpectedly ({type(exc).__name__}). "
                "The application log has the details."
            )
            return

        stats = self._services.classification.stats
        self._job.pages_classified_locally = stats.pages_local
        self._job.pages_classified_by_ai = stats.pages_ai
        self._job.ai_requests = stats.ai_requests

        warning = self._services.pipeline.ocr_unavailable_warning
        if warning:
            self.warned.emit(warning)
        for message in stats.errors[:3]:
            self.warned.emit(message)

        self.finished_batch.emit(results, self._job)

    def _emit_progress(self, update: ProgressUpdate) -> None:
        self.progressed.emit(
            str(update.file_path),
            update.page_index + 1,
            update.page_count,
            update.operation,
            update.overall_fraction,
        )


__all__ = ["AnalysisWorker"]
