"""Page text normalization and feature extraction.

This module turns raw page text into a structured :class:`PageFeatures` record.
Both the rules classifier (*what kind of document is this?*) and the boundary
engine (*does this page start a new document?*) consume these features, which is
exactly why feature extraction lives in its own layer: the two questions are
answered independently from a shared, testable description of the page.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Patterns
# --------------------------------------------------------------------------

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

PHONE_RE = re.compile(
    r"""(?<![\d\-])(?:\+?\d{1,2}[\s.\-]?)?      # optional country code
        \(?\d{3}\)?[\s.\-]\s?\d{3}[\s.\-]\d{4}  # (555) 555-5555 / 555.555.5555
        (?![\d\-])""",
    re.VERBOSE,
)

LINKEDIN_RE = re.compile(
    r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/(?:in|pub)/[A-Za-z0-9_\-%.]+",
    re.IGNORECASE,
)

URL_RE = re.compile(r"(?:https?://|www\.)[A-Za-z0-9.\-]+\.[A-Za-z]{2,}(?:/\S*)?", re.IGNORECASE)

_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t)?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_DASH = r"(?:-|–|—|to|through|until)"

DATE_RANGE_RE = re.compile(
    rf"""(?ix)
    (?:
        {_MONTH}\.?\s+\d{{4}}\s*{_DASH}\s*(?:{_MONTH}\.?\s+\d{{4}}|present|current|now)
      | \b(?:19|20)\d{{2}}\s*{_DASH}\s*(?:(?:19|20)\d{{2}}|present|current|now)\b
      | \b\d{{1,2}}/(?:19|20)\d{{2}}\s*{_DASH}\s*(?:\d{{1,2}}/(?:19|20)\d{{2}}|present|current)\b
    )""",
)

YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")

#: ``Page 2 of 5`` / ``Page 2/5`` anywhere on a line.
PAGE_MARKER_RE = re.compile(r"(?i)\bpage\s+(\d{1,3})\s*(?:of|/)\s*(\d{1,3})\b")
#: A bare ``2 / 5``, accepted only when it is the entire line -- otherwise
#: values like ``Screening Score: 92 / 100`` would masquerade as page numbers.
BARE_PAGE_MARKER_RE = re.compile(r"^\s*(\d{1,3})\s*/\s*(\d{1,3})\s*$")

#: Bullet glyphs, including the middle dot many PDF producers emit.
BULLET_RE = re.compile(r"^\s*(?:[•●▪◦⁃∙·*\-–—+o])\s+\S")

SALUTATION_RE = re.compile(
    r"(?im)^\s*(dear\s+[^\n,]{0,60}|to\s+whom\s+it\s+may\s+concern|"
    r"attention\s*:|attn\s*:|hello\s+[a-z][^\n,]{0,40})[,:]?\s*$"
)

CLOSING_RE = re.compile(
    r"(?i)\b(sincerely|respectfully(?:\s+yours|\s+submitted)?|best\s+regards|kind\s+regards|"
    r"warm\s+regards|yours\s+truly|yours\s+sincerely|thank\s+you\s+for\s+your\s+(?:time|consideration)|"
    r"looking\s+forward\s+to\s+hearing\s+from\s+you)\b"
)

FORM_FIELD_RE = re.compile(r"^\s*[A-Z][A-Za-z /&'()#.\-]{1,44}\s*:\s*\S")

_SENTENCE_END = ".!?\"')]:;"

_LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    " ": " ", "–": "-", "—": "-", "﻿": "",
}


def normalize_text(text: str) -> str:
    """Normalize unicode, ligatures and whitespace without destroying line structure."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    for source, target in _LIGATURES.items():
        normalized = normalized.replace(source, target)
    lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in normalized.splitlines()]
    # Collapse runs of blank lines to a single blank line.
    cleaned: list[str] = []
    for line in lines:
        if not line and cleaned and not cleaned[-1]:
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def flatten(text: str) -> str:
    """Single-line lowercase form used for keyword matching across line breaks."""
    return " ".join((text or "").split()).lower()


@dataclass
class PageFeatures:
    """Structural and lexical description of one page."""

    text: str = ""
    flat: str = ""
    lines: list[str] = field(default_factory=list)

    char_count: int = 0
    word_count: int = 0
    line_count: int = 0
    avg_line_length: float = 0.0
    alpha_ratio: float = 0.0

    bullet_lines: int = 0
    bullet_ratio: float = 0.0
    long_line_count: int = 0
    paragraph_ratio: float = 0.0
    form_field_lines: int = 0
    form_field_ratio: float = 0.0
    heading_lines: list[str] = field(default_factory=list)
    caps_line_count: int = 0
    table_like_lines: int = 0

    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    linkedin: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)

    date_range_count: int = 0
    year_count: int = 0

    has_salutation: bool = False
    salutation_text: str | None = None
    has_closing: bool = False
    closing_text: str | None = None

    page_marker: tuple[int, int] | None = None
    header_line: str | None = None
    footer_line: str | None = None
    first_lines: list[str] = field(default_factory=list)
    last_lines: list[str] = field(default_factory=list)

    starts_lowercase: bool = False
    ends_mid_sentence: bool = False
    is_near_empty: bool = False
    is_label_only: bool = False

    @property
    def has_contact_block(self) -> bool:
        """Contact information concentrated near the top of the page."""
        top = "\n".join(self.first_lines[:8])
        signals = sum(
            (
                bool(EMAIL_RE.search(top)),
                bool(PHONE_RE.search(top)),
                bool(LINKEDIN_RE.search(top)),
            )
        )
        return signals >= 2 or (signals >= 1 and bool(self.emails) and self.line_count < 60)

    @property
    def text_density(self) -> float:
        """Characters per line -- separates dense prose from sparse forms/labels."""
        return self.char_count / self.line_count if self.line_count else 0.0


def _is_heading(line: str) -> bool:
    """Short, punctuation-light lines that read like section headings."""
    stripped = line.strip().rstrip(":")
    if not (2 <= len(stripped) <= 48):
        return False
    words = stripped.split()
    if not (1 <= len(words) <= 6):
        return False
    if stripped.endswith((".", ",", ";")):
        return False
    letters = [c for c in stripped if c.isalpha()]
    if not letters:
        return False
    upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
    if upper_ratio > 0.85:
        return True
    return all(w[0].isupper() for w in words if w and w[0].isalpha()) and upper_ratio > 0.15


def extract_features(raw_text: str) -> PageFeatures:
    """Build a :class:`PageFeatures` record from raw page text."""
    text = normalize_text(raw_text)
    features = PageFeatures(text=text, flat=flatten(text))

    lines = [line for line in text.splitlines() if line.strip()]
    features.lines = lines
    features.line_count = len(lines)
    features.char_count = len(text)
    features.word_count = len(features.flat.split())

    if lines:
        lengths = [len(line) for line in lines]
        features.avg_line_length = sum(lengths) / len(lengths)
        features.header_line = lines[0]
        features.footer_line = lines[-1]
        features.first_lines = lines[:12]
        features.last_lines = lines[-8:]
        features.starts_lowercase = bool(lines[0][:1].islower())
        features.ends_mid_sentence = _ends_mid_sentence(lines)

    alpha = sum(1 for c in text if c.isalpha())
    features.alpha_ratio = alpha / features.char_count if features.char_count else 0.0

    for line in lines:
        if BULLET_RE.match(line):
            features.bullet_lines += 1
        if len(line) > 80:
            features.long_line_count += 1
        if FORM_FIELD_RE.match(line):
            features.form_field_lines += 1
        if _is_heading(line):
            features.heading_lines.append(line.strip().rstrip(":"))
        letters = [c for c in line if c.isalpha()]
        if letters and sum(1 for c in letters if c.isupper()) / len(letters) > 0.85 and len(line) > 2:
            features.caps_line_count += 1
        if re.search(r"\S {2,}\S.* {2,}\S", line):
            features.table_like_lines += 1

    if features.line_count:
        features.bullet_ratio = features.bullet_lines / features.line_count
        features.paragraph_ratio = features.long_line_count / features.line_count
        features.form_field_ratio = features.form_field_lines / features.line_count

    features.emails = _unique(EMAIL_RE.findall(text))
    features.phones = _unique(m.group(0).strip() for m in PHONE_RE.finditer(text))
    features.linkedin = _unique(LINKEDIN_RE.findall(text))
    features.urls = _unique(URL_RE.findall(text))

    features.date_range_count = len(DATE_RANGE_RE.findall(text))
    features.year_count = len(YEAR_RE.findall(text))

    salutation = SALUTATION_RE.search(text)
    if salutation:
        features.has_salutation = True
        features.salutation_text = salutation.group(0).strip()

    closing = CLOSING_RE.search(text)
    if closing:
        features.has_closing = True
        features.closing_text = closing.group(0).strip()

    features.page_marker = _page_marker(text)

    features.is_near_empty = features.word_count < 8
    features.is_label_only = _looks_like_label_only(features)

    return features


#: A line this long that stops without terminal punctuation really was cut off.
_MID_SENTENCE_MIN_LENGTH = 60


def is_footer_line(line: str) -> bool:
    """True for running footers -- page markers, short name/title trailers."""
    stripped = line.strip()
    if not stripped:
        return True
    if PAGE_MARKER_RE.search(stripped) or BARE_PAGE_MARKER_RE.match(stripped):
        return True
    return len(stripped) <= 6


def _ends_mid_sentence(lines: list[str]) -> bool:
    """Detect prose genuinely running past the bottom of the page.

    A page whose last line is a footer (``Benjamin Perez - Page 2 of 3``) has not
    been cut off mid-sentence; treating it that way used to make every page in a
    document look like a continuation for the wrong reason.
    """
    for line in reversed(lines):
        if is_footer_line(line):
            continue
        stripped = line.rstrip()
        if len(stripped) < _MID_SENTENCE_MIN_LENGTH:
            return False
        return not stripped.endswith(tuple(_SENTENCE_END))
    return False


def _unique(values) -> list[str]:
    seen: list[str] = []
    for value in values:
        text = value.strip() if isinstance(value, str) else str(value)
        if text and text not in seen:
            seen.append(text)
    return seen


def _page_marker(text: str) -> tuple[int, int] | None:
    """Find a ``Page 2 of 5`` style marker, preferring the last occurrence.

    Explicit ``page N of M`` wording wins over a bare ``N / M`` line, which is
    only trusted when it occupies a line by itself.
    """
    explicit: tuple[int, int] | None = None
    bare: tuple[int, int] | None = None

    for line in text.splitlines():
        for match in PAGE_MARKER_RE.finditer(line):
            current, total = int(match.group(1)), int(match.group(2))
            if 0 < current <= total <= 999:
                explicit = (current, total)
        bare_match = BARE_PAGE_MARKER_RE.match(line)
        if bare_match:
            current, total = int(bare_match.group(1)), int(bare_match.group(2))
            if 0 < current <= total <= 999:
                bare = (current, total)

    return explicit or bare


def _looks_like_label_only(features: PageFeatures) -> bool:
    """A page carrying essentially nothing but a document label (a separator)."""
    if features.word_count == 0 or features.word_count > 12:
        return False
    if features.emails or features.phones:
        return False
    if features.line_count > 4:
        return False
    return True


def repeated_line_overlap(a: list[str], b: list[str]) -> float:
    """Fraction of ``a`` lines that also appear in ``b`` -- detects running headers."""
    if not a or not b:
        return 0.0
    normalized_b = {flatten(line) for line in b if len(line.strip()) > 3}
    if not normalized_b:
        return 0.0
    hits = sum(1 for line in a if len(line.strip()) > 3 and flatten(line) in normalized_b)
    total = sum(1 for line in a if len(line.strip()) > 3)
    return hits / total if total else 0.0


def line_length_similarity(a: PageFeatures, b: PageFeatures) -> float:
    """Rough layout similarity in [0, 1] from average line length and density."""
    if not a.line_count or not b.line_count:
        return 0.0
    longest = max(a.avg_line_length, b.avg_line_length, 1.0)
    length_similarity = 1.0 - abs(a.avg_line_length - b.avg_line_length) / longest
    densest = max(a.text_density, b.text_density, 1.0)
    density_similarity = 1.0 - abs(a.text_density - b.text_density) / densest
    return max(0.0, min(1.0, 0.5 * length_similarity + 0.5 * density_similarity))


__all__ = [
    "PageFeatures",
    "extract_features",
    "normalize_text",
    "flatten",
    "repeated_line_overlap",
    "line_length_similarity",
    "EMAIL_RE",
    "PHONE_RE",
    "LINKEDIN_RE",
    "URL_RE",
    "DATE_RANGE_RE",
    "PAGE_MARKER_RE",
    "BARE_PAGE_MARKER_RE",
    "BULLET_RE",
    "SALUTATION_RE",
    "CLOSING_RE",
]
