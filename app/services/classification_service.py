"""Classification orchestration.

Implements the escalation policy from the specification:

1. Run the deterministic rules first -- always, for every page.
2. Compute local confidence.
3. Escalate only *ambiguous* pages to the configured AI provider.
4. Combine the two signals honestly: agreement reinforces, disagreement lowers
   confidence so the page surfaces in review instead of silently picking a side.

Identical page bodies are answered from cache, so a 300-PDF batch full of the
same boilerplate does not pay for the same AI call hundreds of times.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field

from app.intelligence.base import (
    BoundaryAssessment,
    DocumentIntelligenceProvider,
    PageClassification,
    PageContext,
    PageInsight,
)
from app.intelligence.rules_provider import RulesProvider
from app.profiles.base import OTHER, DocumentProfile
from app.services.confidence import combine_confidence
from app.utils.hashing import hash_text
from app.utils.logging_setup import get_logger

logger = get_logger("classification")

_CACHE_LIMIT = 2048


@dataclass
class ClassificationStats:
    """Counters reported at the end of a job (spec section 33)."""

    pages_total: int = 0
    pages_local: int = 0
    pages_ai: int = 0
    ai_requests: int = 0
    ai_failures: int = 0
    cache_hits: int = 0
    last_error: str | None = None
    errors: list[str] = field(default_factory=list)

    def record_error(self, message: str) -> None:
        self.last_error = message
        if message not in self.errors:
            self.errors.append(message)


class ClassificationService:
    """Runs rules, decides when to consult AI, and merges the results."""

    def __init__(
        self,
        profile: DocumentProfile,
        rules_provider: RulesProvider | None = None,
        ai_provider: DocumentIntelligenceProvider | None = None,
        *,
        escalation_threshold: float = 0.85,
        enable_cache: bool = True,
    ) -> None:
        self.profile = profile
        self.rules = rules_provider or RulesProvider(profile)
        self.ai_provider = ai_provider
        self.escalation_threshold = escalation_threshold
        self.stats = ClassificationStats()
        self._cache: OrderedDict[str, PageInsight] | None = (
            OrderedDict() if enable_cache else None
        )

    # ------------------------------------------------------------------
    @property
    def ai_enabled(self) -> bool:
        return self.ai_provider is not None

    def analyze_page(self, context: PageContext) -> PageInsight:
        """Produce the final classification and boundary decision for one page."""
        self.stats.pages_total += 1

        local = self.rules.analyze_page(context)

        if not self._should_escalate(local):
            self.stats.pages_local += 1
            return local

        cache_key = self._cache_key(context)
        cached = self._cache_get(cache_key)
        if cached is not None:
            self.stats.cache_hits += 1
            self.stats.pages_ai += 1
            return self._merge(local, cached)

        assert self.ai_provider is not None  # guarded by _should_escalate
        try:
            remote = self.ai_provider.analyze_page(context)
            self.stats.ai_requests += remote.requests or 1
        except Exception as exc:  # provider must never break a batch
            self.stats.ai_failures += 1
            self.stats.record_error(f"{self.ai_provider.name}: {exc}")
            logger.warning("AI provider failed on page %s: %s", context.page_number, exc)
            self.stats.pages_local += 1
            return local

        if remote.error:
            self.stats.ai_failures += 1
            self.stats.record_error(f"{self.ai_provider.name}: {remote.error}")
            self.stats.pages_local += 1
            return local

        self._cache_put(cache_key, remote)
        self.stats.pages_ai += 1
        return self._merge(local, remote)

    # ------------------------------------------------------------------
    def _should_escalate(self, local: PageInsight) -> bool:
        """Escalate only genuinely uncertain pages."""
        if not self.ai_enabled:
            return False
        if local.classification.confidence < self.escalation_threshold:
            return True
        if local.boundary.confidence < self.escalation_threshold:
            return True
        return local.classification.document_type == OTHER

    def _cache_key(self, context: PageContext) -> str:
        """Cache on page body plus the small amount of context the AI sees."""
        signature = "|".join(
            (
                context.text[:4000],
                context.previous_type or "",
                context.previous_text_tail[:200],
                context.next_text_head[:200],
            )
        )
        return hash_text(signature)

    def _cache_get(self, key: str) -> PageInsight | None:
        if self._cache is None:
            return None
        insight = self._cache.get(key)
        if insight is not None:
            self._cache.move_to_end(key)
        return insight

    def _cache_put(self, key: str, insight: PageInsight) -> None:
        if self._cache is None:
            return
        self._cache[key] = insight
        self._cache.move_to_end(key)
        while len(self._cache) > _CACHE_LIMIT:
            self._cache.popitem(last=False)

    # ------------------------------------------------------------------
    def _merge(self, local: PageInsight, remote: PageInsight) -> PageInsight:
        """Combine rules and AI results into one decision."""
        remote_type = self.profile.normalize_type(remote.classification.document_type)
        types_agree = remote_type == local.classification.document_type

        # We only asked the AI because the rules were unsure, so its answer wins
        # the tie -- but a disagreement lowers confidence rather than hiding it.
        chosen_type = remote_type if remote_type != OTHER else local.classification.document_type
        classification_confidence = combine_confidence(
            local.classification.confidence, remote.classification.confidence, types_agree
        )

        boundaries_agree = (
            remote.boundary.starts_new_document == local.boundary.starts_new_document
        )
        starts_new = remote.boundary.starts_new_document
        boundary_confidence = combine_confidence(
            local.boundary.confidence, remote.boundary.confidence, boundaries_agree
        )

        reasons = list(local.boundary.reasons)
        for reason in remote.boundary.reasons:
            if reason not in reasons:
                reasons.append(reason)
        if not boundaries_agree:
            reasons.insert(0, "Rules and AI disagreed about this boundary")

        merged_candidate = remote.candidate.merged_with(local.candidate)

        return PageInsight(
            classification=PageClassification(
                document_type=chosen_type,
                confidence=classification_confidence,
                scores=local.classification.scores,
                reasoning=remote.classification.reasoning or local.classification.reasoning,
                explanation=local.classification.explanation,
                raw=remote.classification.raw,
            ),
            boundary=BoundaryAssessment(
                starts_new_document=starts_new,
                confidence=boundary_confidence,
                reasons=reasons[:8],
                score=local.boundary.score,
                raw=remote.boundary.raw,
            ),
            candidate=merged_candidate,
            reasoning=remote.reasoning or local.reasoning,
            used_ai=True,
            requests=remote.requests,
        )


__all__ = ["ClassificationService", "ClassificationStats"]
