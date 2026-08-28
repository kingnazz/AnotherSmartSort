"""A candidate packet in the review workspace.

The review panel is candidate-first rather than document-first. Eighty pages of
mixed applicants presented as one flat list of forty documents is unreadable;
the same eighty pages presented as fifteen named people, each with their three
or four documents underneath, is the shape the reviewer already thinks in.

This widget draws the header for one such person and hosts their documents.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.models.packet import CandidatePacket
from app.services.confidence import ConfidenceThresholds
from app.ui.theme import Palette
from app.ui.widgets.badges import ConfidencePill, Pill


class PacketSection(QFrame):
    """One applicant: a titled header with their documents beneath it."""

    selected = Signal(str)
    rename_requested = Signal(str)
    merge_requested = Signal(str)
    accept_requested = Signal(str)
    #: (packet id, dropped document id) -- a document refiled by dragging it
    #: onto this applicant.
    document_dropped = Signal(str, str)

    def __init__(
        self,
        packet: CandidatePacket,
        palette: Palette,
        thresholds: ConfidenceThresholds,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.packet_id = packet.id
        self._tokens = palette
        self._thresholds = thresholds

        self.setObjectName("packetSection")
        self.setProperty("unknown", packet.is_unknown)
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        layout.addLayout(self._build_header(packet))

        self.documents_host = QWidget()
        self._documents_layout = QVBoxLayout(self.documents_host)
        self._documents_layout.setContentsMargins(12, 0, 0, 0)
        self._documents_layout.setSpacing(10)
        layout.addWidget(self.documents_host)

        self.update_packet(packet)

    # ------------------------------------------------------------------
    def _build_header(self, packet: CandidatePacket) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(10)

        self.name_label = QLabel(packet.display_name)
        self.name_label.setProperty("role", "packetName")
        font = self.name_label.font()
        font.setPointSizeF(font.pointSizeF() + 1.5)
        font.setBold(True)
        self.name_label.setFont(font)
        header.addWidget(self.name_label)

        self.summary_label = QLabel()
        self.summary_label.setProperty("role", "muted")
        header.addWidget(self.summary_label)

        header.addStretch(1)

        self.review_pill = Pill(
            "Review", background=self._tokens.warning_soft, foreground=self._tokens.warning
        )
        self.review_pill.setVisible(False)
        header.addWidget(self.review_pill)

        self.confidence_pill = ConfidencePill(
            packet.association_confidence, self._thresholds, self._tokens
        )
        header.addWidget(self.confidence_pill)

        self.rename_button = QPushButton("Rename")
        self.rename_button.setProperty("role", "link")
        self.rename_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rename_button.clicked.connect(lambda: self.rename_requested.emit(self.packet_id))
        header.addWidget(self.rename_button)

        self.merge_button = QPushButton("Merge…")
        self.merge_button.setProperty("role", "link")
        self.merge_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.merge_button.clicked.connect(lambda: self.merge_requested.emit(self.packet_id))
        header.addWidget(self.merge_button)

        self.accept_button = QPushButton("Looks right")
        self.accept_button.setProperty("role", "link")
        self.accept_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.accept_button.clicked.connect(lambda: self.accept_requested.emit(self.packet_id))
        header.addWidget(self.accept_button)

        return header

    # ------------------------------------------------------------------
    def add_document_widget(self, widget: QWidget) -> None:
        self._documents_layout.addWidget(widget)

    def update_packet(self, packet: CandidatePacket) -> None:
        """Refresh the header after a correction."""
        self.name_label.setText(packet.display_name)
        documents = packet.document_count
        self.summary_label.setText(
            f"{packet.page_range_label}  ·  "
            f"{documents} document{'s' if documents != 1 else ''}"
        )
        self.confidence_pill.setVisible(not packet.is_unknown)
        if not packet.is_unknown:
            self.confidence_pill.update_value(
                packet.association_confidence, self._thresholds, self._tokens
            )
        self.review_pill.setVisible(packet.requires_review)
        self.accept_button.setVisible(packet.requires_review and not packet.is_unknown)
        self.merge_button.setVisible(not packet.is_unknown)
        self.setToolTip("\n".join(packet.review_reasons) if packet.review_reasons else "")

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.selected.emit(self.packet_id)
        super().mousePressEvent(event)

    # ------------------------------------------------------------------
    # Accepting a document dragged from another applicant
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """A candidate's block is a drop target for somebody else's document.

        This is the physical version of "that resume is Jane's, not Robert's":
        drag it onto Jane. The menu route stays for anyone not using a mouse.
        """
        if not event.mimeData().hasFormat(_document_mime()):
            event.ignore()
            return
        self._set_drop_highlight(True)
        event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.mimeData().hasFormat(_document_mime()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._set_drop_highlight(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._set_drop_highlight(False)
        data = event.mimeData().data(_document_mime())
        if not data:
            event.ignore()
            return
        group_id = bytes(data).decode("utf-8")
        self.document_dropped.emit(self.packet_id, group_id)
        event.acceptProposedAction()

    def _set_drop_highlight(self, active: bool) -> None:
        """Show that this applicant will take the document being dragged.

        Styled inline rather than through a property selector: this widget has
        no entry in the theme stylesheet, so a property alone would change
        nothing on screen and the drop would give no feedback at all.
        """
        self.setProperty("dropTarget", active)
        self.setStyleSheet(
            "QFrame#packetSection { border: 2px dashed #2563eb; border-radius: 8px; }"
            if active
            else ""
        )


def _document_mime() -> str:
    from app.ui.widgets.type_board import DOCUMENT_MIME

    return DOCUMENT_MIME


__all__ = ["PacketSection"]
