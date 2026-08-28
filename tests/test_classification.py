"""Rules classifier behaviour: the document types and confidence calibration."""

from __future__ import annotations

import pytest

from app.intelligence.base import PageContext
from app.intelligence.rules_provider import RulesProvider
from app.profiles.base import OTHER
from app.profiles.recruiting import (
    APPLICATION_REPORT,
    COVER_LETTER,
    REFERENCES,
    RESUME,
    TRANSCRIPT,
)
from app.services.confidence import (
    ConfidenceThresholds,
    calibrate_classification,
    combine_confidence,
)
from scripts import sample_data


def classify(rules: RulesProvider, lines: list[str]):
    text = "\n".join(lines)
    return rules.classify_page(
        PageContext(source_pdf="test.pdf", page_index=0, page_count=1, text=text)
    )


class TestObviousDocuments:
    def test_obvious_resume(self, rules: RulesProvider) -> None:
        result = classify(rules, sample_data.resume_pages(total=3)[0].lines)
        assert result.document_type == RESUME
        assert result.confidence >= 0.85

    def test_obvious_cover_letter(self, rules: RulesProvider) -> None:
        result = classify(rules, sample_data.cover_letter_pages(total=1)[0].lines)
        assert result.document_type == COVER_LETTER
        assert result.confidence >= 0.85

    def test_application_report(self, rules: RulesProvider) -> None:
        result = classify(rules, sample_data.application_report_pages(total=4)[0].lines)
        assert result.document_type == APPLICATION_REPORT
        assert result.confidence >= 0.85

    def test_references(self, rules: RulesProvider) -> None:
        result = classify(rules, sample_data.references_pages(total=2)[0].lines)
        assert result.document_type == REFERENCES
        assert result.confidence >= 0.80

    def test_transcript(self, rules: RulesProvider) -> None:
        result = classify(rules, sample_data.transcript_pages(total=2)[0].lines)
        assert result.document_type == TRANSCRIPT
        assert result.confidence >= 0.80

    def test_every_recruiting_type_is_reachable(self, rules: RulesProvider, profile) -> None:
        """The profile must not declare types the classifier can never produce."""
        assert set(profile.document_types) >= {
            APPLICATION_REPORT, RESUME, COVER_LETTER, REFERENCES, TRANSCRIPT, OTHER
        }


class TestUnclassifiablePages:
    def test_blank_page_is_other_with_low_confidence(self, rules: RulesProvider) -> None:
        result = classify(rules, [""])
        assert result.document_type == OTHER
        assert result.confidence < 0.5

    def test_page_with_no_signals_is_other(self, rules: RulesProvider) -> None:
        result = classify(
            rules,
            [
                "The quick brown fox jumped over the lazy dog and then kept going for a while.",
                "Nothing on this page indicates any particular kind of business document.",
                "It is simply prose that carries no recognisable structure at all.",
            ],
        )
        assert result.document_type == OTHER
        assert result.confidence < 0.7

    def test_ambiguous_page_requires_review(self, rules: RulesProvider) -> None:
        thresholds = ConfidenceThresholds()
        result = classify(rules, sample_data.ambiguous_page().lines)
        assert thresholds.requires_review(result.confidence), (
            f"ambiguous page scored {result.confidence} as {result.document_type}"
        )


class TestSeparatorPages:
    @pytest.mark.parametrize(
        "label, expected",
        [
            ("RESUME", RESUME),
            ("Cover Letter", COVER_LETTER),
            ("REFERENCES", REFERENCES),
            ("Transcript", TRANSCRIPT),
        ],
    )
    def test_label_only_page_is_recognised(
        self, rules: RulesProvider, label: str, expected: str
    ) -> None:
        result = classify(rules, sample_data.separator_page(label).lines)
        assert result.document_type == expected
        assert result.confidence >= 0.9

    def test_a_full_page_of_text_is_not_a_separator(self, rules: RulesProvider, profile) -> None:
        features_source = "\n".join(sample_data.resume_pages(total=1)[0].lines)
        from app.services.text_features import extract_features

        assert profile.separator_type_for(extract_features(features_source)) is None


class TestConfidenceCalibration:
    def test_strong_uncontested_evidence_scores_high(self) -> None:
        assert calibrate_classification(24.0, 1.0) >= 0.90

    def test_tied_scores_land_in_review(self) -> None:
        confidence = calibrate_classification(6.0, 5.5)
        assert confidence < 0.70

    def test_weak_evidence_is_never_high_confidence(self) -> None:
        assert calibrate_classification(1.5, 0.0) < 0.85

    def test_no_evidence_is_floor(self) -> None:
        assert calibrate_classification(0.0, 0.0) == pytest.approx(0.30)

    def test_confidence_is_bounded(self) -> None:
        assert 0.0 <= calibrate_classification(500.0, 0.0) <= 1.0

    def test_more_separation_means_more_confidence(self) -> None:
        assert calibrate_classification(20.0, 1.0) > calibrate_classification(20.0, 15.0)


class TestConfidenceCombination:
    def test_agreement_reinforces(self) -> None:
        assert combine_confidence(0.6, 0.8, agree=True) > 0.8

    def test_agreement_never_reaches_certainty(self) -> None:
        assert combine_confidence(0.99, 0.99, agree=True) < 1.0

    def test_disagreement_lowers_confidence_into_review(self) -> None:
        combined = combine_confidence(0.6, 0.9, agree=False)
        assert combined <= 0.75


class TestThresholds:
    def test_bands(self) -> None:
        thresholds = ConfidenceThresholds(high=0.9, review=0.7)
        assert thresholds.band(0.95).value == "high"
        assert thresholds.band(0.80).value == "review_suggested"
        assert thresholds.band(0.40).value == "review_required"

    def test_review_required_below_high(self) -> None:
        thresholds = ConfidenceThresholds(high=0.9, review=0.7)
        assert thresholds.requires_review(0.89)
        assert not thresholds.requires_review(0.91)

    def test_thresholds_are_configurable(self) -> None:
        relaxed = ConfidenceThresholds(high=0.6, review=0.4)
        assert not relaxed.requires_review(0.65)

    def test_invalid_thresholds_are_corrected(self) -> None:
        """A review bar above the high bar would make banding nonsense."""
        thresholds = ConfidenceThresholds(high=0.5, review=0.9)
        assert thresholds.review < thresholds.high


class TestSystemGeneratedReports:
    """Real ATS exports repeat a machine-written header on every report page.

    Without recognising it, page 2 of an application report -- which lists
    employment history -- scored higher as a Resume than as the report it is
    part of, and every real report landed in the review queue while being
    correctly typed.
    """

    ATS_HEADER = [
        "Confidential Report",
        "Job Opening ID: 87588",
        "Job Posting Title: Assistant Dean of Students",
        "07/23/2026 - 08:56:23 AM",
        "Application Details for Nora Castellanos",
    ]

    def test_a_report_page_full_of_job_history_is_still_the_report(self, rules) -> None:
        page = classify(rules, self.ATS_HEADER + [
            "Ending Job Title",
            "Director, Career Services",
            "Supervisor",
            "Bing Nguyen",
            "Employer",
            "Cabrillo College",
            "Start Date  2019   End Date  2024",
        ])
        assert page.document_type == "Application Report"

    def test_that_page_is_classified_confidently(self, rules) -> None:
        """Being right but unsure still costs the user a review click."""
        page = classify(rules, self.ATS_HEADER + [
            "Ending Job Title", "Director, Career Services",
            "Supervisor", "Bing Nguyen", "Employer", "Cabrillo College",
        ])
        assert page.confidence >= 0.85

    def test_one_marker_alone_is_not_enough(self, rules) -> None:
        """A resume mentioning a requisition number must not become a report."""
        page = classify(rules, [
            "NORA C. CASTELLANOS",
            "(555) 410-8434 | nora@example.com",
            "Applying under Req ID R-87588",
            "",
            "PROFESSIONAL EXPERIENCE",
            "Director, Career Services",
            "Cabrillo College, 2019 - 2024",
            "- Led the career services team through a full redesign.",
            "- Built employer partnerships across the region.",
            "EDUCATION",
            "M.A. Higher Education",
        ])
        assert page.document_type == "Resume"

    def test_an_attachment_without_the_header_is_read_on_its_own_terms(self, rules) -> None:
        """The header appears only on generated pages, never on attachments."""
        page = classify(rules, [
            "NORA C. CASTELLANOS",
            "(555) 410-8434 ~ nora@example.com",
            "",
            "PROFESSIONAL SUMMARY",
            "Student affairs leader with fifteen years of experience.",
            "PROFESSIONAL EXPERIENCE",
            "Director, Career Services - Cabrillo College - 2019 to 2024",
            "- Redesigned the employer partnership programme.",
            "EDUCATION",
            "M.A. Higher Education, San Jose State University",
        ])
        assert page.document_type == "Resume"
