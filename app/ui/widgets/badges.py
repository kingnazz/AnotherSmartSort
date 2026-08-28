"""Small presentational widgets: confidence pills, status chips, section labels."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget

from app.models.enums import ConfidenceBand, FileStatus
from app.services.confidence import ConfidenceThresholds, confidence_percent
from app.ui.theme import Palette


def band_colors(band: ConfidenceBand, palette: Palette) -> tuple[str, str]:
    """(background, foreground) for a confidence band."""
    return {
        ConfidenceBand.HIGH: (palette.success_soft, palette.success),
        ConfidenceBand.REVIEW_SUGGESTED: (palette.warning_soft, palette.warning),
        ConfidenceBand.REVIEW_REQUIRED: (palette.danger_soft, palette.danger),
    }[band]


def status_colors(status: FileStatus, palette: Palette) -> tuple[str, str]:
    """(background, foreground) for a queue status."""
    mapping = {
        FileStatus.WAITING: (palette.surface_alt, palette.text_muted),
        FileStatus.READING: (palette.accent_soft, palette.accent),
        FileStatus.OCR: (palette.accent_soft, palette.accent),
        FileStatus.ANALYZING: (palette.accent_soft, palette.accent),
        FileStatus.EXPORTING: (palette.accent_soft, palette.accent),
        FileStatus.REVIEW_NEEDED: (palette.warning_soft, palette.warning),
        FileStatus.READY: (palette.success_soft, palette.success),
        FileStatus.COMPLETED: (palette.success_soft, palette.success),
        FileStatus.ERROR: (palette.danger_soft, palette.danger),
    }
    return mapping.get(status, (palette.surface_alt, palette.text_muted))


class Pill(QLabel):
    """A compact rounded chip used for statuses and confidences."""

    def __init__(
        self,
        text: str = "",
        *,
        background: str = "transparent",
        foreground: str = "#000000",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.set_colors(background, foreground)

    def set_colors(self, background: str, foreground: str) -> None:
        self.setStyleSheet(
            f"""
            QLabel {{
                background-color: {background};
                color: {foreground};
                border-radius: 9px;
                padding: 2px 9px;
                font-size: 8.5pt;
                font-weight: 600;
            }}
            """
        )


class ConfidencePill(Pill):
    """Shows a confidence percentage coloured by its review band."""

    def __init__(
        self,
        confidence: float,
        thresholds: ConfidenceThresholds,
        palette: Palette,
        parent: QWidget | None = None,
    ) -> None:
        band = thresholds.band(confidence)
        background, foreground = band_colors(band, palette)
        super().__init__(
            confidence_percent(confidence),
            background=background,
            foreground=foreground,
            parent=parent,
        )
        self.setToolTip(f"{band.label} ({confidence_percent(confidence)} confidence)")

    def update_value(
        self, confidence: float, thresholds: ConfidenceThresholds, palette: Palette
    ) -> None:
        band = thresholds.band(confidence)
        background, foreground = band_colors(band, palette)
        self.setText(confidence_percent(confidence))
        self.set_colors(background, foreground)
        self.setToolTip(f"{band.label} ({confidence_percent(confidence)} confidence)")


class StatusPill(Pill):
    """Shows a queue status with its own colour."""

    def __init__(self, status: FileStatus, palette: Palette, parent: QWidget | None = None) -> None:
        background, foreground = status_colors(status, palette)
        super().__init__(status.value, background=background, foreground=foreground, parent=parent)

    def update_status(self, status: FileStatus, palette: Palette) -> None:
        background, foreground = status_colors(status, palette)
        self.setText(status.value)
        self.set_colors(background, foreground)


class SectionLabel(QLabel):
    """An uppercase group heading used above panels."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text.upper(), parent)
        self.setProperty("role", "sectionLabel")


class Card(QFrame):
    """A rounded surface with a subtle border."""

    def __init__(self, parent: QWidget | None = None, *, role: str = "card") -> None:
        super().__init__(parent)
        self.setProperty("role", role)
        self.setFrameShape(QFrame.Shape.NoFrame)


class HSeparator(QFrame):
    """A one-pixel horizontal rule."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "separator")
        self.setFixedHeight(1)


class KeyValueRow(QWidget):
    """A label/value pair for the inspector, with long values elided."""

    def __init__(self, label: str, value: str = "—", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 3, 0, 3)
        layout.setSpacing(10)

        self._label = QLabel(label)
        self._label.setProperty("role", "caption")
        self._label.setFixedWidth(104)
        self._label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self._value = QLabel(value or "—")
        self._value.setProperty("role", "body")
        self._value.setWordWrap(True)
        self._value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        layout.addWidget(self._label)
        layout.addWidget(self._value, 1)

    def set_value(self, value: str | None) -> None:
        text = (value or "").strip() or "—"
        self._value.setText(text)
        self._value.setToolTip(text if text != "—" else "")

    def label_text(self) -> str:
        return self._label.text()

    def value_text(self) -> str:
        return self._value.text()


__all__ = [
    "Pill",
    "ConfidencePill",
    "StatusPill",
    "SectionLabel",
    "Card",
    "HSeparator",
    "KeyValueRow",
    "band_colors",
    "status_colors",
]
