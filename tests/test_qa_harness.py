"""The corpus evaluator, which is how accuracy on real client PDFs gets measured.

If this harness is wrong, every number it reports is wrong, and those numbers
are what will decide whether the product is good enough to use. So the scoring
is tested against cases whose answer is known by construction.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models.candidate import Candidate
from app.models.packet import CandidatePacket
from app.models.source_file import SourceFileAnalysis
from app.profiles.recruiting import APPLICATION_REPORT, COVER_LETTER, RESUME
from scripts.evaluate_corpus import (
    ExpectedDocument,
    ExpectedPacket,
    FileScore,
    load_ground_truth,
    score_packets,
)
from tests.test_packets import make_group


def build_analysis(packets: list[tuple[str | None, list]]) -> SourceFileAnalysis:
    """A finished analysis with the given packets, bypassing the pipeline."""
    analysis = SourceFileAnalysis(path=Path("memory.pdf"))
    for name, groups in packets:
        packet = CandidatePacket(
            source_pdf="memory.pdf",
            candidate=Candidate(name=name),
            is_unknown=name is None,
        )
        if name is None:
            packet.id = "packet-unknown"
        for group in groups:
            packet.add(group)
            group.association_confidence = 0.95
        analysis.packets.append(packet)
        analysis.groups.extend(groups)
    return analysis


def expected(name: str, spans: list[tuple[str, int, int]]) -> ExpectedPacket:
    return ExpectedPacket(
        name=name,
        documents=[ExpectedDocument(t, first, last) for t, first, last in spans],
    )


class TestGroundTruthFormats:
    def test_the_candidate_centric_shape_loads(self, tmp_path: Path) -> None:
        """The shape the specification asks for, and that labellers write."""
        path = tmp_path / "truth.json"
        path.write_text(
            json.dumps(
                {
                    "documents": {
                        "Applicants.pdf": {
                            "candidates": [
                                {
                                    "name": "Jane Smith",
                                    "documents": [
                                        {"type": "Application Report", "pages": [1, 2, 3]},
                                        {"type": "Resume", "pages": [4, 5]},
                                        {"type": "Cover Letter", "pages": [6]},
                                    ],
                                }
                            ]
                        }
                    }
                }
            )
        )

        documents, packets = load_ground_truth(path)
        assert len(documents["Applicants.pdf"]) == 3
        packet = packets["Applicants.pdf"][0]
        assert packet.name == "Jane Smith"
        assert packet.pages == {1, 2, 3, 4, 5, 6}
        assert all(d.candidate == "Jane Smith" for d in packet.documents)

    def test_the_older_flat_shape_still_loads(self, tmp_path: Path) -> None:
        """Existing label files must not stop working."""
        path = tmp_path / "truth.json"
        path.write_text(
            json.dumps(
                {
                    "documents": {
                        "One.pdf": {
                            "documents": [
                                {
                                    "type": "Resume",
                                    "start_page": 1,
                                    "end_page": 2,
                                    "candidate": "Jane Smith",
                                }
                            ]
                        }
                    }
                }
            )
        )

        documents, packets = load_ground_truth(path)
        assert documents["One.pdf"][0].pages == {1, 2}
        assert packets["One.pdf"][0].name == "Jane Smith"

    def test_non_contiguous_pages_are_rejected_clearly(self, tmp_path: Path) -> None:
        """Silently accepting these would corrupt every metric downstream."""
        path = tmp_path / "truth.json"
        path.write_text(
            json.dumps(
                {
                    "documents": {
                        "One.pdf": {
                            "candidates": [
                                {
                                    "name": "Jane Smith",
                                    "documents": [{"type": "Resume", "pages": [1, 2, 7]}],
                                }
                            ]
                        }
                    }
                }
            )
        )
        with pytest.raises(SystemExit, match="not contiguous"):
            load_ground_truth(path)

    def test_a_nameless_candidate_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "truth.json"
        path.write_text(
            json.dumps({"documents": {"One.pdf": {"candidates": [{"documents": []}]}}})
        )
        with pytest.raises(SystemExit, match="no name"):
            load_ground_truth(path)

    def test_the_committed_example_loads(self) -> None:
        documents, packets = load_ground_truth(Path("qa/expected.example.json"))
        assert documents, "the example ground truth is empty"
        mixed = packets.get("Applicants_2026.pdf")
        assert mixed and len(mixed) >= 15, "the example lost its mixed-batch entry"


class TestPacketScoring:
    def test_a_perfect_reconstruction_scores_full_marks(self) -> None:
        analysis = build_analysis(
            [
                ("Jane Smith", [make_group([0, 1, 2], APPLICATION_REPORT),
                                make_group([3, 4], RESUME)]),
                ("Robert Jones", [make_group([5, 6], RESUME)]),
            ]
        )
        truth = [
            expected("Jane Smith", [(APPLICATION_REPORT, 1, 3), (RESUME, 4, 5)]),
            expected("Robert Jones", [(RESUME, 6, 7)]),
        ]

        score = FileScore(name="x")
        score_packets(analysis, truth, score)

        assert score.packets_exact == 2
        assert score.association_correct == score.association_scored == 3
        assert score.false_merges == 0
        assert score.false_splits_candidate == 0

    def test_two_people_merged_into_one_packet_is_caught(self) -> None:
        """The worst failure mode: one person's documents buried in another's."""
        analysis = build_analysis(
            [("Jane Smith", [make_group([0], RESUME), make_group([1], RESUME)])]
        )
        truth = [
            expected("Jane Smith", [(RESUME, 1, 1)]),
            expected("Robert Jones", [(RESUME, 2, 2)]),
        ]

        score = FileScore(name="x")
        score_packets(analysis, truth, score)

        assert score.false_merges == 1
        assert score.packets_exact < 2

    def test_one_person_split_across_packets_is_caught(self) -> None:
        analysis = build_analysis(
            [
                ("Jane Smith", [make_group([0], APPLICATION_REPORT)]),
                ("Jane Smith ", [make_group([1], RESUME)]),
            ]
        )
        # Two packets both named Jane, so the pages are spread across them.
        analysis.packets[1].candidate = Candidate(name="Jane A Smith")
        truth = [expected("Jane Smith", [(APPLICATION_REPORT, 1, 1), (RESUME, 2, 2)])]

        score = FileScore(name="x")
        score_packets(analysis, truth, score)

        assert score.false_splits_candidate == 1
        assert score.packets_exact == 0

    def test_unlabelled_pages_do_not_count_against_a_packet(self) -> None:
        """Ground truth rarely labels separator sheets; owning one is not an error."""
        analysis = build_analysis(
            [("Jane Smith", [make_group([0, 1], RESUME), make_group([2], COVER_LETTER)])]
        )
        # Page 3 is deliberately unlabelled, as a separator page would be.
        truth = [expected("Jane Smith", [(RESUME, 1, 2)])]

        score = FileScore(name="x")
        score_packets(analysis, truth, score)

        assert score.packets_exact == 1, "an unlabelled page was scored as a mistake"

    def test_documents_in_the_unknown_queue_are_reported(self) -> None:
        unknown_group = make_group([2], COVER_LETTER)
        analysis = build_analysis(
            [("Jane Smith", [make_group([0, 1], RESUME)]), (None, [unknown_group])]
        )
        truth = [expected("Jane Smith", [(RESUME, 1, 2)])]

        score = FileScore(name="x")
        score_packets(analysis, truth, score)

        assert score.unknown_documents == 1
        assert score.predicted_packets == 1, "the unknown queue was counted as a person"

    def test_low_confidence_attributions_are_reported_separately(self) -> None:
        """Attributed-but-flagged is different work from unassigned."""
        flagged = make_group([2], COVER_LETTER)
        analysis = build_analysis(
            [("Jane Smith", [make_group([0, 1], RESUME), flagged])]
        )
        flagged.association_review = True
        truth = [expected("Jane Smith", [(RESUME, 1, 2), (COVER_LETTER, 3, 3)])]

        score = FileScore(name="x")
        score_packets(analysis, truth, score)

        assert score.low_confidence_documents == 1
        assert score.unknown_documents == 0
