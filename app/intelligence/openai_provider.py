"""OpenAI document-intelligence provider.

Talks to the Chat Completions API over plain HTTPS (``requests``) rather than
pulling in an extra SDK. The API key comes from Settings, is never written to
source, and is never logged. Every failure mode -- bad key, timeout, rate limit,
malformed output, network loss -- degrades to "use the local rules result"
instead of raising into the pipeline.
"""

from __future__ import annotations

from typing import Any

import requests

from app.intelligence.base import (
    BoundaryAssessment,
    DocumentIntelligenceProvider,
    PageClassification,
    PageContext,
    PageInsight,
    ProviderAvailability,
)
from app.intelligence.prompts import build_messages
from app.intelligence.response_parser import ProviderResponseError, validate_response
from app.models.candidate import Candidate
from app.models.enums import ProviderKind
from app.profiles.base import DocumentProfile
from app.utils.logging_setup import get_logger

logger = get_logger("openai")

DEFAULT_ENDPOINT = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"

#: One retry only, and only for failures that are plausibly transient.
_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 2


class OpenAIProvider(DocumentIntelligenceProvider):
    """Classifies pages and validates boundaries using an OpenAI model."""

    kind = ProviderKind.OPENAI
    name = "OpenAI"
    sends_data_externally = True

    def __init__(
        self,
        profile: DocumentProfile,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        timeout: int = 45,
        endpoint: str = DEFAULT_ENDPOINT,
        session: requests.Session | None = None,
    ) -> None:
        self.profile = profile
        self._api_key = (api_key or "").strip()
        self.model = (model or DEFAULT_MODEL).strip()
        self.timeout = max(5, int(timeout))
        self.endpoint = endpoint
        self._session = session or requests.Session()
        self._owns_session = session is None

    # ------------------------------------------------------------------
    def is_available(self) -> ProviderAvailability:
        if not self._api_key:
            return ProviderAvailability(
                False, "No OpenAI API key has been saved. Add one in Settings."
            )
        if not self.model:
            return ProviderAvailability(False, "No OpenAI model has been selected.")
        return ProviderAvailability(
            True,
            f"OpenAI ({self.model}). Extracted page text is sent to OpenAI for analysis.",
        )

    # ------------------------------------------------------------------
    def classify_page(self, context: PageContext) -> PageClassification:
        return self.analyze_page(context).classification

    def analyze_boundary(self, context: PageContext) -> BoundaryAssessment:
        return self.analyze_page(context).boundary

    def analyze_page(self, context: PageContext) -> PageInsight:
        """Answer both questions in a single request."""
        availability = self.is_available()
        if not availability.available:
            return _failed_insight(availability.message)

        payload = {
            "model": self.model,
            "messages": build_messages(context),
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }

        try:
            content, requests_made = self._post_with_retry(payload)
        except _ProviderCallError as exc:
            logger.warning("OpenAI request failed: %s", exc.user_message)
            return _failed_insight(exc.user_message, request_count=exc.attempts)

        try:
            validated = validate_response(
                content,
                self.profile,
                default_type=context.previous_type,
                default_starts_new=False,
            )
        except ProviderResponseError as exc:
            logger.warning("OpenAI returned unusable output: %s", exc)
            return _failed_insight(
                "OpenAI returned a response that could not be read.", request_count=requests_made
            )

        return _insight_from(validated, requests_made)

    # ------------------------------------------------------------------
    def _post_with_retry(self, payload: dict[str, Any]) -> tuple[str, int]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        last_error: _ProviderCallError | None = None

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = self._session.post(
                    self.endpoint, headers=headers, json=payload, timeout=self.timeout
                )
            except requests.Timeout:
                last_error = _ProviderCallError(
                    f"OpenAI did not respond within {self.timeout} seconds.", attempt
                )
            except requests.ConnectionError:
                last_error = _ProviderCallError(
                    "OpenAI could not be reached. Check the internet connection.", attempt
                )
            except requests.RequestException as exc:
                last_error = _ProviderCallError(f"OpenAI request failed: {exc}", attempt)
            else:
                error = self._error_for(response)
                if error is None:
                    return self._content_of(response), attempt
                last_error = _ProviderCallError(error.user_message, attempt)
                if not error.retryable:
                    raise last_error

            if attempt >= _MAX_ATTEMPTS:
                break

        raise last_error or _ProviderCallError("OpenAI request failed.", _MAX_ATTEMPTS)

    def _error_for(self, response: requests.Response) -> "_ResponseError | None":
        status = response.status_code
        if status == 200:
            return None
        if status == 401:
            return _ResponseError(
                "The OpenAI API key was rejected. Check the key in Settings.", False
            )
        if status == 403:
            return _ResponseError("This OpenAI account cannot use the selected model.", False)
        if status == 404:
            return _ResponseError(
                f"The OpenAI model '{self.model}' was not found.", False
            )
        if status == 429:
            return _ResponseError("OpenAI rate limit reached. Try again shortly.", True)
        return _ResponseError(
            f"OpenAI returned an error (HTTP {status}).", status in _RETRYABLE_STATUS
        )

    @staticmethod
    def _content_of(response: requests.Response) -> str:
        try:
            data = response.json()
        except ValueError as exc:
            raise _ProviderCallError("OpenAI returned a malformed response.", 1) from exc

        choices = data.get("choices") or []
        if not choices:
            raise _ProviderCallError("OpenAI returned no result for this page.", 1)
        message = (choices[0] or {}).get("message") or {}
        content = message.get("content")
        if isinstance(content, list):  # some responses return content parts
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        if not content:
            raise _ProviderCallError("OpenAI returned an empty result for this page.", 1)
        return str(content)

    def close(self) -> None:
        if self._owns_session:
            self._session.close()


class _ResponseError:
    def __init__(self, user_message: str, retryable: bool) -> None:
        self.user_message = user_message
        self.retryable = retryable


class _ProviderCallError(Exception):
    def __init__(self, user_message: str, attempts: int) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.attempts = attempts


def _failed_insight(message: str, *, request_count: int = 0) -> PageInsight:
    """A neutral result that tells the caller to keep the local answer."""
    return PageInsight(
        classification=PageClassification(document_type="Other", confidence=0.0),
        boundary=BoundaryAssessment(starts_new_document=False, confidence=0.0),
        candidate=Candidate(),
        used_ai=False,
        requests=request_count,
        error=message,
    )


def _insight_from(validated, requests_made: int) -> PageInsight:
    return PageInsight(
        classification=PageClassification(
            document_type=validated.document_type,
            confidence=validated.classification_confidence,
            reasoning=validated.reasoning_summary,
            raw=validated.raw,
        ),
        boundary=BoundaryAssessment(
            starts_new_document=validated.starts_new_document,
            confidence=validated.boundary_confidence,
            reasons=[validated.reasoning_summary] if validated.reasoning_summary else [],
            raw=validated.raw,
        ),
        candidate=validated.candidate,
        reasoning=validated.reasoning_summary,
        used_ai=True,
        requests=requests_made,
    )


__all__ = ["OpenAIProvider", "DEFAULT_MODEL", "DEFAULT_ENDPOINT"]
