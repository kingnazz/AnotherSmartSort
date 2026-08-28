"""Visual theme: a restrained, Windows 11 inspired light/dark palette.

The look is built from a small token set (surfaces, strokes, text, accent)
rendered into Qt style sheets. Everything is defined in both light and dark so
the application never inherits an unreadable system palette.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication


class ThemeMode(str, Enum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


@dataclass(frozen=True)
class Palette:
    """Semantic colour tokens for one theme."""

    window: str
    surface: str
    surface_alt: str
    card: str
    card_hover: str
    stroke: str
    stroke_strong: str
    text: str
    text_secondary: str
    text_muted: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_text: str
    accent_soft: str
    success: str
    success_soft: str
    warning: str
    warning_soft: str
    danger: str
    danger_soft: str
    selection: str
    shadow: str
    is_dark: bool


LIGHT = Palette(
    window="#F3F3F3",
    surface="#FFFFFF",
    surface_alt="#FAFAFA",
    card="#FFFFFF",
    card_hover="#F5F7FA",
    stroke="#E3E3E3",
    stroke_strong="#CFCFCF",
    text="#1A1A1A",
    text_secondary="#4A4A4A",
    text_muted="#767676",
    accent="#0F6CBD",
    accent_hover="#115EA3",
    accent_pressed="#0C50A0",
    accent_text="#FFFFFF",
    accent_soft="#EAF2FB",
    success="#0F7B0F",
    success_soft="#E8F5E9",
    warning="#9A6700",
    warning_soft="#FFF4E5",
    danger="#B3261E",
    danger_soft="#FDECEA",
    selection="#E4EFFA",
    shadow="rgba(0, 0, 0, 0.08)",
    is_dark=False,
)

DARK = Palette(
    window="#1F1F1F",
    surface="#272727",
    surface_alt="#2C2C2C",
    card="#2B2B2B",
    card_hover="#333333",
    stroke="#3D3D3D",
    stroke_strong="#4C4C4C",
    text="#F5F5F5",
    text_secondary="#CFCFCF",
    text_muted="#9E9E9E",
    accent="#4CA0E0",
    accent_hover="#63AFE8",
    accent_pressed="#3B8BC9",
    accent_text="#10222F",
    accent_soft="#22374A",
    success="#6CCB6C",
    success_soft="#20321F",
    warning="#E0A23C",
    warning_soft="#382C16",
    danger="#F4837B",
    danger_soft="#3A2220",
    selection="#2E4A63",
    shadow="rgba(0, 0, 0, 0.35)",
    is_dark=True,
)

#: Preferred UI fonts, best first. Segoe UI Variable ships with Windows 11.
_FONT_STACK = (
    "Segoe UI Variable Text",
    "Segoe UI",
    "Inter",
    "Noto Sans",
    "DejaVu Sans",
    "Helvetica Neue",
    "Arial",
)

BASE_FONT_SIZE = 10  # points


def resolve_mode(mode: str | ThemeMode) -> ThemeMode:
    try:
        resolved = ThemeMode(str(mode))
    except ValueError:
        resolved = ThemeMode.SYSTEM
    return resolved


def is_dark_mode(mode: str | ThemeMode) -> bool:
    """Resolve ``system`` against the platform's own light/dark preference."""
    resolved = resolve_mode(mode)
    if resolved is ThemeMode.DARK:
        return True
    if resolved is ThemeMode.LIGHT:
        return False

    app = QApplication.instance()
    if app is not None:
        try:
            scheme = app.styleHints().colorScheme()
            return scheme == Qt.ColorScheme.Dark
        except (AttributeError, TypeError):  # older Qt without colorScheme()
            window = app.palette().color(QPalette.ColorRole.Window)
            return window.lightness() < 128
    return False


def palette_for(mode: str | ThemeMode) -> Palette:
    return DARK if is_dark_mode(mode) else LIGHT


def preferred_font_family() -> str:
    """First installed font from the preferred stack."""
    available = set(QFontDatabase.families())
    for family in _FONT_STACK:
        if family in available:
            return family
    return QApplication.font().family()


def apply_theme(app: QApplication, mode: str | ThemeMode) -> Palette:
    """Apply fonts, Qt palette and the global style sheet."""
    palette = palette_for(mode)

    font = QFont(preferred_font_family(), BASE_FONT_SIZE)
    font.setHintingPreference(QFont.HintingPreference.PreferDefaultHinting)
    app.setFont(font)

    app.setStyle("Fusion")
    app.setPalette(_qt_palette(palette))
    app.setStyleSheet(build_stylesheet(palette))
    return palette


def _qt_palette(tokens: Palette) -> QPalette:
    """Base Qt palette so native-drawn parts match the style sheet."""
    palette = QPalette()
    role = QPalette.ColorRole
    group = QPalette.ColorGroup

    palette.setColor(role.Window, QColor(tokens.window))
    palette.setColor(role.WindowText, QColor(tokens.text))
    palette.setColor(role.Base, QColor(tokens.surface))
    palette.setColor(role.AlternateBase, QColor(tokens.surface_alt))
    palette.setColor(role.Text, QColor(tokens.text))
    palette.setColor(role.Button, QColor(tokens.surface))
    palette.setColor(role.ButtonText, QColor(tokens.text))
    palette.setColor(role.Highlight, QColor(tokens.accent))
    palette.setColor(role.HighlightedText, QColor(tokens.accent_text))
    palette.setColor(role.ToolTipBase, QColor(tokens.surface))
    palette.setColor(role.ToolTipText, QColor(tokens.text))
    palette.setColor(role.PlaceholderText, QColor(tokens.text_muted))
    palette.setColor(role.Link, QColor(tokens.accent))

    for disabled in (role.WindowText, role.Text, role.ButtonText):
        palette.setColor(group.Disabled, disabled, QColor(tokens.text_muted))
    return palette


def build_stylesheet(t: Palette) -> str:
    """The application style sheet, generated from the palette tokens."""
    return f"""
/* ---------- base ---------- */
QWidget {{
    color: {t.text};
    background-color: transparent;
}}
QMainWindow, QDialog {{
    background-color: {t.window};
}}
QToolTip {{
    background-color: {t.surface};
    color: {t.text};
    border: 1px solid {t.stroke};
    border-radius: 6px;
    padding: 6px 8px;
}}

/* ---------- typography ---------- */
QLabel[role="title"] {{
    font-size: 20pt;
    font-weight: 600;
    color: {t.text};
}}
QLabel[role="heading"] {{
    font-size: 14pt;
    font-weight: 600;
    color: {t.text};
}}
QLabel[role="subheading"] {{
    font-size: 11pt;
    font-weight: 600;
    color: {t.text};
}}
QLabel[role="body"] {{
    font-size: 10pt;
    color: {t.text_secondary};
}}
QLabel[role="caption"] {{
    font-size: 9pt;
    color: {t.text_muted};
}}
QLabel[role="sectionLabel"] {{
    font-size: 8.5pt;
    font-weight: 700;
    color: {t.text_muted};
    letter-spacing: 0.6px;
}}
QLabel[role="brand"] {{
    font-size: 15pt;
    font-weight: 600;
    color: {t.text};
}}

/* ---------- surfaces ---------- */
QFrame[role="card"] {{
    background-color: {t.card};
    border: 1px solid {t.stroke};
    border-radius: 8px;
}}
QFrame[role="panel"] {{
    background-color: {t.surface};
    border: 1px solid {t.stroke};
    border-radius: 8px;
}}
QFrame[role="header"] {{
    background-color: {t.surface};
    border: none;
    border-bottom: 1px solid {t.stroke};
}}
QFrame[role="footer"] {{
    background-color: {t.surface};
    border: none;
    border-top: 1px solid {t.stroke};
}}
QFrame[role="separator"] {{
    background-color: {t.stroke};
    border: none;
    max-height: 1px;
}}

/* ---------- buttons ---------- */
QPushButton {{
    background-color: {t.surface};
    color: {t.text};
    border: 1px solid {t.stroke_strong};
    border-radius: 6px;
    padding: 7px 16px;
    min-height: 18px;
}}
QPushButton:hover {{
    background-color: {t.card_hover};
}}
QPushButton:pressed {{
    background-color: {t.surface_alt};
}}
QPushButton:disabled {{
    color: {t.text_muted};
    border-color: {t.stroke};
    background-color: {t.surface_alt};
}}
QPushButton:focus {{
    border: 2px solid {t.accent};
    padding: 6px 15px;
}}
QPushButton[variant="accent"] {{
    background-color: {t.accent};
    color: {t.accent_text};
    border: 1px solid {t.accent};
    font-weight: 600;
}}
QPushButton[variant="accent"]:hover {{
    background-color: {t.accent_hover};
    border-color: {t.accent_hover};
}}
QPushButton[variant="accent"]:pressed {{
    background-color: {t.accent_pressed};
}}
QPushButton[variant="accent"]:disabled {{
    background-color: {t.surface_alt};
    color: {t.text_muted};
    border-color: {t.stroke};
}}
QPushButton[variant="subtle"] {{
    background-color: transparent;
    border: 1px solid transparent;
    color: {t.text_secondary};
    padding: 6px 12px;
}}
QPushButton[variant="subtle"]:hover {{
    background-color: {t.card_hover};
    color: {t.text};
}}
QPushButton[variant="danger"] {{
    color: {t.danger};
    border-color: {t.stroke_strong};
}}
QPushButton[variant="danger"]:hover {{
    background-color: {t.danger_soft};
}}

/* ---------- inputs ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background-color: {t.surface};
    color: {t.text};
    border: 1px solid {t.stroke_strong};
    border-radius: 6px;
    padding: 6px 10px;
    selection-background-color: {t.accent};
    selection-color: {t.accent_text};
    min-height: 18px;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border: 2px solid {t.accent};
    padding: 5px 9px;
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background-color: {t.surface_alt};
    color: {t.text_muted};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {t.text_secondary};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background-color: {t.surface};
    color: {t.text};
    border: 1px solid {t.stroke};
    border-radius: 6px;
    selection-background-color: {t.selection};
    selection-color: {t.text};
    padding: 4px;
    outline: none;
}}

/* ---------- checkboxes / radios ---------- */
QCheckBox, QRadioButton {{
    spacing: 8px;
    color: {t.text};
    padding: 2px;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {t.stroke_strong};
    background-color: {t.surface};
}}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {t.accent};
}}
QCheckBox::indicator:checked {{
    background-color: {t.accent};
    border-color: {t.accent};
    image: none;
}}
QRadioButton::indicator:checked {{
    background-color: {t.accent};
    border: 4px solid {t.surface};
    outline: 1px solid {t.accent};
}}
QCheckBox:disabled, QRadioButton:disabled {{ color: {t.text_muted}; }}

/* ---------- tables ---------- */
QTableWidget, QTableView, QTreeWidget, QTreeView, QListWidget, QListView {{
    background-color: {t.surface};
    alternate-background-color: {t.surface_alt};
    border: 1px solid {t.stroke};
    border-radius: 8px;
    gridline-color: {t.stroke};
    outline: none;
    selection-background-color: {t.selection};
    selection-color: {t.text};
}}
QTableWidget::item, QTreeWidget::item, QListWidget::item {{
    padding: 7px 8px;
    border: none;
}}
QTableWidget::item:selected, QTreeWidget::item:selected, QListWidget::item:selected {{
    background-color: {t.selection};
    color: {t.text};
}}
QHeaderView::section {{
    background-color: {t.surface_alt};
    color: {t.text_muted};
    border: none;
    border-bottom: 1px solid {t.stroke};
    padding: 8px;
    font-weight: 600;
}}
QHeaderView::section:first {{ border-top-left-radius: 8px; }}
QHeaderView::section:last {{ border-top-right-radius: 8px; }}
QTableCornerButton::section {{
    background-color: {t.surface_alt};
    border: none;
}}

/* ---------- scrollbars ---------- */
QScrollBar:vertical {{
    background: transparent;
    width: 12px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {t.stroke_strong};
    border-radius: 5px;
    min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{ background: {t.text_muted}; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 12px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {t.stroke_strong};
    border-radius: 5px;
    min-width: 32px;
}}
QScrollBar::handle:horizontal:hover {{ background: {t.text_muted}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

/* ---------- progress ---------- */
QProgressBar {{
    background-color: {t.surface_alt};
    border: none;
    border-radius: 4px;
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background-color: {t.accent};
    border-radius: 4px;
}}

/* ---------- tabs ---------- */
QTabWidget::pane {{
    border: 1px solid {t.stroke};
    border-radius: 8px;
    background-color: {t.surface};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {t.text_secondary};
    padding: 8px 16px;
    margin-right: 2px;
    border: none;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:hover {{ color: {t.text}; }}
QTabBar::tab:selected {{
    color: {t.accent};
    border-bottom: 2px solid {t.accent};
    font-weight: 600;
}}
QTabBar::tab:focus {{ border-bottom: 2px solid {t.accent}; }}

/* ---------- misc ---------- */
QSplitter::handle {{ background-color: {t.stroke}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QScrollArea {{ border: none; background-color: transparent; }}
QGroupBox {{
    border: 1px solid {t.stroke};
    border-radius: 8px;
    margin-top: 12px;
    padding: 14px 12px 12px 12px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: {t.text_muted};
}}
QMenu {{
    background-color: {t.surface};
    border: 1px solid {t.stroke};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{
    padding: 7px 24px 7px 12px;
    border-radius: 4px;
}}
QMenu::item:selected {{ background-color: {t.selection}; }}
QMenu::separator {{
    height: 1px;
    background: {t.stroke};
    margin: 4px 8px;
}}
"""


def high_dpi_setup() -> None:
    """Enable crisp rendering at 125%/150% Windows scaling.

    Qt 6 scales automatically; this only opts into fractional rounding that
    avoids the blurry half-pixel layouts seen at 125% and 150%.
    """
    if hasattr(QApplication, "setHighDpiScaleFactorRoundingPolicy"):
        from PySide6.QtCore import Qt as _Qt

        QApplication.setHighDpiScaleFactorRoundingPolicy(
            _Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    if sys.platform.startswith("win"):
        # Keep text metrics stable across monitors with different scaling.
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings, True)


__all__ = [
    "ThemeMode",
    "Palette",
    "LIGHT",
    "DARK",
    "apply_theme",
    "palette_for",
    "build_stylesheet",
    "is_dark_mode",
    "resolve_mode",
    "high_dpi_setup",
    "preferred_font_family",
]
