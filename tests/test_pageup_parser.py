"""PageUp bulk compile: detection, exact page ranges, roster, safe failure.

The page ranges asserted here are the real 104-page client file's, reproduced
structurally by ``scripts/pageup_fixtures`` with invented applicants. The real
PDF is confidential and is never committed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.enums import ClassificationSource, SeparatorPolicy
from app.profiles.recruiting import APPLICATION_REPORT, COVER_LETTER, RESUME, TRANSCRIPT
from app.services.export_service import ExportService
from app.services.parsers.pageup import PageUpBulkCompileParser, roster_key
from app.services.parsers.registry import build_default_registry
from app.services.text_features import extract_features
from scripts import pageup_fixtures
from scripts.pageup_fixtures import (
    build_bulk_compile,
    build_multi_attachment_compile,
    build_roster_mismatch_compile,
)
from tests.helpers import build_pipeline


@pytest.fixture(scope="module")
def pageup_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("pageup")
    build_bulk_compile().write(directory)
    return directory


@pytest.fixture
def pageup_pipeline(profile, thresholds):
    return build_pipeline(
        profile,
        thresholds,
        separator_policy=SeparatorPolicy.EXCLUDE,
        parser_registry=build_default_registry(profile),
    )


def features_of(batch) -> list:
    return [extract_features("\n".join(page.lines)) for page in batch.pages]


class TestDetection:
    def test_a_bulk_compile_is_recognised(self, profile) -> None:
        match = PageUpBulkCompileParser(profile).can_parse(
            features_of(build_bulk_compile())
        )
        assert match.matched
        assert match.confidence >= 0.95

    def test_an_unrelated_pdf_is_not_claimed(self, profile) -> None:
        """The parser must not fire on anything that is not a bulk compile."""
        from scripts import sample_data

        for factory in (sample_data.sample_a, sample_data.sample_b, sample_data.sample_f):
            document = factory()
            features = [
                extract_features("\n".join(page.lines)) for page in document.pages
            ]
            assert not PageUpBulkCompileParser(profile).can_parse(features).matched, (
                f"{document.filename} was wrongly claimed as a PageUp bulk compile"
            )

    def test_the_registry_picks_the_pageup_parser(self, profile) -> None:
        selection = build_default_registry(profile).select(
            features_of(build_bulk_compile())
        )
        assert selection.matched
        assert selection.parser is not None
        assert selection.parser.name == "PageUp bulk compile"


class TestRoster:
    def test_the_roster_and_count_are_read_from_the_cover(self, profile) -> None:
        parser = PageUpBulkCompileParser(profile)
        cover = parser._parse_cover(features_of(build_bulk_compile()))

        assert cover.declared_count == 14
        assert len(cover.roster) == 14
        assert cover.declared_types == [RESUME]
        assert [entry.display_name for entry in cover.roster] == list(
            pageup_fixtures.DEFAULT_ROSTER
        )

    def test_display_names_keep_their_decoration(self, profile) -> None:
        """A preferred name or honorific belongs on the user's output file."""
        cover = PageUpBulkCompileParser(profile)._parse_cover(
            features_of(build_bulk_compile())
        )
        names = [entry.display_name for entry in cover.roster]
        assert "Dr Tandalea Merriweather (Tandalea)" in names
        assert "Katelyn Lynwood (Kate)" in names

    def test_identity_matching_ignores_that_decoration(self) -> None:
        """...while still matching the plainer name the form prints."""
        assert roster_key("Dr Tandalea Merriweather (Tandalea)") == roster_key(
            "Tandalea Merriweather"
        )
        assert roster_key("Katelyn Lynwood (Kate)") == roster_key("Katelyn Lynwood")
        assert roster_key("Ismael Briseño Cardona (Ezra)") == roster_key(
            "Ismael Briseño Cardona"
        )

    def test_a_roster_count_mismatch_is_reported(self, pageup_pipeline, tmp_path) -> None:
        batch = build_roster_mismatch_compile()
        analysis = pageup_pipeline.analyze_file(batch.write(tmp_path))

        assert analysis.parser_warnings, "a disagreeing applicant count must be surfaced"
        assert any("applicants" in warning for warning in analysis.parser_warnings)


class TestExactPageRanges:
    """The real file's ground truth, structure for structure."""

    def test_the_fixture_reproduces_the_real_files_shape(self) -> None:
        batch = build_bulk_compile()
        assert batch.page_count == 104
        assert len(batch.applicants) == 14
        # Spot-check the ranges the specification states outright.
        first, last = batch.applicants[0], batch.applicants[-1]
        assert (first.application_first, first.application_last) == (2, 6)
        assert (first.resume_first, first.resume_last) == (7, 8)
        assert (last.application_first, last.application_last) == (99, 102)
        assert (last.resume_first, last.resume_last) == (103, 104)

    def test_every_application_and_resume_range_is_exact(
        self, pageup_pipeline, pageup_dir: Path
    ) -> None:
        batch = build_bulk_compile()
        analysis = pageup_pipeline.analyze_file(pageup_dir / batch.filename)

        for expected in batch.applicants:
            report = next(
                g
                for g in analysis.groups
                if g.document_type == APPLICATION_REPORT
                and g.start_page == expected.application_first
            )
            resume = next(
                g
                for g in analysis.groups
                if g.document_type == RESUME and g.start_page == expected.resume_first
            )
            assert report.end_page == expected.application_last, expected.display_name
            assert resume.end_page == expected.resume_last, expected.display_name

    def test_exactly_fourteen_of_each(self, pageup_pipeline, pageup_dir: Path) -> None:
        batch = build_bulk_compile()
        analysis = pageup_pipeline.analyze_file(pageup_dir / batch.filename)

        assert sum(1 for g in analysis.groups if g.document_type == APPLICATION_REPORT) == 14
        assert sum(1 for g in analysis.groups if g.document_type == RESUME) == 14
        assert sum(1 for g in analysis.groups if g.document_type == COVER_LETTER) == 0

    def test_multi_page_resumes_stay_one_document(
        self, pageup_pipeline, pageup_dir: Path
    ) -> None:
        batch = build_bulk_compile()
        analysis = pageup_pipeline.analyze_file(pageup_dir / batch.filename)

        for expected in batch.applicants:
            resume = next(
                g
                for g in analysis.groups
                if g.document_type == RESUME and g.start_page == expected.resume_first
            )
            assert resume.page_count == len(expected.resume_pages)

    def test_no_pages_leak_between_applicants(
        self, pageup_pipeline, pageup_dir: Path
    ) -> None:
        batch = build_bulk_compile()
        analysis = pageup_pipeline.analyze_file(pageup_dir / batch.filename)

        for expected in batch.applicants:
            owned = set(expected.all_pages)
            for group in analysis.groups:
                if group.candidate.name != expected.display_name:
                    continue
                for index in group.export_page_indexes:
                    assert index + 1 in owned, (
                        f"page {index + 1} was filed under {expected.display_name} "
                        f"but belongs outside their range"
                    )

    def test_every_document_carries_the_right_candidate(
        self, pageup_pipeline, pageup_dir: Path
    ) -> None:
        batch = build_bulk_compile()
        analysis = pageup_pipeline.analyze_file(pageup_dir / batch.filename)

        for expected in batch.applicants:
            resume = next(
                g
                for g in analysis.groups
                if g.document_type == RESUME and g.start_page == expected.resume_first
            )
            assert resume.candidate.name == expected.display_name


class TestCoverPage:
    def test_the_cover_page_is_never_exported(
        self, pageup_pipeline, pageup_dir: Path
    ) -> None:
        batch = build_bulk_compile()
        analysis = pageup_pipeline.analyze_file(pageup_dir / batch.filename)

        for group in analysis.groups:
            assert 0 not in group.export_page_indexes, (
                "the bulk compile cover page reached an applicant's document"
            )

    def test_the_cover_is_excluded_even_when_separators_are_kept(
        self, profile, thresholds, pageup_dir: Path
    ) -> None:
        """The separator policy governs divider pages inside a packet, not the
        file's own manifest. The cover lists every applicant in the batch, so
        letting a global preference keep it would put thirteen other people's
        names inside one applicant's resume."""
        keeping = build_pipeline(
            profile,
            thresholds,
            separator_policy=SeparatorPolicy.INCLUDE,
            parser_registry=build_default_registry(profile),
        )
        batch = build_bulk_compile()
        analysis = keeping.analyze_file(pageup_dir / batch.filename)

        for group in analysis.groups:
            assert 0 not in group.export_page_indexes, (
                f"the bulk cover reached {group.document_type} "
                f"{group.page_range_label} despite being file metadata"
            )

    def test_the_cover_is_excluded_under_every_separator_policy(
        self, profile, thresholds, pageup_dir: Path
    ) -> None:
        batch = build_bulk_compile()
        for policy in (
            SeparatorPolicy.INCLUDE,
            SeparatorPolicy.EXCLUDE,
            SeparatorPolicy.ASK,
        ):
            pipeline = build_pipeline(
                profile,
                thresholds,
                separator_policy=policy,
                parser_registry=build_default_registry(profile),
            )
            analysis = pipeline.analyze_file(pageup_dir / batch.filename)
            for group in analysis.groups:
                assert 0 not in group.export_page_indexes, policy

    def test_the_cover_page_is_not_typed_as_somebodys_document(
        self, pageup_pipeline, pageup_dir: Path
    ) -> None:
        batch = build_bulk_compile()
        analysis = pageup_pipeline.analyze_file(pageup_dir / batch.filename)
        cover = analysis.page(0)

        assert cover is not None
        assert cover.predicted_type not in (RESUME, APPLICATION_REPORT)
        assert cover.is_excluded_separator


class TestMultipleAttachments:
    """Covers where the cover page compiled more than one attachment type.

    With a single declared type the region after "Total score" is that type by
    construction. With several, those documents run together with nothing
    between them, and every test here pins down one way the file says where the
    seam is -- or, when it says nothing, that the pages reach a human instead of
    a guess.
    """

    def analyze(self, pipeline, tmp_path: Path, batch):
        return pipeline.analyze_file(batch.write(tmp_path))

    def documents_for(self, analysis, name: str) -> list:
        """One applicant's exportable documents, in page order.

        The bulk cover is deliberately left out: it exports nothing, and with a
        single-applicant fixture it would otherwise be counted as one of that
        applicant's documents.
        """
        return sorted(
            (
                g
                for g in analysis.groups
                if g.candidate.name == name and g.export_page_indexes
            ),
            key=lambda g: g.start_page,
        )

    def attachment_of(self, analysis, name: str):
        """The one document an applicant uploaded, whatever it was typed as."""
        documents = self.documents_for(analysis, name)
        attachments = [g for g in documents if g.document_type != APPLICATION_REPORT]
        assert len(attachments) == 1, [
            (g.document_type, g.start_page, g.end_page) for g in documents
        ]
        return attachments[0]

    def assert_matches(self, analysis, batch) -> None:
        """Every packet's application form and attachments, page for page."""
        for packet in batch.packets:
            documents = self.documents_for(analysis, packet.display_name)
            expected = [
                (APPLICATION_REPORT, packet.application_first, packet.application_last)
            ] + [
                (a.document_type, a.first_page, a.last_page)
                for a in packet.attachments
                if a.document_type
            ]
            actual = [(g.document_type, g.start_page, g.end_page) for g in documents]
            assert actual == expected, packet.display_name

    # -- the shapes an applicant actually uploads ----------------------
    def test_application_and_resume(self, pageup_pipeline, tmp_path: Path) -> None:
        batch = build_multi_attachment_compile(
            [("Ana Bellweather", 4, [(RESUME, 2)])],
            filename="PageUp_App_Resume.pdf",
        )
        self.assert_matches(self.analyze(pageup_pipeline, tmp_path, batch), batch)

    def test_application_cover_letter_and_resume(
        self, pageup_pipeline, tmp_path: Path
    ) -> None:
        batch = build_multi_attachment_compile(
            [("Bo Castellane", 4, [(COVER_LETTER, 1), (RESUME, 2)])],
            filename="PageUp_App_Letter_Resume.pdf",
        )
        self.assert_matches(self.analyze(pageup_pipeline, tmp_path, batch), batch)

    def test_application_letter_resume_and_transcript(
        self, pageup_pipeline, tmp_path: Path
    ) -> None:
        batch = build_multi_attachment_compile(
            [("Cleo Danforth", 4, [(COVER_LETTER, 1), (RESUME, 2), (TRANSCRIPT, 2)])],
            declared_types=(COVER_LETTER, RESUME, TRANSCRIPT),
            filename="PageUp_App_Letter_Resume_Transcript.pdf",
        )
        self.assert_matches(self.analyze(pageup_pipeline, tmp_path, batch), batch)

    def test_a_multi_page_cover_letter_stays_one_document(
        self, pageup_pipeline, tmp_path: Path
    ) -> None:
        batch = build_multi_attachment_compile(
            [("Dana Whitlock", 4, [(COVER_LETTER, 3), (RESUME, 2)])],
            filename="PageUp_Long_Letter.pdf",
        )
        analysis = self.analyze(pageup_pipeline, tmp_path, batch)
        self.assert_matches(analysis, batch)

        letter = next(
            g for g in analysis.groups if g.document_type == COVER_LETTER
        )
        assert letter.page_count == 3

    def test_a_multi_page_resume_stays_one_document(
        self, pageup_pipeline, tmp_path: Path
    ) -> None:
        batch = build_multi_attachment_compile(
            [("Eli Farthing", 4, [(COVER_LETTER, 1), (RESUME, 4)])],
            filename="PageUp_Long_Resume.pdf",
        )
        analysis = self.analyze(pageup_pipeline, tmp_path, batch)
        self.assert_matches(analysis, batch)

        resume = next(g for g in analysis.groups if g.document_type == RESUME)
        assert resume.page_count == 4

    def test_a_signature_only_page_does_not_become_a_document(
        self, pageup_pipeline, tmp_path: Path
    ) -> None:
        """A letter's last page is often just "Sincerely," and a name."""
        batch = build_multi_attachment_compile(
            [("Faye Grantham", 4, [(COVER_LETTER, 2), (RESUME, 2)])],
            sparse_letter_endings=True,
            filename="PageUp_Sparse_Letter_Page.pdf",
        )
        analysis = self.analyze(pageup_pipeline, tmp_path, batch)
        self.assert_matches(analysis, batch)

        assert len(self.documents_for(analysis, "Faye Grantham")) == 3

    def test_an_uploaded_filename_names_the_attachment(
        self, pageup_pipeline, tmp_path: Path
    ) -> None:
        """The applicant named the file; that beats anything inferred."""
        batch = build_multi_attachment_compile(
            [("Gita Halvorsen", 4, [("filename:Resume", 2)])],
            filename="PageUp_Filename_Banner.pdf",
        )
        analysis = self.analyze(pageup_pipeline, tmp_path, batch)
        self.assert_matches(analysis, batch)

        resume = next(g for g in analysis.groups if g.document_type == RESUME)
        assert resume.classification_source is ClassificationSource.DETERMINISTIC

    # -- what must not happen ------------------------------------------
    def test_nothing_is_fabricated_for_an_applicant_who_uploaded_less(
        self, pageup_pipeline, tmp_path: Path
    ) -> None:
        """Two types declared, one uploaded: one document, not an empty second."""
        batch = build_multi_attachment_compile(
            [
                ("Hal Ingersoll", 4, [(COVER_LETTER, 1), (RESUME, 2)]),
                ("Iris Jerrold", 5, [(RESUME, 2)]),
                ("Jonas Kettleby", 4, []),
            ],
            filename="PageUp_Partial_Uploads.pdf",
        )
        analysis = self.analyze(pageup_pipeline, tmp_path, batch)
        self.assert_matches(analysis, batch)

        assert len(self.documents_for(analysis, "Iris Jerrold")) == 2
        assert not [
            g
            for g in analysis.groups
            if g.candidate.name == "Iris Jerrold" and g.document_type == COVER_LETTER
        ]
        # The applicant who uploaded nothing keeps their application form only.
        assert len(self.documents_for(analysis, "Jonas Kettleby")) == 1

    def test_attachments_never_leak_between_applicants(
        self, pageup_pipeline, tmp_path: Path
    ) -> None:
        batch = build_multi_attachment_compile(
            [
                ("Lena Marchetti", 4, [(COVER_LETTER, 2), (RESUME, 3)]),
                ("Omar Nakashima", 5, [(RESUME, 2)]),
                ("Pia Quintrell", 4, [(COVER_LETTER, 1), (RESUME, 2)]),
            ],
            sparse_letter_endings=True,
            filename="PageUp_Leak_Check.pdf",
        )
        analysis = self.analyze(pageup_pipeline, tmp_path, batch)

        for packet in batch.packets:
            owned = set(range(packet.application_first, packet.application_last + 1))
            for attachment in packet.attachments:
                owned.update(attachment.pages)
            for group in analysis.groups:
                if group.candidate.name != packet.display_name:
                    continue
                for index in group.export_page_indexes:
                    assert index + 1 in owned, (
                        f"page {index + 1} was filed under {packet.display_name}"
                    )

    def test_a_single_declared_type_is_never_split(
        self, pageup_pipeline, pageup_dir: Path
    ) -> None:
        """The 104-page file's guarantee: one declared type, one document."""
        batch = build_bulk_compile()
        analysis = pageup_pipeline.analyze_file(pageup_dir / batch.filename)

        assert sum(1 for g in analysis.groups if g.document_type == RESUME) == 14
        for expected in batch.applicants:
            resume = next(
                g
                for g in analysis.groups
                if g.document_type == RESUME and g.start_page == expected.resume_first
            )
            assert resume.end_page == expected.resume_last

    def test_an_unnumbered_resume_is_not_cut_up(
        self, pageup_pipeline, tmp_path: Path
    ) -> None:
        """A great many real resumes repeat the contact block instead of
        numbering pages, so every continuation page reads like a first page."""
        batch = build_bulk_compile(
            filename="PageUp_Unnumbered_Resumes.pdf", numbered_resumes=False
        )
        analysis = pageup_pipeline.analyze_file(batch.write(tmp_path))

        resumes = [g for g in analysis.groups if g.document_type == RESUME]
        assert len(resumes) == 14, [
            (g.candidate.name, g.start_page, g.end_page) for g in resumes
        ]
        for expected in batch.applicants:
            resume = next(g for g in resumes if g.start_page == expected.resume_first)
            assert resume.end_page == expected.resume_last, expected.display_name

    def test_a_letter_inside_a_resume_does_not_become_a_cover_letter(
        self, pageup_pipeline, tmp_path: Path
    ) -> None:
        """Why the single-declared-type guard exists, not just that it does.

        People paste a covering note into the file they upload as their resume.
        The cover page compiled resumes and only resumes, and that outranks a
        page that merely reads like a letter -- otherwise the batch comes back
        with thirteen cover letters nobody compiled, each carved out of a
        resume that is now missing a page.
        """
        batch = build_bulk_compile(
            filename="PageUp_Letter_Inside_Resume.pdf", letter_inside_resumes=True
        )
        analysis = pageup_pipeline.analyze_file(batch.write(tmp_path))

        assert not [g for g in analysis.groups if g.document_type == COVER_LETTER], (
            "a cover letter was invented from a page inside a declared resume"
        )
        resumes = [g for g in analysis.groups if g.document_type == RESUME]
        assert len(resumes) == 14
        for expected in batch.applicants:
            resume = next(g for g in resumes if g.start_page == expected.resume_first)
            assert resume.end_page == expected.resume_last, expected.display_name

    # -- when the file says nothing ------------------------------------
    def test_two_documents_uploaded_as_one_file_go_to_review(
        self, pageup_pipeline, tmp_path: Path
    ) -> None:
        """Continuous page numbers say "one document"; the text says "two".

        Nothing can place the seam, so the pages must reach a reviewer rather
        than be exported under one name.
        """
        batch = build_multi_attachment_compile(
            [("Rhea Sallinger", 4, [("combined", 3)])],
            filename="PageUp_Combined_Upload.pdf",
        )
        analysis = self.analyze(pageup_pipeline, tmp_path, batch)

        attachment = self.attachment_of(analysis, "Rhea Sallinger")
        assert attachment.needs_attention, "a merged upload was exported without a flag"
        assert attachment.page_count == 3, "the pages must stay together, not be guessed apart"
        assert any("more than one document" in reason for reason in attachment.review_reasons)

    def test_an_unidentifiable_attachment_goes_to_review(
        self, pageup_pipeline, tmp_path: Path
    ) -> None:
        """Nothing names it and the text points nowhere clear: ask a human."""
        batch = build_multi_attachment_compile(
            [("Sven Tarkington", 4, [("unknown", 2)])],
            filename="PageUp_Unknown_Upload.pdf",
        )
        analysis = self.analyze(pageup_pipeline, tmp_path, batch)

        attachment = self.attachment_of(analysis, "Sven Tarkington")
        assert attachment.needs_attention, "an unidentified attachment was filed silently"

    def test_review_flags_do_not_cost_the_applicant_their_pages(
        self, pageup_pipeline, tmp_path: Path
    ) -> None:
        """Flagged is not dropped: the pages are still exportable and attributed."""
        batch = build_multi_attachment_compile(
            [("Uma Verity", 4, [("unknown", 2)])],
            filename="PageUp_Review_Keeps_Pages.pdf",
        )
        analysis = self.analyze(pageup_pipeline, tmp_path, batch)

        documents = self.documents_for(analysis, "Uma Verity")
        assert len(documents) == 2
        assert [index + 1 for index in documents[-1].export_page_indexes] == [6, 7]


class TestExport:
    def test_resume_only_export_writes_exactly_fourteen_pdfs(
        self, pageup_pipeline, pageup_dir: Path, tmp_path: Path
    ) -> None:
        batch = build_bulk_compile()
        analysis = pageup_pipeline.analyze_file(pageup_dir / batch.filename)

        destination = tmp_path / "out"
        ExportService(
            group_by_document_type=True,
            export_combined_packets=False,
            document_types=[RESUME],
        ).export([analysis], destination)

        written = sorted(p.name for p in (destination / "Resumes").glob("*.pdf"))
        assert len(written) == 14
        assert written == sorted(f"{a.display_name}.pdf" for a in batch.applicants)

    def test_resume_pdfs_contain_only_that_applicants_pages(
        self, pageup_pipeline, pageup_dir: Path, tmp_path: Path
    ) -> None:
        from app.services.pdf_service import open_pdf

        batch = build_bulk_compile()
        analysis = pageup_pipeline.analyze_file(pageup_dir / batch.filename)
        destination = tmp_path / "out"
        ExportService(
            group_by_document_type=True,
            export_combined_packets=False,
            document_types=[RESUME],
        ).export([analysis], destination)

        for expected in batch.applicants:
            path = destination / "Resumes" / f"{expected.display_name}.pdf"
            with open_pdf(path) as document:
                assert document.page_count == len(expected.resume_pages)


class TestConfidenceAndDiagnostics:
    def test_structure_confidence_is_high_and_nothing_needs_review(
        self, pageup_pipeline, pageup_dir: Path
    ) -> None:
        """A recognised bulk compile must not report misleading low confidence."""
        batch = build_bulk_compile()
        analysis = pageup_pipeline.analyze_file(pageup_dir / batch.filename)

        assert analysis.parser_name == "PageUp bulk compile"
        assert analysis.structure_confidence >= 0.95
        flagged = [g for g in analysis.groups if g.needs_attention]
        assert not flagged, [(g.document_type, g.review_reasons) for g in flagged]

    def test_interior_pages_are_deterministic_not_scored(
        self, pageup_pipeline, pageup_dir: Path
    ) -> None:
        batch = build_bulk_compile()
        analysis = pageup_pipeline.analyze_file(pageup_dir / batch.filename)

        for group in analysis.groups:
            if group.document_type in (APPLICATION_REPORT, RESUME):
                assert group.classification_source is ClassificationSource.DETERMINISTIC
                assert group.classification_confidence >= 0.98

    def test_a_native_text_file_does_no_ocr(
        self, pageup_pipeline, pageup_dir: Path
    ) -> None:
        batch = build_bulk_compile()
        analysis = pageup_pipeline.analyze_file(pageup_dir / batch.filename)

        assert analysis.ocr_pages == 0
        assert analysis.ocr_failures == 0
        assert analysis.native_text_pages == analysis.page_count


class TestSafeFailure:
    def test_a_compile_with_no_total_score_still_extracts_and_warns(
        self, pageup_pipeline, tmp_path: Path
    ) -> None:
        """Without the section ending, boundaries are a guess -- say so."""
        batch = build_bulk_compile(
            filename="PageUp_No_Total_Score.pdf", include_total_score=False
        )
        analysis = pageup_pipeline.analyze_file(batch.write(tmp_path))

        assert analysis.parser_name == "PageUp bulk compile"
        assert analysis.parser_warnings, "a missing section ending must be surfaced"
        # Each applicant is still found, so nobody's documents vanish.
        reports = [g for g in analysis.groups if g.document_type == APPLICATION_REPORT]
        assert len(reports) == 14

    def test_a_parser_that_throws_falls_through_cleanly(self, profile, thresholds) -> None:
        """A broken parser must not leave half-assigned pages behind."""
        from app.services.parsers.registry import ATSParserRegistry
        from app.services.parsers.base import ParserMatch

        class Exploding:
            name = "exploding"

            def can_parse(self, features_list):
                return ParserMatch(0.99, "always")

            def parse(self, pages, features_list, *, separator_policy):
                pages[0].predicted_type = RESUME
                pages[0].classification_source = ClassificationSource.DETERMINISTIC
                raise RuntimeError("boom")

        pipeline = build_pipeline(
            profile, thresholds, parser_registry=ATSParserRegistry([Exploding()])
        )
        from scripts import sample_data

        directory = Path(tempfile_dir())
        path = sample_data.build_pdf(
            sample_data.sample_b(), directory / sample_data.sample_b().filename
        )
        analysis = pipeline.analyze_file(path)

        # The generic pipeline took over and produced a normal result.
        assert analysis.groups
        assert all(
            g.classification_source is not ClassificationSource.DETERMINISTIC
            for g in analysis.groups
        )


def tempfile_dir() -> str:
    import tempfile

    return tempfile.mkdtemp(prefix="sps-parser-test-")
