"""The review workspace: source list, grouped page thumbnails, inspector.

This is where the user does the only work the product asks of them -- checking
the handful of documents the pipeline was unsure about. Everything here is a
view over the domain model; all corrections are delegated to
:class:`~app.services.grouping_service.GroupingService` so the same rules apply
however a change was made.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.models.document import DocumentGroup
from app.models.enums import FileStatus
from app.models.packet import CandidatePacket
from app.models.source_file import SourceFileAnalysis
from app.services.review_reasons import flagged_groups, review_summary
from app.services.confidence import ConfidenceThresholds
from app.services.correction_history import CorrectionHistory
from app.services.grouping_service import GroupingService
from app.services.packet_service import CandidatePacketService
from app.ui.theme import Palette
from app.ui.widgets.badges import SectionLabel
from app.ui.widgets.group_section import GroupSection
from app.ui.widgets.inspector import Inspector
from app.ui.widgets.packet_section import PacketSection
from app.ui.widgets.type_board import TypeBoard
from app.workers.thumbnail_worker import ThumbnailCache, ThumbnailWorker

_PATH_ROLE = Qt.ItemDataRole.UserRole

#: Which review layout is showing. Type-first is the default: the everyday
#: correction is "that is the wrong kind of document", and a board of type
#: lanes answers it in one drag.
_BY_TYPE = 0
_BY_CANDIDATE = 1


class ReviewView(QWidget):
    """Three-panel review workspace."""

    back_requested = Signal()
    export_requested = Signal()
    documents_changed = Signal()

    def __init__(
        self,
        grouping: GroupingService,
        thresholds: ConfidenceThresholds,
        palette: Palette,
        document_types: list[str],
        parent: QWidget | None = None,
        packets: CandidatePacketService | None = None,
    ) -> None:
        super().__init__(parent)
        self._grouping = grouping
        self._packets = packets or CandidatePacketService(
            grouping.profile, thresholds
        )
        self._thresholds = thresholds
        self._tokens = palette
        self._files: list[SourceFileAnalysis] = []
        self._current: SourceFileAnalysis | None = None
        self._sections: dict[str, GroupSection] = {}
        self._packet_sections: dict[str, PacketSection] = {}
        self._selected_packet_id: str | None = None
        self._selected_group_id: str | None = None
        self._selected_page_index: int | None = None
        self._review_only = False
        #: Document types the user asked for on the home screen. Empty means all.
        self._type_filter: tuple[str, ...] = ()
        #: Every correction made here is undoable, whether it came from a drag,
        #: a menu or the inspector.
        self.history = CorrectionHistory()
        self._document_types = list(document_types)
        #: Set when a correction happened while the board was hidden, so it is
        #: rebuilt once on the way back rather than on every change.
        self._board_stale = False

        self._cache = ThumbnailCache()
        self._thumbnails = ThumbnailWorker(self)
        self._thumbnails.rendered.connect(self._on_thumbnail_ready)
        self._thumbnails.start()

        self._build_ui(document_types)

    # ------------------------------------------------------------------
    def _build_ui(self, document_types: list[str]) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_toolbar())
        layout.addWidget(self._build_review_banner())

        # Two ways to look at the same documents. The type board is the
        # default because the common correction is about a document's kind;
        # the candidate view answers "what did this person send?" instead.
        self._modes = QStackedWidget()

        self.type_board = TypeBoard(document_types, self._thresholds, self._tokens)
        self.type_board.retype_requested.connect(self._retype_from_board)
        self.type_board.pages_move_requested.connect(self._move_pages_from_board)
        self.type_board.document_selected.connect(self._select_group)
        self.type_board.context_menu_requested.connect(self._show_document_menu)
        self.type_board.thumbnails_needed.connect(self._request_board_thumbnails)
        self._modes.addWidget(self.type_board)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(1)

        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_center_panel())

        self.inspector = Inspector(document_types, self._tokens, self._thresholds)
        # Small enough that the three panels still fit a 1366x768 laptop; the
        # inspector scrolls internally, so a narrower panel loses nothing.
        self.inspector.setMinimumWidth(240)
        self.inspector.type_changed.connect(self._change_type)
        self.inspector.split_requested.connect(self._split_before)
        self.inspector.merge_previous_requested.connect(self._merge_previous)
        self.inspector.merge_next_requested.connect(self._merge_next)
        self.inspector.mark_other_requested.connect(self._mark_other)
        self.inspector.exclude_toggled.connect(self._set_excluded)
        self.inspector.separator_toggled.connect(self._set_separator)
        self.inspector.accept_requested.connect(self._accept_group)
        self.inspector.move_to_candidate_requested.connect(self.move_document_to_candidate)
        self.inspector.new_candidate_requested.connect(self._new_candidate_from_document)

        inspector_frame = QFrame()
        inspector_frame.setProperty("role", "panel")
        inspector_layout = QVBoxLayout(inspector_frame)
        inspector_layout.setContentsMargins(0, 0, 0, 0)
        inspector_layout.addWidget(self.inspector)
        splitter.addWidget(inspector_frame)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        self._splitter = splitter
        self._modes.addWidget(splitter)
        self._modes.setCurrentIndex(_BY_TYPE)
        layout.addWidget(self._modes, 1)

    def _build_toolbar(self) -> QWidget:
        bar = QFrame()
        bar.setProperty("role", "header")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(10)

        back = QPushButton("← Back")
        back.setProperty("variant", "subtle")
        back.clicked.connect(self.back_requested.emit)
        back.setShortcut("Alt+Left")

        title = QLabel("Review documents")
        title.setProperty("role", "subheading")

        self._summary_label = QLabel("")
        self._summary_label.setProperty("role", "caption")

        self.by_type_button = QPushButton("By Type")
        self.by_type_button.setCheckable(True)
        self.by_type_button.setChecked(True)
        self.by_type_button.setToolTip("Drag documents between type lanes to correct them")
        self.by_type_button.clicked.connect(lambda: self.set_view_mode(_BY_TYPE))

        self.by_candidate_button = QPushButton("By Candidate")
        self.by_candidate_button.setCheckable(True)
        self.by_candidate_button.setToolTip("See each applicant's documents together")
        self.by_candidate_button.clicked.connect(lambda: self.set_view_mode(_BY_CANDIDATE))

        self.undo_button = QPushButton("Undo")
        self.undo_button.setProperty("variant", "subtle")
        self.undo_button.setShortcut("Ctrl+Z")
        self.undo_button.clicked.connect(self.undo)

        self.redo_button = QPushButton("Redo")
        self.redo_button.setProperty("variant", "subtle")
        self.redo_button.setShortcut("Ctrl+Y")
        self.redo_button.clicked.connect(self.redo)

        self.review_filter_button = QPushButton("Review Needed")
        self.review_filter_button.setCheckable(True)
        self.review_filter_button.toggled.connect(self._toggle_review_filter)
        self.review_filter_button.setToolTip(
            "Show only the documents that need a decision"
        )

        self.approve_all_button = QPushButton("Approve all")
        self.approve_all_button.setProperty("variant", "subtle")
        self.approve_all_button.clicked.connect(self._approve_all)
        self.approve_all_button.setToolTip(
            "Accept every flagged document as correct, without opening each one"
        )

        self.export_button = QPushButton("Split && Save")
        self.export_button.setProperty("variant", "accent")
        self.export_button.clicked.connect(self.export_requested.emit)
        self.export_button.setToolTip("Export every included document (Ctrl+E)")

        layout.addWidget(back)
        layout.addWidget(title)
        layout.addWidget(self.by_type_button)
        layout.addWidget(self.by_candidate_button)
        layout.addWidget(self._summary_label)
        layout.addStretch(1)
        layout.addWidget(self.undo_button)
        layout.addWidget(self.redo_button)
        layout.addWidget(self.review_filter_button)
        layout.addWidget(self.approve_all_button)
        layout.addWidget(self.export_button)
        return bar

    def _build_review_banner(self) -> QWidget:
        """The strip that says what needs review here, and walks through it.

        It exists because the queue could say "Review Needed" and the workspace
        could then look completely ordinary -- the user knew something was
        wrong but not what, or where. This answers which file, how many items,
        and gives the two buttons that move between them.
        """
        self._banner = QFrame()
        self._banner.setObjectName("reviewBanner")
        self._banner.setVisible(False)

        row = QHBoxLayout(self._banner)
        row.setContentsMargins(16, 8, 16, 8)
        row.setSpacing(10)

        self.banner_label = QLabel("")
        self.banner_label.setWordWrap(True)

        self.previous_issue_button = QPushButton("Previous issue")
        self.previous_issue_button.setProperty("variant", "subtle")
        self.previous_issue_button.clicked.connect(lambda: self._step_issue(-1))

        self.next_issue_button = QPushButton("Next issue")
        self.next_issue_button.setProperty("variant", "subtle")
        self.next_issue_button.clicked.connect(lambda: self._step_issue(1))

        self.issue_position_label = QLabel("")
        self.issue_position_label.setProperty("role", "caption")

        self.show_all_button = QPushButton("Show all documents")
        self.show_all_button.setProperty("variant", "subtle")
        self.show_all_button.setToolTip(
            "Leave the review-only view and show every document in this file"
        )
        self.show_all_button.clicked.connect(self._show_all_documents)

        row.addWidget(self.banner_label, 1)
        row.addWidget(self.issue_position_label)
        row.addWidget(self.previous_issue_button)
        row.addWidget(self.next_issue_button)
        row.addWidget(self.show_all_button)
        return self._banner

    def _build_left_panel(self) -> QWidget:
        panel = QFrame()
        panel.setProperty("role", "panel")
        panel.setMinimumWidth(160)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        layout.addWidget(SectionLabel("Source PDFs"))

        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.file_list.currentItemChanged.connect(self._on_file_changed)
        self.file_list.setStyleSheet("QListWidget { border: none; }")
        layout.addWidget(self.file_list, 1)
        return panel

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Keep the three panels proportional to the width actually available.

        Fixed pixel sizes summing to more than the window pushed the inspector
        past the right edge on smaller displays. Proportions adapt instead, so
        the panel is always reachable however narrow the window is.
        """
        super().resizeEvent(event)
        self._apply_splitter_proportions()

    def _apply_splitter_proportions(self) -> None:
        width = self._splitter.width()
        if width <= 0:
            return
        left = max(160, min(260, int(width * 0.20)))
        right = max(240, min(340, int(width * 0.26)))
        centre = max(200, width - left - right)
        self._splitter.setSizes([left, centre, right])

    def _build_center_panel(self) -> QWidget:
        panel = QWidget()
        # Must be able to shrink: otherwise the centre panel's preferred width
        # squeezes the inspector off the edge instead of scrolling itself.
        panel.setMinimumWidth(200)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._groups_host = QWidget()
        self._groups_layout = QVBoxLayout(self._groups_host)
        self._groups_layout.setContentsMargins(16, 16, 16, 16)
        self._groups_layout.setSpacing(12)
        self._groups_layout.addStretch(1)

        self._empty_center = QLabel("Nothing to review here.")
        self._empty_center.setProperty("role", "body")
        self._empty_center.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_center.setVisible(False)
        self._groups_layout.insertWidget(0, self._empty_center)

        self._scroll.setWidget(self._groups_host)
        layout.addWidget(self._scroll, 1)
        return panel

    # ------------------------------------------------------------------
    def load(self, files: list[SourceFileAnalysis]) -> None:
        """Populate the workspace with analysed files."""
        self._files = [f for f in files if f.status is not FileStatus.ERROR and f.groups]
        self._cache.clear()
        self._thumbnails.clear_queue()
        self.history.clear()
        self._populate_file_list()
        self._reload_board()
        self._update_summary()

    def _populate_file_list(self, *, keep_path: str | None = None) -> None:
        """Fill the source list, honouring the Review Needed filter.

        When the filter is on, files with nothing to review are hidden entirely:
        the whole point of the filter is to put the user in front of the few
        documents that actually need a decision.
        """
        visible = [
            analysis
            for analysis in self._files
            # The file being looked at stays listed even once its last item is
            # resolved. Dropping it at that moment moves the selection to some
            # other file and replaces "all resolved" with a view of somebody
            # else's problem -- the user did the work and lost their place.
            if not self._review_only
            or self._file_review_count(analysis)
            or (keep_path is not None and str(analysis.path) == keep_path)
        ]

        self.file_list.blockSignals(True)
        self.file_list.clear()
        for analysis in visible:
            item = QListWidgetItem(self._file_label(analysis))
            item.setData(_PATH_ROLE, str(analysis.path))
            item.setToolTip(str(analysis.path))
            if self._file_review_count(analysis):
                item.setForeground(Qt.GlobalColor.darkYellow)
            self.file_list.addItem(item)
        self.file_list.blockSignals(False)

        if not visible:
            self._current = None
            self._render_groups()
            return

        wanted = keep_path or (str(self._current.path) if self._current else None)
        row = 0
        for index in range(self.file_list.count()):
            if self.file_list.item(index).data(_PATH_ROLE) == wanted:
                row = index
                break
        self.file_list.setCurrentRow(row)
        self._on_file_changed(self.file_list.item(row))

    def _file_review_count(self, analysis: SourceFileAnalysis) -> int:
        """Review items in this file, among the types the user asked for."""
        return sum(
            1
            for group in analysis.groups
            if group.needs_attention and not group.excluded and self.wants(group)
        )

    def _file_label(self, analysis: SourceFileAnalysis) -> str:
        parts = [analysis.name, f"{analysis.page_count} pages"]
        pending = self._file_review_count(analysis)
        if pending:
            parts.append(f"⚠ {pending} to review")
        else:
            parts.append(f"{len(analysis.groups)} documents")
        return "\n".join((parts[0], "  ·  ".join(parts[1:])))

    def refresh_file_labels(self) -> None:
        for row in range(self.file_list.count()):
            item = self.file_list.item(row)
            path = item.data(_PATH_ROLE)
            analysis = self._find_file(path)
            if analysis is not None:
                item.setText(self._file_label(analysis))

    # ------------------------------------------------------------------
    def _on_file_changed(self, current: QListWidgetItem | None, _previous=None) -> None:
        if current is None:
            self._current = None
        else:
            self._current = self._find_file(current.data(_PATH_ROLE))
        self._selected_group_id = None
        self._selected_page_index = None
        self._render_groups()
        self._update_banner()

    def _find_file(self, path: str | None) -> SourceFileAnalysis | None:
        if not path:
            return None
        for analysis in self._files:
            if str(analysis.path) == path:
                return analysis
        return None

    # ------------------------------------------------------------------
    def set_type_filter(self, document_types: list[str] | tuple[str, ...]) -> None:
        """Narrow the whole workspace to the types the user asked for.

        Choosing "Resume" on the home screen means the user wants resumes; being
        shown every cover letter and application report anyway is work they
        explicitly asked not to do. Everything is still analysed and still on
        disk in the model -- this only decides what the screen is about.
        """
        self._type_filter = tuple(document_types)
        self._populate_file_list(
            keep_path=str(self._current.path) if self._current else None
        )
        self._reload_board()
        self._update_summary()

    def wants(self, group: DocumentGroup) -> bool:
        if not self._type_filter:
            return True
        return group.document_type in self._type_filter

    def _visible_groups(self, analysis: SourceFileAnalysis) -> list[DocumentGroup]:
        return [
            group
            for group in analysis.groups
            if self.wants(group) and (not self._review_only or group.needs_attention)
        ]

    def _empty_message(self) -> str:
        """Say which filter emptied the panel, not just that it is empty."""
        if self._type_filter:
            wanted = " or ".join(t.lower() for t in self._type_filter)
            if self._review_only:
                return f"No {wanted} in this file needs review."
            return (
                f"No {wanted} was found in this file.\n\n"
                "Change what you need on the home screen to see everything."
            )
        if self._review_only:
            return "Nothing needs review in this file."
        return "No documents were detected in this file."

    def _render_groups(self) -> None:
        """Rebuild the centre panel for the current file."""
        while self._groups_layout.count() > 2:  # keep empty label + stretch
            item = self._groups_layout.takeAt(1)
            widget = item.widget()
            if widget is not None and widget is not self._empty_center:
                widget.deleteLater()
        self._sections.clear()
        self._packet_sections.clear()
        self._thumbnails.clear_queue()

        analysis = self._current
        if analysis is None:
            self._empty_center.setText("Select a PDF to review its documents.")
            self._empty_center.setVisible(True)
            self.inspector.show_group(None, [])
            return

        groups = self._visible_groups(analysis)
        if not groups:
            self._empty_center.setText(self._empty_message())
            self._empty_center.setVisible(True)
            self.inspector.show_group(None, [])
            return

        self._empty_center.setVisible(False)
        self._render_packet_sections(analysis, groups)

        first = self._selected_group_id or groups[0].id
        self._select_group(first if first in self._sections else groups[0].id)

    def _render_packet_sections(
        self, analysis: SourceFileAnalysis, groups: list[DocumentGroup]
    ) -> None:
        """Draw the visible documents grouped under the candidate they belong to.

        Documents whose packet is unknown -- or that predate a packet build --
        are collected under a trailing section rather than dropped, so nothing
        can silently disappear from the review panel.
        """
        visible = {group.id for group in groups}
        position = 1
        rendered: set[str] = set()

        for packet in analysis.packets:
            members = [d for d in packet.documents if d.id in visible]
            if not members:
                continue
            section = PacketSection(packet, self._tokens, self._thresholds)
            section.selected.connect(self._select_packet)
            section.rename_requested.connect(self._rename_candidate)
            section.merge_requested.connect(self._merge_candidate)
            section.accept_requested.connect(self._accept_packet)
            section.document_dropped.connect(
                lambda packet_id, group_id: self.move_document_to_candidate(
                    group_id, packet_id
                )
            )
            self._packet_sections[packet.id] = section
            self._groups_layout.insertWidget(position, section)
            position += 1

            for group in members:
                section.add_document_widget(self._build_group_section(analysis, group))
                rendered.add(group.id)

        orphans = [group for group in groups if group.id not in rendered]
        for group in orphans:
            self._groups_layout.insertWidget(position, self._build_group_section(analysis, group))
            position += 1

    def _build_group_section(
        self, analysis: SourceFileAnalysis, group: DocumentGroup
    ) -> GroupSection:
        pages = [analysis.page(i) for i in group.page_indexes]
        pages = [page for page in pages if page is not None]
        section = GroupSection(group, pages, self._tokens, self._thresholds)
        section.selected.connect(self._select_group)
        section.page_selected.connect(self._select_page)
        section.split_requested.connect(self._split_before)
        self._sections[group.id] = section
        self._request_thumbnails(analysis, group)
        return section

    def _request_thumbnails(self, analysis: SourceFileAnalysis, group: DocumentGroup) -> None:
        for page_index in group.page_indexes:
            cached = self._cache.get(str(analysis.path), page_index, 0)
            if cached is not None:
                section = self._sections.get(group.id)
                card = section.card_for(page_index) if section else None
                if card is not None:
                    card.set_thumbnail(cached)
            else:
                self._thumbnails.request(str(analysis.path), page_index)

    def _on_thumbnail_ready(self, path: str, page_index: int, _dpi: int, pixmap) -> None:
        if not isinstance(pixmap, QPixmap):
            return
        self._cache.put(path, page_index, 0, pixmap)

        # The board asked for this page, so the board has to be told it
        # arrived. Caching alone is not enough: the board only re-reads the
        # cache when something scrolls, so a thumbnail that finished rendering
        # while the user sat still would stay a placeholder until they moved.
        self.type_board.apply_thumbnail(path, page_index, pixmap)

        if self._current is None or str(self._current.path) != path:
            return
        for section in self._sections.values():
            card = section.card_for(page_index)
            if card is not None:
                card.set_thumbnail(pixmap)
                break

    # ------------------------------------------------------------------
    def _select_group(self, group_id: str) -> None:
        self._selected_group_id = group_id
        for identifier, section in self._sections.items():
            section.set_selected(identifier == group_id)
            if identifier != group_id:
                section.set_page_selected(None)
        self._refresh_inspector()

    def _select_page(self, group_id: str, page_index: int) -> None:
        self._selected_group_id = group_id
        self._selected_page_index = page_index
        for identifier, section in self._sections.items():
            section.set_selected(identifier == group_id)
            section.set_page_selected(page_index if identifier == group_id else None)
        self._refresh_inspector()

    def _refresh_inspector(self) -> None:
        analysis = self._current
        group = self._group_by_id(self._selected_group_id)
        if analysis is None or group is None:
            self.inspector.show_group(None, [])
            return

        pages = [analysis.page(i) for i in group.page_indexes]
        pages = [page for page in pages if page is not None]
        selected_page = (
            analysis.page(self._selected_page_index)
            if self._selected_page_index is not None
            and group.contains(self._selected_page_index)
            else None
        )
        position = analysis.groups.index(group)
        packet = analysis.packet_for_document(group)
        self.inspector.show_group(
            group,
            pages,
            selected_page=selected_page,
            can_merge_previous=position > 0,
            can_merge_next=position < len(analysis.groups) - 1,
            source_name=analysis.name,
            packet_name=packet.display_name if packet else "",
            candidate_choices=self.candidate_choices(),
        )

    # ------------------------------------------------------------------
    # Needs Review: getting to the flagged document, and between them
    # ------------------------------------------------------------------
    def focus_reviews(self, path: str) -> bool:
        """Open ``path`` showing only what needs review, on the first item.

        This is what a double-click on a "Review Needed" queue row lands in.
        Selecting the file was never the hard part -- finding which of its
        documents the status referred to was, and on a forty-document file that
        meant hunting. Returns whether anything was actually flagged, so the
        caller can fall back to the ordinary workspace.
        """
        analysis = self._find_file(path)
        if analysis is None or not flagged_groups(analysis):
            return False

        self._select_file_row(path)
        # Set the flag directly and sync the button: going through the toggle
        # would run _toggle_review_filter, which jumps to "a file with review
        # items" and could land on a different file than the one asked for.
        self._review_only = True
        self.review_filter_button.blockSignals(True)
        self.review_filter_button.setChecked(True)
        self.review_filter_button.setText("Showing review only")
        self.review_filter_button.blockSignals(False)

        self._populate_file_list(keep_path=path)
        self._select_file_row(path)
        self._render_groups()
        self._focus_issue(0)
        self._update_summary()
        return True

    def _select_file_row(self, path: str) -> None:
        for row in range(self.file_list.count()):
            item = self.file_list.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == path:
                self.file_list.setCurrentRow(row)
                return

    def current_issues(self) -> list[DocumentGroup]:
        """Flagged documents in the selected file, in page order."""
        if self._current is None:
            return []
        return flagged_groups(self._current)

    def current_issue_index(self) -> int:
        """Where the selection sits among the flagged documents, or -1."""
        issues = self.current_issues()
        for position, group in enumerate(issues):
            if group.id == self._selected_group_id:
                return position
        return -1

    def _focus_issue(self, position: int) -> None:
        """Select the flagged document at ``position`` and bring it on screen."""
        issues = self.current_issues()
        if not issues:
            self._update_banner()
            return
        position = max(0, min(position, len(issues) - 1))
        group = issues[position]
        self._select_group(group.id)
        self._scroll_group_into_view(group.id)
        self._update_banner()

    def _step_issue(self, delta: int) -> None:
        issues = self.current_issues()
        if not issues:
            return
        current = self.current_issue_index()
        # From "nowhere in particular", forward means the first item rather
        # than the second.
        target = 0 if current < 0 and delta > 0 else current + delta
        self._focus_issue(target)

    def _scroll_group_into_view(self, group_id: str) -> None:
        """Bring a document on screen in whichever view is showing it."""
        section = self._sections.get(group_id)
        if section is not None:
            self._scroll.ensureWidgetVisible(section, 0, 40)
        board_scroll = getattr(self.type_board, "scroll_document_into_view", None)
        if callable(board_scroll):
            board_scroll(group_id)

    def _show_all_documents(self) -> None:
        """Leave review-only and keep the user where they were looking."""
        keep = self._selected_group_id
        self.review_filter_button.setChecked(False)
        if keep:
            self._select_group(keep)
            self._scroll_group_into_view(keep)
        self._update_banner()

    def _advance_after_resolution(self, resolved_group_id: str | None) -> None:
        """Re-render after a correction, then move to whatever is still open.

        Staying on a document the user has just accepted means the next click
        is always "now where was I?"; this answers that for them, and only
        moves on when the item really is resolved.
        """
        remaining = self.current_issues()
        resolved = all(group.id != resolved_group_id for group in remaining)
        self._after_change(resolved_group_id, rebuild=False)

        if not resolved:
            return
        issues = self.current_issues()
        if issues:
            self._focus_issue(0)
        else:
            self._update_banner()

    def _update_banner(self) -> None:
        """Say what is left to review here, or that nothing is."""
        analysis = self._current
        if analysis is None:
            self._banner.setVisible(False)
            return

        issues = self.current_issues()
        showing_review_only = self._review_only

        if not issues and not showing_review_only:
            self._banner.setVisible(False)
            return

        if issues:
            self.banner_label.setText(
                f"{review_summary(len(issues), analysis.name)}\n"
                "Check the highlighted document below."
            )
            position = self.current_issue_index()
            self.issue_position_label.setText(
                f"Review item {position + 1} of {len(issues)}" if position >= 0 else ""
            )
            multiple = len(issues) > 1
            self.previous_issue_button.setVisible(multiple)
            self.next_issue_button.setVisible(multiple)
            self.previous_issue_button.setEnabled(multiple and position > 0)
            self.next_issue_button.setEnabled(multiple and position < len(issues) - 1)
        else:
            self.banner_label.setText(
                f"All review items in {analysis.name} are resolved."
            )
            self.issue_position_label.setText("")
            self.previous_issue_button.setVisible(False)
            self.next_issue_button.setVisible(False)

        self.show_all_button.setVisible(showing_review_only)
        self._banner.setStyleSheet(
            f"""
            QFrame#reviewBanner {{
                background-color: {self._tokens.warning_soft};
                border-bottom: 1px solid {self._tokens.warning};
            }}
            QFrame#reviewBanner QLabel {{ color: {self._tokens.warning}; }}
            """
            if issues
            else f"""
            QFrame#reviewBanner {{
                background-color: {self._tokens.success_soft};
                border-bottom: 1px solid {self._tokens.success};
            }}
            QFrame#reviewBanner QLabel {{ color: {self._tokens.success}; }}
            """
        )
        self._banner.setVisible(True)

    def _group_by_id(self, group_id: str | None) -> DocumentGroup | None:
        if self._current is None or not group_id:
            return None
        for group in self._current.groups:
            if group.id == group_id:
                return group
        return None

    # ------------------------------------------------------------------
    # View mode and undo
    # ------------------------------------------------------------------
    def set_view_mode(self, mode: int) -> None:
        """Switch between the type board and the candidate view."""
        self._modes.setCurrentIndex(mode)
        self.by_type_button.setChecked(mode == _BY_TYPE)
        self.by_candidate_button.setChecked(mode == _BY_CANDIDATE)
        if mode == _BY_TYPE and self._board_stale:
            self._reload_board()

    @property
    def view_mode(self) -> int:
        return self._modes.currentIndex()

    def _reload_board(self) -> None:
        """Rebuild the board, but only when somebody is looking at it.

        A card per document and a thumbnail per page is a lot of widgets. The
        application stays on the home screen after analysing, and review is
        optional, so building all of that eagerly spends real time on a view
        most runs never open -- and spends it again after every correction.
        """
        if not self.isVisible() or self._modes.currentIndex() != _BY_TYPE:
            self._board_stale = True
            return
        self._board_stale = False
        self.type_board.load(self._files, wanted=self._type_filter)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Build the board on the way in, if anything changed while away."""
        super().showEvent(event)
        if self._board_stale:
            self._reload_board()

    def _request_board_thumbnails(self, requests) -> None:
        """Render only what the board can currently show.

        Bounded by the worker's own queue limit as well, so scrolling quickly
        through a long file cannot pile up work for pages already gone by.
        """
        keep = set()
        for source_pdf, page_index in requests or ():
            cached = self._cache.get(source_pdf, page_index, 0)
            if cached is not None:
                self.type_board.apply_thumbnail(source_pdf, page_index, cached)
                continue
            keep.add((str(source_pdf), int(page_index), 0))
            self._thumbnails.request(source_pdf, page_index)
        self._thumbnails.cancel_all_except(keep)

    def undo(self) -> None:
        if self._current is None or not self.history.can_undo:
            return
        label = self.history.undo(self._current)
        if label:
            # Deliberately no packet rebuild: the snapshot already restored the
            # packets *and* the documents together, with their links intact.
            # Re-deriving them here would throw that away and mint new packet
            # identities, so a document would come back attached to a
            # different-looking candidate than the one it left.
            self._after_change(None, reassociate=False)
            self._summary_label.setText(f"Undone: {label}")

    def redo(self) -> None:
        if self._current is None or not self.history.can_redo:
            return
        label = self.history.redo(self._current)
        if label:
            self._after_change(None, reassociate=False)
            self._summary_label.setText(f"Redone: {label}")

    def _refresh_undo_buttons(self) -> None:
        self.undo_button.setEnabled(self.history.can_undo)
        self.redo_button.setEnabled(self.history.can_redo)
        self.undo_button.setToolTip(
            f"Undo: {self.history.undo_label}" if self.history.can_undo else "Nothing to undo"
        )
        self.redo_button.setToolTip(
            f"Redo: {self.history.redo_label}" if self.history.can_redo else "Nothing to redo"
        )

    # ------------------------------------------------------------------
    # Board drops -- routed through the same services as every other correction
    # ------------------------------------------------------------------
    def _file_for_group(self, group_id: str) -> SourceFileAnalysis | None:
        for analysis in self._files:
            if any(group.id == group_id for group in analysis.groups):
                return analysis
        return None

    def _retype_from_board(self, group_id: str, document_type: str) -> None:
        analysis = self._file_for_group(group_id)
        if analysis is None:
            return
        group = next((g for g in analysis.groups if g.id == group_id), None)
        if group is None or group.document_type == document_type:
            return

        self._current = analysis
        self.history.record(
            f"Change type to {document_type}",
            analysis,
            lambda: self._grouping.set_document_type(analysis, group, document_type),
        )
        self._after_change(group_id)

    def _move_pages_from_board(
        self, target_group_id: str, source_pdf: str, page_indexes
    ) -> None:
        analysis = self._file_for_group(target_group_id)
        if analysis is None or str(analysis.path) != str(source_pdf):
            return
        target = next((g for g in analysis.groups if g.id == target_group_id), None)
        if target is None:
            return

        wanted = list(page_indexes)
        allowed, reason = self._grouping.can_move_pages(analysis, wanted, target)
        if not allowed:
            # Say why rather than silently doing nothing: a drop that appears
            # to be ignored reads as the application being broken.
            self._summary_label.setText(reason)
            return

        self._current = analysis
        label = (
            f"Move {len(wanted)} page{'s' if len(wanted) != 1 else ''} "
            f"to {target.document_type}"
        )
        self.history.record(
            label, analysis, lambda: self._grouping.move_pages(analysis, wanted, target)
        )
        self._after_change(target_group_id, reassociate=True)

    def _show_document_menu(self, group_id: str, point) -> None:
        """The keyboard/right-click route to everything the board can drag."""
        from app.ui.widgets.type_board import (
            MOVE_TO_CANDIDATE,
            NEW_CANDIDATE,
            RENAME_CANDIDATE,
            build_context_menu,
        )

        card = self.type_board.card(group_id)
        if card is None:
            return
        menu = build_context_menu(self, self._document_types)
        chosen = menu.exec(card.mapToGlobal(point))
        if chosen is None:
            return

        text = chosen.text()
        if text in self._document_types:
            self._retype_from_board(group_id, text)
        elif text == "Merge with previous":
            self._merge_previous(group_id)
        elif text == "Merge with next":
            self._merge_next(group_id)
        elif text == "Exclude from export":
            self._set_excluded(group_id, True)
        elif text == RENAME_CANDIDATE:
            packet = self._packet_for_group(group_id)
            if packet is not None:
                self._rename_candidate(packet.id)
        elif text == MOVE_TO_CANDIDATE:
            self._move_to_existing_candidate(group_id)
        elif text == NEW_CANDIDATE:
            self._new_candidate_from_document(group_id)

    def _packet_for_group(self, group_id: str) -> CandidatePacket | None:
        analysis = self._file_for_group(group_id)
        if analysis is None:
            return None
        group = next((g for g in analysis.groups if g.id == group_id), None)
        return analysis.packet_for_document(group) if group is not None else None

    def _move_to_existing_candidate(self, group_id: str) -> None:
        """File a document under somebody already found in this PDF.

        Distinct from creating a candidate: this is the common correction --
        the right person is already on screen, the document just landed under
        the wrong one -- and offering only "type a new name" for it was the
        bug that made reassignment unreachable from the board.
        """
        analysis = self._file_for_group(group_id)
        if analysis is None:
            return

        current = self._packet_for_group(group_id)
        options = [
            packet
            for packet in analysis.packets
            if not packet.is_unknown and (current is None or packet.id != current.id)
        ]
        if not options:
            QMessageBox.information(
                self,
                "Move to candidate",
                "There is no other candidate in this PDF to move it to.\n\n"
                "Use “Create new candidate…” to make one.",
            )
            return

        labels = [packet.display_name for packet in options]
        choice, accepted = QInputDialog.getItem(
            self, "Move to candidate", "Move this document to:", labels, 0, False
        )
        if not accepted or not choice:
            return
        self.move_document_to_candidate(group_id, options[labels.index(choice)].id)

    # ------------------------------------------------------------------
    # Corrections -- all delegated to the grouping service
    # ------------------------------------------------------------------
    def _change_type(self, group_id: str, document_type: str) -> None:
        group = self._group_by_id(group_id)
        if group is None or self._current is None:
            return
        analysis = self._current
        self.history.record(
            f"Change type to {document_type}",
            analysis,
            lambda: self._grouping.set_document_type(analysis, group, document_type),
        )
        self._after_change(group_id)

    def _mark_other(self, group_id: str) -> None:
        self._change_type(group_id, "Other")

    def _split_before(self, group_id: str, page_index: int) -> None:
        if self._current is None:
            return
        analysis = self._current
        result = self.history.record(
            "Split document",
            analysis,
            lambda: self._grouping.split_before(analysis, page_index),
        )
        if result is None:
            return
        _head, tail = result
        self._selected_group_id = tail.id
        self._selected_page_index = page_index
        self._after_change(tail.id, reassociate=True)

    def _merge_previous(self, group_id: str) -> None:
        group = self._group_by_id(group_id)
        if group is None or self._current is None:
            return
        analysis = self._current
        merged = self.history.record(
            "Merge with previous",
            analysis,
            lambda: self._grouping.merge_with_previous(analysis, group),
        )
        if merged is not None:
            self._after_change(merged.id, reassociate=True)

    def _merge_next(self, group_id: str) -> None:
        group = self._group_by_id(group_id)
        if group is None or self._current is None:
            return
        analysis = self._current
        merged = self.history.record(
            "Merge with next",
            analysis,
            lambda: self._grouping.merge_with_next(analysis, group),
        )
        if merged is not None:
            self._after_change(merged.id, reassociate=True)

    def _set_excluded(self, group_id: str, excluded: bool) -> None:
        group = self._group_by_id(group_id)
        if group is None or self._current is None:
            return
        analysis = self._current
        self.history.record(
            "Exclude from export" if excluded else "Include in export",
            analysis,
            lambda: self._grouping.set_group_excluded(analysis, group, excluded),
        )
        self._after_change(group_id, rebuild=False)

    def _set_separator(self, group_id: str, page_index: int, included: bool) -> None:
        group = self._group_by_id(group_id)
        if group is None or self._current is None:
            return
        analysis = self._current
        self.history.record(
            "Keep separator page" if included else "Drop separator page",
            analysis,
            lambda: self._grouping.set_separator_included(
                analysis, group, page_index, included
            ),
        )
        self._after_change(group_id, rebuild=False)

    def _accept_group(self, group_id: str) -> None:
        """Accept one document exactly as "Approve all" accepts every document.

        Three flags can hold a document in review -- its own, its packet's, and
        the association flag -- and clearing only the first leaves the document
        still counted, with a button that appears to do nothing. This mirrors
        the per-document body of :meth:`approve_all` rather than inventing a
        second, weaker meaning of "accepted".
        """
        group = self._group_by_id(group_id)
        if group is None or self._current is None:
            return
        self._grouping.mark_reviewed(self._current, group)
        packet = self._current.packet_for_document(group)
        if packet is not None and not packet.is_unknown:
            self._packets.accept_packet(self._current, packet)
        group.association_review = False
        self._current.refresh_status()
        self._advance_after_resolution(group_id)

    # ------------------------------------------------------------------
    # Candidate packet corrections -- delegated to CandidatePacketService
    # ------------------------------------------------------------------
    def _select_packet(self, packet_id: str) -> None:
        self._selected_packet_id = packet_id
        packet = self._packet_by_id(packet_id)
        if packet is not None and packet.documents:
            self._select_group(packet.documents[0].id)

    def _packet_by_id(self, packet_id: str | None) -> CandidatePacket | None:
        if self._current is None or not packet_id:
            return None
        return self._current.packet(packet_id)

    def candidate_choices(self) -> list[tuple[str, str]]:
        """``(packet_id, label)`` for every candidate a document could move to."""
        if self._current is None:
            return []
        return [(p.id, p.display_name) for p in self._current.packets]

    def move_document_to_candidate(self, group_id: str, packet_id: str) -> None:
        """Refile one document under a different applicant.

        Resolved through the file that actually owns the document rather than
        whichever file the source list happens to have selected: the board
        shows every file at once, so the two are frequently not the same.
        """
        analysis = self._file_for_group(group_id) or self._current
        if analysis is None:
            return
        group = next((g for g in analysis.groups if g.id == group_id), None)
        target = analysis.packet(packet_id)
        if group is None or target is None:
            return

        self._current = analysis
        self.history.record(
            f"Move to {target.display_name}",
            analysis,
            lambda: self._packets.move_document(analysis, group, target),
        )
        self._after_change(group_id)

    def create_candidate_for(self, group_id: str, name: str | None = None) -> None:
        """Pull a document out into a candidate of its own."""
        analysis = self._file_for_group(group_id) or self._current
        if analysis is None:
            return
        group = next((g for g in analysis.groups if g.id == group_id), None)
        if group is None:
            return

        self._current = analysis
        self.history.record(
            f"New candidate from {group.document_type}",
            analysis,
            lambda: self._packets.create_packet_for(analysis, group, name),
        )
        self._after_change(group_id)

    def merge_candidates(self, keep_id: str, absorb_id: str) -> None:
        """Join two packets that turned out to be the same person."""
        keep = self._packet_by_id(keep_id)
        absorb = self._packet_by_id(absorb_id)
        if keep is None or absorb is None or self._current is None:
            return
        self._packets.merge_packets(self._current, keep, absorb)
        self._after_change(self._selected_group_id)

    def split_candidate(
        self, packet_id: str, group_ids: list[str], name: str | None = None
    ) -> None:
        """Move some documents out of a packet into a new applicant."""
        packet = self._packet_by_id(packet_id)
        if packet is None or self._current is None:
            return
        documents = [d for d in packet.documents if d.id in set(group_ids)]
        self._packets.split_packet(self._current, packet, documents, name)
        self._after_change(self._selected_group_id)

    def rename_candidate_to(self, packet_id: str, name: str) -> None:
        packet = self._packet_by_id(packet_id)
        if packet is None or self._current is None:
            return
        analysis = self._current
        self.history.record(
            f"Rename candidate to {name}",
            analysis,
            lambda: self._packets.rename_candidate(packet, name),
        )
        self._after_change(self._selected_group_id)

    def _new_candidate_from_document(self, group_id: str) -> None:
        name, accepted = QInputDialog.getText(
            self, "New candidate", "Candidate name (leave blank to use the document):"
        )
        if accepted:
            self.create_candidate_for(group_id, name.strip() or None)

    def _rename_candidate(self, packet_id: str) -> None:
        packet = self._packet_by_id(packet_id)
        if packet is None:
            return
        current = "" if packet.is_unknown else (packet.candidate.name or "")
        name, accepted = QInputDialog.getText(
            self, "Rename candidate", "Candidate name:", text=current
        )
        if accepted and name.strip():
            self.rename_candidate_to(packet_id, name.strip())

    def _merge_candidate(self, packet_id: str) -> None:
        packet = self._packet_by_id(packet_id)
        if packet is None or self._current is None:
            return
        others = [p for p in self._current.packets if p.id != packet_id and not p.is_unknown]
        if not others:
            QMessageBox.information(
                self, "Merge candidates", "There is no other candidate to merge with."
            )
            return
        labels = [p.display_name for p in others]
        choice, accepted = QInputDialog.getItem(
            self,
            "Merge candidates",
            f"Merge {packet.display_name} into:",
            labels,
            0,
            False,
        )
        if accepted and choice:
            target = others[labels.index(choice)]
            self.merge_candidates(target.id, packet_id)

    def _accept_packet(self, packet_id: str) -> None:
        packet = self._packet_by_id(packet_id)
        if packet is None or self._current is None:
            return
        self._packets.accept_packet(self._current, packet)
        self._after_change(self._selected_group_id, rebuild=False)

    def _after_change(
        self, group_id: str | None, *, rebuild: bool = True, reassociate: bool = False
    ) -> None:
        """Re-render what the change affected and tell the window about it."""
        self._selected_group_id = group_id
        if reassociate and self._current is not None:
            # Splitting or merging documents changes what there is to attribute,
            # so ownership has to be worked out again. Anything the reviewer
            # decided by hand survives; CandidatePacketService leaves it alone.
            self._packets.rebuild(self._current)
        if self._review_only:
            # A correction can clear the last review item in this file, which
            # would leave a filtered view showing a file that no longer belongs.
            self._populate_file_list(
                keep_path=str(self._current.path) if self._current else None
            )
        elif rebuild:
            self._render_groups()
        else:
            self._refresh_sections()
            self._refresh_inspector()
        self.refresh_file_labels()
        if self.view_mode == _BY_TYPE:
            self._reload_board()
        self._update_summary()
        self._update_banner()
        self.documents_changed.emit()

    def _refresh_sections(self) -> None:
        analysis = self._current
        if analysis is None:
            return
        for packet in analysis.packets:
            section = self._packet_sections.get(packet.id)
            if section is not None:
                section.update_packet(packet)
        for group in analysis.groups:
            section = self._sections.get(group.id)
            if section is None:
                continue
            pages = [analysis.page(i) for i in group.page_indexes]
            section.update_group(group, [p for p in pages if p is not None])

    # ------------------------------------------------------------------
    def _toggle_review_filter(self, enabled: bool) -> None:
        """Narrow both panels to the documents that still need a decision."""
        self._review_only = enabled
        self.review_filter_button.setText(
            "Showing review only" if enabled else "Review Needed"
        )
        self._update_banner()
        # Jump straight to a file that has review items, rather than leaving the
        # user staring at an empty panel because the selected file was clean.
        first_with_review = next(
            (f for f in self._files if self._file_review_count(f)), None
        )
        keep = (
            str(first_with_review.path)
            if enabled and first_with_review is not None
            else None
        )
        self._populate_file_list(keep_path=keep)

    def visible_review_count(self) -> int:
        """Documents needing review among the types the user asked for."""
        return sum(
            1
            for file in self._files
            for group in file.groups
            if group.needs_attention and not group.excluded and self.wants(group)
        )

    def approve_all(self) -> int:
        """Accept every flagged document as correct. Returns how many.

        Clicking through fifty individually correct documents is not review, it
        is data entry. This clears them in one action -- and only the ones the
        user can currently see, so approving while filtered to resumes does not
        silently sign off cover letters they never looked at.
        """
        approved = 0
        for file in self._files:
            for group in list(file.groups):
                if group.excluded or not group.needs_attention or not self.wants(group):
                    continue
                self._grouping.mark_reviewed(file, group)
                packet = file.packet_for_document(group)
                if packet is not None and not packet.is_unknown:
                    self._packets.accept_packet(file, packet)
                group.association_review = False
                approved += 1
            file.refresh_status()

        if approved:
            self._after_change(self._selected_group_id)
        return approved

    def _approve_all(self) -> None:
        pending = self.visible_review_count()
        if not pending:
            QMessageBox.information(
                self, "Approve all", "Nothing is waiting for review."
            )
            return

        scope = ""
        if self._type_filter:
            scope = " " + " / ".join(t.lower() for t in self._type_filter)
        answer = QMessageBox.question(
            self,
            "Approve all",
            f"Accept {pending}{scope} document{'s' if pending != 1 else ''} as correct "
            "without checking them individually?\n\n"
            "They will be exported exactly as detected.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return

        approved = self.approve_all()
        self._set_approved_status(approved)

    def _set_approved_status(self, approved: int) -> None:
        self._summary_label.setText(
            f"Approved {approved} document{'s' if approved != 1 else ''}."
        )

    def _update_summary(self) -> None:
        documents = sum(
            1 for f in self._files for g in f.groups if self.wants(g)
        )
        review = self.visible_review_count()
        pages = sum(f.page_count for f in self._files)
        noun = "documents"
        if self._type_filter:
            noun = " / ".join(t.lower() + "s" for t in self._type_filter)
        text = (
            f"{len(self._files)} PDF{'s' if len(self._files) != 1 else ''}  ·  "
            f"{pages} pages  ·  {documents} {noun}"
        )
        if review:
            text += f"  ·  {review} need review"
        else:
            text += "  ·  all clear"
        self._summary_label.setText(text)
        self._refresh_undo_buttons()
        self.review_filter_button.setEnabled(review > 0 or self._review_only)
        self.approve_all_button.setEnabled(review > 0)
        self.approve_all_button.setText(
            f"Approve all ({review})" if review else "Approve all"
        )

    def total_review_count(self) -> int:
        return self.visible_review_count()

    def files(self) -> list[SourceFileAnalysis]:
        return self._files

    def set_palette_tokens(self, palette: Palette) -> None:
        self._tokens = palette

    def shutdown(self) -> None:
        """Stop the thumbnail thread and release the workspace's widgets.

        The board holds a card per document and a thumbnail per page, which on
        a large batch is thousands of widgets. Closing the window hides them
        but does not free them, so without this they accumulate for as long as
        the application runs.
        """
        self._thumbnails.stop()
        self._thumbnails.wait(2000)
        self._cache.clear()
        for lane in self.type_board._lanes.values():
            lane.clear()
        self.type_board._cards.clear()
        self._sections.clear()
        self._packet_sections.clear()
        self._files = []
        self._current = None
        self.history.clear()


__all__ = ["ReviewView"]
