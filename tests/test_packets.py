"""Candidate packet reconstruction.

These cover the product's primary promise: one large mixed PDF in, correctly
reconstructed applicant packets out. The cases are the specification's Tests
B through I; the 80-page batch is in ``test_mixed_batch.py``.

Every assertion here is about *attribution* -- which documents belong to whom.
Type detection and boundary detection have their own suites and are only relied
on, never re-tested.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.candidate import Candidate
from app.models.document import DocumentGroup
from app.models.packet import CandidatePacket
from app.models.source_file import SourceFileAnalysis
from app.profiles.recruiting import (
    APPLICATION_REPORT,
    COVER_LETTER,
    REFERENCES,
    RESUME,
)
from app.services.packet_service import CandidatePacketService
from scripts.mixed_batch import (
    APPLICANTS,
    MixedBatchBuilder,
    _anonymous_cover_letter,
    _application_report,
    _cover_letter,
    _references,
    _resume,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def packets_by_name(file: SourceFileAnalysis) -> dict[str, CandidatePacket]:
    return {p.display_name: p for p in file.packets}


def document_types(packet: CandidatePacket) -> list[str]:
    return [d.document_type for d in packet.documents]


def analyze(pipeline, batch, tmp_path: Path) -> SourceFileAnalysis:
    """Render a mixed batch to a PDF and run the real pipeline over it."""
    source = batch.write(tmp_path)
    return pipeline.analyze_file(source)


def make_group(
    page_indexes: list[int],
    document_type: str,
    candidate: Candidate | None = None,
) -> DocumentGroup:
    """A finished document group, bypassing classification."""
    group = DocumentGroup(
        source_pdf="memory.pdf",
        page_indexes=page_indexes,
        document_type=document_type,
        candidate=candidate or Candidate(),
        classification_confidence=0.95,
        boundary_confidence=0.95,
    )
    return group


@pytest.fixture
def packet_service(profile, thresholds) -> CandidatePacketService:
    return CandidatePacketService(profile, thresholds)


# --------------------------------------------------------------------------
# Test B: a complete packet becomes one candidate
# --------------------------------------------------------------------------

class TestCompletePacket:
    def test_application_resume_and_letter_become_one_candidate(
        self, pipeline, tmp_path: Path
    ) -> None:
        applicant = APPLICANTS[0]
        builder = MixedBatchBuilder("complete.pdf")
        builder.start_candidate(applicant.name)
        builder.add_document(APPLICATION_REPORT, _application_report(applicant, 3))
        builder.add_document(RESUME, _resume(applicant, 2))
        builder.add_document(COVER_LETTER, _cover_letter(applicant))

        analysis = analyze(pipeline, builder.build(), tmp_path)

        assert analysis.candidate_count == 1
        packet = analysis.identified_packets[0]
        assert packet.candidate.name == applicant.name
        assert sorted(document_types(packet)) == sorted(
            [APPLICATION_REPORT, RESUME, COVER_LETTER]
        )
        assert packet.page_count == 6


# --------------------------------------------------------------------------
# Test C: a packet with no application report
# --------------------------------------------------------------------------

class TestPacketWithoutApplicationReport:
    def test_resume_and_cover_letter_alone_still_reconstruct(
        self, pipeline, tmp_path: Path
    ) -> None:
        applicant = APPLICANTS[2]
        builder = MixedBatchBuilder("no_report.pdf")
        builder.start_candidate(applicant.name)
        builder.add_document(RESUME, _resume(applicant, 2))
        builder.add_document(COVER_LETTER, _cover_letter(applicant))

        analysis = analyze(pipeline, builder.build(), tmp_path)

        assert analysis.candidate_count == 1
        packet = analysis.identified_packets[0]
        assert packet.candidate.name == applicant.name
        assert set(document_types(packet)) == {RESUME, COVER_LETTER}


# --------------------------------------------------------------------------
# Test D: back-to-back resumes separate on names alone
# --------------------------------------------------------------------------

class TestBackToBackResumes:
    def test_two_resumes_with_no_report_between_them_are_two_candidates(
        self, pipeline, tmp_path: Path
    ) -> None:
        first, second = APPLICANTS[0], APPLICANTS[1]
        builder = MixedBatchBuilder("two_resumes.pdf")
        builder.start_candidate(first.name)
        builder.add_document(RESUME, _resume(first, 2))
        builder.start_candidate(second.name)
        builder.add_document(RESUME, _resume(second, 2))

        analysis = analyze(pipeline, builder.build(), tmp_path)

        names = {p.candidate.name for p in analysis.identified_packets}
        assert names == {first.name, second.name}
        for packet in analysis.identified_packets:
            assert len(packet.documents) == 1


# --------------------------------------------------------------------------
# Test E: an anonymous document inherits the active candidate
# --------------------------------------------------------------------------

class TestContextualAssociation:
    @pytest.fixture
    def analysis(self, pipeline, tmp_path: Path) -> SourceFileAnalysis:
        jane, robert = APPLICANTS[0], APPLICANTS[1]
        builder = MixedBatchBuilder("contextual.pdf")
        jane_packet = builder.start_candidate(jane.name)
        builder.add_document(APPLICATION_REPORT, _application_report(jane, 3))
        builder.add_document(RESUME, _resume(jane, 2))
        builder.add_document(COVER_LETTER, _anonymous_cover_letter(), candidate=jane_packet)
        builder.start_candidate(robert.name)
        builder.add_document(APPLICATION_REPORT, _application_report(robert, 3))
        return analyze(pipeline, builder.build(), tmp_path)

    def test_the_anonymous_letter_goes_to_the_active_candidate(
        self, analysis: SourceFileAnalysis
    ) -> None:
        """Page 6 belongs to Jane: she is active and Robert has not started."""
        jane = packets_by_name(analysis)[APPLICANTS[0].name]
        assert 5 in jane.page_indexes, "the anonymous cover letter was not attributed to Jane"

    def test_that_inference_is_not_presented_as_certain(
        self, analysis: SourceFileAnalysis
    ) -> None:
        """Inferring from position is a guess and has to look like one."""
        letter = next(
            d for d in analysis.groups if d.start_page_index == 5
        )
        assert letter.association_confidence < 0.90
        assert letter.association_confidence >= 0.50
        assert letter.association_reasons

    def test_it_lands_in_review_suggested_rather_than_review_required(
        self, analysis: SourceFileAnalysis, thresholds
    ) -> None:
        """Inferred, but not so doubtful that a human must resolve it.

        An anonymous cover letter is the ordinary case this inference exists to
        handle -- plenty are scanned or sign off with an image. Treating it as
        highly suspect would put routine documents in the required-review queue
        and make the review filter useless.
        """
        letter = next(d for d in analysis.groups if d.start_page_index == 5)
        assert thresholds.band(letter.association_confidence).name == "REVIEW_SUGGESTED"

    def test_an_anonymous_resume_is_doubted_more_than_a_cover_letter(
        self, packet_service: CandidatePacketService
    ) -> None:
        """A resume with no name anywhere is genuinely odd; a letter is not."""
        def attribute(document_type: str) -> float:
            groups = [
                make_group([0], APPLICATION_REPORT, Candidate(name="Jane Smith")),
                make_group([1], document_type, Candidate()),
            ]
            packet_service.build_packets(groups, "memory.pdf")
            return groups[1].association_confidence

        assert attribute(RESUME) < attribute(COVER_LETTER)

    def test_robert_still_gets_his_own_packet(self, analysis: SourceFileAnalysis) -> None:
        names = {p.candidate.name for p in analysis.identified_packets}
        assert APPLICANTS[1].name in names


# --------------------------------------------------------------------------
# The counter-example: explicit identity beats proximity
# --------------------------------------------------------------------------

class TestExplicitIdentityWins:
    def test_a_named_resume_is_not_swallowed_by_the_active_candidate(
        self, pipeline, tmp_path: Path
    ) -> None:
        """Sarah's resume follows Jane's documents with no report in between.

        Nothing about position suggests a new person, so only the name on the
        page can save Sarah's documents from being filed under Jane.
        """
        jane, sarah = APPLICANTS[0], APPLICANTS[2]
        builder = MixedBatchBuilder("explicit.pdf")
        builder.start_candidate(jane.name)
        builder.add_document(APPLICATION_REPORT, _application_report(jane, 3))
        builder.add_document(RESUME, _resume(jane, 2))
        builder.start_candidate(sarah.name)
        builder.add_document(RESUME, _resume(sarah, 2))
        builder.add_document(COVER_LETTER, _cover_letter(sarah))

        analysis = analyze(pipeline, builder.build(), tmp_path)
        by_name = packets_by_name(analysis)

        assert sarah.name in by_name, "Sarah's documents were absorbed into another packet"
        sarah_packet = by_name[sarah.name]
        assert RESUME in document_types(sarah_packet)
        jane_packet = by_name[jane.name]
        assert set(jane_packet.page_indexes).isdisjoint(sarah_packet.page_indexes)


# --------------------------------------------------------------------------
# Test G: a two-page resume stays one document, in one packet
# --------------------------------------------------------------------------

class TestMultiPageResume:
    def test_two_pages_form_one_resume_owned_by_one_candidate(
        self, pipeline, tmp_path: Path
    ) -> None:
        applicant = APPLICANTS[4]
        builder = MixedBatchBuilder("two_page_resume.pdf")
        builder.start_candidate(applicant.name)
        builder.add_document(APPLICATION_REPORT, _application_report(applicant, 3))
        builder.add_document(RESUME, _resume(applicant, 2))

        analysis = analyze(pipeline, builder.build(), tmp_path)
        packet = analysis.identified_packets[0]

        resumes = [d for d in packet.documents if d.document_type == RESUME]
        assert len(resumes) == 1, "the resume was split into two documents"
        assert resumes[0].page_count == 2


# --------------------------------------------------------------------------
# Test H: name normalisation
# --------------------------------------------------------------------------

class TestNameNormalisation:
    @pytest.mark.parametrize(
        "written",
        ["Benjamin Perez", "Benjamin F. Perez", "Benjamin F Perez", "Perez, Benjamin",
         "BENJAMIN PEREZ"],
    )
    def test_all_spellings_join_one_packet(
        self, packet_service: CandidatePacketService, written: str
    ) -> None:
        groups = [
            make_group([0], APPLICATION_REPORT, Candidate(name="Benjamin Perez")),
            make_group([1], RESUME, Candidate(name=written)),
        ]
        packets = packet_service.build_packets(groups, "memory.pdf")

        identified = [p for p in packets if not p.is_unknown]
        assert len(identified) == 1, f"'{written}' was treated as a different person"
        assert len(identified[0].documents) == 2

    def test_a_genuinely_different_name_makes_a_second_packet(
        self, packet_service: CandidatePacketService
    ) -> None:
        groups = [
            make_group([0], RESUME, Candidate(name="Benjamin Perez")),
            make_group([1], RESUME, Candidate(name="Robert Perez")),
        ]
        packets = packet_service.build_packets(groups, "memory.pdf")
        assert len([p for p in packets if not p.is_unknown]) == 2


# --------------------------------------------------------------------------
# Test I: same name, conflicting identifiers
# --------------------------------------------------------------------------

class TestSameNameSafety:
    def test_conflicting_emails_prevent_a_merge(
        self, packet_service: CandidatePacketService
    ) -> None:
        """Two real people can share a name. Merging them hides documents."""
        groups = [
            make_group(
                [0], APPLICATION_REPORT,
                Candidate(name="Jane Smith", email="jane.smith@example.com"),
            ),
            make_group(
                [1], RESUME,
                Candidate(name="Jane Smith", email="j.smith@other.example.com"),
            ),
        ]
        packets = packet_service.build_packets(groups, "memory.pdf")

        identified = [p for p in packets if not p.is_unknown]
        assert len(identified) == 2, "two different people were merged into one packet"

    @pytest.mark.parametrize(
        ("first_id", "second_id"),
        [
            ("A-20001", "A-30002"),
            # Even a short ID that is too weak to *prove* a match is still
            # proof of a mismatch when the two disagree.
            ("A-1", "B-2"),
        ],
    )
    def test_conflicting_applicant_ids_prevent_a_merge(
        self, packet_service: CandidatePacketService, first_id: str, second_id: str
    ) -> None:
        groups = [
            make_group(
                [0], APPLICATION_REPORT, Candidate(name="Jane Smith", applicant_id=first_id)
            ),
            make_group([1], RESUME, Candidate(name="Jane Smith", applicant_id=second_id)),
        ]
        packets = packet_service.build_packets(groups, "memory.pdf")
        assert len([p for p in packets if not p.is_unknown]) == 2

    def test_the_ambiguity_is_flagged_on_both_packets(
        self, packet_service: CandidatePacketService
    ) -> None:
        groups = [
            make_group(
                [0], APPLICATION_REPORT,
                Candidate(name="Jane Smith", email="jane.smith@example.com"),
            ),
            make_group(
                [1], RESUME, Candidate(name="Jane Smith", email="other@example.com"),
            ),
        ]
        packets = [p for p in packet_service.build_packets(groups, "memory.pdf") if not p.is_unknown]
        assert all(packet.review_reasons for packet in packets), (
            "a same-name collision was resolved silently"
        )


# --------------------------------------------------------------------------
# Test F: ambiguity goes to review, not to a guess
# --------------------------------------------------------------------------

class TestUnknownQueue:
    def test_a_document_with_no_identity_and_no_context_is_not_guessed(
        self, packet_service: CandidatePacketService
    ) -> None:
        """The very first document, carrying nothing, has no owner to inherit."""
        groups = [make_group([0], REFERENCES, Candidate())]
        packets = packet_service.build_packets(groups, "memory.pdf")

        assert len(packets) == 1
        assert packets[0].is_unknown
        assert packets[0].documents[0].association_review

    def test_an_identity_matching_two_candidates_equally_is_not_guessed(
        self, packet_service: CandidatePacketService
    ) -> None:
        """A phone number shared by two packets must not pick one at random."""
        shared = "(206) 555-1234"
        groups = [
            make_group([0], RESUME, Candidate(name="Jane Smith", phone=shared)),
            make_group([1], RESUME, Candidate(name="Sarah Lee", phone=shared)),
            make_group([2], REFERENCES, Candidate(phone=shared)),
        ]
        packets = packet_service.build_packets(groups, "memory.pdf")

        unknown = next((p for p in packets if p.is_unknown), None)
        assert unknown is not None, "an equally-good match for two people was guessed"
        assert unknown.documents[0].start_page_index == 2

    def test_the_unknown_queue_sorts_last(
        self, packet_service: CandidatePacketService
    ) -> None:
        groups = [
            make_group([0], REFERENCES, Candidate()),
            make_group([1], RESUME, Candidate(name="Jane Smith")),
        ]
        packets = packet_service.build_packets(groups, "memory.pdf")
        assert packets[-1].is_unknown


# --------------------------------------------------------------------------
# Identity signal strength
# --------------------------------------------------------------------------

class TestIdentitySignals:
    def test_email_matches_across_different_written_names(
        self, packet_service: CandidatePacketService
    ) -> None:
        """A shared email outweighs a name that only partly matches."""
        groups = [
            make_group(
                [0], APPLICATION_REPORT,
                Candidate(name="Jane Smith", email="jane.smith@example.com"),
            ),
            make_group(
                [1], RESUME, Candidate(name="Jane A Smith", email="jane.smith@example.com"),
            ),
        ]
        packets = [p for p in packet_service.build_packets(groups, "memory.pdf") if not p.is_unknown]
        assert len(packets) == 1
        assert packets[0].documents[0].association_confidence > 0.9

    def test_applicant_id_alone_associates_a_document(
        self, packet_service: CandidatePacketService
    ) -> None:
        groups = [
            make_group([0], APPLICATION_REPORT, Candidate(name="Jane Smith", applicant_id="A-20001")),
            make_group([1], REFERENCES, Candidate(applicant_id="A20001")),
        ]
        packets = [p for p in packet_service.build_packets(groups, "memory.pdf") if not p.is_unknown]
        assert len(packets) == 1
        assert len(packets[0].documents) == 2

    def test_a_later_document_fills_in_missing_details(
        self, packet_service: CandidatePacketService
    ) -> None:
        groups = [
            make_group([0], RESUME, Candidate(name="Jane Smith")),
            make_group([1], COVER_LETTER, Candidate(name="Jane Smith", email="j@example.com")),
        ]
        packets = [p for p in packet_service.build_packets(groups, "memory.pdf") if not p.is_unknown]
        assert packets[0].candidate.email == "j@example.com"


# --------------------------------------------------------------------------
# Packet boundaries
# --------------------------------------------------------------------------

class TestPacketBoundaries:
    def test_each_new_applicant_records_a_boundary(
        self, packet_service: CandidatePacketService
    ) -> None:
        groups = [
            make_group([0], RESUME, Candidate(name="Jane Smith")),
            make_group([1], RESUME, Candidate(name="Robert Jones")),
            make_group([2], RESUME, Candidate(name="Sarah Lee")),
        ]
        packets = [p for p in packet_service.build_packets(groups, "memory.pdf") if not p.is_unknown]

        assert len(packets) == 3
        assert packets[0].boundary_reasons == ["first applicant in the file"]
        for packet in packets[1:]:
            assert packet.boundary_confidence >= 0.9
            assert packet.boundary_reasons

    def test_packets_report_their_page_range(
        self, packet_service: CandidatePacketService
    ) -> None:
        groups = [
            make_group([0, 1, 2], APPLICATION_REPORT, Candidate(name="Jane Smith")),
            make_group([3, 4], RESUME, Candidate(name="Jane Smith")),
        ]
        packets = [p for p in packet_service.build_packets(groups, "memory.pdf") if not p.is_unknown]
        assert packets[0].page_range_label == "Pages 1–5"
        assert packets[0].page_count == 5


class TestRealCorpusIdentity:
    """Attribution rules that only real applicant documents exposed."""

    def test_an_ats_record_and_its_attachment_are_one_person(
        self, packet_service: CandidatePacketService
    ) -> None:
        """A real applicant used one address on her ATS record and another on
        her resume, and was split into two packets as a result.

        An email is self-reported and people have several. An applicant ID is
        assigned by the system, one per applicant. A document carrying no ID is
        an attachment inside somebody's record, not a rival applicant, so its
        author's other address proves nothing.
        """
        groups = [
            make_group(
                [0], APPLICATION_REPORT,
                Candidate(name="Amara Okonjo", email="a.okonjo@example.net",
                          applicant_id="A-46187"),
            ),
            make_group(
                [1], RESUME,
                Candidate(name="Amara Okonjo", email="amara.okonjo@example.org"),
            ),
        ]
        packets = [p for p in packet_service.build_packets(groups, "x.pdf") if not p.is_unknown]

        assert len(packets) == 1, "one applicant was split across two packets"
        assert len(packets[0].documents) == 2

    def test_that_tolerance_still_shows_the_reviewer_the_clash(
        self, packet_service: CandidatePacketService
    ) -> None:
        """Tolerated is not the same as ignored."""
        groups = [
            make_group(
                [0], APPLICATION_REPORT,
                Candidate(name="Amara Okonjo", email="a@yahoo.com", applicant_id="A-46187"),
            ),
            make_group([1], RESUME, Candidate(name="Amara Okonjo", email="b@gmail.com")),
        ]
        packet_service.build_packets(groups, "x.pdf")

        resume = groups[1]
        assert resume.association_confidence < 0.90
        assert any("email" in reason for reason in resume.association_reasons)

    def test_two_ats_records_with_different_emails_stay_apart(
        self, packet_service: CandidatePacketService
    ) -> None:
        """Both sides registered separately, so the clash is decisive again."""
        groups = [
            make_group(
                [0], APPLICATION_REPORT,
                Candidate(name="Jane Smith", email="a@x.com", applicant_id="111"),
            ),
            make_group(
                [1], APPLICATION_REPORT,
                Candidate(name="Jane Smith", email="b@y.com", applicant_id="222"),
            ),
        ]
        packets = [p for p in packet_service.build_packets(groups, "x.pdf") if not p.is_unknown]
        assert len(packets) == 2, "two different applicants were merged"

    def test_neither_side_registered_keeps_the_old_protection(
        self, packet_service: CandidatePacketService
    ) -> None:
        """With no ATS ID anywhere, an email is the best identifier there is."""
        groups = [
            make_group([0], RESUME, Candidate(name="Jane Smith", email="a@x.com")),
            make_group([1], RESUME, Candidate(name="Jane Smith", email="b@y.com")),
        ]
        packets = [p for p in packet_service.build_packets(groups, "x.pdf") if not p.is_unknown]
        assert len(packets) == 2

    def test_a_credentialled_resume_joins_its_ats_record(
        self, packet_service: CandidatePacketService
    ) -> None:
        groups = [
            make_group([0], APPLICATION_REPORT,
                       Candidate(name="Amara Okonjo", applicant_id="A-46187")),
            make_group([1], RESUME, Candidate(name="Amara Okonjo, PhD")),
        ]
        packets = [p for p in packet_service.build_packets(groups, "x.pdf") if not p.is_unknown]
        assert len(packets) == 1
