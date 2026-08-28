"""Background export worker: splitting and writing PDFs off the UI thread."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QThread, Signal

from app.models.processing_job import ProcessingJob
from app.models.source_file import SourceFileAnalysis
from app.services.confidence import ConfidenceThresholds
from app.services.excel_service import write_document_index
from app.services.export_service import ExportResult, ExportService
from app.services.processing_service import CancellationToken
from app.utils.logging_setup import get_logger

logger = get_logger("worker.export")


class ExportWorker(QThread):
    """Writes reviewed documents to disk and optionally builds the Excel index."""

    #: (completed, total, current file name)
    progressed = Signal(int, int, str)
    #: (ExportResult, ProcessingJob)
    finished_export = Signal(object, object)
    failed = Signal(str)

    def __init__(
        self,
        export_service: ExportService,
        files: Sequence[SourceFileAnalysis],
        output_directory: Path,
        *,
        job: ProcessingJob,
        create_excel_index: bool = True,
        thresholds: ConfidenceThresholds | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._service = export_service
        self._files = list(files)
        self._output = Path(output_directory)
        self._job = job
        self._create_index = create_excel_index
        self._thresholds = thresholds or ConfidenceThresholds()
        self._token = CancellationToken()

    def cancel(self) -> None:
        self._token.cancel()

    @property
    def is_cancelled(self) -> bool:
        return self._token.is_cancelled

    def run(self) -> None:  # noqa: D102 - QThread entry point
        try:
            result: ExportResult = self._service.export(
                self._files,
                self._output,
                job=self._job,
                on_progress=lambda done, total, name: self.progressed.emit(done, total, name),
                token=self._token,
            )
        except Exception as exc:  # pragma: no cover - defensive backstop
            logger.exception("Export worker crashed")
            self.failed.emit(
                f"Export stopped unexpectedly ({type(exc).__name__}). "
                "The application log has the details."
            )
            return

        if self._create_index and result.exported and not result.cancelled:
            # The run's own folder, not the base directory the user picked:
            # the index describes this batch, so it belongs beside it.
            index_path = write_document_index(
                result.exported,
                result.output_directory,
                thresholds=self._thresholds,
                packets=[packet for file in self._files for packet in file.packets],
            )
            if index_path is not None:
                self._job.excel_index_path = str(index_path)

        self.finished_export.emit(result, self._job)


__all__ = ["ExportWorker"]
