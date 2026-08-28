"""About dialog: version, configuration and where this installation keeps files.

Deliberately shows the things a support conversation actually needs — version,
which intelligence provider is active, whether OCR works, and the paths to the
log and data folders — without ever revealing a secret.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app import APP_NAME, APP_VERSION, PUBLISHER
from app.storage.settings_store import AppSettings
from app.ui.theme import Palette
from app.ui.widgets.badges import HSeparator, KeyValueRow, SectionLabel
from app.utils.paths import app_data_dir, log_file_path, resource_path
from app.utils.system import open_in_file_manager
from app.version import windows_version


class AboutDialog(QDialog):
    """Shows what this installation is and where it keeps things."""

    def __init__(
        self,
        settings: AppSettings,
        palette: Palette,
        *,
        provider_description: str = "",
        ocr_description: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tokens = palette

        self.setWindowTitle(f"About {APP_NAME}")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(14)

        layout.addLayout(self._build_header())
        layout.addWidget(HSeparator())

        layout.addWidget(SectionLabel("This installation"))
        self._rows: list[KeyValueRow] = []

        version_row = KeyValueRow("Version", f"{APP_VERSION}  ({windows_version()})")
        provider_row = KeyValueRow(
            "Provider", provider_description or settings.provider_kind.label
        )
        profile_row = KeyValueRow("Profile", settings.profile_name)
        ocr_row = KeyValueRow("OCR", ocr_description or "Not checked")
        for row in (version_row, provider_row, profile_row, ocr_row):
            layout.addWidget(row)
            self._rows.append(row)

        layout.addWidget(SectionLabel("Files"))
        self._data_dir = app_data_dir()
        self._log_file = log_file_path()
        layout.addWidget(KeyValueRow("Settings and history", str(self._data_dir)))
        layout.addWidget(KeyValueRow("Log file", str(self._log_file)))
        layout.addWidget(
            KeyValueRow("Output folder", settings.output_directory or "Not set")
        )

        privacy = QLabel(self._privacy_text(settings))
        privacy.setWordWrap(True)
        privacy.setProperty("role", "caption")
        layout.addWidget(privacy)

        layout.addStretch(1)
        layout.addLayout(self._build_buttons())

    # ------------------------------------------------------------------
    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(16)

        icon = QLabel()
        icon_path = Path(resource_path("assets", "icon.png"))
        if icon_path.exists():
            pixmap = QPixmap(str(icon_path)).scaled(
                56, 56,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            icon.setPixmap(pixmap)
        icon.setFixedSize(56, 56)

        text = QVBoxLayout()
        text.setSpacing(2)

        name = QLabel(APP_NAME)
        name.setProperty("role", "heading")

        tagline = QLabel(
            "Understands combined PDFs, groups the pages into separate documents, "
            "and asks you to check only the uncertain ones."
        )
        tagline.setProperty("role", "body")
        tagline.setWordWrap(True)

        publisher = QLabel(PUBLISHER)
        publisher.setProperty("role", "caption")

        text.addWidget(name)
        text.addWidget(tagline)
        text.addWidget(publisher)

        header.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        header.addLayout(text, 1)
        return header

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)

        open_data = QPushButton("Open data folder")
        open_data.clicked.connect(lambda: open_in_file_manager(self._data_dir))
        open_data.setToolTip(str(self._data_dir))

        open_log = QPushButton("Open log folder")
        open_log.clicked.connect(lambda: open_in_file_manager(self._log_file.parent))
        open_log.setToolTip(str(self._log_file))

        copy_button = QPushButton("Copy details")
        copy_button.clicked.connect(self._copy_details)
        copy_button.setToolTip("Copy this information for a support request")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        buttons.accepted.connect(self.accept)
        close = buttons.button(QDialogButtonBox.StandardButton.Close)
        close.setProperty("variant", "accent")
        close.setDefault(True)

        row.addWidget(open_data)
        row.addWidget(open_log)
        row.addWidget(copy_button)
        row.addStretch(1)
        row.addWidget(buttons)
        return row

    # ------------------------------------------------------------------
    @staticmethod
    def _privacy_text(settings: AppSettings) -> str:
        if settings.uses_external_provider:
            return (
                "This installation is configured to use an external AI provider. "
                "Extracted page text is sent to that provider for analysis. "
                "Your API key is stored in the Windows credential store and is never "
                "written to the settings file or the log."
            )
        return (
            "This installation processes documents entirely on this computer. "
            "No document text is sent anywhere, and no usage data is collected."
        )

    def details_text(self) -> str:
        """Plain-text summary for support, with no secrets in it."""
        lines = [f"{APP_NAME} {APP_VERSION} ({windows_version()})"]
        for row in self._rows:
            lines.append(f"{row.label_text()}: {row.value_text()}")
        lines.append(f"Data folder: {self._data_dir}")
        lines.append(f"Log file: {self._log_file}")
        return "\n".join(lines)

    def _copy_details(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.details_text())


__all__ = ["AboutDialog"]
