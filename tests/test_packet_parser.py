"""Submitted applicant packets: form/attachment boundaries and corpus totals.

The shapes asserted here mirror the real 17-file client corpus -- a generated
application form followed by uploaded attachments with no separators between
them -- reproduced structurally by ``scripts/packet_fixtures`` with invented
applicants. The real files are confidential and are never committed.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from app.models.enums import ClassificationSource, SeparatorPolicy
from app.profiles.recruiting import APPLICATION_REPORT, COVER_LETTER, RESUME, TRANSCRIPT
from app.services.export_service import ExportService
from app.services.parsers.registry import build_default_registry
from app.services.parsers.submitted_packet import SubmittedApplicantPacketParser
from app.services.text_features import extract_features
from scripts.packet_fixtures import (
    build_concatenated_corpus,
    build_corpus,
    build_packet,
)
from tests.helpers import build_pipeline


@pytest.fixture
def packet_pipeline(profile, thresholds):
    return build_pipeline(
        profile,
        thresholds,
        separator_policy=SeparatorPolicy.EXCLUDE,
        parser_registry=build_default_registry(profile),
    )


@pytest.fixture(scope="module")
def corpus_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("packets")
    for fixture in build_corpus():
        fixture.write(directory)
    return directory


def features_of(fixture) -> list:
    return [extract_features("\n".join(page.lines)) for page in fixture.pages]


class TestDetection:
    def test_a_submitted_packet_is_recognised(self, profile) -> None:
        fixture = build_packet(
            filename="x.pdf", display_name="Ana Ruiz", surname="Ruiz",
            given="Ana", application=5, resume=2,
        )
        match = SubmittedApplicantPacketParser(profile).can_parse(features_of(fixture))
        assert match.matched
        assert match.confidence >= 0.90

    def test_unrelated_pdfs_are_not_claimed(self, profile) -> None:
        from scripts import sample_data

        parser = SubmittedApplicantPacketParser(profile)
        for factory in (sample_data.sample_a, sample_data.sample_b, sample_data.sample_h):
            document = factory()
            features = [
                extract_features("\n".join(page.lines)) for page in document.pages
            ]
            assert not parser.can_parse(features).matched, document.filename

    def test_the_registry_picks_the_packet_parser(self, profile) -> None:
        fixture = build_packet(
            filename="x.pdf", display_name="Ana Ruiz", surname="Ruiz",
            given="Ana", application=5, resume=2,
        )
        selection = build_default_registry(profile).select(features_of(fixture))
        assert selection.matched
        assert selection.parser is not None
        assert selection.parser.name == "Submitted applicant packet"


class TestCorpusTotals:
    """The whole 17-file corpus, against the specification's expected totals."""

    def test_the_fixture_corpus_matches_the_real_ones_shape(self) -> None:
        corpus = build_corpus()
        assert len(corpus) == 17
        assert sum(f.page_count for f in corpus) == 161

        counts = Counter(d.document_type for f in corpus for d in f.expected)
        assert counts[APPLICATION_REPORT] == 17
        assert counts[RESUME] == 16
        assert counts[COVER_LETTER] == 10
        assert counts[TRANSCRIPT] == 1
        assert sum(counts.values()) == 44

    def test_every_logical_document_is_found_with_exact_pages(
        self, packet_pipeline, corpus_dir: Path
    ) -> None:
        exact = 0
        expected_total = 0
        misses: list[str] = []

        for fixture in build_corpus():
            analysis = packet_pipeline.analyze_file(corpus_dir / fixture.filename)
            for document in fixture.expected:
                expected_total += 1
                if any(
                    g.document_type == document.document_type
                    and g.start_page == document.first_page
                    and g.end_page == document.last_page
                    for g in analysis.groups
                ):
                    exact += 1
                else:
                    misses.append(
                        f"{fixture.filename}: expected {document.document_type} "
                        f"{document.first_page}-{document.last_page}, got "
                        + ", ".join(
                            f"{g.document_type} {g.start_page}-{g.end_page}"
                            for g in analysis.groups
                        )
                    )

        assert exact == expected_total == 44, "\n".join(misses)

    def test_document_counts_across_the_corpus(
        self, packet_pipeline, corpus_dir: Path
    ) -> None:
        counts: Counter[str] = Counter()
        for fixture in build_corpus():
            analysis = packet_pipeline.analyze_file(corpus_dir / fixture.filename)
            counts.update(g.document_type for g in analysis.groups)

        assert counts[APPLICATION_REPORT] == 17
        assert counts[RESUME] == 16
        assert counts[COVER_LETTER] == 10
        assert counts[TRANSCRIPT] == 1
        assert sum(counts.values()) == 44

    def test_type_filtered_exports_write_the_expected_counts(
        self, packet_pipeline, corpus_dir: Path, tmp_path: Path
    ) -> None:
        analyses = [
            packet_pipeline.analyze_file(corpus_dir / f.filename) for f in build_corpus()
        ]

        for document_type, folder, expected in (
            (RESUME, "Resumes", 16),
            (COVER_LETTER, "Cover Letters", 10),
            (TRANSCRIPT, "Transcripts", 1),
        ):
            destination = tmp_path / folder.replace(" ", "_")
            ExportService(
                group_by_document_type=True,
                export_combined_packets=False,
                document_types=[document_type],
            ).export(analyses, destination)
            written = list((destination / folder).glob("*.pdf"))
            assert len(written) == expected, f"{document_type}: {[p.name for p in written]}"


class TestBoundaryCases:
    def test_a_packet_with_no_attachments_invents_nothing(
        self, packet_pipeline, corpus_dir: Path
    ) -> None:
        """The real corpus's attachment-less applicant must not gain a resume."""
        fixture = next(f for f in build_corpus() if f.filename.startswith("Lunye"))
        analysis = packet_pipeline.analyze_file(corpus_dir / fixture.filename)

        assert [g.document_type for g in analysis.groups] == [APPLICATION_REPORT]
        assert analysis.groups[0].start_page == 1
        assert analysis.groups[0].end_page == fixture.page_count

    def test_a_blank_page_inside_the_form_stays_in_the_form(
        self, packet_pipeline, corpus_dir: Path
    ) -> None:
        fixture = next(f for f in build_corpus() if f.filename.startswith("Lunye"))
        analysis = packet_pipeline.analyze_file(corpus_dir / fixture.filename)
        # The fixture puts a blank sheet at page 5 of the form.
        assert analysis.groups[0].contains(4)

    def test_a_sparse_signature_page_stays_with_its_cover_letter(
        self, packet_pipeline, corpus_dir: Path
    ) -> None:
        """The corpus's important regression: a two-page letter whose second
        page holds only the closing must not split into ``Other``."""
        fixture = next(
            f for f in build_corpus() if f.filename.startswith("Nicole Stevens")
        )
        analysis = packet_pipeline.analyze_file(corpus_dir / fixture.filename)

        letter = next(g for g in analysis.groups if g.document_type == COVER_LETTER)
        assert (letter.start_page, letter.end_page) == (6, 7)
        assert letter.page_count == 2

    def test_a_multi_page_resume_stays_one_document(
        self, packet_pipeline, corpus_dir: Path
    ) -> None:
        fixture = next(f for f in build_corpus() if f.filename.startswith("Ramon"))
        analysis = packet_pipeline.analyze_file(corpus_dir / fixture.filename)

        resume = next(g for g in analysis.groups if g.document_type == RESUME)
        assert (resume.start_page, resume.end_page) == (13, 16)

    def test_the_application_form_is_not_mistaken_for_a_resume(
        self, packet_pipeline, corpus_dir: Path
    ) -> None:
        """Form pages list employment history; that must not read as a resume."""
        for fixture in build_corpus():
            analysis = packet_pipeline.analyze_file(corpus_dir / fixture.filename)
            report = next(
                g for g in analysis.groups if g.document_type == APPLICATION_REPORT
            )
            expected = fixture.expect(APPLICATION_REPORT)
            assert expected is not None
            assert (report.start_page, report.end_page) == (
                expected.first_page,
                expected.last_page,
            ), fixture.filename


class TestIdentity:
    def test_attachments_inherit_the_forms_identity(
        self, packet_pipeline, corpus_dir: Path
    ) -> None:
        for fixture in build_corpus():
            analysis = packet_pipeline.analyze_file(corpus_dir / fixture.filename)
            for group in analysis.groups:
                assert group.candidate.name, f"{fixture.filename}: {group.document_type}"

    def test_a_suffixed_name_survives(self, packet_pipeline, corpus_dir: Path) -> None:
        fixture = next(f for f in build_corpus() if "Grayson" in f.filename)
        analysis = packet_pipeline.analyze_file(corpus_dir / fixture.filename)
        names = {g.candidate.name for g in analysis.groups}
        assert len(names) == 1
        assert "Grayson" in names.pop()

    def test_a_hyphenated_name_survives(self, packet_pipeline, corpus_dir: Path) -> None:
        fixture = next(f for f in build_corpus() if "Stevens-Bothwell" in f.filename)
        analysis = packet_pipeline.analyze_file(corpus_dir / fixture.filename)
        names = {g.candidate.name for g in analysis.groups}
        assert len(names) == 1
        assert "Stevens-Bothwell" in names.pop()


class TestConcatenatedBatch:
    def test_many_packets_in_one_pdf_split_correctly(
        self, packet_pipeline, tmp_path: Path
    ) -> None:
        fixture = build_concatenated_corpus()
        analysis = packet_pipeline.analyze_file(fixture.write(tmp_path))

        for document in fixture.expected:
            assert any(
                g.document_type == document.document_type
                and g.start_page == document.first_page
                and g.end_page == document.last_page
                for g in analysis.groups
            ), (
                f"expected {document.document_type} "
                f"{document.first_page}-{document.last_page}, got "
                + ", ".join(
                    f"{g.document_type} {g.start_page}-{g.end_page}"
                    for g in analysis.groups
                )
            )

    def test_no_pages_leak_between_concatenated_applicants(
        self, packet_pipeline, tmp_path: Path
    ) -> None:
        fixture = build_concatenated_corpus()
        analysis = packet_pipeline.analyze_file(fixture.write(tmp_path))

        assert len(analysis.packets) == 4
        for packet in analysis.packets:
            indexes = packet.page_indexes
            assert indexes == list(range(indexes[0], indexes[-1] + 1)), (
                f"{packet.display_name}'s pages are not contiguous, so another "
                "applicant's pages were mixed in"
            )


class TestDiagnostics:
    def test_packets_are_deterministic_and_need_no_review(
        self, packet_pipeline, corpus_dir: Path
    ) -> None:
        for fixture in build_corpus():
            analysis = packet_pipeline.analyze_file(corpus_dir / fixture.filename)
            assert analysis.parser_name == "Submitted applicant packet"
            assert analysis.structure_confidence >= 0.90
            for group in analysis.groups:
                assert group.classification_source is ClassificationSource.DETERMINISTIC
            flagged = [g for g in analysis.groups if g.needs_attention]
            assert not flagged, (
                fixture.filename,
                [(g.document_type, g.review_reasons) for g in flagged],
            )

    def test_native_text_packets_do_no_ocr(
        self, packet_pipeline, corpus_dir: Path
    ) -> None:
        for fixture in build_corpus():
            analysis = packet_pipeline.analyze_file(corpus_dir / fixture.filename)
            assert analysis.ocr_pages == 0
            assert analysis.native_text_pages == analysis.page_count
