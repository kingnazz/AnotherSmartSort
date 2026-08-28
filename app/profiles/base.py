"""Document profile architecture.

A :class:`DocumentProfile` declares *what document types exist* and *what
evidence points at each one*. All domain-specific knowledge lives inside a
profile, so adding an Accounting or Medical Records profile later means adding
one module -- never editing the pipeline, the grouping engine, or the UI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from app.services.text_features import PageFeatures

OTHER = "Other"


@dataclass(frozen=True)
class Signal:
    """One piece of weighted evidence for (or against) a document type.

    A signal fires from keywords, a regular expression, or a structural
    predicate over :class:`PageFeatures`. Weights may be negative to express
    "this makes the type *less* likely".
    """

    name: str
    weight: float
    keywords: Sequence[str] = ()
    regex: re.Pattern[str] | None = None
    predicate: Callable[[PageFeatures], bool] | None = None
    #: Additional weight per extra distinct keyword hit, rewarding corroboration.
    per_hit_weight: float = 0.0
    #: Cap on how many extra hits can contribute.
    max_extra_hits: int = 4

    def hits(self, features: PageFeatures) -> int:
        """Number of independent matches this signal found on the page."""
        count = 0
        if self.keywords:
            count += sum(1 for keyword in self.keywords if _keyword_present(keyword, features.flat))
        if self.regex is not None and self.regex.search(features.text):
            count += 1
        if self.predicate is not None:
            try:
                if self.predicate(features):
                    count += 1
            except Exception:  # pragma: no cover - a bad predicate must not break analysis
                return count
        return count

    def evaluate(self, features: PageFeatures) -> float:
        """Weighted contribution of this signal for the given page."""
        count = self.hits(features)
        if count <= 0:
            return 0.0
        extra = min(count - 1, self.max_extra_hits) * self.per_hit_weight
        return self.weight + extra


_KEYWORD_CACHE: dict[str, re.Pattern[str]] = {}


def _keyword_present(keyword: str, flat_text: str) -> bool:
    """Word-boundary keyword match against the flattened lowercase page text."""
    pattern = _KEYWORD_CACHE.get(keyword)
    if pattern is None:
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(keyword.lower())}(?![a-z0-9])")
        _KEYWORD_CACHE[keyword] = pattern
    return bool(pattern.search(flat_text))


@dataclass
class DocumentProfile:
    """A named family of document types plus the evidence that identifies them."""

    name: str
    description: str = ""
    document_types: list[str] = field(default_factory=lambda: [OTHER])
    signals: dict[str, list[Signal]] = field(default_factory=dict)

    #: Types whose pages usually carry candidate/subject identity. Used to pick
    #: whose name to trust when pages of one document disagree.
    identity_types: tuple[str, ...] = ()
    #: Types where a *missing* name is itself surprising. A narrower set: a
    #: cover letter often carries a signature, but a scanned one just as often
    #: does not, whereas a resume with no name anywhere is genuinely odd. Used
    #: to decide how much to trust attributing an anonymous document by
    #: position alone.
    identity_expected_types: tuple[str, ...] = ()
    #: Separator-page label text -> document type it introduces.
    separator_labels: dict[str, str] = field(default_factory=dict)
    #: Alternate spellings (from users or AI providers) -> canonical type.
    type_aliases: dict[str, str] = field(default_factory=dict)
    #: Types that are almost always a single page (weak boundary hint only).
    usually_single_page: tuple[str, ...] = ()
    #: Types that commonly run to several pages (weak continuation hint only).
    usually_multi_page: tuple[str, ...] = ()
    #: Order document types appear in a combined candidate packet PDF. Types
    #: absent from a candidate are skipped; types absent from this list follow
    #: in source order.
    packet_order: tuple[str, ...] = ()
    #: The handful of types the home screen's type selector highlights by
    #: default (the rest stay reachable, just tucked under "more types").
    #: Empty means no narrowing -- every type shows equally.
    primary_document_types: tuple[str, ...] = ()

    default_type: str = OTHER

    # -- type handling ----------------------------------------------------
    def is_known_type(self, document_type: str | None) -> bool:
        return bool(document_type) and document_type in self.document_types

    def normalize_type(self, document_type: str | None) -> str:
        """Map arbitrary text onto a supported type, falling back to ``Other``.

        Used to validate provider output -- an AI returning ``"curriculum vitae"``
        or ``"RESUME"`` must never inject an unsupported type into the model.
        """
        if not document_type:
            return self.default_type
        raw = str(document_type).strip()
        if raw in self.document_types:
            return raw

        key = re.sub(r"[^a-z0-9]+", " ", raw.lower()).strip()
        if not key:
            return self.default_type

        for known in self.document_types:
            if re.sub(r"[^a-z0-9]+", " ", known.lower()).strip() == key:
                return known
        alias = self.type_aliases.get(key)
        if alias and alias in self.document_types:
            return alias
        return self.default_type

    def score_page(self, features: PageFeatures) -> dict[str, float]:
        """Raw (unnormalized) evidence score for every document type."""
        scores: dict[str, float] = {}
        for document_type, signals in self.signals.items():
            total = sum(signal.evaluate(features) for signal in signals)
            scores[document_type] = round(total, 4)
        return scores

    def explain_page(self, features: PageFeatures, document_type: str) -> list[tuple[str, float]]:
        """Per-signal breakdown for a type -- powers the review inspector."""
        results: list[tuple[str, float]] = []
        for signal in self.signals.get(document_type, []):
            value = signal.evaluate(features)
            if value:
                results.append((signal.name, round(value, 3)))
        results.sort(key=lambda item: abs(item[1]), reverse=True)
        return results

    def separator_type_for(self, features: PageFeatures) -> str | None:
        """If this page is a label-only separator, the type it announces."""
        if not features.is_label_only:
            return None
        key = re.sub(r"[^a-z0-9 ]+", " ", features.flat).strip()
        key = re.sub(r"\s+", " ", key)
        if not key:
            return None
        if key in self.separator_labels:
            return self.separator_labels[key]
        # Tolerate "resume:" / "section 2 - resume" style label pages.
        for label, document_type in self.separator_labels.items():
            if re.fullmatch(rf"(?:section\s*\d*\s*[-:]?\s*)?{re.escape(label)}s?", key):
                return document_type
        return None


class ProfileRegistry:
    """Lookup for the profiles available to the application."""

    def __init__(self, profiles: Iterable[DocumentProfile] | None = None) -> None:
        self._profiles: dict[str, DocumentProfile] = {}
        for profile in profiles or ():
            self.register(profile)

    def register(self, profile: DocumentProfile) -> None:
        self._profiles[profile.name] = profile

    def get(self, name: str | None) -> DocumentProfile:
        """Return the named profile, falling back to the first registered one."""
        if name and name in self._profiles:
            return self._profiles[name]
        if not self._profiles:
            raise LookupError("No document profiles are registered")
        return next(iter(self._profiles.values()))

    def names(self) -> list[str]:
        return list(self._profiles)

    def __contains__(self, name: object) -> bool:
        return name in self._profiles


__all__ = ["DocumentProfile", "Signal", "ProfileRegistry", "OTHER"]
