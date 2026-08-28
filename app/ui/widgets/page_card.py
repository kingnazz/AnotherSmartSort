"""Page thumbnail card shown inside a document group in the review workspace."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QMouseEvent, QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from app.models.page import PageAnalysis
from app.services.confidence import ConfidenceThresholds, confidence_percent
from app.ui.theme import Palette

THUMBNAIL_SIZE = QSize(104, 134)


class PageCard(QFrame):
    """One page: thumbnail, page number, and any review warning."""

    clicked = Signal(int)
    split_requested = Signal(int)

    def __init__(
        self,
        page: PageAnalysis,
        palette: Palette,
        thresholds: ConfidenceThresholds,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.page_index = page.page_index
        self._tokens = palette
        self._thresholds = thresholds
        self._selected = False

        self.setObjectName("pageCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(THUMBNAIL_SIZE.width() + 16)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 6)
        layout.setSpacing(5)

        self._thumbnail = QLabel()
        self._thumbnail.setFixedSize(THUMBNAIL_SIZE)
        self._thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumbnail.setStyleSheet(
            f"background-color: {palette.surface_alt};"
            f"border: 1px solid {palette.stroke}; border-radius: 4px;"
        )
        self._thumbnail.setText("…")

        self._caption = QLabel(self._caption_text(page))
        self._caption.setProperty("role", "caption")
        self._caption.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self._thumbnail, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self._caption)

        self.update_page(page)
        self._apply_style()

    # ------------------------------------------------------------------
    def set_thumbnail(self, pixmap: QPixmap) -> None:
        scaled = pixmap.scaled(
            THUMBNAIL_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._thumbnail.setPixmap(scaled)

    def update_page(self, page: PageAnalysis) -> None:
        self._caption.setText(self._caption_text(page))
        self.setToolTip(self._tooltip_text(page))
        self._needs_review = page.requires_review
        self._excluded = page.excluded or page.is_excluded_separator
        self._apply_style()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._apply_style()

    # ------------------------------------------------------------------
    def _caption_text(self, page: PageAnalysis) -> str:
        marker = ""
        if page.is_excluded_separator:
            marker = "  ⊘"
        elif page.requires_review:
            marker = "  !"
        return f"Page {page.page_number}{marker}"

    def _tooltip_text(self, page: PageAnalysis) -> str:
        lines = [
            f"Page {page.page_number} of {page.page_count}",
            f"Type: {page.predicted_type} "
            f"({confidence_percent(page.classification_confidence)})",
            f"Grouping: {'starts a new document' if page.starts_new_document else 'continues the previous document'} "
            f"({confidence_percent(page.boundary_confidence)})",
            f"Text: {page.text_source.value}"
            + (" (OCR)" if page.ocr_used else ""),
        ]
        if page.separator_label:
            state = "excluded from output" if page.is_excluded_separator else "included"
            lines.append(f"Separator page for {page.separator_label} - {state}")
        if page.review_reasons:
            lines.append("")
            lines.extend(f"• {reason}" for reason in page.review_reasons)
        if page.boundary_reasons:
            lines.append("")
            lines.append("Why:")
            lines.extend(f"• {reason}" for reason in page.boundary_reasons[:4])
        return "\n".join(lines)

    def _apply_style(self) -> None:
        tokens = self._tokens
        border = tokens.stroke
        background = tokens.card
        width = 1

        if getattr(self, "_needs_review", False):
            border = tokens.warning
        if getattr(self, "_excluded", False):
            background = tokens.surface_alt
        if self._selected:
            border = tokens.accent
            background = tokens.accent_soft
            width = 2

        self.setStyleSheet(
            f"""
            QFrame#pageCard {{
                background-color: {background};
                border: {width}px solid {border};
                border-radius: 6px;
            }}
            QFrame#pageCard:hover {{
                border-color: {tokens.accent};
            }}
            """
        )

    # ------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.page_index)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self.split_requested.emit(self.page_index)
        super().mouseDoubleClickEvent(event)


__all__ = ["PageCard", "THUMBNAIL_SIZE"]
