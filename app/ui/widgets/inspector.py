"""The right-hand inspector: document details and every correction action."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.models.document import DocumentGroup
from app.models.enums import SeparatorState
from app.models.page import PageAnalysis
from app.services.confidence import ConfidenceThresholds, confidence_percent
from app.ui.theme import Palette
from app.ui.widgets.badges import (
    ConfidencePill,
    HSeparator,
    KeyValueRow,
    SectionLabel,
)


class Inspector(QWidget):
    """Shows the selected document and offers the correction actions."""

    type_changed = Signal(str, str)          # group id, new type
    split_requested = Signal(str, int)       # group id, page index
    merge_previous_requested = Signal(str)
    merge_next_requested = Signal(str)
    mark_other_requested = Signal(str)
    exclude_toggled = Signal(str, bool)
    separator_toggled = Signal(str, int, bool)
    accept_requested = Signal(str)
    move_to_candidate_requested = Signal(str, str)   # group id, packet id
    new_candidate_requested = Signal(str)            # group id

    def __init__(
        self,
        document_types: list[str],
        palette: Palette,
        thresholds: ConfidenceThresholds,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tokens = palette
        self._thresholds = thresholds
        self._group: DocumentGroup | None = None
        self._selected_page: PageAnalysis | None = None
        self._updating = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        self._layout = QVBoxLayout(content)
        self._layout.setContentsMargins(16, 16, 16, 16)
        self._layout.setSpacing(10)

        self._empty_label = QLabel("Select a document to see its details.")
        self._empty_label.setProperty("role", "body")
        self._empty_label.setWordWrap(True)
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(self._empty_label)

        self._body = QWidget()
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(10)
        self._build_body(body_layout, document_types)
        self._layout.addWidget(self._body)
        self._layout.addStretch(1)

        scroll.setWidget(content)
        outer.addWidget(scroll)

        self.show_group(None, [])

    # ------------------------------------------------------------------
    def _build_body(self, layout: QVBoxLayout, document_types: list[str]) -> None:
        self._title = QLabel("Document")
        self._title.setProperty("role", "subheading")
        self._title.setWordWrap(True)
        layout.addWidget(self._title)

        self._pages_label = QLabel("")
        self._pages_label.setProperty("role", "caption")
        layout.addWidget(self._pages_label)

        self._review_banner = QLabel("")
        self._review_banner.setWordWrap(True)
        self._review_banner.setVisible(False)
        layout.addWidget(self._review_banner)

        layout.addWidget(HSeparator())

        # -- confidence ---------------------------------------------------
        layout.addWidget(SectionLabel("Confidence"))
        confidence_row = QGridLayout()
        confidence_row.setHorizontalSpacing(10)
        confidence_row.setVerticalSpacing(6)

        classification_label = QLabel("Document type")
        classification_label.setProperty("role", "caption")
        boundary_label = QLabel("Page grouping")
        boundary_label.setProperty("role", "caption")

        self._classification_pill = ConfidencePill(0.0, self._thresholds, self._tokens)
        self._boundary_pill = ConfidencePill(0.0, self._thresholds, self._tokens)

        confidence_row.addWidget(classification_label, 0, 0)
        confidence_row.addWidget(self._classification_pill, 0, 1, Qt.AlignmentFlag.AlignLeft)
        confidence_row.addWidget(boundary_label, 1, 0)
        confidence_row.addWidget(self._boundary_pill, 1, 1, Qt.AlignmentFlag.AlignLeft)
        confidence_row.setColumnStretch(2, 1)
        layout.addLayout(confidence_row)

        self._reasoning = QLabel("")
        self._reasoning.setProperty("role", "caption")
        self._reasoning.setWordWrap(True)
        layout.addWidget(self._reasoning)

        layout.addWidget(HSeparator())

        # -- document type ------------------------------------------------
        layout.addWidget(SectionLabel("Document type"))
        self._type_combo = QComboBox()
        self._type_combo.addItems(document_types)
        self._type_combo.currentTextChanged.connect(self._on_type_changed)
        self._type_combo.setToolTip("Change what kind of document this is")
        layout.addWidget(self._type_combo)

        # -- who this document belongs to ---------------------------------
        # Separate from the candidate details below: those describe what is
        # written on these pages, this describes which applicant packet the
        # document was filed under, which is not always the same thing.
        layout.addWidget(SectionLabel("Belongs to"))
        self._packet_label = QLabel("—")
        self._packet_label.setWordWrap(True)
        layout.addWidget(self._packet_label)

        self._association_pill = ConfidencePill(0.0, self._thresholds, self._tokens)
        layout.addWidget(self._association_pill)

        self._association_reason = QLabel("")
        self._association_reason.setProperty("role", "caption")
        self._association_reason.setWordWrap(True)
        layout.addWidget(self._association_reason)

        self._candidate_combo = QComboBox()
        self._candidate_combo.setToolTip("Move this document to a different candidate")
        self._candidate_combo.currentIndexChanged.connect(self._on_candidate_changed)
        layout.addWidget(self._candidate_combo)

        self._new_candidate_button = QPushButton("New candidate from this document")
        self._new_candidate_button.clicked.connect(self._on_new_candidate)
        layout.addWidget(self._new_candidate_button)

        layout.addWidget(HSeparator())

        # -- candidate ----------------------------------------------------
        layout.addWidget(SectionLabel("Candidate"))
        self._rows = {
            "Name": KeyValueRow("Name"),
            "Email": KeyValueRow("Email"),
            "Phone": KeyValueRow("Phone"),
            "LinkedIn": KeyValueRow("LinkedIn"),
            "Job": KeyValueRow("Job"),
            "Applicant ID": KeyValueRow("Applicant ID"),
        }
        for row in self._rows.values():
            layout.addWidget(row)

        layout.addWidget(SectionLabel("Source"))
        self._source_rows = {
            "Start page": KeyValueRow("Start page"),
            "End page": KeyValueRow("End page"),
            "Source PDF": KeyValueRow("Source PDF"),
        }
        for row in self._source_rows.values():
            layout.addWidget(row)

        layout.addWidget(HSeparator())

        # -- actions ------------------------------------------------------
        layout.addWidget(SectionLabel("Corrections"))

        self._split_button = QPushButton("Split before selected page")
        self._split_button.clicked.connect(self._on_split)
        self._split_button.setToolTip(
            "Start a new document at the selected page (or double-click a page)"
        )

        merge_row = QHBoxLayout()
        merge_row.setSpacing(8)
        self._merge_previous = QPushButton("Merge with previous")
        self._merge_previous.clicked.connect(self._on_merge_previous)
        self._merge_next = QPushButton("Merge with next")
        self._merge_next.clicked.connect(self._on_merge_next)
        merge_row.addWidget(self._merge_previous)
        merge_row.addWidget(self._merge_next)

        self._mark_other = QPushButton("Mark as Other")
        self._mark_other.clicked.connect(self._on_mark_other)

        self._exclude_button = QPushButton("Exclude from export")
        self._exclude_button.clicked.connect(self._on_exclude)

        self._separator_button = QPushButton("Include separator page")
        self._separator_button.clicked.connect(self._on_separator)
        self._separator_button.setVisible(False)

        self._accept_button = QPushButton("Looks correct")
        self._accept_button.setProperty("variant", "accent")
        self._accept_button.clicked.connect(self._on_accept)
        self._accept_button.setToolTip("Clear the review flag on this document")

        layout.addWidget(self._split_button)
        layout.addLayout(merge_row)
        layout.addWidget(self._mark_other)
        layout.addWidget(self._exclude_button)
        layout.addWidget(self._separator_button)
        layout.addSpacing(4)
        layout.addWidget(self._accept_button)

    # ------------------------------------------------------------------
    def show_group(
        self,
        group: DocumentGroup | None,
        pages: list[PageAnalysis],
        *,
        selected_page: PageAnalysis | None = None,
        can_merge_previous: bool = False,
        can_merge_next: bool = False,
        source_name: str = "",
        packet_name: str = "",
        candidate_choices: list[tuple[str, str]] | None = None,
    ) -> None:
        """Display a group (or the empty state when ``group`` is ``None``)."""
        self._group = group
        self._selected_page = selected_page
        self._empty_label.setVisible(group is None)
        self._body.setVisible(group is not None)
        if group is None:
            return

        self._updating = True
        try:
            self._title.setText(group.document_type)
            self._pages_label.setText(
                f"{group.page_range_label}  ·  "
                f"{group.page_count} page{'s' if group.page_count != 1 else ''}"
            )

            self._classification_pill.update_value(
                group.classification_confidence, self._thresholds, self._tokens
            )
            self._boundary_pill.update_value(
                group.boundary_confidence, self._thresholds, self._tokens
            )

            self._update_review_banner(group)

            index = self._type_combo.findText(group.document_type)
            if index >= 0:
                self._type_combo.setCurrentIndex(index)

            self._update_association(group, packet_name, candidate_choices or [])

            candidate = group.candidate
            self._rows["Name"].set_value(candidate.name)
            self._rows["Email"].set_value(candidate.email)
            self._rows["Phone"].set_value(candidate.phone)
            self._rows["LinkedIn"].set_value(candidate.linkedin)
            self._rows["Job"].set_value(candidate.job_title)
            self._rows["Applicant ID"].set_value(candidate.applicant_id)

            self._source_rows["Start page"].set_value(str(group.start_page))
            self._source_rows["End page"].set_value(str(group.end_page))
            self._source_rows["Source PDF"].set_value(source_name)

            reasoning = self._reasoning_text(group, pages)
            self._reasoning.setText(reasoning)
            self._reasoning.setVisible(bool(reasoning))

            self._merge_previous.setEnabled(can_merge_previous)
            self._merge_next.setEnabled(can_merge_next)

            can_split = (
                selected_page is not None
                and selected_page.page_index != group.start_page_index
                and group.contains(selected_page.page_index)
            )
            self._split_button.setEnabled(can_split)
            self._split_button.setText(
                f"Split before page {selected_page.page_number}"
                if can_split and selected_page
                else "Split before selected page"
            )

            self._exclude_button.setText(
                "Include in export" if group.excluded else "Exclude from export"
            )
            self._accept_button.setEnabled(group.requires_review)

            self._update_separator_button(selected_page)
        finally:
            self._updating = False

    def _update_review_banner(self, group: DocumentGroup) -> None:
        if not group.requires_review:
            self._review_banner.setVisible(False)
            return
        reasons = "\n".join(f"• {reason}" for reason in group.review_reasons)
        self._review_banner.setText(f"Needs review\n{reasons}")
        self._review_banner.setStyleSheet(
            f"""
            QLabel {{
                background-color: {self._tokens.warning_soft};
                color: {self._tokens.warning};
                border: 1px solid {self._tokens.warning};
                border-radius: 6px;
                padding: 8px 10px;
                font-size: 9pt;
            }}
            """
        )
        self._review_banner.setVisible(True)

    def _update_separator_button(self, page: PageAnalysis | None) -> None:
        if page is None or page.separator_state is SeparatorState.NOT_SEPARATOR:
            self._separator_button.setVisible(False)
            return
        excluded = page.separator_state is SeparatorState.EXCLUDED
        self._separator_button.setText(
            f"Include separator page {page.page_number} in output"
            if excluded
            else f"Remove separator page {page.page_number} from output"
        )
        self._separator_button.setVisible(True)

    @staticmethod
    def _reasoning_text(group: DocumentGroup, pages: list[PageAnalysis]) -> str:
        if group.type_manually_set:
            return "Set by you."
        first = next(
            (page for page in pages if page.page_index == group.start_page_index), None
        )
        parts = []
        if first is not None and first.reasoning_summary:
            parts.append(first.reasoning_summary)
        if first is not None and first.boundary_reasons:
            parts.append("Grouping: " + "; ".join(first.boundary_reasons[:2]))
        return "\n".join(parts)

    # ------------------------------------------------------------------
    def _on_type_changed(self, text: str) -> None:
        if self._updating or self._group is None or not text:
            return
        if text != self._group.document_type:
            self.type_changed.emit(self._group.id, text)

    # ------------------------------------------------------------------
    def _update_association(
        self,
        group: DocumentGroup,
        packet_name: str,
        choices: list[tuple[str, str]],
    ) -> None:
        """Show which applicant owns this document, and how sure we are."""
        self._packet_label.setText(packet_name or "Not assigned to a candidate")

        known = bool(packet_name) and group.association_confidence > 0
        self._association_pill.setVisible(known)
        if known:
            self._association_pill.update_value(
                group.association_confidence, self._thresholds, self._tokens
            )

        if group.association_manually_set:
            reason = "Assigned by you."
        elif group.association_reasons:
            reason = "Matched because: " + "; ".join(group.association_reasons[:2]) + "."
        else:
            reason = ""
        self._association_reason.setText(reason)
        self._association_reason.setVisible(bool(reason))

        self._candidate_combo.clear()
        for packet_id, label in choices:
            self._candidate_combo.addItem(label, packet_id)
        current = self._candidate_combo.findData(group.packet_id)
        if current >= 0:
            self._candidate_combo.setCurrentIndex(current)
        self._candidate_combo.setVisible(bool(choices))

    def _on_candidate_changed(self, index: int) -> None:
        if self._updating or self._group is None or index < 0:
            return
        packet_id = self._candidate_combo.itemData(index)
        if packet_id and packet_id != self._group.packet_id:
            self.move_to_candidate_requested.emit(self._group.id, packet_id)

    def _on_new_candidate(self) -> None:
        if self._group is not None:
            self.new_candidate_requested.emit(self._group.id)

    def _on_split(self) -> None:
        if self._group is not None and self._selected_page is not None:
            self.split_requested.emit(self._group.id, self._selected_page.page_index)

    def _on_merge_previous(self) -> None:
        if self._group is not None:
            self.merge_previous_requested.emit(self._group.id)

    def _on_merge_next(self) -> None:
        if self._group is not None:
            self.merge_next_requested.emit(self._group.id)

    def _on_mark_other(self) -> None:
        if self._group is not None:
            self.mark_other_requested.emit(self._group.id)

    def _on_exclude(self) -> None:
        if self._group is not None:
            self.exclude_toggled.emit(self._group.id, not self._group.excluded)

    def _on_separator(self) -> None:
        if self._group is None or self._selected_page is None:
            return
        excluded = self._selected_page.separator_state is SeparatorState.EXCLUDED
        self.separator_toggled.emit(
            self._group.id, self._selected_page.page_index, excluded
        )

    def _on_accept(self) -> None:
        if self._group is not None:
            self.accept_requested.emit(self._group.id)


__all__ = ["Inspector"]
