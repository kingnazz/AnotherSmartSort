"""Parsing and validating structured output from AI providers.

AI output is treated as hostile input. Every field is optional, every value is
coerced and clamped, unsupported document types collapse to the profile's
fallback, and anything unparseable raises :class:`ProviderResponseError` so the
caller can fall back to the local rules result. The application must never
crash, or silently trust, because a model returned something odd.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.models.candidate import Candidate
from app.profiles.base import DocumentProfile

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_MAX_REASONING = 400
_MAX_FIELD = 200

_TRUE_VALUES = {"true", "yes", "1", "y", "new", "starts_new_document"}
_FALSE_VALUES = {"false", "no", "0", "n", "continues", "continuation"}


class ProviderResponseError(ValueError):
    """Raised when a provider response cannot be understood at all."""


@dataclass
class ValidatedInsight:
    """A provider response after validation, safe to merge into the pipeline."""

    document_type: str
    classification_confidence: float
    starts_new_document: bool
    boundary_confidence: float
    candidate: Candidate = field(default_factory=Candidate)
    reasoning_summary: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    #: Fields the provider omitted, so callers can weight the answer.
    missing_fields: list[str] = field(default_factory=list)


def extract_json_object(text: str) -> dict[str, Any]:
    """Pull a JSON object out of a model response.

    Tolerates markdown fences and chatty preambles, both of which models emit
    even when asked for JSON only.
    """
    if not text or not text.strip():
        raise ProviderResponseError("The provider returned an empty response.")

    candidates: list[str] = []
    fenced = _FENCE_RE.search(text)
    if fenced:
        candidates.append(fenced.group(1).strip())
    candidates.append(text.strip())

    braced = _first_balanced_object(text)
    if braced:
        candidates.append(braced)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    return item

    raise ProviderResponseError("The provider did not return valid JSON.")


def _first_balanced_object(text: str) -> str | None:
    """Find the first balanced ``{...}`` span, ignoring braces inside strings."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _coerce_confidence(value: Any, default: float) -> float:
    """Accept 0-1 floats, 0-100 percentages, and numeric strings."""
    if value is None:
        return default
    if isinstance(value, bool):
        return 0.9 if value else 0.5
    try:
        number = float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
        return default
    if number > 1.0:
        number = number / 100.0
    return max(0.0, min(1.0, number))


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    return default


def _coerce_text(value: Any, limit: int = _MAX_FIELD) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = " ".join(str(value).split()).strip()
    if not text or text.lower() in {"null", "none", "n/a", "unknown", "not provided", ""}:
        return None
    return text[:limit]


def validate_response(
    payload: dict[str, Any] | str,
    profile: DocumentProfile,
    *,
    default_type: str | None = None,
    default_starts_new: bool = False,
) -> ValidatedInsight:
    """Validate a provider payload into a :class:`ValidatedInsight`.

    ``default_type`` / ``default_starts_new`` come from the local rules result
    and are used wherever the provider omitted a field.
    """
    data = extract_json_object(payload) if isinstance(payload, str) else payload
    if not isinstance(data, dict):
        raise ProviderResponseError("The provider response was not a JSON object.")

    missing: list[str] = []

    raw_type = data.get("document_type", data.get("documentType"))
    if raw_type is None:
        missing.append("document_type")
    document_type = profile.normalize_type(
        raw_type if raw_type is not None else (default_type or profile.default_type)
    )

    if "classification_confidence" not in data and "classificationConfidence" not in data:
        missing.append("classification_confidence")
    classification_confidence = _coerce_confidence(
        data.get("classification_confidence", data.get("classificationConfidence")), 0.6
    )

    if "starts_new_document" not in data and "startsNewDocument" not in data:
        missing.append("starts_new_document")
    starts_new = _coerce_bool(
        data.get("starts_new_document", data.get("startsNewDocument")), default_starts_new
    )

    if "boundary_confidence" not in data and "boundaryConfidence" not in data:
        missing.append("boundary_confidence")
    boundary_confidence = _coerce_confidence(
        data.get("boundary_confidence", data.get("boundaryConfidence")), 0.6
    )

    # A model that guessed a type we do not support is not a confident model.
    if raw_type is not None and document_type == profile.default_type:
        normalized_raw = str(raw_type).strip().lower()
        if normalized_raw and normalized_raw != profile.default_type.lower():
            classification_confidence = min(classification_confidence, 0.5)

    if missing:
        # Missing structure means a partially usable answer, not a trusted one.
        classification_confidence = min(classification_confidence, 0.7)
        boundary_confidence = min(boundary_confidence, 0.7)

    candidate = Candidate(
        name=_coerce_text(data.get("candidate_name", data.get("candidateName"))),
        email=_coerce_text(data.get("email")),
        phone=_coerce_text(data.get("phone")),
        linkedin=_coerce_text(data.get("linkedin", data.get("linkedIn"))),
        job_title=_coerce_text(data.get("job_title", data.get("jobTitle"))),
        applicant_id=_coerce_text(data.get("applicant_id", data.get("applicantId"))),
    )

    return ValidatedInsight(
        document_type=document_type,
        classification_confidence=classification_confidence,
        starts_new_document=starts_new,
        boundary_confidence=boundary_confidence,
        candidate=candidate,
        reasoning_summary=_coerce_text(
            data.get("reasoning_summary", data.get("reasoning")), _MAX_REASONING
        ),
        raw=data,
        missing_fields=missing,
    )


__all__ = [
    "validate_response",
    "extract_json_object",
    "ValidatedInsight",
    "ProviderResponseError",
]
