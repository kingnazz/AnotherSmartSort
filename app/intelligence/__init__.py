"""Document intelligence providers and the factory that selects one."""

from __future__ import annotations

from app.intelligence.base import (
    BoundaryAssessment,
    DocumentIntelligenceProvider,
    PageClassification,
    PageContext,
    PageInsight,
    ProviderAvailability,
)
from app.intelligence.ollama_provider import OllamaProvider
from app.intelligence.openai_provider import OpenAIProvider
from app.intelligence.response_parser import ProviderResponseError, validate_response
from app.intelligence.rules_provider import RulesProvider
from app.models.enums import ProviderKind
from app.profiles.base import DocumentProfile


def build_ai_provider(
    kind: ProviderKind,
    profile: DocumentProfile,
    *,
    openai_api_key: str = "",
    openai_model: str = "gpt-4o-mini",
    openai_timeout: int = 45,
    ollama_url: str = "http://localhost:11434",
    ollama_model: str = "llama3.1",
    ollama_timeout: int = 90,
) -> DocumentIntelligenceProvider | None:
    """Build the configured AI provider, or ``None`` for Rules Only.

    Returning ``None`` is the normal case: the application is fully functional
    without any AI provider at all.
    """
    if kind is ProviderKind.OPENAI:
        return OpenAIProvider(
            profile,
            openai_api_key,
            model=openai_model,
            timeout=openai_timeout,
        )
    if kind is ProviderKind.OLLAMA:
        return OllamaProvider(
            profile,
            url=ollama_url,
            model=ollama_model,
            timeout=ollama_timeout,
        )
    return None


__all__ = [
    "DocumentIntelligenceProvider",
    "ProviderAvailability",
    "PageContext",
    "PageClassification",
    "BoundaryAssessment",
    "PageInsight",
    "RulesProvider",
    "OpenAIProvider",
    "OllamaProvider",
    "build_ai_provider",
    "validate_response",
    "ProviderResponseError",
]
