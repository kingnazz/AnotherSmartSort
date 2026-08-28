"""Candidate metadata extraction."""

from __future__ import annotations

import pytest

from app.models.candidate import Candidate, normalize_person_name
from app.services.metadata_service import (
    MetadataExtractor,
    looks_like_person_name,
    merge_candidates,
)
from app.services.text_features import extract_features
from scripts import sample_data


def extract(metadata: MetadataExtractor, lines: list[str], document_type: str | None = None):
    return metadata.extract(extract_features("\n".join(lines)), document_type=document_type)


class TestFieldExtraction:
    def test_application_report_fields(self, metadata: MetadataExtractor) -> None:
        page = sample_data.application_report_pages(total=1)[0]
        candidate = extract(metadata, page.lines, "Application Report")

        assert candidate.name == "Benjamin Perez"
        assert candidate.email == "benjamin.perez@example.com"
        assert candidate.phone == "(555) 214-8890"
        assert candidate.applicant_id == "A-10482"
        assert candidate.job_title == "Senior Operations Analyst"

    def test_resume_header_fields(self, metadata: MetadataExtractor) -> None:
        page = sample_data.resume_pages(total=1)[0]
        candidate = extract(metadata, page.lines, "Resume")

        assert candidate.name == "Benjamin Perez"
        assert candidate.email == "benjamin.perez@example.com"
        assert candidate.linkedin == "linkedin.com/in/benjaminperez"

    def test_cover_letter_signature_name(self, metadata: MetadataExtractor) -> None:
        page = sample_data.cover_letter_pages(total=1)[0]
        candidate = extract(metadata, page.lines, "Cover Letter")
        assert candidate.name == "Benjamin Perez"

    def test_job_title_from_letter_prose(self, metadata: MetadataExtractor) -> None:
        candidate = extract(
            metadata,
            [
                "Dear Hiring Manager,",
                "I am writing to apply for the Senior Operations Analyst position at Rivermark.",
                "Sincerely,",
                "Ann Lee",
            ],
            "Cover Letter",
        )
        assert candidate.job_title == "Senior Operations Analyst"

    def test_missing_metadata_yields_an_empty_candidate(
        self, metadata: MetadataExtractor
    ) -> None:
        candidate = extract(metadata, ["Nothing here but words.", "And more words."])
        assert candidate.is_empty

    def test_placeholder_values_are_ignored(self, metadata: MetadataExtractor) -> None:
        candidate = extract(metadata, ["Name: N/A", "Email: none", "Phone: --"])
        assert candidate.name is None
        assert candidate.email is None

    def test_running_footer_is_not_glued_onto_a_field(
        self, metadata: MetadataExtractor
    ) -> None:
        """PDF extraction can collapse column spacing into one line."""
        candidate = extract(metadata, ["Applicant ID: A-10482 Page 2 of 4"])
        assert candidate.applicant_id == "A-10482"

    def test_last_first_name_order(self, metadata: MetadataExtractor) -> None:
        candidate = extract(metadata, ["Name: Perez, Benjamin"])
        assert candidate.name == "Benjamin Perez"

    def test_split_first_and_last_name_fields(self, metadata: MetadataExtractor) -> None:
        candidate = extract(metadata, ["First Name: Benjamin", "Last Name: Perez"])
        assert candidate.name == "Benjamin Perez"


class TestThirdPartyContacts:
    def test_reference_contacts_are_not_taken_as_the_candidate(
        self, metadata: MetadataExtractor
    ) -> None:
        page = sample_data.references_pages(total=1)[0]
        candidate = extract(metadata, page.lines, "References")

        assert candidate.name == "Benjamin Perez"
        assert candidate.email is None, "a referee's email must not become the applicant's"
        assert candidate.phone is None

    def test_the_same_page_read_as_a_resume_would_take_the_contacts(
        self, metadata: MetadataExtractor
    ) -> None:
        page = sample_data.references_pages(total=1)[0]
        candidate = extract(metadata, page.lines, "Resume")
        assert candidate.email is not None


class TestNameHeuristics:
    @pytest.mark.parametrize(
        "text", ["Benjamin Perez", "PEREZ, BENJAMIN", "Ann Marie Lee", "J. R. Whitfield"]
    )
    def test_accepts_real_names(self, text: str) -> None:
        assert looks_like_person_name(text)

    @pytest.mark.parametrize(
        "text",
        [
            "PROFESSIONAL EXPERIENCE",
            "Date Stage Disposition",
            "Senior Operations Analyst",
            "Application Report",
            "benjamin@example.com",
            "Page 2 of 4",
            "A",
            "",
            "The quick brown fox jumped over the lazy dog and kept running",
        ],
    )
    def test_rejects_non_names(self, text: str) -> None:
        assert not looks_like_person_name(text)

    def test_table_headers_are_not_read_as_names(self, metadata: MetadataExtractor) -> None:
        candidate = extract(
            metadata,
            [
                "CANDIDATE APPLICATION REPORT",
                "APPLICATION HISTORY",
                "Date Stage Disposition",
                "March 12, 2024 Application Received Advanced",
            ],
            "Application Report",
        )
        assert candidate.name is None


class TestNameNormalization:
    def test_comma_order_is_normalised(self) -> None:
        assert normalize_person_name("Perez, Benjamin") == normalize_person_name("Benjamin Perez")

    def test_case_and_punctuation_are_ignored(self) -> None:
        assert normalize_person_name("BENJAMIN PEREZ") == normalize_person_name("Benjamin Perez")


class TestMergeCandidates:
    def test_fields_combine_across_pages(self) -> None:
        merged = merge_candidates(
            [
                Candidate(name="Benjamin Perez", email="b@example.com"),
                Candidate(phone="(555) 214-8890", applicant_id="A-1"),
            ]
        )
        assert merged.name == "Benjamin Perez"
        assert merged.email == "b@example.com"
        assert merged.phone == "(555) 214-8890"
        assert merged.applicant_id == "A-1"

    def test_most_common_name_wins(self) -> None:
        merged = merge_candidates(
            [
                Candidate(name="Benjamin Perez"),
                Candidate(name="Benjamin Perez"),
                Candidate(name="Jane Smith"),
            ]
        )
        assert merged.name == "Benjamin Perez"

    def test_conflicting_names_are_recorded_not_discarded(self) -> None:
        merged = merge_candidates([Candidate(name="Benjamin Perez"), Candidate(name="Jane Smith")])
        assert merged.has_conflict
        assert "Jane Smith" in merged.conflicting_names

    def test_empty_input(self) -> None:
        assert merge_candidates([]).is_empty


class TestCandidateModel:
    def test_display_name_falls_back_to_email(self) -> None:
        assert Candidate(email="ann.lee@example.com").display_name == "Ann Lee"

    def test_display_name_falls_back_to_unknown(self) -> None:
        assert Candidate().display_name == "Unknown"

    def test_identity_key_prefers_email(self) -> None:
        candidate = Candidate(name="Ann Lee", email="ann@example.com")
        assert candidate.identity_key == "email:ann@example.com"

    def test_round_trip_serialisation(self) -> None:
        candidate = Candidate(name="Ann Lee", email="ann@example.com", applicant_id="A-9")
        assert Candidate.from_dict(candidate.to_dict()) == candidate


class TestRealAtsLayouts:
    """Cases found by running the first real client documents through the pipeline.

    Everything here failed on genuine applicant tracking exports while passing
    on synthetic samples, which is exactly the gap synthetic data cannot close.
    """

    def test_labels_above_values_are_read(self, metadata, features_of) -> None:
        """The layout real systems use: label on one line, value on the next.

        Synthetic samples all wrote ``Name: X`` on a single line. The client's
        ATS writes the label and the value on separate lines, so nothing was
        extracted at all and the candidate name fell back to the email's local
        part -- producing folders named things like "Sofiabbrennan".
        """
        candidate = metadata.extract(features_of([
            "General Information",
            "Name",
            "Sofia Brennan",
            "Applicant ID",
            "1338895",
            "Applicant Type",
            "External Applicant",
            "Email",
            "sofia.brennan@example.com",
        ]))
        assert candidate.name == "Sofia Brennan"
        assert candidate.applicant_id == "1338895"
        assert candidate.email == "sofia.brennan@example.com"

    def test_an_empty_field_does_not_swallow_the_next_label(
        self, metadata, features_of
    ) -> None:
        """A blank Middle Name must not read "Last Name" as its value."""
        candidate = metadata.extract(features_of([
            "First Name",
            "Sofia",
            "Middle Name",
            "Last Name",
            "Brennan",
        ]))
        assert candidate.name == "Sofia Brennan"

    def test_the_report_states_its_own_subject(self, metadata, features_of) -> None:
        """"Application Details for X" is the system naming the applicant."""
        candidate = metadata.extract(features_of([
            "Confidential Report",
            "Job Opening ID: 87588",
            "Application Details for Sofia Brennan",
            "Dear Hiring Manager",
        ]))
        assert candidate.name == "Sofia Brennan"

    @pytest.mark.parametrize(
        ("written", "expected"),
        [
            ("Amara Okonjo, PhD", "Amara Okonjo"),
            ("Sofia Brennan, M.D.", "Sofia Brennan"),
            ("Miriam Kessler MBA", "Miriam Kessler"),
        ],
    )
    def test_credentials_are_not_mistaken_for_names(
        self, metadata, features_of, written: str, expected: str
    ) -> None:
        """``Amara Okonjo, PhD`` hit the ``Last, First`` rule and became "PhD Amara Okonjo".

        Which then failed to match her ATS record, splitting one applicant into
        two packets.
        """
        candidate = metadata.extract(features_of([
            written,
            "San Diego, CA | someone@example.com",
            "EXECUTIVE SUMMARY",
            "Nine years of experience in higher education partnerships.",
        ]))
        assert candidate.name == expected

    def test_a_recommendation_letter_names_its_subject_not_its_author(
        self, metadata, features_of
    ) -> None:
        """A referee's letter was filing the packet under the referee.

        The signature belongs to whoever wrote it; the applicant is the person
        being recommended.
        """
        candidate = metadata.extract(features_of([
            "Dear Hiring Committee Chair:",
            "I am writing to offer my recommendation for Amara Okonjo as she applies",
            "for senior administrative roles in higher education.",
            "",
            "Sincerely,",
            "Andy Vaughn",
        ]))
        assert candidate.name == "Amara Okonjo", "the letter was filed under its author"

    def test_the_subject_capture_stops_at_the_name(self, metadata, features_of) -> None:
        """A case-insensitive capture ran on into the rest of the sentence."""
        candidate = metadata.extract(features_of([
            "I am writing to offer my recommendation for Amara Okonjo as she applies",
            "for a role in higher education.",
        ]))
        assert candidate.name == "Amara Okonjo"
