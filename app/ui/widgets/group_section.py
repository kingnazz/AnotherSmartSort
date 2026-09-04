"""A document group in the review workspace: header plus its page thumbnails.

This is the widget that makes grouping *visible* -- the user sees
``RESUME · Pages 5–7 · 97%`` above exactly the pages that will be exported as
one PDF.
"""

from __future__ import annotations

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QDrag, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.models.document import DocumentGroup
from app.models.page import PageAnalysis
from app.services.review_reasons import review_reasons_for
from app.services.confidence import ConfidenceThresholds
from app.ui.theme import Palette
from app.ui.widgets.badges import ConfidencePill, Pill
from app.ui.widgets.page_card import PageCard
from app.ui.widgets.flow_layout import FlowLayout


class GroupSection(QFrame):
    """One logical document, drawn as a titled block of page thumbnails."""

    selected = Signal(str)
    page_selected = Signal(str, int)
    split_requested = Signal(str, int)

    def __init__(
        self,
        group: DocumentGroup,
        pages: list[PageAnalysis],
        palette: Palette,
        thresholds: ConfidenceThresholds,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.group_id = group.id
        self._tokens = palette
        self._thresholds = thresholds
        self._selected = False
        self._flagged = group.needs_attention
        self._cards: dict[int, PageCard] = {}

        self.setObjectName("groupSection")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)

        layout.addLayout(self._build_header(group))
        self.setToolTip("\n".join(review_reasons_for(group, pages)))

        cards_host = QWidget()
        self._flow = FlowLayout(cards_host, margin=0, spacing=8)
        for page in pages:
            card = PageCard(page, palette, thresholds)
            card.clicked.connect(lambda index: self.page_selected.emit(self.group_id, index))
            card.split_requested.connect(
                lambda index: self.split_requested.emit(self.group_id, index)
            )
            self._cards[page.page_index] = card
            self._flow.addWidget(card)
        layout.addWidget(cards_host)

        self._apply_style()

    # ------------------------------------------------------------------
    def _build_header(self, group: DocumentGroup) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(9)

        self._type_label = QLabel(group.document_type.upper())
        self._type_label.setProperty("role", "sectionLabel")
        self._type_label.setStyleSheet(
            f"color: {self._tokens.text}; font-size: 9.5pt; font-weight: 700;"
        )

        self._range_label = QLabel(group.page_range_label)
        self._range_label.setProperty("role", "caption")

        self._confidence = ConfidencePill(
            group.overall_confidence, self._thresholds, self._tokens
        )

        self._review_pill = Pill(
            "Needs Review",
            background=self._tokens.warning_soft,
            foreground=self._tokens.warning,
        )
        # needs_attention, not requires_review: the queue's "N to review" counts
        # association-only flags too, so keying the pill off the narrower flag
        # let a file claim an item the board never marked.
        self._review_pill.setVisible(group.needs_attention)

        self._excluded_pill = Pill(
            "Excluded",
            background=self._tokens.surface_alt,
            foreground=self._tokens.text_muted,
        )
        self._excluded_pill.setVisible(group.excluded)

        self._candidate_label = QLabel(group.candidate.name or "No name found")
        self._candidate_label.setProperty("role", "caption")

        header.addWidget(self._type_label)
        header.addWidget(self._range_label)
        header.addWidget(self._confidence)
        header.addWidget(self._review_pill)
        header.addWidget(self._excluded_pill)
        header.addStretch(1)
        header.addWidget(self._candidate_label)
        return header

    # ------------------------------------------------------------------
    def update_group(self, group: DocumentGroup, pages: list[PageAnalysis]) -> None:
        """Refresh header and page states in place after a correction."""
        self._type_label.setText(group.document_type.upper())
        self._range_label.setText(group.page_range_label)
        self._confidence.update_value(
            group.overall_confidence, self._thresholds, self._tokens
        )
        self._review_pill.setVisible(group.needs_attention)
        self._excluded_pill.setVisible(group.excluded)
        self._candidate_label.setText(group.candidate.name or "No name found")
        self.setToolTip("\n".join(review_reasons_for(group, pages)))
        self._flagged = group.needs_attention
        self._apply_style()

        for page in pages:
            card = self._cards.get(page.page_index)
            if card is not None:
                card.update_page(page)
        self._apply_style()

    def card_for(self, page_index: int) -> PageCard | None:
        return self._cards.get(page_index)

    def page_indexes(self) -> list[int]:
        return sorted(self._cards)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._apply_style()

    def set_page_selected(self, page_index: int | None) -> None:
        for index, card in self._cards.items():
            card.set_selected(index == page_index)

    # ------------------------------------------------------------------
    def _apply_style(self) -> None:
        tokens = self._tokens
        if self._selected:
            border, width = tokens.accent, 2
        elif self._flagged:
            # Unmistakable without being loud: normal documents keep the plain
            # hairline, so a board of forty is not a wall of amber.
            border, width = tokens.warning, 2
        else:
            border, width = tokens.stroke, 1
        self.setStyleSheet(
            f"""
            QFrame#groupSection {{
                background-color: {tokens.card};
                border: {width}px solid {border};
                border-radius: 8px;
            }}
            """
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_at = event.position().toPoint()
        self.selected.emit(self.group_id)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        """Drag this document onto another applicant to refile it.

        The same payload the type board uses, so a document dragged in either
        view is understood by whatever it lands on.
        """
        press_at = getattr(self, "_press_at", None)
        if press_at is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if (event.position().toPoint() - press_at).manhattanLength() < 12:
            return

        from app.ui.widgets.type_board import DOCUMENT_MIME

        mime = QMimeData()
        mime.setData(DOCUMENT_MIME, self.group_id.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)

        preview = QPixmap(self.size())
        preview.fill(Qt.GlobalColor.transparent)
        painter = QPainter(preview)
        painter.setOpacity(0.75)
        self.render(painter)
        painter.end()
        drag.setPixmap(preview)

        drag.exec(Qt.DropAction.MoveAction)


__all__ = ["GroupSection"]
