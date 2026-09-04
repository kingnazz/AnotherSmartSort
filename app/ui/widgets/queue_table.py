"""The file queue table on the home screen."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from app.models.enums import FileStatus
from app.models.source_file import SourceFileAnalysis
from app.services.confidence import ConfidenceThresholds, confidence_percent
from app.ui.theme import Palette
from app.ui.widgets.badges import ConfidencePill, StatusPill

COLUMNS = ("File", "Pages", "Status", "Documents", "Confidence")

#: Per-row flag for 'this file has review items', so the queue hint can be
#: derived from the table itself rather than re-reading the model.
_REVIEW_ROLE = Qt.ItemDataRole.UserRole + 1


class QueueTable(QTableWidget):
    """Shows every queued PDF with live status, documents found and confidence."""

    file_activated = Signal(str)

    def __init__(
        self,
        palette: Palette,
        thresholds: ConfidenceThresholds,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(0, len(COLUMNS), parent)
        self._palette_tokens = palette
        self._thresholds = thresholds
        self._rows: dict[str, int] = {}

        self.setHorizontalHeaderLabels(COLUMNS)
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setWordWrap(False)
        self.verticalHeader().setDefaultSectionSize(40)

        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for index in range(1, len(COLUMNS)):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
        header.setHighlightSections(False)

        self.itemDoubleClicked.connect(self._on_double_click)

    # ------------------------------------------------------------------
    def set_palette_tokens(self, palette: Palette) -> None:
        self._palette_tokens = palette

    def set_thresholds(self, thresholds: ConfidenceThresholds) -> None:
        self._thresholds = thresholds

    def clear_files(self) -> None:
        self.setRowCount(0)
        self._rows.clear()

    def row_for(self, path: str | Path) -> int | None:
        return self._rows.get(str(path))

    def selected_path(self) -> str | None:
        rows = self.selectionModel().selectedRows() if self.selectionModel() else []
        if not rows:
            return None
        item = self.item(rows[0].row(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    # ------------------------------------------------------------------
    def upsert(self, analysis: SourceFileAnalysis) -> None:
        """Insert or refresh the row for a file."""
        key = str(analysis.path)
        row = self._rows.get(key)
        if row is None:
            row = self.rowCount()
            self.insertRow(row)
            self._rows[key] = row

        name_item = QTableWidgetItem(analysis.name)
        name_item.setData(Qt.ItemDataRole.UserRole, key)
        tooltip = str(analysis.path)
        if analysis.duplicate_of:
            name_item.setText(f"{analysis.name}   ⟳")
            tooltip += (
                f"\n\nThis file appears to have been processed previously "
                f"(as {analysis.duplicate_of})."
            )
        if analysis.error:
            tooltip += f"\n\n{analysis.error}"
        name_item.setToolTip(tooltip)
        name_item.setData(_REVIEW_ROLE, bool(analysis.review_group_count))
        self.setItem(row, 0, name_item)

        pages = QTableWidgetItem(str(analysis.page_count) if analysis.page_count else "—")
        pages.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setItem(row, 1, pages)

        self.setCellWidget(row, 2, self._status_cell(analysis))

        documents = self._documents_text(analysis)
        documents_item = QTableWidgetItem(documents)
        documents_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        review = analysis.review_group_count
        if review:
            documents_item.setToolTip(
                f"{self._document_phrase(review)} needs review.\n"
                "Double-click this row to go directly to it."
                if review == 1
                else f"{self._document_phrase(review)} need review.\n"
                "Double-click this row to go directly to them."
            )
        self.setItem(row, 3, documents_item)

        self.setCellWidget(row, 4, self._confidence_cell(analysis))

    def set_progress_text(self, path: str | Path, text: str) -> None:
        """Show a transient operation label (``Reading page 3 of 10``)."""
        row = self._rows.get(str(path))
        if row is None:
            return
        label = QLabel(text)
        label.setProperty("role", "caption")
        label.setContentsMargins(8, 0, 8, 0)
        self.setCellWidget(row, 2, label)

    # ------------------------------------------------------------------
    def _status_cell(self, analysis: SourceFileAnalysis) -> QWidget:
        container = QWidget()
        from PySide6.QtWidgets import QHBoxLayout

        layout = QHBoxLayout(container)
        layout.setContentsMargins(8, 0, 8, 0)
        pill = StatusPill(analysis.status, self._palette_tokens)
        if analysis.error:
            pill.setToolTip(analysis.error)
        elif analysis.status is FileStatus.REVIEW_NEEDED:
            pill.setToolTip(self._review_tooltip(analysis))
        layout.addWidget(pill)
        layout.addStretch(1)
        return container

    def _confidence_cell(self, analysis: SourceFileAnalysis) -> QWidget:
        container = QWidget()
        from PySide6.QtWidgets import QHBoxLayout

        layout = QHBoxLayout(container)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lowest = analysis.lowest_confidence
        if lowest is None:
            placeholder = QLabel("—")
            placeholder.setProperty("role", "caption")
            layout.addWidget(placeholder)
        else:
            pill = ConfidencePill(lowest, self._thresholds, self._palette_tokens)
            pill.setToolTip(
                f"Lowest document confidence in this file: {confidence_percent(lowest)}"
            )
            layout.addWidget(pill)
        return container

    @staticmethod
    def _document_phrase(count: int) -> str:
        return "1 document" if count == 1 else f"{count} documents"

    def _review_tooltip(self, analysis: SourceFileAnalysis) -> str:
        count = analysis.review_group_count
        if count == 1:
            return (
                "This file has 1 document that needs review.\n"
                "Double-click this row to open the item that needs attention."
            )
        return (
            f"This file has {count} documents that need review.\n"
            "Double-click this row to open the items that need attention."
        )

    def review_hint(self) -> str:
        """A one-line nudge for the queue, empty when nothing needs review."""
        return (
            "Double-click a Review Needed row to go directly to the item."
            if self.review_file_count()
            else ""
        )

    def review_file_count(self) -> int:
        rows = (self.item(row, 0) for row in range(self.rowCount()))
        return sum(1 for item in rows if item is not None and item.data(_REVIEW_ROLE))

    @staticmethod
    def _documents_text(analysis: SourceFileAnalysis) -> str:
        if analysis.status is FileStatus.ERROR:
            return "—"
        if not analysis.groups:
            return "—"
        count = len(analysis.groups)
        review = analysis.review_group_count
        if review:
            return f"{count}  ({review} to review)"
        return str(count)

    def _on_double_click(self, item: QTableWidgetItem) -> None:
        path_item = self.item(item.row(), 0)
        if path_item is not None:
            path = path_item.data(Qt.ItemDataRole.UserRole)
            if path:
                self.file_activated.emit(path)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(760, 320)


__all__ = ["QueueTable", "COLUMNS"]
