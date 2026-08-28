"""What the user wants out of this batch, chosen before analysis.

The common job is not "sort everything and let me sift it" -- it is "give me
the resumes". Asking that question on the home screen, next to the button that
starts the work, turns a two-stage chore into one decision made up front.

Choosing here only narrows what gets *saved*. Everything is still detected,
grouped and reviewable, so this can be changed afterwards and re-exported
without analysing anything again.
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

from app.ui.theme import Palette
from app.ui.widgets.flow_layout import FlowLayout

#: Label used when no type is singled out.
EVERYTHING = "Everything"


class ExtractSelector(QFrame):
    """A row of toggles naming the document types to save."""

    selection_changed = Signal(list)

    def __init__(
        self,
        document_types: list[str],
        palette: Palette,
        parent: QWidget | None = None,
        *,
        primary_types: list[str] | None = None,
    ) -> None:
        """Build the selector.

        ``primary_types``, when given, narrows the chip row shown by default
        to just those types (the rest of ``document_types`` -- everything
        still fully supported -- is one click away under "more types" rather
        than crowding the everyday choice). Every type still gets a button in
        ``_buttons``, so callers and tests can look one up by name regardless
        of which row it renders in.
        """
        super().__init__(parent)
        self._tokens = palette
        self._buttons: dict[str, QPushButton] = {}
        self._updating = False

        self.setObjectName("extractSelector")
        self.setProperty("role", "card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(10)
        title = QLabel("What do you want?")
        title.setProperty("role", "subheading")
        header.addWidget(title)

        self._summary = QLabel()
        self._summary.setProperty("role", "caption")
        header.addWidget(self._summary)
        header.addStretch(1)

        self._all_button = QPushButton(EVERYTHING)
        self._all_button.setCheckable(True)
        self._all_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._all_button.setProperty("variant", "subtle")
        self._all_button.clicked.connect(self._select_everything)
        header.addWidget(self._all_button)
        layout.addLayout(header)

        wanted_primary = [t for t in (primary_types or []) if t in document_types]
        primary = wanted_primary or list(document_types)
        secondary = [t for t in document_types if t not in primary]

        chips = QWidget()
        chip_layout = FlowLayout(chips, margin=0, spacing=8)
        for document_type in primary:
            chip_layout.addWidget(self._make_chip(document_type))
        layout.addWidget(chips)

        if secondary:
            self._build_secondary_row(layout, secondary)

        self.set_selection([])

    def _make_chip(self, document_type: str) -> QPushButton:
        button = QPushButton(document_type)
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setProperty("variant", "subtle")
        button.toggled.connect(self._on_toggled)
        self._buttons[document_type] = button
        return button

    def _build_secondary_row(self, layout: QVBoxLayout, secondary: list[str]) -> None:
        """Less common types, tucked behind a disclosure so they never crowd
        the everyday choice but are always one click away."""
        collapsed = f"More types ({len(secondary)}) ▾"
        expanded = "Fewer types ▴"

        toggle = QPushButton(collapsed)
        toggle.setCheckable(True)
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        toggle.setProperty("variant", "subtle")

        more_chips = QWidget()
        more_layout = FlowLayout(more_chips, margin=0, spacing=8)
        for document_type in secondary:
            more_layout.addWidget(self._make_chip(document_type))
        more_chips.setVisible(False)

        def _toggle(checked: bool) -> None:
            more_chips.setVisible(checked)
            toggle.setText(expanded if checked else collapsed)

        toggle.toggled.connect(_toggle)
        layout.addWidget(toggle)
        layout.addWidget(more_chips)

    # ------------------------------------------------------------------
    def selection(self) -> list[str]:
        """Chosen types, or ``[]`` meaning everything."""
        chosen = [name for name, button in self._buttons.items() if button.isChecked()]
        return [] if len(chosen) == len(self._buttons) else chosen

    def set_selection(self, document_types: list[str]) -> None:
        wanted = set(document_types)
        self._updating = True
        try:
            for name, button in self._buttons.items():
                button.setChecked(bool(wanted) and name in wanted)
        finally:
            self._updating = False
        self._refresh()

    # ------------------------------------------------------------------
    def _select_everything(self) -> None:
        self.set_selection([])
        self.selection_changed.emit([])

    def _on_toggled(self, _checked: bool) -> None:
        if self._updating:
            return
        self._refresh()
        self.selection_changed.emit(self.selection())

    def _refresh(self) -> None:
        chosen = [name for name, button in self._buttons.items() if button.isChecked()]
        everything = not chosen or len(chosen) == len(self._buttons)

        self._all_button.setChecked(everything)
        if everything:
            self._summary.setText("Saving every document found")
        elif len(chosen) == 1:
            self._summary.setText(f"Saving only {chosen[0].lower()}s")
        else:
            self._summary.setText("Saving " + ", ".join(c.lower() for c in chosen))

        for name, button in self._buttons.items():
            # An explicit choice is highlighted; "everything" leaves them plain
            # rather than lighting all of them up, which would read as eight
            # separate decisions instead of one.
            button.setProperty(
                "variant", "accent" if button.isChecked() and not everything else "subtle"
            )
            style = button.style()
            style.unpolish(button)
            style.polish(button)


__all__ = ["ExtractSelector", "EVERYTHING"]
