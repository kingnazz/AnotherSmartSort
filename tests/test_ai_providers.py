"""AI provider behaviour: response validation and graceful failure.

No network is used. HTTP is stubbed so every failure mode the specification
lists can be exercised deterministically.
"""

from __future__ import annotations

import json

import pytest
import requests

from app.intelligence.base import PageContext
from app.intelligence.ollama_provider import OllamaProvider
from app.intelligence.openai_provider import OpenAIProvider
from app.intelligence.response_parser import (
    ProviderResponseError,
    extract_json_object,
    validate_response,
)
from app.intelligence.rules_provider import RulesProvider
from app.models.enums import ProviderKind
from app.profiles.base import OTHER
from app.profiles.recruiting import COVER_LETTER, RESUME
from app.services.classification_service import ClassificationService
from app.services.text_features import extract_features
from scripts import sample_data

VALID_RESPONSE = {
    "document_type": "Resume",
    "classification_confidence": 0.97,
    "starts_new_document": False,
    "boundary_confidence": 0.93,
    "candidate_name": "Benjamin Perez",
    "email": "benjamin@example.com",
    "phone": "555-555-5555",
    "linkedin": None,
    "job_title": None,
    "applicant_id": None,
    "reasoning_summary": "The page continues the employment history from the previous page.",
}


def make_context(text: str = "Some page text", profile=None) -> PageContext:
    return PageContext(
        source_pdf="test.pdf",
        page_index=1,
        page_count=3,
        text=text,
        features=extract_features(text),
        previous_type=RESUME,
        previous_confidence=0.9,
        document_types=tuple(profile.document_types) if profile else (),
        profile_name="Recruiting",
    )


class FakeResponse:
    def __init__(self, status_code: int = 200, payload=None, text: str | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self._text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """Records calls and replays queued responses or raises queued exceptions."""

    def __init__(self, *responses) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def _next(self):
        if not self._responses:
            raise AssertionError("no more queued responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self._next()

    def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self._next()

    def close(self) -> None:
        pass


def openai_payload(content) -> dict:
    body = content if isinstance(content, str) else json.dumps(content)
    return {"choices": [{"message": {"content": body}}]}


# ----------------------------------------------------------------------
class TestResponseValidation:
    def test_valid_structured_response(self, profile) -> None:
        result = validate_response(VALID_RESPONSE, profile)
        assert result.document_type == RESUME
        assert result.classification_confidence == pytest.approx(0.97)
        assert result.starts_new_document is False
        assert result.boundary_confidence == pytest.approx(0.93)
        assert result.candidate.name == "Benjamin Perez"

    def test_json_wrapped_in_markdown_fences(self, profile) -> None:
        raw = f"```json\n{json.dumps(VALID_RESPONSE)}\n```"
        assert validate_response(raw, profile).document_type == RESUME

    def test_json_with_a_chatty_preamble(self, profile) -> None:
        raw = f"Sure! Here is the analysis:\n{json.dumps(VALID_RESPONSE)}\nHope that helps."
        assert validate_response(raw, profile).document_type == RESUME

    def test_invalid_json_raises(self, profile) -> None:
        with pytest.raises(ProviderResponseError):
            validate_response("this is not json at all", profile)

    def test_empty_response_raises(self, profile) -> None:
        with pytest.raises(ProviderResponseError):
            validate_response("", profile)

    def test_unsupported_document_type_falls_back_and_loses_confidence(self, profile) -> None:
        result = validate_response(
            {**VALID_RESPONSE, "document_type": "Tax Return"}, profile
        )
        assert result.document_type == OTHER
        assert result.classification_confidence <= 0.5

    def test_type_aliases_are_normalised(self, profile) -> None:
        assert validate_response({"document_type": "CV"}, profile).document_type == RESUME
        assert (
            validate_response({"document_type": "letter of application"}, profile).document_type
            == COVER_LETTER
        )

    def test_missing_fields_are_defaulted_and_confidence_capped(self, profile) -> None:
        result = validate_response({"document_type": "Resume"}, profile)
        assert result.document_type == RESUME
        assert result.classification_confidence <= 0.7
        assert result.missing_fields

    def test_percentage_confidence_is_accepted(self, profile) -> None:
        result = validate_response({**VALID_RESPONSE, "classification_confidence": 97}, profile)
        assert result.classification_confidence == pytest.approx(0.97)

    def test_out_of_range_confidence_is_clamped(self, profile) -> None:
        result = validate_response(
            {**VALID_RESPONSE, "classification_confidence": -5, "boundary_confidence": 900},
            profile,
        )
        assert 0.0 <= result.classification_confidence <= 1.0
        assert 0.0 <= result.boundary_confidence <= 1.0

    def test_string_booleans_are_accepted(self, profile) -> None:
        result = validate_response({**VALID_RESPONSE, "starts_new_document": "yes"}, profile)
        assert result.starts_new_document is True

    def test_camel_case_keys_are_accepted(self, profile) -> None:
        result = validate_response(
            {"documentType": "Resume", "classificationConfidence": 0.9,
             "startsNewDocument": True, "boundaryConfidence": 0.9},
            profile,
        )
        assert result.document_type == RESUME
        assert result.starts_new_document is True

    def test_null_metadata_becomes_none(self, profile) -> None:
        result = validate_response(
            {**VALID_RESPONSE, "candidate_name": "null", "email": "unknown"}, profile
        )
        assert result.candidate.name is None
        assert result.candidate.email is None

    def test_json_array_response_uses_first_object(self, profile) -> None:
        assert validate_response(json.dumps([VALID_RESPONSE]), profile).document_type == RESUME

    def test_nested_braces_in_strings_do_not_break_parsing(self) -> None:
        raw = 'prefix {"reasoning_summary": "contains } a brace", "document_type": "Resume"} tail'
        assert extract_json_object(raw)["document_type"] == "Resume"

    def test_non_object_json_raises(self, profile) -> None:
        with pytest.raises(ProviderResponseError):
            validate_response("42", profile)


# ----------------------------------------------------------------------
class TestOpenAIProvider:
    def test_missing_api_key_is_reported(self, profile) -> None:
        provider = OpenAIProvider(profile, "")
        assert not provider.is_available().available
        assert "key" in provider.is_available().message.lower()

    def test_availability_warns_that_text_is_sent_externally(self, profile) -> None:
        provider = OpenAIProvider(profile, "sk-test")
        assert provider.is_available().available
        assert "sent to openai" in provider.is_available().message.lower()
        assert provider.sends_data_externally is True

    def test_successful_call(self, profile) -> None:
        session = FakeSession(FakeResponse(200, openai_payload(VALID_RESPONSE)))
        provider = OpenAIProvider(profile, "sk-test", session=session)
        insight = provider.analyze_page(make_context(profile=profile))

        assert insight.used_ai
        assert insight.error is None
        assert insight.classification.document_type == RESUME
        assert insight.boundary.starts_new_document is False

    def test_only_bounded_context_is_sent(self, profile) -> None:
        session = FakeSession(FakeResponse(200, openai_payload(VALID_RESPONSE)))
        provider = OpenAIProvider(profile, "sk-test", session=session)
        provider.analyze_page(make_context("x" * 50_000, profile=profile))

        sent = json.dumps(session.calls[0]["json"])
        assert len(sent) < 20_000, "the whole page must not be sent verbatim"

    def test_invalid_key_is_not_retried(self, profile) -> None:
        session = FakeSession(FakeResponse(401))
        provider = OpenAIProvider(profile, "sk-bad", session=session)
        insight = provider.analyze_page(make_context(profile=profile))

        assert insight.error is not None
        assert not insight.used_ai
        assert len(session.calls) == 1

    def test_rate_limit_is_retried_once(self, profile) -> None:
        session = FakeSession(
            FakeResponse(429), FakeResponse(200, openai_payload(VALID_RESPONSE))
        )
        provider = OpenAIProvider(profile, "sk-test", session=session)
        insight = provider.analyze_page(make_context(profile=profile))

        assert insight.used_ai
        assert len(session.calls) == 2

    def test_retries_are_bounded(self, profile) -> None:
        session = FakeSession(FakeResponse(500), FakeResponse(500), FakeResponse(500))
        provider = OpenAIProvider(profile, "sk-test", session=session)
        insight = provider.analyze_page(make_context(profile=profile))

        assert insight.error is not None
        assert len(session.calls) == 2, "must not retry indefinitely"

    def test_timeout_is_handled(self, profile) -> None:
        session = FakeSession(requests.Timeout(), requests.Timeout())
        provider = OpenAIProvider(profile, "sk-test", session=session)
        insight = provider.analyze_page(make_context(profile=profile))
        assert insight.error is not None
        assert "did not respond" in insight.error.lower()

    def test_connection_failure_is_handled(self, profile) -> None:
        session = FakeSession(requests.ConnectionError(), requests.ConnectionError())
        provider = OpenAIProvider(profile, "sk-test", session=session)
        insight = provider.analyze_page(make_context(profile=profile))
        assert insight.error is not None
        assert not insight.used_ai

    def test_malformed_json_body_is_handled(self, profile) -> None:
        session = FakeSession(FakeResponse(200, openai_payload("not json")))
        provider = OpenAIProvider(profile, "sk-test", session=session)
        insight = provider.analyze_page(make_context(profile=profile))
        assert insight.error is not None
        assert not insight.used_ai

    def test_missing_model_is_reported(self, profile) -> None:
        session = FakeSession(FakeResponse(404))
        provider = OpenAIProvider(profile, "sk-test", model="nope", session=session)
        insight = provider.analyze_page(make_context(profile=profile))
        assert "not found" in (insight.error or "").lower()

    def test_api_key_never_appears_in_the_error(self, profile) -> None:
        session = FakeSession(FakeResponse(401))
        provider = OpenAIProvider(profile, "sk-supersecret-key", session=session)
        insight = provider.analyze_page(make_context(profile=profile))
        assert "sk-supersecret-key" not in (insight.error or "")


# ----------------------------------------------------------------------
class TestOllamaProvider:
    def test_is_local_only(self, profile) -> None:
        assert OllamaProvider(profile).sends_data_externally is False
        assert OllamaProvider(profile).kind is ProviderKind.OLLAMA

    def test_server_unavailable(self, profile) -> None:
        session = FakeSession(requests.ConnectionError())
        provider = OllamaProvider(profile, session=session)
        availability = provider.is_available()
        assert not availability.available
        assert "not reachable" in availability.message.lower()

    def test_model_missing_is_reported_with_a_fix(self, profile) -> None:
        session = FakeSession(
            FakeResponse(200, {"models": [{"name": "mistral:latest"}]})
        )
        provider = OllamaProvider(profile, model="llama3.1", session=session)
        availability = provider.is_available()
        assert not availability.available
        assert "ollama pull llama3.1" in availability.message

    def test_available_when_model_installed(self, profile) -> None:
        session = FakeSession(FakeResponse(200, {"models": [{"name": "llama3.1:8b"}]}))
        provider = OllamaProvider(profile, model="llama3.1", session=session)
        assert provider.is_available().available

    def test_successful_call(self, profile) -> None:
        session = FakeSession(
            FakeResponse(200, {"message": {"content": json.dumps(VALID_RESPONSE)}})
        )
        provider = OllamaProvider(profile, session=session)
        insight = provider.analyze_page(make_context(profile=profile))
        assert insight.used_ai
        assert insight.classification.document_type == RESUME

    def test_malformed_output_is_handled(self, profile) -> None:
        session = FakeSession(FakeResponse(200, {"message": {"content": "gibberish"}}))
        provider = OllamaProvider(profile, session=session)
        insight = provider.analyze_page(make_context(profile=profile))
        assert insight.error is not None
        assert not insight.used_ai

    def test_empty_output_is_handled(self, profile) -> None:
        session = FakeSession(FakeResponse(200, {"message": {"content": "   "}}))
        provider = OllamaProvider(profile, session=session)
        assert provider.analyze_page(make_context(profile=profile)).error is not None


# ----------------------------------------------------------------------
class StubProvider:
    """Minimal AI provider stub for escalation tests."""

    kind = ProviderKind.OLLAMA
    name = "Stub"
    sends_data_externally = False

    def __init__(self, insight=None, error=None) -> None:
        self.calls = 0
        self._insight = insight
        self._error = error

    def is_available(self):
        from app.intelligence.base import ProviderAvailability

        return ProviderAvailability(True, "stub")

    def classify_page(self, context):
        return self.analyze_page(context).classification

    def analyze_boundary(self, context):
        return self.analyze_page(context).boundary

    def analyze_page(self, context):
        from app.intelligence.base import (
            BoundaryAssessment,
            PageClassification,
            PageInsight,
        )

        self.calls += 1
        if self._error:
            raise RuntimeError(self._error)
        return self._insight or PageInsight(
            classification=PageClassification(RESUME, 0.95),
            boundary=BoundaryAssessment(False, 0.95),
            used_ai=True,
            requests=1,
        )

    def close(self):
        pass


class TestEscalationPolicy:
    def _context(self, profile, text: str) -> PageContext:
        return PageContext(
            source_pdf="t.pdf",
            page_index=0,
            page_count=1,
            text=text,
            features=extract_features(text),
            document_types=tuple(profile.document_types),
        )

    def test_confident_pages_are_not_escalated(self, profile) -> None:
        stub = StubProvider()
        service = ClassificationService(
            profile, RulesProvider(profile), stub, escalation_threshold=0.5
        )
        text = "\n".join(sample_data.resume_pages(total=1)[0].lines)
        service.analyze_page(self._context(profile, text))

        assert stub.calls == 0
        assert service.stats.pages_local == 1
        assert service.stats.pages_ai == 0

    def test_ambiguous_pages_are_escalated(self, profile) -> None:
        stub = StubProvider()
        service = ClassificationService(
            profile, RulesProvider(profile), stub, escalation_threshold=0.99
        )
        text = "\n".join(sample_data.ambiguous_page().lines)
        service.analyze_page(self._context(profile, text))

        assert stub.calls == 1
        assert service.stats.pages_ai == 1

    def test_rules_only_never_escalates(self, profile) -> None:
        service = ClassificationService(profile, RulesProvider(profile), None)
        text = "\n".join(sample_data.ambiguous_page().lines)
        service.analyze_page(self._context(profile, text))
        assert service.stats.pages_ai == 0

    def test_identical_pages_are_answered_from_cache(self, profile) -> None:
        stub = StubProvider()
        service = ClassificationService(
            profile, RulesProvider(profile), stub, escalation_threshold=0.99
        )
        text = "\n".join(sample_data.ambiguous_page().lines)
        for _ in range(4):
            service.analyze_page(self._context(profile, text))

        assert stub.calls == 1, "identical pages must not be re-sent"
        assert service.stats.cache_hits == 3

    def test_provider_exception_falls_back_to_rules(self, profile) -> None:
        stub = StubProvider(error="boom")
        service = ClassificationService(
            profile, RulesProvider(profile), stub, escalation_threshold=0.99
        )
        text = "\n".join(sample_data.resume_pages(total=1)[0].lines)
        insight = service.analyze_page(self._context(profile, text))

        assert insight.classification.document_type == RESUME
        assert service.stats.ai_failures == 1
        assert service.stats.last_error is not None

    def test_disagreement_lowers_confidence_into_review(self, profile, thresholds) -> None:
        from app.intelligence.base import (
            BoundaryAssessment,
            PageClassification,
            PageInsight,
        )

        stub = StubProvider(
            insight=PageInsight(
                classification=PageClassification(COVER_LETTER, 0.9),
                boundary=BoundaryAssessment(True, 0.9),
                used_ai=True,
                requests=1,
            )
        )
        service = ClassificationService(
            profile, RulesProvider(profile), stub, escalation_threshold=0.99
        )
        text = "\n".join(sample_data.resume_pages(total=1)[0].lines)
        insight = service.analyze_page(self._context(profile, text))

        assert thresholds.requires_review(insight.classification.confidence)
