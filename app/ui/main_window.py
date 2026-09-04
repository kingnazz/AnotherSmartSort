"""The main application window: home screen, review workspace, and job flow."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app import APP_NAME, APP_VERSION
from app.models.enums import FileStatus, JobStatus
from app.models.processing_job import ProcessingJob
from app.models.source_file import SourceFileAnalysis
from app.services.app_services import build_analysis_services, build_export_service
from app.services.export_service import ExportResult
from app.services.file_discovery import discover_pdfs
from app.services.processing_service import mark_duplicates
from app.storage.history_store import HistoryStore
from app.storage.settings_store import AppSettings, SettingsStore
from app.ui.history_dialog import HistoryDialog
from app.ui.review_view import ReviewView
from app.ui.settings_dialog import SettingsDialog
from app.ui.theme import apply_theme, palette_for
from app.ui.widgets.badges import SectionLabel
from app.ui.widgets.drop_zone import DropZone
from app.ui.widgets.extract_selector import ExtractSelector
from app.ui.widgets.queue_table import QueueTable
from app.utils.logging_setup import get_logger, log_file_path
from app.utils.paths import resource_path
from app.utils.system import format_duration, open_in_file_manager, plural
from app.workers.analysis_worker import AnalysisWorker
from app.workers.export_worker import ExportWorker

logger = get_logger("ui.main")

_HOME_PAGE = 0
_REVIEW_PAGE = 1


class MainWindow(QMainWindow):
    """Hosts the home screen and the review workspace."""

    def __init__(
        self,
        settings: AppSettings,
        settings_store: SettingsStore,
        history: HistoryStore | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.settings_store = settings_store
        self.history = history or HistoryStore()

        self._tokens = palette_for(settings.theme)
        self._services = build_analysis_services(settings, settings_store=settings_store)
        self._files: dict[str, SourceFileAnalysis] = {}
        self._pending_paths: list[Path] = []
        self._analysis_worker: AnalysisWorker | None = None
        self._export_worker: ExportWorker | None = None
        self._job: ProcessingJob | None = None
        #: Set by Sort & Save so the export that follows analysis runs
        #: straight through, with no extra click and no review-count prompt.
        self._auto_export_pending = False
        self._skip_review_confirmation = False

        self.setWindowTitle(APP_NAME)
        self._apply_initial_geometry()

        self._build_ui()
        self._install_shortcuts()
        self._restore_geometry()
        self._update_actions()
        self._update_provider_banner()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_header())

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_home_page())

        self.review_view = ReviewView(
            self._services.grouping,
            self._services.thresholds,
            self._tokens,
            list(self._services.profile.document_types),
            packets=self._services.packets,
        )
        self.review_view.back_requested.connect(lambda: self._show_page(_HOME_PAGE))
        self.review_view.export_requested.connect(self._start_export)
        self.review_view.documents_changed.connect(self._on_documents_changed)
        self.review_view.set_type_filter(list(self.settings.export_document_types))
        self._stack.addWidget(self.review_view)

        layout.addWidget(self._stack, 1)
        layout.addWidget(self._build_status_bar())
        self.setCentralWidget(central)

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setProperty("role", "header")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 12, 20, 12)
        layout.setSpacing(10)

        # The square mark, not the wordmark: this bar is ~44px tall, and the
        # wordmark sets the name over three lines. Scaled to fit, its type
        # would be a few pixels high. The mark survives being small; that is
        # what it is for.
        mark = QLabel()
        mark_path = Path(resource_path("assets", "icon.png"))
        if mark_path.exists():
            mark.setPixmap(
                QPixmap(str(mark_path)).scaled(
                    24, 24,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

        brand = QLabel(APP_NAME)
        brand.setProperty("role", "brand")

        self._provider_label = QLabel("")
        self._provider_label.setProperty("role", "caption")

        history_button = QPushButton("History")
        history_button.setProperty("variant", "subtle")
        history_button.clicked.connect(self._open_history)

        settings_button = QPushButton("Settings")
        settings_button.setProperty("variant", "subtle")
        settings_button.clicked.connect(self._open_settings)

        about_button = QPushButton("About")
        about_button.setProperty("variant", "subtle")
        about_button.clicked.connect(self._open_about)

        layout.addWidget(mark)
        layout.addWidget(brand)
        layout.addWidget(self._provider_label)
        layout.addStretch(1)
        layout.addWidget(about_button)
        layout.addWidget(history_button)
        layout.addWidget(settings_button)
        return header

    def _build_home_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(16)

        self.drop_zone = DropZone(self._tokens)
        self.drop_zone.paths_added.connect(self.add_paths)
        layout.addWidget(self.drop_zone)

        # Asked before the work starts, because "give me the resumes" is the
        # job, not an afterthought buried in Settings.
        self.extract_selector = ExtractSelector(
            list(self._services.profile.document_types),
            self._tokens,
            primary_types=list(self._services.profile.primary_document_types),
        )
        self.extract_selector.set_selection(list(self.settings.export_document_types))
        self.extract_selector.selection_changed.connect(self._on_extract_selection_changed)
        layout.addWidget(self.extract_selector)

        queue_header = QHBoxLayout()
        queue_header.setSpacing(10)
        queue_header.addWidget(SectionLabel("Queue"))

        self._queue_summary = QLabel("")
        self._queue_summary.setProperty("role", "caption")
        queue_header.addWidget(self._queue_summary)
        queue_header.addStretch(1)

        self._clear_button = QPushButton("Clear")
        self._clear_button.setProperty("variant", "subtle")
        self._clear_button.clicked.connect(self._clear_queue)
        queue_header.addWidget(self._clear_button)
        layout.addLayout(queue_header)

        self.queue_table = QueueTable(self._tokens, self._services.thresholds)
        self.queue_table.file_activated.connect(self._open_review_for)
        layout.addWidget(self.queue_table, 1)

        # Shown only while something is actually flagged: a permanent hint is
        # furniture, and the queue already has enough of it.
        self.review_hint_label = QLabel("")
        self.review_hint_label.setProperty("role", "caption")
        self.review_hint_label.setVisible(False)
        layout.addWidget(self.review_hint_label)

        self._empty_queue_label = QLabel(
            "No PDFs yet. Drop files above, or use Add PDFs to browse."
        )
        self._empty_queue_label.setProperty("role", "caption")
        self._empty_queue_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._empty_queue_label)

        # The primary action: analyze, split, classify, name and save, all in
        # one click. This is the everyday path -- put PDFs in, get sorted
        # documents out -- so it is the one button that always stands out.
        self.sort_save_button = QPushButton("Sort && Save")
        self.sort_save_button.setObjectName("sortSaveButton")
        self.sort_save_button.setProperty("variant", "accent")
        self.sort_save_button.setMinimumHeight(44)
        self.sort_save_button.setMinimumWidth(200)
        self.sort_save_button.setToolTip(
            "Analyze, split, classify, name and save everything in one step (Ctrl+Return)"
        )
        self.sort_save_button.clicked.connect(self._start_sort_and_save)
        sort_save_row = QHBoxLayout()
        sort_save_row.addWidget(self.sort_save_button)
        sort_save_row.addStretch(1)
        layout.addLayout(sort_save_row)

        # Everything below is advanced: analyzing and saving as two separate
        # steps, and opening the review workspace directly. None of it is
        # needed for the normal one-click path above.
        advanced = QHBoxLayout()
        advanced.setSpacing(10)

        advanced_label = QLabel("Advanced:")
        advanced_label.setProperty("role", "caption")

        self._review_button = QPushButton("Review documents")
        self._review_button.setProperty("variant", "subtle")
        self._review_button.setToolTip(
            "Open the review workspace. Double-click a Review Needed row to "
            "jump directly to that file's flagged item."
        )
        self._review_button.clicked.connect(lambda: self._show_page(_REVIEW_PAGE))

        self.analyze_button = QPushButton("Analyze only")
        self.analyze_button.setProperty("variant", "subtle")
        self.analyze_button.setToolTip("Analyze without saving anything yet (Ctrl+R)")
        self.analyze_button.clicked.connect(self._start_analysis)

        self.export_button = QPushButton("Split && Save")
        self.export_button.setProperty("variant", "subtle")
        self.export_button.setToolTip("Export every included document (Ctrl+E)")
        self.export_button.clicked.connect(self._start_export)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setProperty("variant", "danger")
        self.cancel_button.clicked.connect(self._cancel_current_job)
        self.cancel_button.setVisible(False)

        advanced.addWidget(advanced_label)
        advanced.addWidget(self._review_button)
        advanced.addWidget(self.analyze_button)
        advanced.addWidget(self.export_button)
        advanced.addStretch(1)
        advanced.addWidget(self.cancel_button)
        layout.addLayout(advanced)
        return page

    def _on_extract_selection_changed(self, document_types: list[str]) -> None:
        """Remember the choice, and point the whole workspace at it."""
        self.settings.export_document_types = list(document_types)
        self.settings_store.save(self.settings)
        self.review_view.set_type_filter(document_types)
        self._update_actions()

    def _build_status_bar(self) -> QWidget:
        bar = QFrame()
        bar.setProperty("role", "footer")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 8, 20, 8)
        layout.setSpacing(12)

        self._status_label = QLabel("Ready")
        self._status_label.setProperty("role", "caption")

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFixedWidth(220)
        self._progress.setVisible(False)

        layout.addWidget(self._status_label, 1)
        layout.addWidget(self._progress)
        return bar

    def _install_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+O"), self, self.drop_zone._browse_files)
        QShortcut(QKeySequence("Ctrl+Shift+O"), self, self.drop_zone._browse_folder)
        QShortcut(QKeySequence("Ctrl+Return"), self, self._start_sort_and_save)
        QShortcut(QKeySequence("Ctrl+R"), self, self._start_analysis)
        QShortcut(QKeySequence("Ctrl+E"), self, self._start_export)
        QShortcut(QKeySequence("Ctrl+,"), self, self._open_settings)
        QShortcut(QKeySequence("Ctrl+H"), self, self._open_history)
        QShortcut(QKeySequence("Escape"), self, self._on_escape)

    # ------------------------------------------------------------------
    # Queue management
    # ------------------------------------------------------------------
    def add_paths(self, paths: list[Path]) -> None:
        """Add files or folders to the queue, expanding folders to PDFs."""
        output_dir = Path(self.settings.output_directory)
        discovered = discover_pdfs(
            paths,
            include_subfolders=self.settings.include_subfolders,
            exclude_dirs=[output_dir],
        )
        if not discovered:
            self._set_status("No PDFs were found in what you added.")
            return

        added = 0
        for path in discovered:
            key = str(path)
            if key in self._files:
                continue
            analysis = SourceFileAnalysis(path=path, status=FileStatus.WAITING)
            try:
                analysis.page_count = 0
            except OSError:
                pass
            self._files[key] = analysis
            self.queue_table.upsert(analysis)
            added += 1

        self._check_duplicates()
        self._update_actions()
        self._set_status(
            f"Added {plural(added, 'PDF')}."
            if added
            else "Those PDFs are already in the queue."
        )

    def _check_duplicates(self) -> None:
        """Flag files whose content was processed in a previous job."""
        if not self.settings.warn_on_duplicates:
            return
        known = self.history.known_hashes()
        if not known:
            return
        pending = [f for f in self._files.values() if f.content_hash]
        flagged = mark_duplicates(pending, known)
        for analysis in flagged:
            self.queue_table.upsert(analysis)

    def _clear_queue(self) -> None:
        if self._busy:
            return
        self._files.clear()
        self.queue_table.clear_files()
        self.review_view.load([])
        self._update_actions()
        self._set_status("Queue cleared.")

    def _update_actions(self) -> None:
        has_files = bool(self._files)
        analyzed = [f for f in self._files.values() if f.is_analyzed]
        can_export = any(f.active_groups for f in analyzed)

        self.sort_save_button.setEnabled(has_files and not self._busy)
        self.analyze_button.setEnabled(has_files and not self._busy)
        self._clear_button.setEnabled(has_files and not self._busy)
        self._review_button.setEnabled(bool(analyzed) and not self._busy)
        self.export_button.setEnabled(can_export and not self._busy)
        self._empty_queue_label.setVisible(not has_files)
        self.queue_table.setVisible(has_files)

        hint = self.queue_table.review_hint()
        self.review_hint_label.setText(hint)
        self.review_hint_label.setVisible(has_files and bool(hint))

        if not has_files:
            self._queue_summary.setText("")
        else:
            review = sum(f.review_group_count for f in self._files.values())
            documents = sum(len(f.groups) for f in self._files.values())
            parts = [plural(len(self._files), "PDF")]
            if documents:
                parts.append(plural(documents, "document"))
            if review:
                parts.append(f"{review} need review")
            self._queue_summary.setText("  ·  ".join(parts))

    @property
    def _busy(self) -> bool:
        return bool(
            (self._analysis_worker and self._analysis_worker.isRunning())
            or (self._export_worker and self._export_worker.isRunning())
        )

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------
    def _start_sort_and_save(self) -> None:
        """The primary action: analyze, then save immediately, no extra click.

        Review remains available afterwards for whatever is still flagged --
        this does not wait on it, and does not ask about it either, since the
        whole point is that one click is the normal amount of work.
        """
        if self._busy or not self._files:
            return
        self._auto_export_pending = True
        self._start_analysis()

    def _start_analysis(self) -> None:
        if self._busy or not self._files:
            return

        pending = [f.path for f in self._files.values()]
        self._services = build_analysis_services(
            self.settings, settings_store=self.settings_store
        )
        self.review_view._grouping = self._services.grouping

        availability = self._services.provider_availability()
        if not availability.available:
            proceed = QMessageBox.question(
                self,
                "Intelligence provider",
                f"{availability.message}\n\nAnalyze using the built-in rules instead?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes,
            )
            if proceed is not QMessageBox.StandardButton.Yes:
                return
            self._services.ai_provider = None
            self._services.classification.ai_provider = None

        self._job = ProcessingJob(inputs=[str(p) for p in pending])
        worker = AnalysisWorker(self._services, pending, self._job)
        worker.progressed.connect(self._on_analysis_progress)
        worker.file_completed.connect(self._on_file_completed)
        worker.finished_batch.connect(self._on_analysis_finished)
        worker.warned.connect(self._on_warning)
        worker.failed.connect(self._on_worker_failed)
        self._analysis_worker = worker

        self._set_busy(True, "Analyzing…")
        worker.start()

    def _on_analysis_progress(
        self, path: str, page: int, page_count: int, operation: str, fraction: float
    ) -> None:
        name = Path(path).name
        detail = f"{operation} {page} of {page_count}" if page_count else operation
        self.queue_table.set_progress_text(path, detail)
        self._progress.setValue(int(fraction * 100))
        self._set_status(f"{name} — {detail}")

    def _on_file_completed(self, analysis: object) -> None:
        if not isinstance(analysis, SourceFileAnalysis):
            return
        self._files[str(analysis.path)] = analysis
        self.queue_table.upsert(analysis)
        self._update_actions()

    def _on_analysis_finished(self, results: object, job: object) -> None:
        if not isinstance(results, list) or not isinstance(job, ProcessingJob):
            return

        cancelled = bool(self._analysis_worker and self._analysis_worker.is_cancelled)
        job.finish(JobStatus.CANCELLED if cancelled else JobStatus.ANALYZED)

        for analysis in results:
            self._files[str(analysis.path)] = analysis
            self.queue_table.upsert(analysis)

        self._set_busy(False)
        self._analysis_worker = None
        analyzed = [f for f in self._files.values() if f.is_analyzed]
        self.review_view.set_type_filter(list(self.settings.export_document_types))
        self.review_view.load(analyzed)
        self._update_actions()

        # Read and clear before any early return, so a cancelled Sort & Save
        # never leaves the flag stuck for the next, unrelated analysis.
        auto_export = self._auto_export_pending
        self._auto_export_pending = False

        if cancelled:
            self._set_status("Analysis cancelled. Files already analyzed were kept.")
            return

        review = sum(f.review_group_count for f in analyzed)
        documents = sum(len(f.groups) for f in analyzed)
        candidates = sum(f.candidate_count for f in analyzed)
        unassigned = sum(
            len(f.unknown_packet.documents) for f in analyzed if f.unknown_packet
        )
        job.candidates_found = candidates
        errors = len(job.errors)

        # Candidates first: for one large mixed PDF, "17 candidates" is the
        # number that tells the user whether the run worked.
        summary = (
            f"Analyzed {plural(job.pdfs_processed, 'PDF')} · "
            f"{plural(job.pages_processed, 'page')} · "
            f"{plural(candidates, 'candidate')} · "
            f"{plural(documents, 'document')} found"
        )
        if unassigned:
            summary += f" · {plural(unassigned, 'document')} unassigned"
        if review:
            summary += f" · {review} need review"
        if errors:
            summary += f" · {plural(errors, 'file')} could not be read"
        summary += f" · {format_duration(job.duration_seconds)}"
        self._set_status(summary)

        # Stay on the home screen: the primary path is upload, separate,
        # Sort & Save. Forcing everyone into the review screen made "just
        # export it" cost an extra screen and a hunt for the button, even
        # when nothing needed a decision. Review is one click away for
        # whoever wants it.
        if errors:
            self._show_error_digest(job)

        if auto_export and any(f.active_groups for f in analyzed):
            # Sort & Save means one click does the whole job: proceed
            # straight to export without asking about review items again --
            # the user already committed to "sort and save" up front.
            self._skip_review_confirmation = True
            self._start_export()

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def _start_export(self) -> None:
        # Read and clear immediately: this flag is only ever meant to cover
        # the one export call Sort & Save triggers automatically, never a
        # later manual click.
        skip_confirmation = self._skip_review_confirmation
        self._skip_review_confirmation = False

        if self._busy:
            return
        files = [f for f in self._files.values() if f.active_groups]
        if not files:
            if not skip_confirmation:
                QMessageBox.information(
                    self, APP_NAME, "There are no documents to export yet. Analyze some PDFs first."
                )
            return

        review = sum(f.review_group_count for f in files)
        if review and not skip_confirmation:
            proceed = QMessageBox.question(
                self,
                "Export",
                f"{plural(review, 'document')} still need review.\n\n"
                "Export everything anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if proceed is not QMessageBox.StandardButton.Yes:
                return

        output = Path(self.settings.output_directory)
        job = self._job or ProcessingJob()
        job.output_directory = str(output)

        worker = ExportWorker(
            build_export_service(self.settings),
            files,
            output,
            job=job,
            create_excel_index=self.settings.create_excel_index,
            thresholds=self._services.thresholds,
        )
        worker.progressed.connect(self._on_export_progress)
        worker.finished_export.connect(self._on_export_finished)
        worker.failed.connect(self._on_worker_failed)
        self._export_worker = worker

        self._set_busy(True, "Exporting…")
        worker.start()

    def _on_export_progress(self, done: int, total: int, name: str) -> None:
        if total:
            self._progress.setValue(int(done / total * 100))
        self._set_status(f"Saving documents… {done} of {total}" + (f" ({name})" if name else ""))

    def _on_export_finished(self, result: object, job: object) -> None:
        if not isinstance(result, ExportResult) or not isinstance(job, ProcessingJob):
            return

        cancelled = bool(self._export_worker and self._export_worker.is_cancelled)
        job.finish(JobStatus.CANCELLED if cancelled else JobStatus.COMPLETED)
        self._set_busy(False)
        self._export_worker = None

        for analysis in self._files.values():
            self.queue_table.upsert(analysis)
        self._update_actions()

        self.history.record_job(job, list(self._files.values()))
        self._show_completion(result, job, cancelled)

    def _show_completion(
        self, result: ExportResult, job: ProcessingJob, cancelled: bool
    ) -> None:
        review = sum(f.review_group_count for f in self._files.values())
        headline = self._completion_headline(result, job, review)
        details = self._completion_details(result, job)

        title = "Export cancelled" if cancelled else "Sort & Save complete"
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(
            QMessageBox.Icon.Warning if (cancelled or result.has_errors) else QMessageBox.Icon.Information
        )
        box.setText("\n".join(headline))
        if details:
            box.setInformativeText("\n".join(details))
        if result.errors:
            box.setDetailedText(
                "\n".join(f"{name}: {message}" for name, message in result.errors)
            )

        open_button = box.addButton("Open Output Folder", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Close", QMessageBox.ButtonRole.RejectRole)
        box.exec()

        self._set_status(
            f"Saved {plural(result.document_count, 'document')} to {result.output_directory}"
        )

        if box.clickedButton() is open_button or (
            self.settings.open_output_when_complete and not cancelled
        ):
            open_in_file_manager(result.output_directory)

    def _completion_headline(
        self, result: ExportResult, job: ProcessingJob, review: int
    ) -> list[str]:
        """The at-a-glance summary: what was processed, and what was saved by type.

        "48 PDFs processed / 48 resumes saved / 42 cover letters saved / ... /
        2 items need review" -- meant to be read in a glance, not studied.
        """
        lines = [f"{plural(job.pdfs_processed, 'PDF')} processed"]

        counts: dict[str, int] = {}
        for document in result.exported:
            document_type = document.group.document_type
            counts[document_type] = counts.get(document_type, 0) + 1
        for document_type in sorted(counts, key=lambda t: (-counts[t], t)):
            label = document_type.lower()
            if not label.endswith("s"):
                label += "s"
            lines.append(f"{counts[document_type]} {label} saved")

        if review == 1:
            lines.append("1 item needs review")
        elif review:
            lines.append(f"{review} items need review")
        return lines

    def _completion_details(self, result: ExportResult, job: ProcessingJob) -> list[str]:
        """Secondary detail for anyone who wants it: packets, OCR, AI, errors."""
        candidates = sum(f.candidate_count for f in self._files.values())
        unassigned = sum(
            len(f.unknown_packet.documents)
            for f in self._files.values()
            if f.unknown_packet
        )
        details = [f"Candidates detected:  {candidates}"]
        if result.packet_count:
            details.append(f"Combined candidate packets:  {result.packet_count}")
        if unassigned:
            details.append(f"Documents left unassigned:  {unassigned}")
        if job.pages_classified_by_ai:
            details.append(
                f"Pages classified by AI:  {job.pages_classified_by_ai} "
                f"({job.ai_requests} requests)"
            )
        if job.ocr_pages:
            details.append(f"Pages needing OCR:  {job.ocr_pages}")
        if job.excel_index_path:
            details.append(f"Excel index:  {Path(job.excel_index_path).name}")
        if job.errors:
            details.append(f"Errors:  {len(job.errors)}")
        details.append(f"Output folder:\n{result.output_directory}")
        return details

    # ------------------------------------------------------------------
    # Shared job plumbing
    # ------------------------------------------------------------------
    def _cancel_current_job(self) -> None:
        if self._analysis_worker and self._analysis_worker.isRunning():
            self._analysis_worker.cancel()
            self._set_status("Finishing the current page, then stopping…")
        elif self._export_worker and self._export_worker.isRunning():
            self._export_worker.cancel()
            self._set_status("Finishing the current document, then stopping…")

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._progress.setVisible(busy)
        self._progress.setValue(0)
        self.cancel_button.setVisible(busy)
        self.drop_zone.setEnabled(not busy)
        self.review_view.export_button.setEnabled(not busy)
        self.export_button.setEnabled(
            not busy and any(f.active_groups for f in self._files.values())
        )
        if message:
            self._set_status(message)
        self._update_actions()

    def _set_status(self, message: str) -> None:
        self._status_label.setText(message)

    def _on_warning(self, message: str) -> None:
        self._set_status(message)
        logger.info("Warning surfaced to user: %s", message)

    def _on_worker_failed(self, message: str) -> None:
        self._set_busy(False)
        self._analysis_worker = None
        self._export_worker = None
        QMessageBox.warning(self, APP_NAME, f"{message}\n\nLog file:\n{log_file_path()}")

    def _show_error_digest(self, job: ProcessingJob) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Some files could not be read")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(
            f"{plural(len(job.errors), 'file')} could not be processed. "
            "Everything else was analyzed normally."
        )
        box.setDetailedText("\n".join(error.message for error in job.errors))
        box.exec()

    def _on_documents_changed(self) -> None:
        for analysis in self.review_view.files():
            self._files[str(analysis.path)] = analysis
            self.queue_table.upsert(analysis)
        self._update_actions()

    def _open_review_for(self, path: str) -> None:
        """Open the review workspace on a file the user double-clicked.

        For a file that needs review this goes further than selecting it: the
        workspace narrows to the flagged documents and lands on the first one,
        because "Review Needed" used to tell the user something was wrong and
        then leave them to find it. A file with nothing flagged opens exactly
        as it always did.
        """
        analyzed = [f for f in self._files.values() if f.is_analyzed]
        if not analyzed:
            return
        self.review_view.load(analyzed)

        if not self.review_view.focus_reviews(path):
            for row in range(self.review_view.file_list.count()):
                item = self.review_view.file_list.item(row)
                if item.data(Qt.ItemDataRole.UserRole) == path:
                    self.review_view.file_list.setCurrentRow(row)
                    break
        self._show_page(_REVIEW_PAGE)

    def _show_page(self, index: int) -> None:
        if index == _REVIEW_PAGE:
            analyzed = [f for f in self._files.values() if f.is_analyzed]
            if not analyzed:
                return
            if not self.review_view.files():
                self.review_view.load(analyzed)
        self._stack.setCurrentIndex(index)

    def _on_escape(self) -> None:
        if self._busy:
            self._cancel_current_job()
        elif self._stack.currentIndex() == _REVIEW_PAGE:
            self._show_page(_HOME_PAGE)

    # ------------------------------------------------------------------
    # Dialogs
    # ------------------------------------------------------------------
    def _open_settings(self) -> None:
        previous_theme = self.settings.theme
        dialog = SettingsDialog(self.settings, self.settings_store, self._tokens, self)
        if dialog.exec() != SettingsDialog.DialogCode.Accepted:
            return

        self._services = build_analysis_services(
            self.settings, settings_store=self.settings_store
        )
        self.review_view._grouping = self._services.grouping
        self.review_view._packets = self._services.packets
        self.queue_table.set_thresholds(self._services.thresholds)
        self._update_provider_banner()
        # The home screen and Settings edit the same choice; whichever was used
        # last has to be what the other one shows.
        self.extract_selector.set_selection(list(self.settings.export_document_types))
        self.review_view.set_type_filter(list(self.settings.export_document_types))

        if self.settings.theme != previous_theme:
            self._apply_theme()

        self._reevaluate_reviews()
        self._set_status("Settings saved.")

    def _reevaluate_reviews(self) -> None:
        """Re-apply review thresholds to already-analysed documents."""
        for analysis in self._files.values():
            if not analysis.groups:
                continue
            for group in analysis.groups:
                self._services.grouping.evaluate_review(group, analysis.pages)
            analysis.refresh_status()
            self.queue_table.upsert(analysis)
        self.review_view.load([f for f in self._files.values() if f.is_analyzed])
        self._update_actions()

    def _apply_theme(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        self._tokens = apply_theme(app, self.settings.theme)
        self.drop_zone.set_palette_tokens(self._tokens)
        self.queue_table.set_palette_tokens(self._tokens)
        self.review_view.set_palette_tokens(self._tokens)
        for analysis in self._files.values():
            self.queue_table.upsert(analysis)
        # Rebuild the review workspace so its cards pick up the new palette.
        self.review_view.load([f for f in self._files.values() if f.is_analyzed])

    def _open_history(self) -> None:
        HistoryDialog(self.history, self._tokens, self).exec()

    def _open_about(self) -> None:
        from app.services.ocr_service import describe_ocr_runtime
        from app.ui.about_dialog import AboutDialog

        AboutDialog(
            self.settings,
            self._tokens,
            provider_description=self._services.provider_availability().message,
            ocr_description=describe_ocr_runtime(self.settings.tesseract_path),
            parent=self,
        ).exec()

    def _update_provider_banner(self) -> None:
        name = self._services.provider_name
        if self._services.sends_data_externally:
            text = f"{name}  ·  page text is sent to this provider"
            color = self._tokens.warning
        else:
            text = f"{name}  ·  nothing leaves this computer"
            color = self._tokens.text_muted
        self._provider_label.setText(text)
        self._provider_label.setStyleSheet(f"color: {color};")
        self._provider_label.setToolTip(self._services.provider_availability().message)

    # ------------------------------------------------------------------
    # Window lifecycle
    # ------------------------------------------------------------------
    #: The size the window would like, on a display with room for it.
    PREFERRED_SIZE = (1180, 780)
    #: The smallest the layout still works at. Reduced further on small screens
    #: rather than forcing the window wider than the display, which is what put
    #: the right-hand panel off the edge on laptops and scaled displays.
    PREFERRED_MINIMUM = (940, 620)

    def _available_geometry(self):
        screen = self.screen() or QApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else None

    def _apply_initial_geometry(self) -> None:
        """Size the window to the display it is actually on.

        Asking for 1180x780 unconditionally is wrong on a 1366x768 laptop and
        worse at 125% scaling: the window ends up larger than the screen and
        the inspector sits beyond the right edge where it cannot be reached.
        """
        available = self._available_geometry()
        if available is None:  # pragma: no cover - no screen (headless)
            self.setMinimumSize(*self.PREFERRED_MINIMUM)
            self.resize(*self.PREFERRED_SIZE)
            return

        # Leave a margin so the window never sits flush against the edges.
        max_width = max(640, int(available.width() * 0.96))
        max_height = max(480, int(available.height() * 0.94))

        self.setMinimumSize(
            min(self.PREFERRED_MINIMUM[0], max_width),
            min(self.PREFERRED_MINIMUM[1], max_height),
        )
        self.resize(
            min(self.PREFERRED_SIZE[0], max_width),
            min(self.PREFERRED_SIZE[1], max_height),
        )

    def _restore_geometry(self) -> None:
        raw = self.settings.window_geometry
        if not raw:
            return
        try:
            from PySide6.QtCore import QByteArray

            self.restoreGeometry(QByteArray.fromBase64(raw.encode("ascii")))
        except Exception:  # pragma: no cover - stored geometry is best effort
            logger.debug("Could not restore window geometry")
            return
        self._pull_onto_screen()

    def _pull_onto_screen(self) -> None:
        """Bring a restored window back within the current display.

        Geometry saved on a second monitor, or before a resolution change,
        otherwise reopens the window somewhere the user cannot see it.
        """
        available = self._available_geometry()
        if available is None:  # pragma: no cover - no screen (headless)
            return

        frame = self.frameGeometry()
        if available.contains(frame):
            return

        width = min(frame.width(), available.width())
        height = min(frame.height(), available.height())
        self.resize(width, height)

        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())

    def _save_geometry(self) -> None:
        try:
            self.settings.window_geometry = bytes(
                self.saveGeometry().toBase64()
            ).decode("ascii")
            self.settings_store.save(self.settings)
        except Exception:  # pragma: no cover
            logger.debug("Could not save window geometry")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        if self._busy:
            confirm = QMessageBox.question(
                self,
                APP_NAME,
                "Work is still in progress. Stop it and close?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm is not QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._cancel_current_job()
            for worker in (self._analysis_worker, self._export_worker):
                if worker is not None and worker.isRunning():
                    worker.wait(4000)

        self._save_geometry()
        self.review_view.shutdown()
        self._services.close()
        event.accept()


__all__ = ["MainWindow"]
