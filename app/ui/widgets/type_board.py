"""The type-first review board: documents as cards, types as lanes.

Reviewing used to mean selecting a document, finding the right control, and
choosing from a list -- several deliberate actions to say something as simple
as "that one is a cover letter". On a file with forty documents that is a lot
of clicking to fix a handful of mistakes.

Here each logical document is a card and each document type is a lane, so
correcting a sort is dragging the card to the right pile, the way somebody
would do it with paper. Pages can be dragged too, between adjacent documents
or out into a document of their own, for the cases where the boundary rather
than the type is wrong.

Two rules hold the design together:

*The board never edits the model.* Every drop calls a service method through
the correction history, so the same rules apply however a change was made and
every change can be undone. A widget that mutated a document directly would
bypass both.

*A drop that cannot be done safely is refused, visibly.* The cursor and the
lane say so before the mouse is released, rather than the drop silently doing
something else or corrupting page order.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QMimeData, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QDrag, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.models.document import DocumentGroup
from app.models.source_file import SourceFileAnalysis
from app.services.confidence import ConfidenceThresholds
from app.ui.theme import Palette

#: Carries a document's id when a whole card is dragged.
DOCUMENT_MIME = "application/x-smartpdfsorter-document"
#: Carries "<source path>|<comma-separated page indexes>" when pages are dragged.
PAGES_MIME = "application/x-smartpdfsorter-pages"
#: The lane holding anything still flagged, whatever its predicted type.
NEEDS_REVIEW_LANE = "Needs Review"

#: How long to wait after the last scroll before asking for thumbnails. Long
#: enough that dragging a scrollbar across a 300-page file queues one batch
#: rather than one per pixel, short enough that letting go feels immediate.
SCROLL_SETTLE_MS = 90

#: How far beyond the visible area to render, as a fraction of the viewport.
#: Thumbnails are then already there when the user scrolls rather than being
#: requested at the moment they are needed and arriving after.
VIEWPORT_LOOKAHEAD = 0.75

#: Context-menu labels, named so the menu and its handler cannot drift apart.
MOVE_TO_CANDIDATE = "Move to candidate…"
NEW_CANDIDATE = "Create new candidate…"
RENAME_CANDIDATE = "Rename candidate…"


@dataclass(frozen=True)
class PageDrag:
    """The pages a drag is carrying, and which PDF they came from."""

    source_pdf: str
    page_indexes: tuple[int, ...]

    def encode(self) -> bytes:
        indexes = ",".join(str(index) for index in self.page_indexes)
        return f"{self.source_pdf}|{indexes}".encode("utf-8")

    @classmethod
    def decode(cls, payload: bytes) -> "PageDrag | None":
        try:
            text = bytes(payload).decode("utf-8")
            source, _, indexes = text.partition("|")
        except (UnicodeDecodeError, ValueError):
            return None
        if not source or not indexes:
            return None
        try:
            parsed = tuple(int(part) for part in indexes.split(",") if part)
        except ValueError:
            return None
        return cls(source_pdf=source, page_indexes=parsed) if parsed else None


class PageThumb(QLabel):
    """One draggable page inside an expanded document card."""

    clicked = Signal(int, Qt.KeyboardModifier)

    def __init__(self, page_index: int, source_pdf: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.page_index = page_index
        self.source_pdf = source_pdf
        self._selected = False
        self._press_at: QPoint | None = None

        self.setFixedSize(58, 76)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setText(str(page_index + 1))
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip(f"Page {page_index + 1} — drag to move it to another document")
        self._restyle()

    # -- selection ------------------------------------------------------
    def set_selected(self, selected: bool) -> None:
        if self._selected == selected:
            return
        self._selected = selected
        self._restyle()

    @property
    def selected(self) -> bool:
        return self._selected

    def _restyle(self) -> None:
        border = "#2563eb" if self._selected else "#cbd5e1"
        weight = 2 if self._selected else 1
        self.setStyleSheet(
            f"QLabel {{ border: {weight}px solid {border}; border-radius: 4px;"
            f" background: #ffffff; color: #475569; font-size: 8pt; }}"
        )

    def set_thumbnail(self, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            return
        self.setPixmap(
            pixmap.scaled(
                self.width() - 6,
                self.height() - 6,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    # -- drag -----------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_at = event.position().toPoint()
            self.clicked.emit(self.page_index, event.modifiers())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._press_at is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if (event.position().toPoint() - self._press_at).manhattanLength() < 12:
            return

        board = self._board()
        indexes = (
            board.selected_pages(self.source_pdf)
            if board is not None and self._selected
            else (self.page_index,)
        )
        if self.page_index not in indexes:
            indexes = (self.page_index,)

        payload = PageDrag(source_pdf=self.source_pdf, page_indexes=tuple(sorted(indexes)))
        mime = QMimeData()
        mime.setData(PAGES_MIME, payload.encode())

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(_ghost(self, f"{len(indexes)} page" + ("s" if len(indexes) != 1 else "")))
        drag.exec(Qt.DropAction.MoveAction)

    def _board(self) -> "TypeBoard | None":
        widget = self.parent()
        while widget is not None and not isinstance(widget, TypeBoard):
            widget = widget.parent()
        return widget


class DocumentCard(QFrame):
    """One logical document, draggable between lanes."""

    selected = Signal(str)
    pages_dropped = Signal(str, object)  # target group id, PageDrag
    page_clicked = Signal(str, int, Qt.KeyboardModifier)
    context_menu_requested = Signal(str, QPoint)

    def __init__(
        self,
        group: DocumentGroup,
        candidate_name: str,
        thresholds: ConfidenceThresholds,
        palette: Palette,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.group_id = group.id
        self.source_pdf = str(group.source_pdf)
        self._tokens = palette
        self._press_at: QPoint | None = None
        self._thumbs: dict[int, PageThumb] = {}

        self.setAcceptDrops(True)
        self.setProperty("role", "card")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda point: self.context_menu_requested.emit(self.group_id, point)
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        title = QLabel(candidate_name or "Unknown")
        title.setProperty("role", "subheading")
        layout.addWidget(title)

        detail = QLabel(f"{group.document_type} · {group.page_range_label}")
        detail.setProperty("role", "caption")
        layout.addWidget(detail)

        confidence = group.overall_confidence
        summary = QLabel(
            f"{group.page_count} page{'s' if group.page_count != 1 else ''} · "
            f"{confidence * 100:.0f}%"
        )
        summary.setProperty("role", "caption")
        layout.addWidget(summary)

        strip = QWidget()
        strip_layout = QHBoxLayout(strip)
        strip_layout.setContentsMargins(0, 4, 0, 0)
        strip_layout.setSpacing(4)
        for page_index in group.page_indexes[:8]:
            thumb = PageThumb(page_index, self.source_pdf, self)
            thumb.clicked.connect(
                lambda index, modifiers: self.page_clicked.emit(
                    self.group_id, index, modifiers
                )
            )
            self._thumbs[page_index] = thumb
            strip_layout.addWidget(thumb)
        if group.page_count > 8:
            more = QLabel(f"+{group.page_count - 8}")
            more.setProperty("role", "caption")
            strip_layout.addWidget(more)
        strip_layout.addStretch(1)
        layout.addWidget(strip)

        self._base_style = (
            "QFrame[role='card'] { border: 1px solid #e2e8f0; border-radius: 8px;"
            " background: #ffffff; }"
        )
        self.setStyleSheet(self._base_style)

    # -- thumbnails -----------------------------------------------------
    def page_indexes(self) -> list[int]:
        return sorted(self._thumbs)

    def thumb_for(self, page_index: int) -> PageThumb | None:
        return self._thumbs.get(page_index)

    def set_page_selected(self, page_index: int, selected: bool) -> None:
        thumb = self._thumbs.get(page_index)
        if thumb is not None:
            thumb.set_selected(selected)

    def clear_page_selection(self) -> None:
        for thumb in self._thumbs.values():
            thumb.set_selected(False)

    # -- dragging the whole document ------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_at = event.position().toPoint()
            self.selected.emit(self.group_id)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._press_at is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if (event.position().toPoint() - self._press_at).manhattanLength() < 12:
            return

        mime = QMimeData()
        mime.setData(DOCUMENT_MIME, self.group_id.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(_ghost(self, ""))
        drag.exec(Qt.DropAction.MoveAction)

    # -- accepting pages ------------------------------------------------
    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt naming
        payload = _pages_of(event)
        if payload is None or payload.source_pdf != self.source_pdf:
            event.ignore()
            return
        self._highlight(True)
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._highlight(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._highlight(False)
        payload = _pages_of(event)
        if payload is None:
            event.ignore()
            return
        self.pages_dropped.emit(self.group_id, payload)
        event.acceptProposedAction()

    def _highlight(self, active: bool) -> None:
        if active:
            self.setStyleSheet(
                "QFrame[role='card'] { border: 2px solid #2563eb; border-radius: 8px;"
                " background: #eff6ff; }"
            )
        else:
            self.setStyleSheet(self._base_style)


class TypeLane(QFrame):
    """A column of documents of one type, and a drop target for that type."""

    document_dropped = Signal(str, str)  # group id, document type

    def __init__(
        self, document_type: str, palette: Palette, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.document_type = document_type
        self._tokens = palette

        self.setAcceptDrops(True)
        self.setProperty("role", "panel")
        self.setMinimumWidth(210)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self._heading = QLabel(document_type.upper())
        self._heading.setProperty("role", "caption")
        layout.addWidget(self._heading)

        self._cards_host = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_host)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(8)
        self._cards_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self._cards_host)
        # The board scrolls horizontally between lanes; each lane scrolls its
        # own cards, so a lane with thirty documents does not stretch the page.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll = scroll
        layout.addWidget(scroll, 1)

        self._base_style = ""
        self._count = 0
        self._refresh_heading()

    # ------------------------------------------------------------------
    def add_card(self, card: DocumentCard) -> None:
        self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
        self._count += 1
        self._refresh_heading()

    def clear(self) -> None:
        while self._cards_layout.count() > 1:
            item = self._cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._count = 0
        self._refresh_heading()

    @property
    def count(self) -> int:
        return self._count

    @property
    def viewport(self) -> QWidget:
        """The area of this lane the user can actually see."""
        return self._scroll.viewport()

    @property
    def scrolled(self):
        """Fires whenever this lane's cards move under its viewport."""
        return self._scroll.verticalScrollBar().valueChanged

    def _refresh_heading(self) -> None:
        self._heading.setText(f"{self.document_type.upper()}  ({self._count})")

    # ------------------------------------------------------------------
    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if not event.mimeData().hasFormat(DOCUMENT_MIME):
            event.ignore()
            return
        self._highlight(True)
        event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.mimeData().hasFormat(DOCUMENT_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._highlight(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._highlight(False)
        data = event.mimeData().data(DOCUMENT_MIME)
        if not data:
            event.ignore()
            return
        group_id = bytes(data).decode("utf-8")
        self.document_dropped.emit(group_id, self.document_type)
        event.acceptProposedAction()

    def _highlight(self, active: bool) -> None:
        self.setStyleSheet(
            "QFrame[role='panel'] { border: 2px dashed #2563eb; border-radius: 8px; }"
            if active
            else self._base_style
        )


class TypeBoard(QWidget):
    """Lanes of document types, with documents as cards between them."""

    #: (group id, new document type)
    retype_requested = Signal(str, str)
    #: (target group id, source pdf, tuple of page indexes)
    pages_move_requested = Signal(str, str, object)
    #: (group id, point in the card's coordinates)
    context_menu_requested = Signal(str, QPoint)
    document_selected = Signal(str)
    #: Emitted when the set of cards needing thumbnails changes.
    thumbnails_needed = Signal(object)

    def __init__(
        self,
        document_types: list[str],
        thresholds: ConfidenceThresholds,
        palette: Palette,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._thresholds = thresholds
        self._tokens = palette
        self._cards: dict[str, DocumentCard] = {}
        #: Which lane each card sits in, so a scroll only has to test cards
        #: against their own lane's viewport rather than every lane's.
        self._card_lanes: dict[str, TypeLane] = {}
        self._selected_pages: dict[str, set[int]] = {}
        self._last_clicked_page: int | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        lanes_host = QWidget()
        self._lanes_layout = QHBoxLayout(lanes_host)
        self._lanes_layout.setContentsMargins(12, 12, 12, 12)
        self._lanes_layout.setSpacing(10)

        # One batch of thumbnail requests per scroll gesture, not one per
        # scrollbar step: dragging down a lane of ninety documents would
        # otherwise queue and cancel work faster than it could be rendered.
        self._scan_timer = QTimer(self)
        self._scan_timer.setSingleShot(True)
        self._scan_timer.setInterval(SCROLL_SETTLE_MS)
        self._scan_timer.timeout.connect(self._emit_visible_requests)

        self._lanes: dict[str, TypeLane] = {}
        for document_type in [*document_types, NEEDS_REVIEW_LANE]:
            lane = TypeLane(document_type, palette)
            lane.document_dropped.connect(self._on_document_dropped)
            lane.scrolled.connect(self.refresh_visible_thumbnails)
            self._lanes[document_type] = lane
            self._lanes_layout.addWidget(lane)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(lanes_host)
        scroll.horizontalScrollBar().valueChanged.connect(self.refresh_visible_thumbnails)
        scroll.verticalScrollBar().valueChanged.connect(self.refresh_visible_thumbnails)
        self._scroll = scroll
        layout.addWidget(scroll, 1)

    # ------------------------------------------------------------------
    def lane(self, document_type: str) -> TypeLane | None:
        return self._lanes.get(document_type)

    def card(self, group_id: str) -> DocumentCard | None:
        return self._cards.get(group_id)

    def scroll_document_into_view(self, group_id: str) -> bool:
        """Bring one document's card on screen. False if it is not on the board.

        Used when arriving from a "Review Needed" queue row: the flagged card
        is often far down a long lane, and selecting something the user cannot
        see is barely better than not selecting it at all.
        """
        card = self._cards.get(group_id)
        if card is None:
            return False
        self._scroll.ensureWidgetVisible(card, 0, 40)
        return True

    def lane_counts(self) -> dict[str, int]:
        return {name: lane.count for name, lane in self._lanes.items()}

    def load(self, files: list[SourceFileAnalysis], *, wanted: tuple[str, ...] = ()) -> None:
        """Rebuild the board from the analysed files."""
        for lane in self._lanes.values():
            lane.clear()
        self._cards.clear()
        self._card_lanes.clear()
        self._selected_pages.clear()

        for analysis in files:
            for group in analysis.groups:
                if group.excluded or not group.export_page_indexes:
                    continue
                if wanted and group.document_type not in wanted:
                    continue
                packet = analysis.packet_for_document(group)
                name = (
                    packet.candidate.name
                    if packet is not None and packet.candidate.name
                    else group.candidate.display_name
                )
                lane_name = (
                    NEEDS_REVIEW_LANE if group.needs_attention else group.document_type
                )
                lane = self._lanes.get(lane_name) or self._lanes.get(NEEDS_REVIEW_LANE)
                if lane is None:
                    continue

                card = DocumentCard(group, name, self._thresholds, self._tokens)
                card.selected.connect(self.document_selected.emit)
                card.pages_dropped.connect(self._on_pages_dropped)
                card.page_clicked.connect(self._on_page_clicked)
                card.context_menu_requested.connect(self.context_menu_requested.emit)
                self._cards[group.id] = card
                self._card_lanes[group.id] = lane
                lane.add_card(card)

        self.refresh_visible_thumbnails(immediate=True)

    # ------------------------------------------------------------------
    # Lazy thumbnails
    # ------------------------------------------------------------------
    def refresh_visible_thumbnails(self, *_args, immediate: bool = False) -> None:
        """Ask again for whatever is on screen now.

        Called on every scroll, resize and rebuild. Takes ``*_args`` because Qt
        scrollbars pass their new value; the value is irrelevant, only that
        something moved.
        """
        if immediate:
            self._scan_timer.stop()
            self._emit_visible_requests()
            return
        self._scan_timer.start()

    def _emit_visible_requests(self) -> None:
        self.thumbnails_needed.emit(self.visible_page_requests())

    def visible_page_requests(self) -> list[tuple[str, int]]:
        """Pages worth rendering now: those on cards the user can nearly see.

        A 300-page file has hundreds of thumbnails; rendering them all before
        showing anything is what made large files feel broken. Only what is on
        screen -- plus a little beyond, so scrolling meets finished pictures
        rather than placeholders -- is requested, and the rest is asked for as
        it comes into view.
        """
        # A board nobody is looking at needs no pictures. This is not just an
        # optimisation: the review workspace defers building the board until
        # it is opened, and rendering for a hidden board would spend the exact
        # time that deferral was there to save.
        if not self.isVisible():
            return []

        windows: dict[int, tuple[QWidget, QRect]] = {}
        for lane in self._lanes.values():
            viewport = lane.viewport
            window = viewport.rect()
            if window.isEmpty():
                continue
            margin = int(window.height() * VIEWPORT_LOOKAHEAD)
            windows[id(lane)] = (viewport, window.adjusted(0, -margin, 0, margin))

        wanted: list[tuple[str, int]] = []
        for group_id, card in self._cards.items():
            lane = self._card_lanes.get(group_id)
            if lane is None:
                continue
            found = windows.get(id(lane))
            if found is None or not _within(card, *found):
                continue
            for page_index in card.page_indexes():
                wanted.append((card.source_pdf, page_index))
        return wanted

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """A wider window shows more cards, which need more thumbnails."""
        super().resizeEvent(event)
        self.refresh_visible_thumbnails()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        self.refresh_visible_thumbnails()

    def apply_thumbnail(self, source_pdf: str, page_index: int, pixmap: QPixmap) -> None:
        for card in self._cards.values():
            if card.source_pdf != source_pdf:
                continue
            thumb = card.thumb_for(page_index)
            if thumb is not None:
                thumb.set_thumbnail(pixmap)

    # ------------------------------------------------------------------
    def selected_pages(self, source_pdf: str) -> tuple[int, ...]:
        return tuple(sorted(self._selected_pages.get(source_pdf, set())))

    def _on_page_clicked(
        self, group_id: str, page_index: int, modifiers: Qt.KeyboardModifier
    ) -> None:
        """Ctrl adds one page; Shift extends a run; a plain click starts over."""
        card = self._cards.get(group_id)
        if card is None:
            return
        chosen = self._selected_pages.setdefault(card.source_pdf, set())

        if modifiers & Qt.KeyboardModifier.ControlModifier:
            chosen.symmetric_difference_update({page_index})
        elif modifiers & Qt.KeyboardModifier.ShiftModifier and self._last_clicked_page is not None:
            low, high = sorted((self._last_clicked_page, page_index))
            chosen.update(range(low, high + 1))
        else:
            chosen.clear()
            chosen.add(page_index)

        self._last_clicked_page = page_index
        for other in self._cards.values():
            if other.source_pdf != card.source_pdf:
                other.clear_page_selection()
                continue
            for index in other.page_indexes():
                other.set_page_selected(index, index in chosen)

    def _on_document_dropped(self, group_id: str, document_type: str) -> None:
        if document_type == NEEDS_REVIEW_LANE:
            # "Needs Review" is a state, not a type. Dropping into it would be
            # asking to un-decide something, which is not a correction.
            return
        self.retype_requested.emit(group_id, document_type)

    def _on_pages_dropped(self, target_group_id: str, payload: PageDrag) -> None:
        self.pages_move_requested.emit(
            target_group_id, payload.source_pdf, payload.page_indexes
        )


# ----------------------------------------------------------------------
def _within(card: QWidget, viewport: QWidget, window: QRect) -> bool:
    """Whether a card falls inside a lane's (widened) visible window.

    Uses geometry rather than ``visibleRegion``, which reports nothing for a
    widget whose window is not on screen -- true of every card during a test
    run, and of a board being populated before it is shown.
    """
    if not viewport.isAncestorOf(card):
        return False
    top_left = card.mapTo(viewport, QPoint(0, 0))
    return QRect(top_left, card.size()).intersects(window)


def _pages_of(event) -> PageDrag | None:
    mime: QMimeData = event.mimeData()
    if not mime.hasFormat(PAGES_MIME):
        return None
    return PageDrag.decode(mime.data(PAGES_MIME))


def _ghost(widget: QWidget, caption: str) -> QPixmap:
    """A translucent picture of what is being dragged."""
    pixmap = QPixmap(widget.size())
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setOpacity(0.75)
    widget.render(painter)
    if caption:
        painter.setOpacity(1.0)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, caption)
    painter.end()
    return pixmap


def build_context_menu(parent: QWidget, document_types: list[str]) -> QMenu:
    """The right-click alternative to dragging.

    Everything the board can do by drag is also reachable from a menu, because
    dragging is not available to everyone and is not discoverable on its own.
    """
    menu = QMenu(parent)
    type_menu = menu.addMenu("Change type")
    for document_type in document_types:
        type_menu.addAction(document_type)
    menu.addSeparator()
    # Two distinct actions, deliberately not one: filing a document under
    # somebody already in the file is the common case, and creating a person
    # who is not there yet is a different decision with a different risk.
    menu.addAction(MOVE_TO_CANDIDATE)
    menu.addAction(NEW_CANDIDATE)
    menu.addAction(RENAME_CANDIDATE)
    menu.addSeparator()
    menu.addAction("Merge with previous")
    menu.addAction("Merge with next")
    menu.addSeparator()
    menu.addAction("Exclude from export")
    return menu


__all__ = [
    "TypeBoard",
    "TypeLane",
    "DocumentCard",
    "PageThumb",
    "PageDrag",
    "build_context_menu",
    "DOCUMENT_MIME",
    "PAGES_MIME",
    "NEEDS_REVIEW_LANE",
    "MOVE_TO_CANDIDATE",
    "NEW_CANDIDATE",
    "RENAME_CANDIDATE",
]
