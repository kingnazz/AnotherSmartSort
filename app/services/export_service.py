"""Exporting document groups to disk.

Two output layouts are supported. The default, document-type-first layout
(``group_by_document_type=True``) is what the product now ships with -- take
a batch of ATS applicant PDFs and put the useful attachments in folders by
type::

    Output/
        Resumes/
            Marcus Delgado.pdf
            Sofia Brennan.pdf
        Cover Letters/
            Marcus Delgado.pdf
        Application Reports/
            Marcus Delgado.pdf
            Sofia Brennan.pdf
        Needs Review/
            Unknown_001.pdf

The older, candidate-first layout (``folder_per_candidate=True``,
``group_by_document_type=False``) groups a candidate's documents together
instead::

    Output/
        Benjamin Perez/
            Benjamin_Perez_Application_Report.pdf
            Benjamin_Perez_Resume.pdf
        Unknown/
            Unknown_Resume_001.pdf

With ``batch_folder=True`` -- which is how Sort & Save runs -- that whole
structure is placed inside a folder named for the moment the run started::

    Output/
        2026-08-26_10-32-AM/
            Resumes/
            Cover Letters/

One run, one folder, however many PDFs went into it. Without it two runs into
the same directory interleave, and afterwards there is no way to tell which
resume came from which batch.

Nothing is ever silently overwritten: a colliding name becomes ``..._2.pdf``.
Names are sanitised for Windows, and every PDF is written atomically so a
cancelled run leaves no corrupt files behind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Sequence

from app.models.candidate import Candidate
from app.models.document import DocumentGroup
from app.models.enums import FileStatus
from app.models.packet import CandidatePacket
from app.models.processing_job import ProcessingJob
from app.models.source_file import SourceFileAnalysis
from app.profiles.base import OTHER
from app.services.pdf_service import PdfError, combine_pdf, split_pdf
from app.services.processing_service import CancellationToken
from app.utils.filenames import (
    DEFAULT_TEMPLATE,
    render_filename_template,
    sanitize_filename,
    sanitize_folder_name,
    unique_path,
)
from app.utils.logging_setup import get_logger, log_event

logger = get_logger("export")

UNKNOWN_FOLDER = "Unknown"
#: Suffix for the single-file version of a candidate's whole packet.
PACKET_SUFFIX = "Complete_Packet"
#: Destination for anything still flagged when it is exported, regardless of
#: its predicted type -- a dedicated pile to check by hand rather than mixed
#: in with the documents that are already known to be right.
NEEDS_REVIEW_FOLDER = "Needs Review"

#: How many suffixed names to try before giving up on finding a free one.
#: Only reached if something else is creating folders as fast as we are, which
#: means the directory is not ours to write into.
_MAX_BATCH_ATTEMPTS = 1000


def batch_folder_name(moment: datetime) -> str:
    """Name of the run folder for an export starting at ``moment``.

    ``2026-08-26_10-32-AM`` -- sortable date first, then a twelve-hour clock,
    because the people reading these folders read clocks that way. The
    meridiem is derived rather than taken from ``%p``, which follows the C
    locale and can come back lowercase or empty on a machine set to one that
    has no such concept.

    Nothing here needs sanitising: no colons, no separators, nothing Windows
    reserves.
    """
    hour = moment.hour % 12 or 12
    meridiem = "AM" if moment.hour < 12 else "PM"
    return f"{moment:%Y-%m-%d}_{hour:02d}-{moment:%M}-{meridiem}"


def create_batch_directory(base: Path, *, moment: datetime | None = None) -> Path:
    """Create a directory that belongs to exactly one export run.

    Two runs in the same minute share a name, so the folder is *created* here
    rather than checked and created later: ``mkdir`` without ``exist_ok`` is
    atomic, so the loser of that race gets ``FileExistsError`` and moves on to
    ``… (2)``. Checking first and creating after would let both runs believe
    they owned the same folder and interleave their output, which is the one
    outcome this whole change exists to prevent.
    """
    name = batch_folder_name(moment or datetime.now())
    for attempt in range(1, _MAX_BATCH_ATTEMPTS + 1):
        candidate = base / (name if attempt == 1 else f"{name} ({attempt})")
        try:
            candidate.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        return candidate
    raise OSError(f"Could not find an unused run folder name in {base}")


def _type_folder_name(document_type: str) -> str:
    """The plural folder a document type is filed under (``Resume`` -> ``Resumes/``)."""
    if document_type == OTHER:
        return OTHER
    return document_type if document_type.endswith("s") else f"{document_type}s"


@dataclass
class ExportedDocument:
    """One document group successfully written to disk."""

    group: DocumentGroup
    source_pdf: Path
    output_path: Path
    page_numbers: list[int] = field(default_factory=list)


@dataclass
class ExportedPacket:
    """One candidate's combined packet PDF."""

    packet: CandidatePacket
    source_pdf: Path
    output_path: Path
    document_count: int = 0
    page_numbers: list[int] = field(default_factory=list)


@dataclass
class ExportResult:
    """Outcome of an export run."""

    output_directory: Path
    exported: list[ExportedDocument] = field(default_factory=list)
    packets: list[ExportedPacket] = field(default_factory=list)
    skipped: list[tuple[DocumentGroup, str]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    cancelled: bool = False

    @property
    def document_count(self) -> int:
        return len(self.exported)

    @property
    def packet_count(self) -> int:
        return len(self.packets)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


ExportProgressCallback = Callable[[int, int, str], None]


class ExportService:
    """Writes reviewed document groups out as individual PDFs."""

    def __init__(
        self,
        *,
        filename_template: str = DEFAULT_TEMPLATE,
        folder_per_candidate: bool = True,
        group_by_document_type: bool = False,
        export_separate_documents: bool = True,
        export_combined_packets: bool = True,
        packet_order: tuple[str, ...] = (),
        document_types: Sequence[str] = (),
        batch_folder: bool = False,
    ) -> None:
        self.filename_template = filename_template or DEFAULT_TEMPLATE
        #: Put this run's output in its own timestamped folder inside the
        #: chosen directory, so two Sort & Save runs never mix. Off by default
        #: so that calling ``export`` with a path still writes to exactly that
        #: path; the application turns it on in ``build_export_service``,
        #: which is where "a run" is a meaningful unit.
        self.batch_folder = batch_folder
        self.folder_per_candidate = folder_per_candidate
        #: Folder by document type (Resumes/, Cover Letters/, ...) instead of
        #: by candidate. Takes priority over ``folder_per_candidate`` when
        #: both are set, since the two are mutually exclusive layouts.
        self.group_by_document_type = group_by_document_type
        self.export_separate_documents = export_separate_documents
        self.export_combined_packets = export_combined_packets
        self.packet_order = tuple(packet_order)
        #: Types to write out. Empty means everything.
        self.document_types = tuple(document_types)

    def wants(self, document: DocumentGroup) -> bool:
        """Whether a document is one of the types being exported.

        Filtering happens here, at the point of writing, rather than during
        analysis: every document is still detected, grouped and reviewable, so
        narrowing the output to resumes does not hide what else was found or
        require re-analysing to change your mind.
        """
        if not self.document_types:
            return True
        return document.document_type in self.document_types

    # ------------------------------------------------------------------
    def export(
        self,
        files: Sequence[SourceFileAnalysis],
        output_directory: str | Path,
        *,
        job: ProcessingJob | None = None,
        on_progress: ExportProgressCallback | None = None,
        token: CancellationToken | None = None,
    ) -> ExportResult:
        """Export every included document group from ``files``."""
        token = CancellationToken() if token is None else token
        base = Path(output_directory)
        destination = base
        result = ExportResult(output_directory=destination)

        # Decide what there is to write before creating anything. A run with
        # nothing to export must not leave a timestamped folder behind that
        # looks like it holds results.
        usable = [file for file in files if file.status is not FileStatus.ERROR]
        pending = (
            [
                (file, group)
                for file in usable
                for group in file.groups
                if not group.excluded and self.wants(group)
            ]
            if self.export_separate_documents
            else []
        )
        packet_work = (
            [
                (file, packet)
                for file in usable
                for packet in file.packets
                if not packet.is_unknown and self._packet_documents(packet)
            ]
            if self.export_combined_packets
            else []
        )
        total = len(pending) + len(packet_work)

        try:
            base.mkdir(parents=True, exist_ok=True)
            if self.batch_folder and total:
                destination = create_batch_directory(base)
                result.output_directory = destination
        except OSError as exc:
            message = f"The output folder could not be created: {exc.strerror or exc}"
            result.errors.append((str(base), message))
            if job is not None:
                job.add_error(base, message)
            return result

        # Reserve names across the whole run so one export never collides with itself.
        taken: set[str] = set()
        sequence_by_folder: dict[str, int] = {}

        for index, (file, group) in enumerate(pending):
            if token.is_cancelled:
                result.cancelled = True
                break

            if on_progress is not None:
                on_progress(index, total, file.name)

            pages = group.export_page_indexes
            if not pages:
                result.skipped.append((group, "Every page in this document was excluded."))
                continue

            try:
                output_path = self._write_group(
                    file, group, destination, taken, sequence_by_folder
                )
            except PdfError as exc:
                result.errors.append((file.name, exc.message))
                if job is not None:
                    job.add_error(file.path, exc.message)
                logger.warning("Export failed for %s: %s", file.name, exc.message)
                continue
            except OSError as exc:
                message = f"{file.name} could not be exported: {exc.strerror or exc}"
                result.errors.append((file.name, message))
                if job is not None:
                    job.add_error(file.path, message)
                continue

            group.exported_path = str(output_path)
            group.output_filename = output_path.name
            result.exported.append(
                ExportedDocument(
                    group=group,
                    source_pdf=file.path,
                    output_path=output_path,
                    page_numbers=[i + 1 for i in pages],
                )
            )
            log_event(
                logger,
                "document.exported",
                source=file.name,
                pages=len(pages),
                document_type=group.document_type,
                output=output_path.name,
            )

        if not result.cancelled:
            self._export_packets(
                packet_work,
                destination,
                taken,
                result,
                job=job,
                on_progress=on_progress,
                token=token,
                offset=len(pending),
                total=total,
            )

        if on_progress is not None:
            on_progress(total, total, "")

        touched = {document.source_pdf for document in result.exported}
        touched.update(packet.source_pdf for packet in result.packets)
        for file in files:
            if file.status is not FileStatus.ERROR and file.path in touched:
                file.status = FileStatus.COMPLETED

        if job is not None:
            job.documents_exported += len(result.exported)
            job.packets_exported += len(result.packets)
            job.output_directory = str(destination)

        return result

    # ------------------------------------------------------------------
    def _export_packets(
        self,
        packet_work: list[tuple[SourceFileAnalysis, CandidatePacket]],
        destination: Path,
        taken: set[str],
        result: ExportResult,
        *,
        job: ProcessingJob | None,
        on_progress: ExportProgressCallback | None,
        token: CancellationToken,
        offset: int,
        total: int,
    ) -> None:
        """Write one combined PDF per candidate, in the configured order."""
        for index, (file, packet) in enumerate(packet_work):
            if token.is_cancelled:
                result.cancelled = True
                return

            if on_progress is not None:
                on_progress(offset + index, total, f"{packet.display_name} (packet)")

            documents = self._packet_documents(packet)
            if len(documents) == 1 and self.export_separate_documents:
                # The combined file would be a byte-for-byte duplicate of the
                # single document already being written beside it. Common once
                # the output is narrowed to one type, where every "complete
                # packet" would just be a second copy of the resume.
                continue
            sections = [(file.path, document.export_page_indexes) for document in documents]
            page_numbers = [
                index_ + 1 for _source, indexes in sections for index_ in indexes
            ]

            folder = self._packet_folder(packet, destination)
            folder.mkdir(parents=True, exist_ok=True)
            stem = f"{self._packet_stem(packet)}_{PACKET_SUFFIX}"
            output_path = unique_path(folder, stem, ".pdf", taken=taken)

            try:
                combine_pdf(sections, output_path)
            except PdfError as exc:
                result.errors.append((file.name, exc.message))
                if job is not None:
                    job.add_error(file.path, exc.message)
                logger.warning("Packet export failed for %s: %s", packet.display_name, exc.message)
                continue
            except OSError as exc:
                message = (
                    f"{packet.display_name}'s combined packet could not be written: "
                    f"{exc.strerror or exc}"
                )
                result.errors.append((file.name, message))
                if job is not None:
                    job.add_error(file.path, message)
                continue

            for document in documents:
                document.packet_export_path = str(output_path)

            result.packets.append(
                ExportedPacket(
                    packet=packet,
                    source_pdf=file.path,
                    output_path=output_path,
                    document_count=len(documents),
                    page_numbers=page_numbers,
                )
            )
            log_event(
                logger,
                "packet.exported",
                candidate=packet.display_name,
                documents=len(documents),
                pages=len(page_numbers),
                output=output_path.name,
            )

    def _packet_documents(self, packet: CandidatePacket) -> list[DocumentGroup]:
        """A packet's documents in output order, narrowed to the wanted types."""
        return [d for d in packet.ordered_documents(self.packet_order) if self.wants(d)]

    def _packet_folder(self, packet: CandidatePacket, destination: Path) -> Path:
        if not self.folder_per_candidate:
            return destination
        return destination / sanitize_folder_name(
            packet.candidate.name or UNKNOWN_FOLDER, fallback=UNKNOWN_FOLDER
        )

    def _packet_stem(self, packet: CandidatePacket) -> str:
        name = packet.candidate.name or UNKNOWN_FOLDER
        return "_".join(part for part in str(name).split() if part) or UNKNOWN_FOLDER

    # ------------------------------------------------------------------
    def _write_group(
        self,
        file: SourceFileAnalysis,
        group: DocumentGroup,
        destination: Path,
        taken: set[str],
        sequence_by_folder: dict[str, int],
    ) -> Path:
        owner = self._owner_of(file, group)
        folder = self._folder_for(owner, group, destination)
        folder.mkdir(parents=True, exist_ok=True)

        folder_key = str(folder).lower()
        sequence_by_folder[folder_key] = sequence_by_folder.get(folder_key, 0) + 1
        sequence = sequence_by_folder[folder_key]

        stem = self._stem_for(file, group, owner, sequence)
        output_path = unique_path(folder, stem, ".pdf", taken=taken)
        return split_pdf(file.path, group.export_page_indexes, output_path)

    def _owner_of(self, file: SourceFileAnalysis, group: DocumentGroup) -> Candidate:
        """Who a document is filed under.

        The packet is the authority: a cover letter that names nobody still
        belongs in its candidate's folder once association has attributed it.
        Documents in the unknown queue deliberately stay unfiled.
        """
        packet = file.packet_for_document(group)
        if packet is not None and not packet.is_unknown and packet.candidate.name:
            return packet.candidate
        return group.candidate

    def _folder_for(self, owner: Candidate, group: DocumentGroup, destination: Path) -> Path:
        if self.group_by_document_type:
            name = NEEDS_REVIEW_FOLDER if group.needs_attention else _type_folder_name(
                group.document_type
            )
            return destination / name
        if not self.folder_per_candidate:
            return destination
        name = owner.name or UNKNOWN_FOLDER
        return destination / sanitize_folder_name(name, fallback=UNKNOWN_FOLDER)

    def _stem_for(
        self,
        file: SourceFileAnalysis,
        group: DocumentGroup,
        owner: Candidate,
        sequence: int,
    ) -> str:
        candidate_name = owner.name or UNKNOWN_FOLDER

        if self.group_by_document_type:
            # The folder already says what type this is; repeating it in the
            # filename would be redundant, and the desired shape is just
            # "<Candidate Name>.pdf" -- spaces kept, matching folder names.
            stem = sanitize_filename(candidate_name, fallback=UNKNOWN_FOLDER, spaces_to=" ")
            if not owner.name:
                stem = f"{stem}_{sequence:03d}"
            return stem

        stem = render_filename_template(
            self.filename_template,
            candidate=candidate_name,
            document_type=group.document_type,
            source_file=file.name,
            applicant_id=owner.applicant_id or group.candidate.applicant_id,
            sequence=sequence,
            when=date.today(),
        )

        # Unidentified documents get a sequence so they stay distinguishable
        # (Unknown_Resume_001.pdf) rather than relying on collision suffixes.
        if not owner.name and "{sequence}" not in self.filename_template:
            stem = f"{stem}_{sequence:03d}"
        return stem


__all__ = [
    "ExportService",
    "ExportResult",
    "ExportedDocument",
    "ExportedPacket",
    "UNKNOWN_FOLDER",
    "PACKET_SUFFIX",
    "batch_folder_name",
    "create_batch_directory",
]
