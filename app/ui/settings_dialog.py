"""Settings dialog.

Every option in the specification, grouped into tabs. The API key is written
through the credential store, never into the settings file, and the privacy
consequences of each provider are stated plainly on screen.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app import APP_NAME, APP_VERSION
from app.intelligence.ollama_provider import OllamaProvider
from app.models.enums import ProviderKind, SeparatorPolicy
from app.profiles import available_profiles, get_profile
from app.services.ocr_service import TesseractOCRProvider
from app.services.update_service import RELEASES_PAGE_URL
from app.storage.settings_store import AppSettings, SettingsStore
from app.ui.theme import Palette
from app.utils.filenames import SUPPORTED_VARIABLES, describe_variables, template_preview

_THEMES = [("Match Windows", "system"), ("Light", "light"), ("Dark", "dark")]
_MASKED = "••••••••••••••••"


class SettingsDialog(QDialog):
    """Edits :class:`AppSettings` and persists them on accept."""

    def __init__(
        self,
        settings: AppSettings,
        store: SettingsStore,
        palette: Palette,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._store = store
        self._tokens = palette
        self._api_key_changed = False
        self._update_worker = None
        self._download_worker = None
        self._release_url = RELEASES_PAGE_URL
        self._update_check = None

        self.setWindowTitle("Settings")
        self.setMinimumSize(620, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        tabs = QTabWidget()
        tabs.addTab(self._build_general_tab(), "General")
        tabs.addTab(self._build_classification_tab(), "Classification")
        tabs.addTab(self._build_ocr_tab(), "OCR")
        tabs.addTab(self._build_output_tab(), "Output")
        layout.addWidget(tabs, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Save).setProperty("variant", "accent")
        layout.addWidget(buttons)

        self._load_values()

    # ------------------------------------------------------------------
    def _build_general_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(16, 16, 16, 16)
        form.setSpacing(11)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.output_edit = QLineEdit()
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_output)
        output_row = QHBoxLayout()
        output_row.setSpacing(8)
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(browse)
        output_container = QWidget()
        output_container.setLayout(output_row)
        form.addRow("Default output folder", output_container)

        self.include_subfolders = QCheckBox("Include PDFs in subfolders")
        self.create_excel = QCheckBox("Create an Excel index (DocumentIndex.xlsx)")
        self.open_output = QCheckBox("Open the output folder when finished")
        self.warn_duplicates = QCheckBox("Warn about files processed before")
        form.addRow("", self.include_subfolders)
        form.addRow("", self.create_excel)
        form.addRow("", self.open_output)
        form.addRow("", self.warn_duplicates)

        self.theme_combo = QComboBox()
        for label, _value in _THEMES:
            self.theme_combo.addItem(label)
        form.addRow("Theme", self.theme_combo)

        self.high_slider = QSlider(Qt.Orientation.Horizontal)
        self.high_slider.setRange(50, 99)
        self.high_label = QLabel()
        high_row = QHBoxLayout()
        high_row.addWidget(self.high_slider, 1)
        high_row.addWidget(self.high_label)
        high_container = QWidget()
        high_container.setLayout(high_row)
        self.high_slider.valueChanged.connect(self._on_thresholds_changed)
        form.addRow("No review needed at or above", high_container)

        self.review_slider = QSlider(Qt.Orientation.Horizontal)
        self.review_slider.setRange(10, 95)
        self.review_label = QLabel()
        review_row = QHBoxLayout()
        review_row.addWidget(self.review_slider, 1)
        review_row.addWidget(self.review_label)
        review_container = QWidget()
        review_container.setLayout(review_row)
        self.review_slider.valueChanged.connect(self._on_thresholds_changed)
        form.addRow("Review required below", review_container)

        hint = QLabel(
            "Documents at or above the first value are exported without review. "
            "Anything below the second value is highlighted as needing a decision."
        )
        hint.setProperty("role", "caption")
        hint.setWordWrap(True)
        form.addRow("", hint)

        form.addRow("", self._build_update_row())
        return page

    # ------------------------------------------------------------------
    def _build_update_row(self) -> QWidget:
        """Check for a newer release, then fetch and install it.

        The button does one of two things depending on where the application
        is running from. An installed build downloads the MSI, verifies it
        against the published checksum, and hands it to Windows Installer. A
        portable build or a source checkout cannot upgrade itself that way --
        the MSI would install a second copy beside the portable EXE -- so those
        keep opening the release page, which is what this always used to do.
        """
        self.update_button = QPushButton("Check for updates")
        self.update_button.clicked.connect(self._check_for_updates)

        self.update_status = QLabel(f"Version {APP_VERSION}")
        self.update_status.setProperty("role", "caption")
        self.update_status.setWordWrap(True)

        self.update_download_button = QPushButton("Get the update…")
        self.update_download_button.setProperty("variant", "accent")
        self.update_download_button.setVisible(False)
        self.update_download_button.clicked.connect(self._start_update_download)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.update_button)
        row.addWidget(self.update_download_button)
        row.addWidget(self.update_status, 1)

        container = QWidget()
        container.setLayout(row)
        return container

    def _check_for_updates(self) -> None:
        if self._update_worker is not None and self._update_worker.isRunning():
            return
        self.update_button.setEnabled(False)
        self.update_download_button.setVisible(False)
        self.update_status.setText("Checking…")

        from app.workers.update_worker import UpdateCheckWorker

        self._update_worker = UpdateCheckWorker(self)
        self._update_worker.completed.connect(self._on_update_checked)
        self._update_worker.start()

    def _can_install_updates(self) -> bool:
        from app.services.update_installer import can_self_install

        return can_self_install()

    def _installable(self, result) -> bool:
        """Whether this update can be installed rather than merely fetched."""
        return bool(
            result is not None and result.can_download and self._can_install_updates()
        )

    def _on_update_checked(self, result) -> None:
        self.update_button.setEnabled(True)
        self.update_status.setText(result.message)
        self.update_download_button.setVisible(result.update_available)
        self._release_url = result.release_url
        self._update_check = result
        self.update_download_button.setText(
            "Download and install…" if self._installable(result) else "Get the update…"
        )

    # -- downloading ---------------------------------------------------
    def _start_update_download(self) -> None:
        """Download the update, or fall back to the browser.

        Falling back is the normal path for a portable build, a source
        checkout, and any release published without an MSI attached. None of
        those is a failure, so none of them says anything alarming -- the
        button simply opens the page it always opened.
        """
        if not self._installable(self._update_check):
            self._open_release_page()
            return
        if self._download_worker is not None and self._download_worker.isRunning():
            return

        proceed = QMessageBox.question(
            self,
            "Install the update?",
            f"{APP_NAME} will download version {self._update_check.latest_version} "
            "and start the installer.\n\nThe application will close so it can be "
            "replaced. Your settings and processing history are kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if proceed is not QMessageBox.StandardButton.Yes:
            return

        from app.workers.update_download_worker import UpdateDownloadWorker

        self.update_button.setEnabled(False)
        self.update_download_button.setEnabled(False)
        self.update_status.setText("Downloading…")

        self._download_worker = UpdateDownloadWorker(self._update_check, self)
        self._download_worker.progressed.connect(self._on_download_progress)
        self._download_worker.completed.connect(self._on_download_finished)
        self._download_worker.start()

    def _on_download_progress(self, received: int, total: int) -> None:
        if total > 0:
            self.update_status.setText(f"Downloading… {int(received / total * 100)}%")
        else:
            self.update_status.setText(f"Downloading… {received // (1024 * 1024)} MB")

    def _on_download_finished(self, outcome) -> None:
        self.update_button.setEnabled(True)
        self.update_download_button.setEnabled(True)
        self._download_worker = None

        if not outcome.ok:
            # An empty error means cancellation, which needs no announcement.
            if outcome.error:
                self.update_status.setText(outcome.error)
                QMessageBox.warning(self, APP_NAME, outcome.error)
            else:
                self.update_status.setText("Download cancelled.")
            return

        from app.services.update_installer import launch_installer

        if not launch_installer(outcome.path):
            self.update_status.setText("The installer could not be started.")
            QMessageBox.warning(
                self,
                APP_NAME,
                "The update was downloaded but the installer could not be "
                f"started. You can run it yourself:\n\n{outcome.path}",
            )
            return

        self.update_status.setText("Starting the installer…")
        self._quit_for_update()

    def _quit_for_update(self) -> None:
        """Close the application so the installer can replace its files.

        Windows Installer can ask a running program to close, but relying on
        that leaves the user watching an unexplained prompt. Standing aside
        deliberately is clearer, and it is what a person doing this by hand
        would do.
        """
        from PySide6.QtWidgets import QApplication

        self.accept()
        application = QApplication.instance()
        if application is not None:
            application.quit()

    def _open_release_page(self) -> None:
        QDesktopServices.openUrl(QUrl(self._release_url))

    def _build_classification_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(11)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.profile_combo = QComboBox()
        self.profile_combo.addItems(available_profiles())
        form.addRow("Document profile", self.profile_combo)

        self.provider_combo = QComboBox()
        for kind in (ProviderKind.RULES, ProviderKind.OPENAI, ProviderKind.OLLAMA):
            self.provider_combo.addItem(kind.label, kind.value)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        form.addRow("Intelligence provider", self.provider_combo)
        layout.addLayout(form)

        self.privacy_label = QLabel()
        self.privacy_label.setWordWrap(True)
        layout.addWidget(self.privacy_label)

        # -- OpenAI -------------------------------------------------------
        self.openai_group = QWidget()
        openai_form = QFormLayout(self.openai_group)
        openai_form.setContentsMargins(0, 6, 0, 0)
        openai_form.setSpacing(10)
        openai_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.openai_key = QLineEdit()
        self.openai_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.openai_key.setPlaceholderText("sk-…")
        self.openai_key.textEdited.connect(self._on_key_edited)
        openai_form.addRow("API key", self.openai_key)

        self.key_storage_label = QLabel()
        self.key_storage_label.setProperty("role", "caption")
        self.key_storage_label.setWordWrap(True)
        openai_form.addRow("", self.key_storage_label)

        self.openai_model = QLineEdit()
        openai_form.addRow("Model", self.openai_model)

        self.openai_timeout = QSpinBox()
        self.openai_timeout.setRange(5, 300)
        self.openai_timeout.setSuffix(" seconds")
        openai_form.addRow("Timeout", self.openai_timeout)
        layout.addWidget(self.openai_group)

        # -- Ollama -------------------------------------------------------
        self.ollama_group = QWidget()
        ollama_form = QFormLayout(self.ollama_group)
        ollama_form.setContentsMargins(0, 6, 0, 0)
        ollama_form.setSpacing(10)
        ollama_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.ollama_url = QLineEdit()
        ollama_form.addRow("Server address", self.ollama_url)
        self.ollama_model = QLineEdit()
        ollama_form.addRow("Model", self.ollama_model)
        self.ollama_timeout = QSpinBox()
        self.ollama_timeout.setRange(5, 600)
        self.ollama_timeout.setSuffix(" seconds")
        ollama_form.addRow("Timeout", self.ollama_timeout)

        test_ollama = QPushButton("Test connection")
        test_ollama.clicked.connect(self._test_ollama)
        ollama_form.addRow("", test_ollama)
        layout.addWidget(self.ollama_group)

        escalation_form = QFormLayout()
        escalation_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.escalation_slider = QSlider(Qt.Orientation.Horizontal)
        self.escalation_slider.setRange(0, 99)
        self.escalation_label = QLabel()
        self.escalation_slider.valueChanged.connect(
            lambda value: self.escalation_label.setText(f"{value}%")
        )
        escalation_row = QHBoxLayout()
        escalation_row.addWidget(self.escalation_slider, 1)
        escalation_row.addWidget(self.escalation_label)
        escalation_container = QWidget()
        escalation_container.setLayout(escalation_row)
        escalation_form.addRow("Ask the AI when local confidence is below", escalation_container)
        layout.addLayout(escalation_form)

        layout.addStretch(1)
        return page

    def _build_ocr_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(16, 16, 16, 16)
        form.setSpacing(11)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.ocr_enabled = QCheckBox("Read scanned pages with OCR when they have no text")
        form.addRow("", self.ocr_enabled)

        self.tesseract_path = QLineEdit()
        self.tesseract_path.setPlaceholderText("Found automatically if installed")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_tesseract)
        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        path_row.addWidget(self.tesseract_path, 1)
        path_row.addWidget(browse)
        path_container = QWidget()
        path_container.setLayout(path_row)
        form.addRow("Tesseract path", path_container)

        self.ocr_language = QLineEdit()
        self.ocr_language.setPlaceholderText("eng")
        form.addRow("OCR language", self.ocr_language)

        check = QPushButton("Check OCR")
        check.clicked.connect(self._check_ocr)
        form.addRow("", check)

        self.ocr_status = QLabel()
        self.ocr_status.setProperty("role", "caption")
        self.ocr_status.setWordWrap(True)
        form.addRow("", self.ocr_status)

        note = QLabel(
            "OCR is only used to understand scanned pages. Exported PDFs always contain "
            "the original pages, never a re-rendered image."
        )
        note.setProperty("role", "caption")
        note.setWordWrap(True)
        form.addRow("", note)
        return page

    def _build_output_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(16, 16, 16, 16)
        form.setSpacing(11)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.template_edit = QLineEdit()
        self.template_edit.textChanged.connect(self._update_preview)
        form.addRow("Filename template", self.template_edit)

        variables = QLabel(describe_variables(SUPPORTED_VARIABLES))
        variables.setProperty("role", "caption")
        variables.setWordWrap(True)
        form.addRow("Available", variables)

        self.preview_label = QLabel()
        self.preview_label.setProperty("role", "body")
        self.preview_label.setWordWrap(True)
        form.addRow("Preview", self.preview_label)

        self.group_by_document_type = QCheckBox(
            "Group output by document type (Resumes/, Cover Letters/, …)"
        )
        self.group_by_document_type.setToolTip(
            "Files are named just “<Candidate Name>.pdf” inside a folder per type. "
            "This is the default -- turn it off to get one folder per candidate instead."
        )
        self.group_by_document_type.toggled.connect(self._on_layout_changed)
        form.addRow("", self.group_by_document_type)

        self.folder_per_candidate = QCheckBox("Create a folder for each candidate instead")
        form.addRow("", self.folder_per_candidate)

        self.export_separate_documents = QCheckBox("Create separate document PDFs")
        self.export_separate_documents.setToolTip(
            "One PDF per document: resume, cover letter, application report and so on"
        )
        form.addRow("Output", self.export_separate_documents)

        self.export_combined_packets = QCheckBox(
            "Create a combined packet PDF for each candidate"
        )
        self.export_combined_packets.setToolTip(
            "One PDF per applicant holding all of their documents, in packet order"
        )
        form.addRow("", self.export_combined_packets)

        # -- which document types to save ---------------------------------
        # Everything is still detected and reviewable; this only decides what
        # reaches the output folder, so narrowing it is not destructive and
        # does not need a re-analysis to undo.
        self.document_type_boxes: dict[str, QCheckBox] = {}
        types_container = QWidget()
        types_layout = QVBoxLayout(types_container)
        types_layout.setContentsMargins(0, 0, 0, 0)
        types_layout.setSpacing(3)
        for document_type in get_profile(self._settings.profile_name).document_types:
            box = QCheckBox(document_type)
            box.setChecked(True)
            box.toggled.connect(self._on_document_types_changed)
            self.document_type_boxes[document_type] = box
            types_layout.addWidget(box)
        form.addRow("Save which documents", types_container)

        self.document_types_hint = QLabel()
        self.document_types_hint.setProperty("role", "caption")
        self.document_types_hint.setWordWrap(True)
        form.addRow("", self.document_types_hint)

        self.separator_combo = QComboBox()
        for policy in (
            SeparatorPolicy.INCLUDE,
            SeparatorPolicy.EXCLUDE,
            SeparatorPolicy.ASK,
        ):
            self.separator_combo.addItem(policy.label, policy.value)
        form.addRow("Separator pages", self.separator_combo)

        separator_note = QLabel(
            "A separator page is a page that only carries a document label, such as a "
            "page reading just “RESUME”."
        )
        separator_note.setProperty("role", "caption")
        separator_note.setWordWrap(True)
        form.addRow("", separator_note)
        return page

    # ------------------------------------------------------------------
    def _load_values(self) -> None:
        settings = self._settings
        self.output_edit.setText(settings.output_directory)
        self.include_subfolders.setChecked(settings.include_subfolders)
        self.create_excel.setChecked(settings.create_excel_index)
        self.open_output.setChecked(settings.open_output_when_complete)
        self.warn_duplicates.setChecked(settings.warn_on_duplicates)

        theme_values = [value for _label, value in _THEMES]
        if settings.theme in theme_values:
            self.theme_combo.setCurrentIndex(theme_values.index(settings.theme))

        self.high_slider.setValue(int(round(settings.confidence_high * 100)))
        self.review_slider.setValue(int(round(settings.confidence_review * 100)))
        self._on_thresholds_changed()

        if settings.profile_name in available_profiles():
            self.profile_combo.setCurrentText(settings.profile_name)
        index = self.provider_combo.findData(settings.provider_kind.value)
        if index >= 0:
            self.provider_combo.setCurrentIndex(index)

        existing_key = self._store.get_openai_key()
        self.openai_key.setText(_MASKED if existing_key else "")
        self.key_storage_label.setText(
            f"Stored in: {self._store.secrets.backend_description}. "
            "The key is never written to the settings file or the log."
        )
        self.openai_model.setText(settings.openai_model)
        self.openai_timeout.setValue(settings.openai_timeout_seconds)

        self.ollama_url.setText(settings.ollama_url)
        self.ollama_model.setText(settings.ollama_model)
        self.ollama_timeout.setValue(settings.ollama_timeout_seconds)

        self.escalation_slider.setValue(int(round(settings.ai_escalation_threshold * 100)))
        self.escalation_label.setText(f"{self.escalation_slider.value()}%")

        self.ocr_enabled.setChecked(settings.ocr_enabled)
        self.tesseract_path.setText(settings.tesseract_path)
        self.ocr_language.setText(settings.ocr_language)

        self.template_edit.setText(settings.filename_template)
        self.group_by_document_type.setChecked(settings.group_by_document_type)
        self.folder_per_candidate.setChecked(settings.folder_per_candidate)
        self._on_layout_changed(settings.group_by_document_type)
        self.export_separate_documents.setChecked(settings.export_separate_documents)
        self.export_combined_packets.setChecked(settings.export_combined_packets)
        wanted = set(settings.export_document_types)
        for document_type, box in self.document_type_boxes.items():
            box.blockSignals(True)
            box.setChecked(not wanted or document_type in wanted)
            box.blockSignals(False)
        self._on_document_types_changed()
        separator_index = self.separator_combo.findData(settings.separator_policy_enum.value)
        if separator_index >= 0:
            self.separator_combo.setCurrentIndex(separator_index)

        self._on_provider_changed()
        self._update_preview()
        self._check_ocr(quiet=True)

    # ------------------------------------------------------------------
    def _on_layout_changed(self, grouped_by_type: bool) -> None:
        """The two folder layouts are mutually exclusive -- make that visible."""
        self.folder_per_candidate.setEnabled(not grouped_by_type)

    def _on_thresholds_changed(self) -> None:
        high = self.high_slider.value()
        review = self.review_slider.value()
        if review >= high:
            review = max(10, high - 1)
            self.review_slider.blockSignals(True)
            self.review_slider.setValue(review)
            self.review_slider.blockSignals(False)
        self.high_label.setText(f"{high}%")
        self.review_label.setText(f"{review}%")

    def _on_provider_changed(self) -> None:
        kind = ProviderKind(self.provider_combo.currentData())
        self.openai_group.setVisible(kind is ProviderKind.OPENAI)
        self.ollama_group.setVisible(kind is ProviderKind.OLLAMA)

        if kind is ProviderKind.OPENAI:
            message = (
                "Enabling OpenAI sends extracted page text to OpenAI's servers for "
                "analysis. Applicant details in those pages leave this computer. "
                "Only the text needed to classify a page is sent — never whole PDFs."
            )
            colors = (self._tokens.warning_soft, self._tokens.warning)
        elif kind is ProviderKind.OLLAMA:
            message = (
                "Ollama runs on this computer or your own server. Page text is sent to "
                "the address below and does not go to any third party."
            )
            colors = (self._tokens.accent_soft, self._tokens.accent)
        else:
            message = (
                "Rules Only works completely offline. No document text leaves this "
                "computer, and no API key is needed."
            )
            colors = (self._tokens.success_soft, self._tokens.success)

        self.privacy_label.setText(message)
        self.privacy_label.setStyleSheet(
            f"QLabel {{ background-color: {colors[0]}; color: {colors[1]};"
            f" border-radius: 6px; padding: 10px 12px; font-size: 9pt; }}"
        )

    def _on_key_edited(self, _text: str) -> None:
        self._api_key_changed = True

    def _update_preview(self) -> None:
        self.preview_label.setText(template_preview(self.template_edit.text()))

    # ------------------------------------------------------------------
    def _on_document_types_changed(self) -> None:
        chosen = [t for t, box in self.document_type_boxes.items() if box.isChecked()]
        if not chosen:
            self.document_types_hint.setText(
                "Nothing is selected, so nothing would be saved. Every type will "
                "be saved instead."
            )
        elif len(chosen) == len(self.document_type_boxes):
            self.document_types_hint.setText("Every document type will be saved.")
        else:
            self.document_types_hint.setText(
                "Only " + ", ".join(chosen) + " will be saved. Other documents are "
                "still detected and shown in review, just not written out."
            )

    def _browse_output(self) -> None:
        current = self.output_edit.text() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Choose output folder", current)
        if folder:
            self.output_edit.setText(folder)

    def _browse_tesseract(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Locate the Tesseract program", str(Path.home())
        )
        if path:
            self.tesseract_path.setText(path)
            self._check_ocr()

    def _check_ocr(self, quiet: bool = False) -> None:
        provider = TesseractOCRProvider(self.tesseract_path.text())
        availability = provider.is_available(refresh=True)
        color = self._tokens.success if availability.available else self._tokens.danger
        self.ocr_status.setText(availability.message)
        self.ocr_status.setStyleSheet(f"color: {color};")
        if availability.available and availability.languages and not quiet:
            language = self.ocr_language.text().strip() or "eng"
            if not provider.supports_language(language):
                installed = ", ".join(availability.languages)
                self.ocr_status.setText(
                    f"{availability.message}\nLanguage '{language}' is not installed. "
                    f"Available: {installed}"
                )
                self.ocr_status.setStyleSheet(f"color: {self._tokens.warning};")

    def _test_ollama(self) -> None:
        provider = OllamaProvider(
            get_profile(self.profile_combo.currentText()),
            url=self.ollama_url.text(),
            model=self.ollama_model.text(),
            timeout=self.ollama_timeout.value(),
        )
        availability = provider.is_available()
        icon = (
            QMessageBox.Icon.Information
            if availability.available
            else QMessageBox.Icon.Warning
        )
        box = QMessageBox(icon, "Ollama", availability.message, parent=self)
        box.exec()

    # ------------------------------------------------------------------
    def _save(self) -> None:
        settings = self._settings
        settings.output_directory = self.output_edit.text().strip() or settings.output_directory
        settings.include_subfolders = self.include_subfolders.isChecked()
        settings.create_excel_index = self.create_excel.isChecked()
        settings.open_output_when_complete = self.open_output.isChecked()
        settings.warn_on_duplicates = self.warn_duplicates.isChecked()
        settings.theme = _THEMES[self.theme_combo.currentIndex()][1]
        settings.confidence_high = self.high_slider.value() / 100.0
        settings.confidence_review = self.review_slider.value() / 100.0

        settings.profile_name = self.profile_combo.currentText()
        settings.provider = str(self.provider_combo.currentData())
        settings.ai_escalation_threshold = self.escalation_slider.value() / 100.0

        settings.openai_model = self.openai_model.text().strip() or settings.openai_model
        settings.openai_timeout_seconds = self.openai_timeout.value()
        settings.ollama_url = self.ollama_url.text().strip() or settings.ollama_url
        settings.ollama_model = self.ollama_model.text().strip() or settings.ollama_model
        settings.ollama_timeout_seconds = self.ollama_timeout.value()

        settings.ocr_enabled = self.ocr_enabled.isChecked()
        settings.tesseract_path = self.tesseract_path.text().strip()
        settings.ocr_language = self.ocr_language.text().strip() or "eng"

        settings.filename_template = self.template_edit.text().strip() or "{candidate}_{document_type}"
        settings.group_by_document_type = self.group_by_document_type.isChecked()
        settings.folder_per_candidate = self.folder_per_candidate.isChecked()
        settings.export_separate_documents = self.export_separate_documents.isChecked()
        settings.export_combined_packets = self.export_combined_packets.isChecked()
        chosen = [t for t, box in self.document_type_boxes.items() if box.isChecked()]
        # All types selected is stored as "no restriction", so adding a type to
        # the profile later is included rather than silently dropped.
        settings.export_document_types = (
            [] if len(chosen) == len(self.document_type_boxes) else chosen
        )
        if not (settings.export_separate_documents or settings.export_combined_packets):
            # Turning both off would export nothing at all, which is never what
            # someone means. Keep the separate documents.
            settings.export_separate_documents = True
        settings.separator_policy = str(self.separator_combo.currentData())

        # The masked placeholder means "leave the stored key alone".
        key_text = self.openai_key.text()
        if self._api_key_changed and key_text != _MASKED:
            if not self._store.set_openai_key(key_text):
                QMessageBox.warning(
                    self,
                    "Settings",
                    "The API key could not be saved to the credential store.",
                )

        if not self._store.save(settings):
            QMessageBox.warning(
                self,
                "Settings",
                "Your settings could not be saved. Check that the application data "
                "folder is writable.",
            )
            return
        self.accept()

    @property
    def settings(self) -> AppSettings:
        return self._settings


__all__ = ["SettingsDialog"]
