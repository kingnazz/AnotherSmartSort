"""The primary client workflow: one large mixed PDF in, applicant packets out.

This is specification Test A. A single ~85-page source PDF holds a mixed run of
documents for fifteen different applicants -- application reports, resumes of
one to three pages, cover letters, references, transcripts, packets missing
their report, and separator pages. Nothing about the file says where one
applicant ends and the next begins.

The suite measures three separate accuracies, because they fail for different
reasons and a single number would hide which one broke:

* **document accuracy** -- were the right pages grouped into the right document?
* **packet accuracy** -- did each candidate's documents end up together?
* **page attribution** -- did any page land under the wrong person?

The last one is the metric that matters most. A document filed under the wrong
applicant is worse than one left in the review queue, because nobody goes
looking for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from app.models.candidate import normalize_person_name
from app.models.source_file import SourceFileAnalysis
from app.services.export_service import ExportService
from app.services.pdf_service import open_pdf
from scripts.mixed_batch import MixedBatch, build_mixed_batch


@dataclass
class Attribution:
    """How the pipeline's packets line up with the ground truth."""

    batch: MixedBatch
    analysis: SourceFileAnalysis

    @property
    def expected_page_owner(self) -> dict[int, str]:
        """1-based page number -> the name it should be filed under."""
        owners: dict[int, str] = {}
        for candidate in self.batch.candidates:
            for page in candidate.pages:
                owners[page] = normalize_person_name(candidate.name)
        return owners

    @property
    def actual_page_owner(self) -> dict[int, str]:
        owners: dict[int, str] = {}
        for packet in self.analysis.packets:
            if packet.is_unknown:
                continue
            name = normalize_person_name(packet.candidate.name or "")
            for index in packet.page_indexes:
                owners[index + 1] = name
        return owners

    @property
    def unknown_pages(self) -> set[int]:
        packet = self.analysis.unknown_packet
        if packet is None:
            return set()
        return {index + 1 for index in packet.page_indexes}

    def misattributed_pages(self) -> list[tuple[int, str, str]]:
        """``(page, expected_owner, actual_owner)`` for pages filed wrongly.

        Pages parked in the unknown queue are not counted as misattributed:
        they are visibly unresolved, which is the safe failure.
        """
        wrong: list[tuple[int, str, str]] = []
        actual = self.actual_page_owner
        for page, expected in self.expected_page_owner.items():
            if page in self.unknown_pages:
                continue
            got = actual.get(page)
            if got is None:
                continue
            if got != expected:
                wrong.append((page, expected, got))
        return wrong

    def recovered_candidates(self) -> set[str]:
        return {
            normalize_person_name(p.candidate.name or "")
            for p in self.analysis.identified_packets
            if p.candidate.name
        }

    def expected_candidates(self) -> set[str]:
        return {normalize_person_name(c.name) for c in self.batch.candidates}


@pytest.fixture(scope="module")
def batch() -> MixedBatch:
    return build_mixed_batch()


@pytest.fixture(scope="module")
def mixed_pdf(batch: MixedBatch, tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("mixed")
    return batch.write(directory)


@pytest.fixture(scope="module")
def analysis(pipeline_factory, mixed_pdf: Path) -> SourceFileAnalysis:
    """Analysed once: the file is large and the result is read-only here."""
    return pipeline_factory().analyze_file(mixed_pdf)


@pytest.fixture(scope="module")
def attribution(batch: MixedBatch, analysis: SourceFileAnalysis) -> Attribution:
    return Attribution(batch=batch, analysis=analysis)


class TestTheBatchItself:
    def test_it_is_the_size_the_client_actually_sends(self, batch: MixedBatch) -> None:
        assert 75 <= batch.page_count <= 95, f"{batch.page_count} pages"
        assert 15 <= len(batch.candidates) <= 20, f"{len(batch.candidates)} candidates"

    def test_it_contains_the_awkward_cases(self, batch: MixedBatch) -> None:
        """A batch of tidy identical packets would prove nothing."""
        resume_lengths = {
            len(document.pages)
            for candidate in batch.candidates
            for document in candidate.documents
            if document.document_type == "Resume"
        }
        assert len(resume_lengths) >= 2, "every resume is the same length"

        without_report = [
            c for c in batch.candidates
            if not any(d.document_type == "Application Report" for d in c.documents)
        ]
        assert without_report, "no candidate is missing an application report"
        assert batch.separator_pages, "no separator pages in the batch"


class TestAnalysis:
    def test_every_page_is_analysed(
        self, batch: MixedBatch, analysis: SourceFileAnalysis
    ) -> None:
        assert analysis.page_count == batch.page_count
        assert len(analysis.pages) == batch.page_count

    def test_documents_are_detected(
        self, batch: MixedBatch, analysis: SourceFileAnalysis
    ) -> None:
        """Roughly the right number of logical documents, not one per page."""
        assert len(analysis.groups) < batch.page_count, "pages were not grouped at all"
        assert len(analysis.groups) >= batch.document_count * 0.7

    def test_no_page_belongs_to_two_documents(self, analysis: SourceFileAnalysis) -> None:
        seen: set[int] = set()
        for group in analysis.groups:
            overlap = seen & set(group.page_indexes)
            assert not overlap, f"pages {sorted(overlap)} are in two documents"
            seen.update(group.page_indexes)

    def test_every_page_is_accounted_for(self, analysis: SourceFileAnalysis) -> None:
        covered = {index for group in analysis.groups for index in group.page_indexes}
        assert covered == {page.page_index for page in analysis.pages}


class TestCandidateReconstruction:
    def test_candidates_are_discovered(self, attribution: Attribution) -> None:
        expected = attribution.expected_candidates()
        recovered = attribution.recovered_candidates()
        missing = expected - recovered
        assert not missing, f"these applicants were never identified: {sorted(missing)}"

    def test_no_phantom_candidates(self, attribution: Attribution) -> None:
        """Inventing applicants is as damaging as losing them."""
        extra = attribution.recovered_candidates() - attribution.expected_candidates()
        assert not extra, f"invented applicants: {sorted(extra)}"

    def test_the_candidate_count_is_right(self, attribution: Attribution) -> None:
        assert attribution.analysis.candidate_count == len(attribution.batch.candidates)

    def test_no_page_is_filed_under_the_wrong_person(
        self, attribution: Attribution
    ) -> None:
        """The metric that matters: a misfiled page is one nobody will find."""
        wrong = attribution.misattributed_pages()
        assert not wrong, (
            f"{len(wrong)} pages were attributed to the wrong candidate: "
            f"{wrong[:10]}"
        )

    def test_packets_do_not_overlap(self, analysis: SourceFileAnalysis) -> None:
        seen: set[int] = set()
        for packet in analysis.packets:
            overlap = seen & set(packet.page_indexes)
            assert not overlap, f"pages {sorted(overlap)} are in two packets"
            seen.update(packet.page_indexes)

    def test_most_pages_are_confidently_attributed(
        self, attribution: Attribution
    ) -> None:
        """Leaving everything in review would technically pass the safety tests."""
        total = attribution.batch.page_count
        unresolved = len(attribution.unknown_pages)
        assert unresolved / total < 0.15, (
            f"{unresolved} of {total} pages were left unattributed"
        )


class TestPacketExport:
    @pytest.fixture(scope="class")
    def exported(self, analysis: SourceFileAnalysis, tmp_path_factory) -> Path:
        destination = tmp_path_factory.mktemp("mixed-export")
        service = ExportService(packet_order=("Application Report", "Resume", "Cover Letter"))
        result = service.export([analysis], destination)
        assert not result.errors, result.errors
        return destination

    def test_one_combined_packet_per_multi_document_candidate(
        self, exported: Path, analysis: SourceFileAnalysis
    ) -> None:
        """A candidate with a single document needs no combined copy of it."""
        written = list(exported.rglob("*_Complete_Packet.pdf"))
        expected = [
            p for p in analysis.identified_packets if len(p.ordered_documents(())) > 1
        ]
        assert len(written) == len(expected)

    def test_each_candidate_gets_a_folder(
        self, exported: Path, analysis: SourceFileAnalysis
    ) -> None:
        folders = {p.name for p in exported.iterdir() if p.is_dir()}
        for packet in analysis.identified_packets:
            assert packet.candidate.name in folders

    def test_a_combined_packet_holds_exactly_its_candidates_pages(
        self, exported: Path, analysis: SourceFileAnalysis
    ) -> None:
        for packet in analysis.identified_packets:
            documents = packet.ordered_documents(())
            if len(documents) < 2:
                continue  # written as the single document itself, not a packet
            path = (
                exported
                / packet.candidate.name
                / f"{packet.candidate.name.replace(' ', '_')}_Complete_Packet.pdf"
            )
            assert path.exists(), f"no combined packet for {packet.candidate.name}"
            expected_pages = sum(len(d.export_page_indexes) for d in documents)
            with open_pdf(path) as document:
                assert document.page_count == expected_pages, packet.candidate.name

    def test_combined_packets_keep_their_text_layer(
        self, exported: Path, analysis: SourceFileAnalysis
    ) -> None:
        """Combining must copy pages, never rasterise them."""
        packet = next(
            p for p in analysis.identified_packets if len(p.ordered_documents(())) > 1
        )
        path = (
            exported
            / packet.candidate.name
            / f"{packet.candidate.name.replace(' ', '_')}_Complete_Packet.pdf"
        )
        with open_pdf(path) as document:
            text = document.load_page(0).get_text("text")
        assert len(text.strip()) > 100, "the combined packet lost its searchable text"

    def test_the_unknown_queue_is_not_exported_as_a_candidate(
        self, exported: Path
    ) -> None:
        assert not (exported / "Unknown" / "Unknown_Complete_Packet.pdf").exists()


class TestProcessingSummary:
    def test_the_summary_numbers_are_self_consistent(
        self, analysis: SourceFileAnalysis
    ) -> None:
        """What the completion screen reports has to add up."""
        packet_documents = sum(len(p.documents) for p in analysis.packets)
        assert packet_documents == len(analysis.groups), (
            "some documents are in no packet at all"
        )
