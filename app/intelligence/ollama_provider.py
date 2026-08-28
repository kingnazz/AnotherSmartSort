"""Ollama document-intelligence provider.

Runs against a local Ollama server, so page text never leaves the machine. Like
the OpenAI provider, every failure -- server down, model missing, malformed
output -- degrades gracefully to the local rules result.
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

logger = get_logger("ollama")

DEFAULT_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3.1"


class OllamaProvider(DocumentIntelligenceProvider):
    """Classifies pages using a locally hosted Ollama model."""

    kind = ProviderKind.OLLAMA
    name = "Ollama"
    sends_data_externally = False

    def __init__(
        self,
        profile: DocumentProfile,
        *,
        url: str = DEFAULT_URL,
        model: str = DEFAULT_MODEL,
        timeout: int = 90,
        session: requests.Session | None = None,
    ) -> None:
        self.profile = profile
        self.base_url = (url or DEFAULT_URL).strip().rstrip("/")
        self.model = (model or DEFAULT_MODEL).strip()
        self.timeout = max(5, int(timeout))
        self._session = session or requests.Session()
        self._owns_session = session is None

    # ------------------------------------------------------------------
    @property
    def chat_endpoint(self) -> str:
        return f"{self.base_url}/api/chat"

    @property
    def tags_endpoint(self) -> str:
        return f"{self.base_url}/api/tags"

    def is_available(self) -> ProviderAvailability:
        """Check the server is reachable and the configured model is installed."""
        if not self.base_url:
            return ProviderAvailability(False, "No Ollama address has been configured.")
        if not self.model:
            return ProviderAvailability(False, "No Ollama model has been selected.")

        try:
            response = self._session.get(self.tags_endpoint, timeout=min(10, self.timeout))
        except requests.ConnectionError:
            return ProviderAvailability(
                False,
                f"Ollama is not reachable at {self.base_url}. Start Ollama, or switch to "
                "Rules Only in Settings.",
            )
        except requests.Timeout:
            return ProviderAvailability(False, f"Ollama at {self.base_url} did not respond.")
        except requests.RequestException as exc:
            return ProviderAvailability(False, f"Ollama could not be contacted: {exc}")

        if response.status_code != 200:
            return ProviderAvailability(
                False, f"Ollama returned HTTP {response.status_code} when listing models."
            )

        try:
            models = {
                str(item.get("name", "")).split(":")[0]
                for item in (response.json().get("models") or [])
            }
        except ValueError:
            models = set()

        if models and self.model.split(":")[0] not in models:
            installed = ", ".join(sorted(m for m in models if m)) or "none"
            return ProviderAvailability(
                False,
                f"The Ollama model '{self.model}' is not installed. Available: {installed}. "
                f"Run: ollama pull {self.model}",
            )

        return ProviderAvailability(
            True, f"Ollama ({self.model}) at {self.base_url}. Analysis stays on this computer."
        )

    # ------------------------------------------------------------------
    def classify_page(self, context: PageContext) -> PageClassification:
        return self.analyze_page(context).classification

    def analyze_boundary(self, context: PageContext) -> BoundaryAssessment:
        return self.analyze_page(context).boundary

    def analyze_page(self, context: PageContext) -> PageInsight:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": build_messages(context),
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        }

        try:
            response = self._session.post(
                self.chat_endpoint, json=payload, timeout=self.timeout
            )
        except requests.ConnectionError:
            return _failed_insight(f"Ollama is not reachable at {self.base_url}.")
        except requests.Timeout:
            return _failed_insight(
                f"Ollama did not respond within {self.timeout} seconds."
            )
        except requests.RequestException as exc:
            return _failed_insight(f"Ollama request failed: {exc}")

        if response.status_code == 404:
            return _failed_insight(
                f"The Ollama model '{self.model}' is not installed. "
                f"Run: ollama pull {self.model}"
            )
        if response.status_code != 200:
            return _failed_insight(f"Ollama returned an error (HTTP {response.status_code}).")

        try:
            data = response.json()
        except ValueError:
            return _failed_insight("Ollama returned a malformed response.")

        content = ((data.get("message") or {}).get("content")) or data.get("response") or ""
        if not str(content).strip():
            return _failed_insight("Ollama returned an empty result for this page.")

        try:
            validated = validate_response(
                str(content),
                self.profile,
                default_type=context.previous_type,
                default_starts_new=False,
            )
        except ProviderResponseError as exc:
            logger.warning("Ollama returned unusable output: %s", exc)
            return _failed_insight("Ollama returned a response that could not be read.", 1)

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
            requests=1,
        )

    def close(self) -> None:
        if self._owns_session:
            self._session.close()


def _failed_insight(message: str, request_count: int = 1) -> PageInsight:
    return PageInsight(
        classification=PageClassification(document_type="Other", confidence=0.0),
        boundary=BoundaryAssessment(starts_new_document=False, confidence=0.0),
        candidate=Candidate(),
        used_ai=False,
        requests=request_count,
        error=message,
    )


__all__ = ["OllamaProvider", "DEFAULT_URL", "DEFAULT_MODEL"]
