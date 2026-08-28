"""The large drag-and-drop area on the home screen."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.services.file_discovery import is_pdf
from app.ui.theme import Palette


class DropZone(QFrame):
    """Accepts dropped PDFs and folders, and offers browse buttons."""

    paths_added = Signal(list)

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette_tokens = palette
        self._hovering = False

        self.setAcceptDrops(True)
        self.setMinimumHeight(210)
        self.setObjectName("dropZone")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._icon = QLabel("⬇")
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon.setStyleSheet(
            f"font-size: 26pt; color: {palette.accent}; background: transparent;"
        )

        self._headline = QLabel("Drop PDFs or folders here")
        self._headline.setProperty("role", "heading")
        self._headline.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._subtitle = QLabel(
            "Smart PDF Sorter will identify, group, and organize the documents automatically."
        )
        self._subtitle.setProperty("role", "body")
        self._subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._subtitle.setWordWrap(True)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.add_files_button = QPushButton("Add PDFs")
        self.add_files_button.setProperty("variant", "accent")
        self.add_files_button.setMinimumWidth(132)
        self.add_files_button.clicked.connect(self._browse_files)
        self.add_files_button.setToolTip("Choose one or more PDF files (Ctrl+O)")

        self.add_folder_button = QPushButton("Add Folder")
        self.add_folder_button.setMinimumWidth(132)
        self.add_folder_button.clicked.connect(self._browse_folder)
        self.add_folder_button.setToolTip("Choose a folder of PDFs (Ctrl+Shift+O)")

        buttons.addWidget(self.add_files_button)
        buttons.addWidget(self.add_folder_button)

        layout.addWidget(self._icon)
        layout.addSpacing(2)
        layout.addWidget(self._headline)
        layout.addWidget(self._subtitle)
        layout.addSpacing(14)
        layout.addLayout(buttons)

        self._apply_style()

    # ------------------------------------------------------------------
    def set_palette_tokens(self, palette: Palette) -> None:
        self._palette_tokens = palette
        self._icon.setStyleSheet(
            f"font-size: 26pt; color: {palette.accent}; background: transparent;"
        )
        self._apply_style()

    def _apply_style(self) -> None:
        tokens = self._palette_tokens
        border = tokens.accent if self._hovering else tokens.stroke_strong
        background = tokens.accent_soft if self._hovering else tokens.surface
        self.setStyleSheet(
            f"""
            QFrame#dropZone {{
                background-color: {background};
                border: 2px dashed {border};
                border-radius: 10px;
            }}
            """
        )

    # ------------------------------------------------------------------
    def _browse_files(self) -> None:
        paths, _filter = QFileDialog.getOpenFileNames(
            self, "Add PDFs", str(Path.home()), "PDF documents (*.pdf)"
        )
        if paths:
            self.paths_added.emit([Path(p) for p in paths])

    def _browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add folder", str(Path.home()))
        if folder:
            self.paths_added.emit([Path(folder)])

    # ------------------------------------------------------------------
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - Qt API
        if self._has_acceptable_payload(event):
            event.acceptProposedAction()
            self._hovering = True
            self._apply_style()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:  # noqa: N802 - Qt API
        self._hovering = False
        self._apply_style()
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt API
        self._hovering = False
        self._apply_style()

        paths = [
            Path(url.toLocalFile())
            for url in event.mimeData().urls()
            if url.isLocalFile() and url.toLocalFile()
        ]
        accepted = [path for path in paths if path.is_dir() or is_pdf(path)]
        if accepted:
            event.acceptProposedAction()
            self.paths_added.emit(accepted)
        else:
            event.ignore()

    @staticmethod
    def _has_acceptable_payload(event) -> bool:
        mime = event.mimeData()
        if not mime.hasUrls():
            return False
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_dir() or is_pdf(path):
                return True
        return False


__all__ = ["DropZone"]
