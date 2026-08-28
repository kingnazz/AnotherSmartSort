"""Confidence calibration and review banding.

Confidence is what makes the product efficient: the user should inspect the 13
uncertain documents out of 300, not all 300. Calibration therefore has to be
honest -- a score is high only when the evidence is both *strong* (lots of
signal) and *decisive* (clearly ahead of the runner-up).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.models.enums import ConfidenceBand

#: Evidence saturation constant for classification scores.
_CLASSIFICATION_TAU = 6.0
#: Evidence saturation constant for boundary scores.
_BOUNDARY_TAU = 2.5

_MIN_CONFIDENCE = 0.30
_MAX_CONFIDENCE = 0.985

DEFAULT_HIGH_THRESHOLD = 0.90
DEFAULT_REVIEW_THRESHOLD = 0.70


@dataclass(frozen=True)
class ConfidenceThresholds:
    """User-configurable review thresholds.

    * ``>= high`` -- no review required
    * ``>= review`` and ``< high`` -- review suggested
    * ``< review`` -- review required
    """

    high: float = DEFAULT_HIGH_THRESHOLD
    review: float = DEFAULT_REVIEW_THRESHOLD

    def __post_init__(self) -> None:
        high = _clamp(self.high, 0.05, 1.0)
        review = _clamp(self.review, 0.01, high - 0.01 if high > 0.02 else 0.01)
        object.__setattr__(self, "high", round(high, 4))
        object.__setattr__(self, "review", round(review, 4))

    def band(self, confidence: float) -> ConfidenceBand:
        if confidence >= self.high:
            return ConfidenceBand.HIGH
        if confidence >= self.review:
            return ConfidenceBand.REVIEW_SUGGESTED
        return ConfidenceBand.REVIEW_REQUIRED

    def requires_review(self, confidence: float) -> bool:
        """True when the value is below the "no review required" bar."""
        return confidence < self.high


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def calibrate_classification(top_score: float, runner_up_score: float) -> float:
    """Turn raw type scores into a calibrated confidence in [0.30, 0.985].

    Combines *evidence strength* (how much signal fired at all) with
    *separation* (how far ahead the winner is). A page where two types tie
    lands near 0.5 and is correctly routed to review.
    """
    if top_score <= 0:
        return _MIN_CONFIDENCE

    evidence = 1.0 - math.exp(-top_score / _CLASSIFICATION_TAU)
    runner_up = max(0.0, runner_up_score)
    separation = _clamp((top_score - runner_up) / top_score, 0.0, 1.0)

    raw = 0.5 * evidence + 0.5 * separation
    return round(_MIN_CONFIDENCE + (_MAX_CONFIDENCE - _MIN_CONFIDENCE) * raw, 4)


def calibrate_boundary(score: float) -> float:
    """Turn a signed boundary score into a confidence in [0.5, 0.985].

    The *sign* decides new-document vs continuation; the *magnitude* decides how
    sure we are. A score near zero yields ~0.5 and is flagged for review.
    """
    magnitude = 1.0 - math.exp(-abs(score) / _BOUNDARY_TAU)
    return round(0.5 + (_MAX_CONFIDENCE - 0.5) * magnitude, 4)


def combine_confidence(local: float, remote: float, agree: bool) -> float:
    """Blend a local (rules) confidence with a provider confidence.

    Agreement reinforces (bounded below 1.0); disagreement pulls the result
    toward uncertainty so the page surfaces in review rather than silently
    taking one side.
    """
    local = _clamp(local, 0.0, 1.0)
    remote = _clamp(remote, 0.0, 1.0)
    if agree:
        combined = 1.0 - (1.0 - local) * (1.0 - remote)
        return round(min(_MAX_CONFIDENCE, combined), 4)
    weaker = min(local, remote)
    stronger = max(local, remote)
    return round(_clamp(0.5 * stronger + 0.5 * (1.0 - weaker), 0.15, 0.75), 4)


def confidence_percent(value: float) -> str:
    """Format a confidence for display (``0.9732`` -> ``"97%"``)."""
    return f"{round(_clamp(value, 0.0, 1.0) * 100):.0f}%"


__all__ = [
    "ConfidenceThresholds",
    "calibrate_classification",
    "calibrate_boundary",
    "combine_confidence",
    "confidence_percent",
    "ConfidenceBand",
    "DEFAULT_HIGH_THRESHOLD",
    "DEFAULT_REVIEW_THRESHOLD",
]
