"""Processing history: previous jobs, their summaries, and their output folders."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.storage.history_store import HistoryEntry, HistoryStore
from app.ui.theme import Palette
from app.ui.widgets.badges import SectionLabel
from app.utils.system import open_in_file_manager

_COLUMNS = ("When", "PDFs", "Pages", "Documents", "Exported", "Review", "Errors", "Output folder")
_PATH_ROLE = Qt.ItemDataRole.UserRole


class HistoryDialog(QDialog):
    """Shows past jobs and opens their output folders."""

    def __init__(
        self, store: HistoryStore, palette: Palette, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._tokens = palette
        self._entries: list[HistoryEntry] = []

        self.setWindowTitle("Processing history")
        self.setMinimumSize(880, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        layout.addWidget(SectionLabel("Previous jobs"))

        self._empty_label = QLabel(
            "Nothing has been processed yet.\n"
            "Once you analyze and export some PDFs, each job will be listed here."
        )
        self._empty_label.setProperty("role", "body")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        layout.addWidget(self._empty_label)

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.itemSelectionChanged.connect(self._update_buttons)
        self._table.itemDoubleClicked.connect(lambda _item: self._open_output())

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(len(_COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)
        for index in range(len(_COLUMNS) - 1):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._table, 1)

        self._detail = QLabel("")
        self._detail.setProperty("role", "caption")
        self._detail.setWordWrap(True)
        layout.addWidget(self._detail)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)

        self._open_button = QPushButton("Open output folder")
        self._open_button.clicked.connect(self._open_output)

        clear_button = QPushButton("Clear history")
        clear_button.setProperty("variant", "danger")
        clear_button.clicked.connect(self._clear)

        close_button = QPushButton("Close")
        close_button.setProperty("variant", "accent")
        close_button.clicked.connect(self.accept)
        close_button.setDefault(True)

        buttons.addWidget(self._open_button)
        buttons.addWidget(clear_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self.reload()

    # ------------------------------------------------------------------
    def reload(self) -> None:
        self._entries = self._store.recent_jobs()
        self._table.setRowCount(0)

        for entry in self._entries:
            row = self._table.rowCount()
            self._table.insertRow(row)

            when = QTableWidgetItem(entry.display_time)
            when.setData(_PATH_ROLE, entry.output_directory or "")
            self._table.setItem(row, 0, when)

            values = (
                entry.pdfs_processed,
                entry.pages_processed,
                entry.documents_found,
                entry.documents_exported,
                entry.review_documents,
                entry.error_count,
            )
            for offset, value in enumerate(values, start=1):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                if offset == 6 and value:
                    item.setToolTip(entry.error_summary or "")
                self._table.setItem(row, offset, item)

            folder = QTableWidgetItem(entry.output_directory or "—")
            folder.setToolTip(entry.output_directory or "")
            self._table.setItem(row, 7, folder)

        has_rows = bool(self._entries)
        self._table.setVisible(has_rows)
        self._empty_label.setVisible(not has_rows)
        if not self._store.is_available:
            self._empty_label.setText(
                "Processing history is unavailable on this computer.\n"
                "Analysis and export still work normally."
            )
        if has_rows:
            self._table.selectRow(0)
        self._update_buttons()

    # ------------------------------------------------------------------
    def _selected_entry(self) -> HistoryEntry | None:
        rows = self._table.selectionModel().selectedRows() if self._table.selectionModel() else []
        if not rows:
            return None
        index = rows[0].row()
        if 0 <= index < len(self._entries):
            return self._entries[index]
        return None

    def _update_buttons(self) -> None:
        entry = self._selected_entry()
        folder = Path(entry.output_directory) if entry and entry.output_directory else None
        self._open_button.setEnabled(bool(folder and folder.exists()))

        if entry is None:
            self._detail.setText("")
            return

        parts = [entry.summary, f"took {entry.duration_seconds:.1f}s"]
        if entry.pages_ai:
            parts.append(
                f"{entry.pages_local} pages classified locally, "
                f"{entry.pages_ai} with AI ({entry.ai_requests} requests)"
            )
        if entry.ocr_pages:
            parts.append(f"{entry.ocr_pages} pages needed OCR")
        if entry.sources:
            shown = ", ".join(entry.sources[:4])
            if len(entry.sources) > 4:
                shown += f" and {len(entry.sources) - 4} more"
            parts.append(shown)
        if entry.error_summary:
            parts.append(f"Errors: {entry.error_summary}")
        self._detail.setText("  ·  ".join(parts))

    def _open_output(self) -> None:
        entry = self._selected_entry()
        if entry is None or not entry.output_directory:
            return
        folder = Path(entry.output_directory)
        if not folder.exists():
            QMessageBox.information(
                self, "History", "That output folder no longer exists."
            )
            return
        open_in_file_manager(folder)

    def _clear(self) -> None:
        if not self._entries:
            return
        confirm = QMessageBox.question(
            self,
            "Clear history",
            "Remove the record of every previous job?\n\n"
            "This only clears the history list. No exported PDFs are deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm is QMessageBox.StandardButton.Yes:
            self._store.clear()
            self.reload()


__all__ = ["HistoryDialog"]
