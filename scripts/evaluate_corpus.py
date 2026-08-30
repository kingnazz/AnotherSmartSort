"""Measure classification and grouping accuracy against a labelled corpus.

Synthetic tests prove the pipeline behaves as designed. They cannot tell you how
it performs on a client's real applicant packets — only labelled real documents
can do that. This is the harness for that measurement.

    AS_RESUME_SORTER_PRIVATE_QA_DIR=/private/path python -m scripts.evaluate_corpus
    AS_RESUME_SORTER_PRIVATE_QA_DIR=/private/path python -m scripts.evaluate_corpus --json report.json
    AS_RESUME_SORTER_PRIVATE_QA_DIR=/private/path python -m scripts.evaluate_corpus --make-template

Ground truth format (see qa/expected.example.json). Label by applicant, which
is how a mixed batch reads to a person working through it::

    {
      "documents": {
        "Applicants_2026.pdf": {
          "candidates": [
            {
              "name": "Jane Smith",
              "documents": [
                {"type": "Application Report", "pages": [1, 2, 3]},
                {"type": "Resume", "pages": [4, 5]},
                {"type": "Cover Letter", "pages": [6]}
              ]
            }
          ]
        }
      }
    }

The older flat form -- a list of documents each carrying a ``candidate`` name
and ``start_page``/``end_page`` -- still loads, so existing label files keep
working.

Client PDFs and labels must live outside the repository. Set
``AS_RESUME_SORTER_PRIVATE_QA_DIR`` to that private directory; this script reads
the files in place and never copies document text into its report.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.candidate import normalize_person_name  # noqa: E402
from app.models.enums import FileStatus  # noqa: E402
from app.services.app_services import build_analysis_services  # noqa: E402
from app.services.file_discovery import discover_pdfs  # noqa: E402
from app.storage.settings_store import AppSettings  # noqa: E402


@dataclass
class ExpectedDocument:
    """One labelled document: a type and an inclusive 1-based page range."""

    document_type: str
    start_page: int
    end_page: int
    candidate: str | None = None

    @property
    def pages(self) -> set[int]:
        return set(range(self.start_page, self.end_page + 1))


@dataclass
class ExpectedPacket:
    """One labelled applicant: a name and the documents that belong to them."""

    name: str
    documents: list[ExpectedDocument] = field(default_factory=list)

    @property
    def pages(self) -> set[int]:
        pages: set[int] = set()
        for document in self.documents:
            pages |= document.pages
        return pages


@dataclass
class FileScore:
    """Per-file measurements."""

    name: str
    page_count: int = 0
    expected_documents: int = 0
    predicted_documents: int = 0

    pages_correct_type: int = 0
    pages_scored: int = 0

    boundaries_correct: int = 0
    boundaries_scored: int = 0

    documents_exact: int = 0
    false_splits: int = 0
    missed_splits: int = 0

    candidates_correct: int = 0
    candidates_scored: int = 0

    # -- candidate packet reconstruction ---------------------------------
    expected_packets: int = 0
    predicted_packets: int = 0
    #: Expected applicants whose pages we recovered exactly.
    packets_exact: int = 0
    #: Expected documents attributed to the right applicant.
    association_correct: int = 0
    association_scored: int = 0
    #: One predicted packet holding pages from two different real applicants.
    false_merges: int = 0
    #: One real applicant's pages spread across several predicted packets.
    false_splits_candidate: int = 0
    #: Documents parked in the unknown queue for a human to assign.
    unknown_documents: int = 0
    #: Documents attributed to somebody, but not confidently enough to skip review.
    low_confidence_documents: int = 0

    review_documents: int = 0
    error: str | None = None


@dataclass
class CorpusReport:
    """Aggregate metrics across the corpus."""

    files: list[FileScore] = field(default_factory=list)
    seconds: float = 0.0
    provider: str = "Rules Only"

    # -- aggregates ------------------------------------------------------
    def _sum(self, attribute: str) -> int:
        return sum(getattr(score, attribute) for score in self.files)

    @property
    def analysed_files(self) -> list[FileScore]:
        return [score for score in self.files if score.error is None]

    @property
    def page_type_accuracy(self) -> float | None:
        return _ratio(self._sum("pages_correct_type"), self._sum("pages_scored"))

    @property
    def boundary_accuracy(self) -> float | None:
        return _ratio(self._sum("boundaries_correct"), self._sum("boundaries_scored"))

    @property
    def document_accuracy(self) -> float | None:
        return _ratio(self._sum("documents_exact"), self._sum("expected_documents"))

    @property
    def candidate_accuracy(self) -> float | None:
        return _ratio(self._sum("candidates_correct"), self._sum("candidates_scored"))

    @property
    def review_rate(self) -> float | None:
        return _ratio(self._sum("review_documents"), self._sum("predicted_documents"))

    @property
    def packet_accuracy(self) -> float | None:
        """Applicants whose packet we reconstructed exactly.

        The headline metric for the primary workflow: not "did we read this
        page correctly" but "did this person's documents end up together".
        """
        return _ratio(self._sum("packets_exact"), self._sum("expected_packets"))

    @property
    def association_accuracy(self) -> float | None:
        return _ratio(self._sum("association_correct"), self._sum("association_scored"))

    @property
    def unknown_rate(self) -> float | None:
        return _ratio(self._sum("unknown_documents"), self._sum("predicted_documents"))

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "seconds": round(self.seconds, 2),
            "files_total": len(self.files),
            "files_failed": len(self.files) - len(self.analysed_files),
            "pages": self._sum("page_count"),
            "documents_expected": self._sum("expected_documents"),
            "documents_predicted": self._sum("predicted_documents"),
            "packets_expected": self._sum("expected_packets"),
            "packets_predicted": self._sum("predicted_packets"),
            "metrics": {
                "page_type_accuracy": _round(self.page_type_accuracy),
                "boundary_accuracy": _round(self.boundary_accuracy),
                "document_accuracy": _round(self.document_accuracy),
                "candidate_accuracy": _round(self.candidate_accuracy),
                "candidate_packet_accuracy": _round(self.packet_accuracy),
                "document_association_accuracy": _round(self.association_accuracy),
                "review_rate": _round(self.review_rate),
                "unknown_rate": _round(self.unknown_rate),
            },
            "false_splits": self._sum("false_splits"),
            "missed_splits": self._sum("missed_splits"),
            "false_candidate_merges": self._sum("false_merges"),
            "false_candidate_splits": self._sum("false_splits_candidate"),
            "unknown_assignments": self._sum("unknown_documents"),
            "low_confidence_assignments": self._sum("low_confidence_documents"),
            "files": [
                {
                    "name": score.name,
                    "pages": score.page_count,
                    "expected": score.expected_documents,
                    "predicted": score.predicted_documents,
                    "exact": score.documents_exact,
                    "false_splits": score.false_splits,
                    "missed_splits": score.missed_splits,
                    "review": score.review_documents,
                    "error": score.error,
                }
                for score in self.files
            ],
        }


def _ratio(numerator: int, denominator: int) -> float | None:
    return (numerator / denominator) if denominator else None


def _round(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _percent(value: float | None) -> str:
    return f"{value * 100:5.1f}%" if value is not None else "    n/a"


# ----------------------------------------------------------------------
# Ground truth
# ----------------------------------------------------------------------

def _parse_document(item: dict, filename: str, candidate: str | None) -> ExpectedDocument:
    """Read one labelled document in either supported shape.

    A document may give ``start_page``/``end_page`` or an explicit ``pages``
    list. The list form is what a person labelling a large mixed batch by hand
    will naturally write, so both are accepted.
    """
    try:
        if "pages" in item:
            pages = sorted(int(page) for page in item["pages"])
            if not pages:
                raise ValueError("a document needs at least one page")
            start, end = pages[0], pages[-1]
            if pages != list(range(start, end + 1)):
                raise ValueError(
                    f"pages {pages} are not contiguous; split them into separate documents"
                )
        else:
            start, end = int(item["start_page"]), int(item["end_page"])
        return ExpectedDocument(
            document_type=str(item["type"]),
            start_page=start,
            end_page=end,
            candidate=item.get("candidate", candidate),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"Malformed entry for {filename}: {exc}")


def load_ground_truth(
    path: Path,
) -> tuple[dict[str, list[ExpectedDocument]], dict[str, list[ExpectedPacket]]]:
    """Load labels, returning both the flat documents and the packets.

    Two shapes are accepted per file. The candidate-centric one is the shape
    the real workflow produces, because a labeller working through a mixed
    batch thinks in people::

        {"Applicants.pdf": {"candidates": [
            {"name": "Jane Smith", "documents": [
                {"type": "Resume", "pages": [4, 5]}]}]}}

    The older flat list of documents still loads, so existing label files keep
    working; packet metrics are simply derived from each document's
    ``candidate`` field in that case.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Could not read the ground truth file: {exc}")

    documents = raw.get("documents", raw)
    if not isinstance(documents, dict):
        raise SystemExit("Ground truth must be an object keyed by PDF filename.")

    expected: dict[str, list[ExpectedDocument]] = {}
    packets: dict[str, list[ExpectedPacket]] = {}

    for filename, entry in documents.items():
        parsed: list[ExpectedDocument] = []
        file_packets: list[ExpectedPacket] = []

        if isinstance(entry, dict) and "candidates" in entry:
            for candidate_entry in entry["candidates"]:
                name = str(candidate_entry.get("name") or "").strip()
                if not name:
                    raise SystemExit(f"A candidate in {filename} has no name.")
                packet = ExpectedPacket(name=name)
                for item in candidate_entry.get("documents", []):
                    document = _parse_document(item, filename, name)
                    packet.documents.append(document)
                    parsed.append(document)
                packet.documents.sort(key=lambda d: d.start_page)
                file_packets.append(packet)
        else:
            items = entry.get("documents", []) if isinstance(entry, dict) else entry
            for item in items:
                parsed.append(_parse_document(item, filename, None))
            by_name: dict[str, ExpectedPacket] = {}
            for document in parsed:
                if not document.candidate:
                    continue
                key = normalize_person_name(document.candidate)
                packet = by_name.setdefault(
                    key, ExpectedPacket(name=document.candidate)
                )
                packet.documents.append(document)
            file_packets = list(by_name.values())

        expected[filename] = sorted(parsed, key=lambda d: d.start_page)
        packets[filename] = file_packets

    return expected, packets


def make_template(input_dir: Path, settings: AppSettings) -> dict:
    """Analyse a folder and emit the predictions as an editable ground truth.

    Labelling a corpus by hand is tedious; start from what the pipeline
    predicted, then correct it. Always review the result before trusting it —
    an unreviewed template measures the classifier against itself.
    """
    services = build_analysis_services(settings)
    try:
        documents: dict[str, dict] = {}
        for pdf in discover_pdfs([input_dir]):
            analysis = services.pipeline.analyze_file(pdf)
            candidates = []
            for packet in analysis.packets:
                candidates.append(
                    {
                        "name": packet.candidate.name or "UNKNOWN - name this person",
                        "documents": [
                            {
                                "type": group.document_type,
                                "pages": [i + 1 for i in group.page_indexes],
                            }
                            for group in packet.documents
                        ],
                    }
                )
            documents[pdf.name] = {
                "_review_this": "Predictions, not verified. Correct them by hand.",
                "candidates": candidates,
            }
        return {"documents": documents}
    finally:
        services.close()


# ----------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------

def score_packets(
    analysis, expected_packets: list[ExpectedPacket], score: FileScore
) -> None:
    """Measure candidate packet reconstruction: who did each document go to?

    This is scored on *pages* rather than on document spans, so a boundary
    mistake is not double-counted as an attribution mistake. The question here
    is only whether a page ended up under the right person.
    """
    score.expected_packets = len(expected_packets)
    score.predicted_packets = len(analysis.identified_packets)

    unknown = analysis.unknown_packet
    score.unknown_documents = len(unknown.documents) if unknown else 0
    score.low_confidence_documents = sum(
        1
        for group in analysis.groups
        if group.association_review and group.packet_id != (unknown.id if unknown else None)
    )

    if not expected_packets:
        return

    # page -> the applicant it should belong to, and the one it went to
    wanted_owner: dict[int, str] = {}
    for packet in expected_packets:
        key = normalize_person_name(packet.name)
        for page in packet.pages:
            wanted_owner[page] = key

    actual_owner: dict[int, str] = {}
    pages_by_predicted: dict[str, set[int]] = {}
    for packet in analysis.identified_packets:
        key = normalize_person_name(packet.candidate.name or "")
        pages = {index + 1 for index in packet.page_indexes}
        pages_by_predicted[packet.id] = pages
        for page in pages:
            actual_owner[page] = key

    # -- per-document association ---------------------------------------
    for packet in expected_packets:
        key = normalize_person_name(packet.name)
        for document in packet.documents:
            score.association_scored += 1
            owners = {actual_owner.get(page) for page in document.pages}
            owners.discard(None)
            if owners == {key}:
                score.association_correct += 1

    # -- whole-packet accuracy ------------------------------------------
    # Scored only over pages the labeller actually labelled. Ground truth
    # rarely covers every page of a real batch -- separator sheets, cover
    # pages and blanks are usually left out -- and attributing one of those to
    # a candidate is not a mistake. Page type accuracy already skips unlabelled
    # pages; this applies the same rule so the two agree.
    labelled = set(wanted_owner)
    recovered_pages: dict[str, set[int]] = {}
    for packet in analysis.identified_packets:
        key = normalize_person_name(packet.candidate.name or "")
        recovered_pages.setdefault(key, set()).update(
            {index + 1 for index in packet.page_indexes if index + 1 in labelled}
        )

    for packet in expected_packets:
        key = normalize_person_name(packet.name)
        if recovered_pages.get(key) == packet.pages:
            score.packets_exact += 1

    # -- false merges: one predicted packet spanning two real applicants ---
    for pages in pages_by_predicted.values():
        owners = {wanted_owner.get(page) for page in pages}
        owners.discard(None)
        if len(owners) > 1:
            score.false_merges += 1

    # -- false splits: one real applicant spread over several packets ------
    for packet in expected_packets:
        holding = {
            packet_id
            for packet_id, pages in pages_by_predicted.items()
            if pages & packet.pages
        }
        if len(holding) > 1:
            score.false_splits_candidate += 1


def score_file(
    analysis,
    expected: list[ExpectedDocument],
    expected_packets: list[ExpectedPacket] | None = None,
) -> FileScore:
    """Compare one file's predictions against its labels."""
    score = FileScore(name=analysis.name, page_count=analysis.page_count)
    score.expected_documents = len(expected)

    if analysis.status is FileStatus.ERROR:
        score.error = analysis.error
        return score

    score.predicted_documents = len(analysis.groups)
    score.review_documents = analysis.review_group_count

    # -- page-level type accuracy ---------------------------------------
    expected_type_by_page: dict[int, str] = {}
    for document in expected:
        for page in document.pages:
            expected_type_by_page[page] = document.document_type

    for page in analysis.pages:
        wanted = expected_type_by_page.get(page.page_number)
        if wanted is None:
            continue
        score.pages_scored += 1
        if page.predicted_type == wanted:
            score.pages_correct_type += 1

    # -- boundary accuracy ----------------------------------------------
    # For every page after the first, did we agree about "starts a new document"?
    expected_starts = {document.start_page for document in expected}
    for page in analysis.pages:
        if page.page_number == 1 or page.page_number not in expected_type_by_page:
            continue
        score.boundaries_scored += 1
        if page.starts_new_document == (page.page_number in expected_starts):
            score.boundaries_correct += 1

    # -- whole-document accuracy ----------------------------------------
    predicted_ranges = {
        (group.start_page, group.end_page): group for group in analysis.groups
    }
    expected_ranges = {(d.start_page, d.end_page): d for d in expected}

    for span, document in expected_ranges.items():
        group = predicted_ranges.get(span)
        if group is not None and group.document_type == document.document_type:
            score.documents_exact += 1

    # A false split: a predicted document sits strictly inside an expected one.
    for span in predicted_ranges:
        for expected_span in expected_ranges:
            if span == expected_span:
                continue
            if span[0] >= expected_span[0] and span[1] <= expected_span[1]:
                score.false_splits += 1
                break

    # A missed split: an expected boundary that we did not predict.
    predicted_starts = {group.start_page for group in analysis.groups}
    score.missed_splits = len(expected_starts - predicted_starts - {1})

    # -- candidate identity ---------------------------------------------
    for span, document in expected_ranges.items():
        if not document.candidate:
            continue
        score.candidates_scored += 1
        group = predicted_ranges.get(span)
        if group is None or not group.candidate.name:
            continue
        if normalize_person_name(group.candidate.name) == normalize_person_name(
            document.candidate
        ):
            score.candidates_correct += 1

    score_packets(analysis, expected_packets or [], score)
    return score


def evaluate(
    input_dir: Path,
    ground_truth: dict[str, list[ExpectedDocument]],
    settings: AppSettings,
    packets: dict[str, list[ExpectedPacket]] | None = None,
) -> CorpusReport:
    services = build_analysis_services(settings)
    report = CorpusReport(provider=services.provider_name)
    started = time.monotonic()

    try:
        pdfs = discover_pdfs([input_dir])
        if not pdfs:
            raise SystemExit(f"No PDFs were found in {input_dir}")

        unlabelled: list[str] = []
        for pdf in pdfs:
            expected = ground_truth.get(pdf.name)
            if expected is None:
                unlabelled.append(pdf.name)
                continue
            analysis = services.pipeline.analyze_file(pdf)
            report.files.append(
                score_file(analysis, expected, (packets or {}).get(pdf.name, []))
            )

        if unlabelled:
            print(f"  Skipped {len(unlabelled)} unlabelled PDF(s):")
            for name in unlabelled[:10]:
                print(f"    {name}")
            if len(unlabelled) > 10:
                print(f"    ... and {len(unlabelled) - 10} more")
    finally:
        services.close()

    report.seconds = time.monotonic() - started
    return report


def render(report: CorpusReport) -> str:
    lines = [
        "",
        "Corpus evaluation",
        "=================",
        f"  Provider         : {report.provider}",
        f"  Files scored     : {len(report.analysed_files)} of {len(report.files)}",
        f"  Pages            : {report._sum('page_count')}",
        f"  Documents        : {report._sum('predicted_documents')} predicted, "
        f"{report._sum('expected_documents')} expected",
        f"  Elapsed          : {report.seconds:.1f}s",
        "",
        "Accuracy",
        "--------",
        f"  Page type        : {_percent(report.page_type_accuracy)}",
        f"  Boundary         : {_percent(report.boundary_accuracy)}",
        f"  Whole document   : {_percent(report.document_accuracy)}",
        f"  Candidate name   : {_percent(report.candidate_accuracy)}",
        "",
        "Candidate packets  (the metric that matters most)",
        "-------------------------------------------------",
        f"  Packets recovered: {_percent(report.packet_accuracy)} "
        f"({report._sum('packets_exact')} of {report._sum('expected_packets')} applicants)",
        f"  Doc association  : {_percent(report.association_accuracy)}",
        f"  False merges     : {report._sum('false_merges')}"
        "   (two applicants combined into one packet)",
        f"  False splits     : {report._sum('false_splits_candidate')}"
        "   (one applicant spread over several packets)",
        "",
        "Workload",
        "--------",
        f"  Needing review   : {_percent(report.review_rate)} "
        f"({report._sum('review_documents')} of {report._sum('predicted_documents')})",
        f"  Unassigned       : {report._sum('unknown_documents')} document(s) "
        "awaiting a candidate",
        f"  Low confidence   : {report._sum('low_confidence_documents')} attributed "
        "but flagged",
        f"  False splits     : {report._sum('false_splits')}",
        f"  Missed splits    : {report._sum('missed_splits')}",
    ]

    problems = [
        score
        for score in report.files
        if score.error
        or score.false_splits
        or score.missed_splits
        or score.false_merges
        or score.false_splits_candidate
        or score.packets_exact < score.expected_packets
        or score.documents_exact < score.expected_documents
    ]
    if problems:
        lines += ["", "Files needing attention", "-----------------------"]
        for score in problems[:25]:
            if score.error:
                lines.append(f"  {score.name}: ERROR - {score.error}")
            else:
                lines.append(
                    f"  {score.name}: "
                    f"{score.packets_exact}/{score.expected_packets} packets, "
                    f"{score.documents_exact}/{score.expected_documents} documents"
                    f", {score.false_merges} merge(s)"
                    f", {score.false_splits_candidate} candidate split(s)"
                    f", {score.false_splits} false split(s)"
                    f", {score.missed_splits} missed split(s)"
                )
        if len(problems) > 25:
            lines.append(f"  ... and {len(problems) - 25} more")

    lines.append("")
    return "\n".join(lines)


# ----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--input",
        help="Folder of PDFs to evaluate (defaults to AS_RESUME_SORTER_PRIVATE_QA_DIR).",
    )
    parser.add_argument(
        "--ground-truth",
        help="JSON labels (defaults to expected.json in the private QA directory).",
    )
    parser.add_argument("--json", help="Also write the full report to this JSON file.")
    parser.add_argument(
        "--make-template",
        nargs="?",
        const="",
        metavar="INPUT_DIR",
        help=(
            "Analyse a folder and print a template "
            "(defaults to the private QA directory)."
        ),
    )
    parser.add_argument(
        "--provider",
        choices=["rules", "openai", "ollama"],
        default="rules",
        help="Intelligence provider to evaluate with (default: rules).",
    )
    arguments = parser.parse_args(argv)

    settings = AppSettings()
    settings.provider = arguments.provider

    private_qa_dir = os.environ.get("AS_RESUME_SORTER_PRIVATE_QA_DIR")

    if arguments.make_template is not None:
        template_input = arguments.make_template or private_qa_dir
        if not template_input:
            parser.error(
                "--make-template needs INPUT_DIR or AS_RESUME_SORTER_PRIVATE_QA_DIR"
            )
        template = make_template(Path(template_input), settings)
        print(json.dumps(template, indent=2))
        return 0

    input_value = arguments.input or private_qa_dir
    ground_truth_value = arguments.ground_truth
    if not ground_truth_value and private_qa_dir:
        ground_truth_value = str(Path(private_qa_dir) / "expected.json")
    if not input_value or not ground_truth_value:
        parser.error(
            "--input and --ground-truth are required unless "
            "AS_RESUME_SORTER_PRIVATE_QA_DIR is set"
        )

    input_dir = Path(input_value)
    if not input_dir.is_dir():
        raise SystemExit(f"Input folder not found: {input_dir}")

    ground_truth, expected_packets = load_ground_truth(Path(ground_truth_value))
    print(f"Evaluating {input_dir} against {len(ground_truth)} labelled file(s)…")

    report = evaluate(input_dir, ground_truth, settings, expected_packets)
    print(render(report))

    if arguments.json:
        Path(arguments.json).write_text(
            json.dumps(report.to_dict(), indent=2), encoding="utf-8"
        )
        print(f"  Full report written to {arguments.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
