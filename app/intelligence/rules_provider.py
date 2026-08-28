"""Rules-only document intelligence.

This is the default provider and the reason the application is fully functional
with no internet connection, no API key and no local model server. Everything
it does is deterministic, offline, and unit-testable.
"""

from __future__ import annotations

from app.intelligence.base import (
    BoundaryAssessment,
    DocumentIntelligenceProvider,
    PageClassification,
    PageContext,
    PageInsight,
    ProviderAvailability,
)
from app.models.enums import ProviderKind
from app.profiles.base import OTHER, DocumentProfile
from app.services.boundary_engine import BoundaryEngine
from app.services.confidence import calibrate_classification
from app.services.text_features import PageFeatures, extract_features

#: Below this many words a page carries too little evidence to classify.
_MIN_WORDS_FOR_CLASSIFICATION = 6
#: Confidence assigned to a recognised separator/title page.
_SEPARATOR_CONFIDENCE = 0.92


class RulesProvider(DocumentIntelligenceProvider):
    """Deterministic classifier and boundary detector built on the active profile."""

    kind = ProviderKind.RULES
    name = "Rules Only"
    sends_data_externally = False

    def __init__(self, profile: DocumentProfile) -> None:
        self.profile = profile
        self.boundary_engine = BoundaryEngine(profile)

    # ------------------------------------------------------------------
    def is_available(self) -> ProviderAvailability:
        return ProviderAvailability(True, "Local rules engine — nothing leaves this computer.")

    # ------------------------------------------------------------------
    def classify_page(self, context: PageContext) -> PageClassification:
        features = context.features or extract_features(context.text)

        separator_type = self.profile.separator_type_for(features)
        if separator_type:
            return PageClassification(
                document_type=separator_type,
                confidence=_SEPARATOR_CONFIDENCE,
                scores={separator_type: 0.0},
                reasoning=f"Separator page announcing {separator_type}.",
                explanation=[("Separator/title page", 1.0)],
            )

        if features.word_count < _MIN_WORDS_FOR_CLASSIFICATION:
            return PageClassification(
                document_type=OTHER,
                confidence=0.32,
                scores={},
                reasoning="Page contains too little readable text to classify.",
            )

        scores = self.profile.score_page(features)
        ranked = sorted(
            ((name, value) for name, value in scores.items() if name != OTHER),
            key=lambda item: item[1],
            reverse=True,
        )

        if not ranked or ranked[0][1] <= 0:
            return PageClassification(
                document_type=OTHER,
                confidence=0.35,
                scores=scores,
                reasoning="No document type accumulated meaningful evidence on this page.",
            )

        best_type, best_score = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
        confidence = calibrate_classification(best_score, runner_up)

        return PageClassification(
            document_type=best_type,
            confidence=confidence,
            scores=scores,
            reasoning=_describe(best_type, best_score, ranked[1] if len(ranked) > 1 else None),
            explanation=self.profile.explain_page(features, best_type),
        )

    # ------------------------------------------------------------------
    def analyze_boundary(self, context: PageContext) -> BoundaryAssessment:
        classification = self.classify_page(context)
        return self._boundary_for(context, classification)

    def analyze_page(self, context: PageContext) -> PageInsight:
        classification = self.classify_page(context)
        boundary = self._boundary_for(context, classification)
        return PageInsight(
            classification=classification,
            boundary=boundary,
            reasoning=classification.reasoning,
            used_ai=False,
        )

    # ------------------------------------------------------------------
    def separator_type_for(self, features: PageFeatures) -> str | None:
        """Expose separator detection so the pipeline can record separator state."""
        return self.profile.separator_type_for(features)

    def _boundary_for(
        self, context: PageContext, classification: PageClassification
    ) -> BoundaryAssessment:
        features = context.features or extract_features(context.text)
        separator_type = self.profile.separator_type_for(features)
        return self.boundary_engine.assess(
            context, classification, separator_type=separator_type
        )


def _describe(best_type: str, best_score: float, runner_up: tuple[str, float] | None) -> str:
    """Short, human-readable rationale shown in the review inspector."""
    text = f"Rules matched {best_type} with a score of {best_score:.1f}."
    if runner_up and runner_up[1] > 0:
        text += f" Closest alternative was {runner_up[0]} at {runner_up[1]:.1f}."
    return text


__all__ = ["RulesProvider"]
