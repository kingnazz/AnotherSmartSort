"""Candidate packet reconstruction.

This is the third intelligence problem, and it is deliberately its own layer.
Classification answers *what kind of document is this page?*; the boundary
engine answers *does this page start a new document?*; this service answers
*which applicant does this document belong to?* None of the three may reach into
another's reasoning.

The input is one source PDF's logical documents in page order. The output is a
list of candidate packets, plus a holding area for documents nobody can
confidently claim.

Two principles shape every decision here:

**Explicit identity beats proximity.** A resume that names Sarah Lee belongs to
Sarah Lee even if it sits directly beneath Jane Smith's cover letter with no
application report in between.

**Silence is not agreement.** A document carrying no identity at all inherits
the active candidate, because that is usually right and the alternative -- a
pile of orphans -- helps nobody. But it inherits at a confidence that says so,
and it lands in review rather than being presented as certain.
"""

from __future__ import annotations

from app.models.candidate import Candidate
from app.models.document import DocumentGroup
from app.models.packet import UNKNOWN_PACKET_ID, CandidatePacket
from app.models.source_file import SourceFileAnalysis
from app.profiles.base import OTHER, DocumentProfile
from app.services.confidence import ConfidenceThresholds
from app.services.identity import (
    STRONG_MATCH,
    IdentityComparison,
    IdentitySignals,
    compare_identities,
    identity_signals,
    merge_signals,
    name_similarity,
)
from app.utils.logging_setup import get_logger

logger = get_logger("packets")

#: Association confidence below which a document goes to the unknown queue
#: rather than being attributed to somebody on thin evidence.
ASSOCIATION_FLOOR = 0.50

#: Starting confidence for a document attributed purely because it follows the
#: active candidate's documents. Capped below the "no review needed" bar on
#: purpose: an inference from position alone should always be visible.
_PROXIMITY_BASE = 0.72
_PROXIMITY_CEILING = 0.85

#: How close a runner-up match has to be before the attribution is ambiguous.
_AMBIGUITY_MARGIN = 0.05


class CandidatePacketService:
    """Reconstructs applicant packets from a source PDF's logical documents."""

    def __init__(
        self,
        profile: DocumentProfile,
        thresholds: ConfidenceThresholds | None = None,
    ) -> None:
        self.profile = profile
        self.thresholds = thresholds or ConfidenceThresholds()

    # ------------------------------------------------------------------
    # Building
    # ------------------------------------------------------------------
    def build_packets(
        self, groups: list[DocumentGroup], source_pdf: str
    ) -> list[CandidatePacket]:
        """Attribute each logical document to an applicant, in page order."""
        packets: list[CandidatePacket] = []
        unknown = CandidatePacket(
            source_pdf=source_pdf, id=UNKNOWN_PACKET_ID, is_unknown=True
        )
        signals_by_packet: dict[str, IdentitySignals] = {}
        active: CandidatePacket | None = None

        ordered = sorted(groups, key=lambda g: g.start_page_index)
        for document in ordered:
            if document.association_manually_set:
                # A reviewer already answered this; never overrule them.
                continue

            signals = identity_signals(document.candidate)
            best, runner_up, conflicts = self._best_match(signals, packets, signals_by_packet)

            if best is not None and self._is_ambiguous(best, runner_up):
                self._send_to_unknown(
                    document,
                    unknown,
                    "the same identity matches more than one candidate",
                )
                continue

            if best is not None:
                packet, comparison = best
                self._attach(packet, document, comparison.strength, comparison.reasons)
                signals_by_packet[packet.id] = merge_signals(
                    signals_by_packet[packet.id], signals
                )
                self._absorb_identity(packet, document.candidate)
                active = packet
                continue

            if signals.is_empty:
                attached = self._attach_by_proximity(document, active, ordered)
                if not attached:
                    self._send_to_unknown(
                        document, unknown, "no candidate details and no active candidate"
                    )
                continue

            # Has identity, matches nobody: this is a new applicant.
            packet = self._start_packet(
                source_pdf, document, signals, packets, conflicts, previous=active
            )
            signals_by_packet[packet.id] = signals
            packets.append(packet)
            active = packet

        if unknown.documents:
            packets.append(unknown)

        self._evaluate_reviews(packets)
        return packets

    # ------------------------------------------------------------------
    def _best_match(
        self,
        signals: IdentitySignals,
        packets: list[CandidatePacket],
        signals_by_packet: dict[str, IdentitySignals],
    ) -> tuple[
        tuple[CandidatePacket, IdentityComparison] | None,
        tuple[CandidatePacket, IdentityComparison] | None,
        list[tuple[CandidatePacket, IdentityComparison]],
    ]:
        """Strongest match, the runner-up, and any packets we clash with."""
        matches: list[tuple[CandidatePacket, IdentityComparison]] = []
        conflicts: list[tuple[CandidatePacket, IdentityComparison]] = []

        for packet in packets:
            if packet.is_unknown:
                continue
            comparison = compare_identities(signals, signals_by_packet.get(packet.id, IdentitySignals()))
            if comparison.conflict:
                conflicts.append((packet, comparison))
            elif comparison.strength >= STRONG_MATCH:
                matches.append((packet, comparison))

        matches.sort(key=lambda item: item[1].strength, reverse=True)
        best = matches[0] if matches else None
        runner_up = matches[1] if len(matches) > 1 else None
        return best, runner_up, conflicts

    def _is_ambiguous(
        self,
        best: tuple[CandidatePacket, IdentityComparison],
        runner_up: tuple[CandidatePacket, IdentityComparison] | None,
    ) -> bool:
        """Two candidates fit equally well, so choosing either would be a guess."""
        if runner_up is None:
            return False
        return abs(best[1].strength - runner_up[1].strength) <= _AMBIGUITY_MARGIN

    # ------------------------------------------------------------------
    def _attach(
        self,
        packet: CandidatePacket,
        document: DocumentGroup,
        confidence: float,
        reasons: list[str],
    ) -> None:
        packet.add(document)
        document.set_association(packet.id, confidence, reasons)

    def _attach_by_proximity(
        self,
        document: DocumentGroup,
        active: CandidatePacket | None,
        ordered: list[DocumentGroup],
    ) -> bool:
        """Attribute an anonymous document to the candidate currently in scope."""
        if active is None:
            return False

        confidence = _PROXIMITY_BASE
        reasons = [f"follows {active.display_name}'s documents"]

        if document.document_type in self._identity_expected_types():
            # A resume or application report that names nobody is odd enough to
            # be worth a second look, even when position suggests an owner. A
            # cover letter without a signature is not unusual, so it is not
            # penalised for being anonymous -- that is the ordinary case this
            # inference exists to handle.
            confidence -= 0.12
            reasons.append(f"a {document.document_type} would normally carry a name")
        else:
            confidence += 0.06

        gap = document.start_page_index - active.end_page_index
        if gap > 1:
            confidence -= 0.10
            reasons.append("does not directly follow that candidate's pages")

        if document.document_type == OTHER:
            confidence -= 0.08
            reasons.append("document type is unknown")

        confidence = max(0.0, min(_PROXIMITY_CEILING, confidence))
        if confidence < ASSOCIATION_FLOOR:
            return False

        self._attach(active, document, confidence, reasons)
        return True

    def _identity_expected_types(self) -> tuple[str, ...]:
        """Types where a missing name is itself a reason for doubt."""
        return self.profile.identity_expected_types or self.profile.identity_types

    def _send_to_unknown(
        self, document: DocumentGroup, unknown: CandidatePacket, reason: str
    ) -> None:
        unknown.add(document)
        document.set_association(unknown.id, 0.0, [reason])
        document.association_review = True

    def _start_packet(
        self,
        source_pdf: str,
        document: DocumentGroup,
        signals: IdentitySignals,
        packets: list[CandidatePacket],
        conflicts: list[tuple[CandidatePacket, IdentityComparison]],
        *,
        previous: CandidatePacket | None,
    ) -> CandidatePacket:
        packet = CandidatePacket(source_pdf=source_pdf, candidate=document.candidate)
        confidence = 0.98 if signals.has_strong_identifier else 0.93
        reasons = [f"identified as {document.candidate.display_name}"]

        packet.boundary_confidence = confidence
        packet.boundary_reasons = (
            [f"a different applicant to {previous.display_name}"] if previous else ["first applicant in the file"]
        )

        # Same name, contradictory details: keep them apart and say why on
        # both. Checked by actual name similarity, not by the wording of the
        # conflict reason -- an email clash is reported as "different email
        # addresses" whether the two applicants share a name or not, and two
        # people with unrelated names (Jane Smith, Robert Jones) who simply
        # used different emails are not ambiguous at all. Flagging every such
        # pair in a large batch buried every packet under a review note about
        # people it was never actually confusable with.
        for other, comparison in conflicts:
            if not comparison.reasons:
                continue
            if name_similarity(document.candidate.name, other.candidate.name) <= 0.0:
                continue
            note = (
                f"shares a name with another packet but has "
                f"{comparison.reasons[0]}"
            )
            packet.add_review_reason(note)
            other.add_review_reason(note)

        self._attach(packet, document, confidence, reasons)
        return packet

    def _absorb_identity(self, packet: CandidatePacket, candidate: Candidate) -> None:
        """Let a later document fill in details the first one lacked."""
        if packet.manually_named:
            return
        if candidate.is_empty:
            return
        packet.candidate = packet.candidate.merged_with(candidate)

    # ------------------------------------------------------------------
    # Review policy
    # ------------------------------------------------------------------
    def _evaluate_reviews(self, packets: list[CandidatePacket]) -> None:
        for packet in packets:
            for document in packet.documents:
                self.evaluate_association(document, packet)

    def evaluate_association(
        self, document: DocumentGroup, packet: CandidatePacket
    ) -> None:
        """Flag a document whose owner is uncertain, and say what is uncertain."""
        if document.association_manually_set:
            document.association_review = False
            return

        if not document.export_page_indexes:
            # Nothing here will be written -- a bulk compile's cover sheet, a
            # divider page. Asking who it belongs to is a question with no
            # consequence and no answer.
            document.association_review = False
            return

        if packet.is_unknown:
            document.association_review = True
            document.add_review_reason(
                "This document could not be matched to a candidate - assign it"
            )
            return

        document.association_review = self.thresholds.requires_review(
            document.association_confidence
        )
        if document.association_review:
            why = document.association_reasons[0] if document.association_reasons else "weak evidence"
            document.add_review_reason(
                f"Belongs to {packet.display_name}? ({why}) - "
                f"{document.association_confidence * 100:.0f}% sure"
            )
        if not packet.is_identified:
            document.association_review = True
            document.add_review_reason("No candidate name was found for this packet")

    # ------------------------------------------------------------------
    # Refresh after a grouping correction
    # ------------------------------------------------------------------
    def rebuild(self, file: SourceFileAnalysis) -> list[CandidatePacket]:
        """Re-derive packets after the documents themselves changed."""
        for document in file.groups:
            if not document.association_manually_set:
                document.packet_id = None
                document.association_confidence = 0.0
                document.association_reasons = []
                document.association_review = False
        file.packets = self.build_packets(file.groups, str(file.path))
        file.refresh_status()
        return file.packets

    # ------------------------------------------------------------------
    # Corrections
    # ------------------------------------------------------------------
    def move_document(
        self,
        file: SourceFileAnalysis,
        document: DocumentGroup,
        target: CandidatePacket,
    ) -> None:
        """Move one document into another candidate's packet."""
        for packet in file.packets:
            if document in packet.documents:
                packet.remove(document)
        target.add(document)
        document.set_association(
            target.id,
            1.0,
            [f"assigned to {target.display_name} by reviewer"],
            manual=not target.is_unknown,
        )
        if target.is_unknown:
            document.association_manually_set = False
            document.association_review = True
        self._prune_empty(file)
        file.refresh_status()

    def create_packet_for(
        self,
        file: SourceFileAnalysis,
        document: DocumentGroup,
        name: str | None = None,
    ) -> CandidatePacket:
        """Pull a document out into a brand new candidate."""
        candidate = Candidate(name=name) if name else document.candidate
        packet = CandidatePacket(source_pdf=str(file.path), candidate=candidate)
        packet.manually_named = bool(name)
        packet.boundary_confidence = 1.0
        packet.boundary_reasons = ["created by reviewer"]
        file.packets.append(packet)
        self.move_document(file, document, packet)
        self._sort_packets(file)
        return packet

    def merge_packets(
        self, file: SourceFileAnalysis, keep: CandidatePacket, absorb: CandidatePacket
    ) -> CandidatePacket:
        """Combine two packets that turned out to be the same person."""
        if keep is absorb:
            return keep
        for document in list(absorb.documents):
            absorb.remove(document)
            keep.add(document)
            document.set_association(
                keep.id,
                1.0,
                [f"merged into {keep.display_name} by reviewer"],
                manual=True,
            )
        if not keep.manually_named:
            keep.candidate = keep.candidate.merged_with(absorb.candidate)
        keep.clear_review()
        if absorb in file.packets:
            file.packets.remove(absorb)
        self._sort_packets(file)
        file.refresh_status()
        return keep

    def split_packet(
        self,
        file: SourceFileAnalysis,
        packet: CandidatePacket,
        documents: list[DocumentGroup],
        name: str | None = None,
    ) -> CandidatePacket | None:
        """Move some documents out of a packet into a new candidate."""
        moving = [d for d in documents if d in packet.documents]
        if not moving or len(moving) == len(packet.documents):
            return None

        new_packet = CandidatePacket(
            source_pdf=str(file.path),
            candidate=Candidate(name=name) if name else Candidate(),
        )
        new_packet.manually_named = bool(name)
        new_packet.boundary_confidence = 1.0
        new_packet.boundary_reasons = ["split out by reviewer"]
        file.packets.append(new_packet)

        for document in moving:
            packet.remove(document)
            new_packet.add(document)
            document.set_association(
                new_packet.id,
                1.0,
                [f"split into {new_packet.display_name} by reviewer"],
                manual=True,
            )

        if not name:
            # Fall back to whatever identity the moved documents carry.
            merged = Candidate()
            for document in moving:
                merged = merged.merged_with(document.candidate)
            new_packet.candidate = merged

        packet.clear_review()
        self._sort_packets(file)
        file.refresh_status()
        return new_packet

    def rename_candidate(self, packet: CandidatePacket, name: str) -> None:
        """Correct an extracted candidate name."""
        cleaned = " ".join(str(name).split()).strip()
        if not cleaned:
            return
        packet.candidate = Candidate(
            name=cleaned,
            email=packet.candidate.email,
            phone=packet.candidate.phone,
            linkedin=packet.candidate.linkedin,
            job_title=packet.candidate.job_title,
            applicant_id=packet.candidate.applicant_id,
        )
        packet.manually_named = True
        packet.is_unknown = False
        packet.clear_review()
        for document in packet.documents:
            document.candidate = packet.candidate.merged_with(document.candidate)

    def accept_packet(self, file: SourceFileAnalysis, packet: CandidatePacket) -> None:
        """Accept a packet's attribution as correct, clearing its review flags."""
        packet.clear_review()
        for document in packet.documents:
            document.association_manually_set = True
            document.association_review = False
        file.refresh_status()

    # ------------------------------------------------------------------
    def _prune_empty(self, file: SourceFileAnalysis) -> None:
        file.packets = [p for p in file.packets if p.documents or p.manually_named]

    def _sort_packets(self, file: SourceFileAnalysis) -> None:
        """Page order, with the unknown queue always last."""
        file.packets.sort(
            key=lambda p: (
                1 if p.is_unknown else 0,
                p.start_page_index if p.page_indexes else 1 << 30,
            )
        )


__all__ = ["CandidatePacketService", "ASSOCIATION_FLOOR"]
