"""Undo and redo for review corrections.

Once corrections can be made by dragging, undo stops being a nicety: a drop
lands in one gesture, and the wrong drop has to be as cheap to take back as it
was to make. Without that, a mis-drag on a 40-document file means finding what
moved and reconstructing it by hand.

The stack records *state*, not *actions*. Every correction -- retype, split,
merge, page move, candidate reassignment, exclusion, rename -- is captured as
the file's document structure before and after, so undo restores exactly what
was there rather than trying to invert each operation's logic. Inverting a
merge means remembering where the seam was; inverting a split means
remembering what the two halves were; inverting a drag that did both means
remembering both. Snapshots make all of that one mechanism, and one that
cannot drift out of step as new corrections are added.

Critically, undo never re-runs analysis. Re-analysing would discard every
*other* correction the user had made, and on a 300-page file would take long
enough to feel broken.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable

from app.models.source_file import SourceFileAnalysis
from app.utils.logging_setup import get_logger

logger = get_logger("corrections")

#: How many corrections can be taken back. Deep enough to cover a review
#: session's worth of mistakes, bounded so a long session cannot grow without
#: limit. A snapshot is document structure only -- page indexes, types, names
#: -- never page text, so each one is small.
DEFAULT_DEPTH = 100


@dataclass
class _Snapshot:
    """One file's correctable state, captured whole."""

    groups: list[Any]
    packets: list[Any]
    pages: dict[int, dict[str, Any]]


@dataclass
class Correction:
    """One undoable change, and what to call it in the UI."""

    label: str
    source_pdf: str
    before: _Snapshot
    after: _Snapshot


@dataclass
class CorrectionHistory:
    """The undo/redo stack for a review session."""

    depth: int = DEFAULT_DEPTH
    _undo: list[Correction] = field(default_factory=list)
    _redo: list[Correction] = field(default_factory=list)
    #: Called after an undo or redo restores state, so the UI can re-render.
    on_change: Callable[[], None] | None = None

    # ------------------------------------------------------------------
    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str:
        return self._undo[-1].label if self._undo else ""

    @property
    def redo_label(self) -> str:
        return self._redo[-1].label if self._redo else ""

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()

    # ------------------------------------------------------------------
    def record(
        self,
        label: str,
        analysis: SourceFileAnalysis,
        action: Callable[[], Any],
    ) -> Any:
        """Run a correction and make it undoable.

        The action's own return value is passed straight back, so this can wrap
        a call that returns something (the new group from a split) without the
        caller restructuring.

        A correction that changes nothing is not recorded: an undo that
        appears to do nothing is worse than no undo entry at all.
        """
        before = _capture(analysis)
        result = action()
        after = _capture(analysis)

        if _same(before, after):
            return result

        self._undo.append(
            Correction(
                label=label,
                source_pdf=str(analysis.path),
                before=before,
                after=after,
            )
        )
        # A new correction makes the redo branch unreachable -- it described a
        # future that no longer follows from this state.
        self._redo.clear()
        while len(self._undo) > self.depth:
            self._undo.pop(0)
        return result

    # ------------------------------------------------------------------
    def undo(self, analysis: SourceFileAnalysis) -> str | None:
        """Take back the most recent correction. Returns its label."""
        if not self._undo:
            return None
        correction = self._undo.pop()
        _restore(analysis, correction.before)
        self._redo.append(correction)
        self._notify()
        logger.info("Undid correction: %s", correction.label)
        return correction.label

    def redo(self, analysis: SourceFileAnalysis) -> str | None:
        """Re-apply the most recently undone correction. Returns its label."""
        if not self._redo:
            return None
        correction = self._redo.pop()
        _restore(analysis, correction.after)
        self._undo.append(correction)
        self._notify()
        logger.info("Redid correction: %s", correction.label)
        return correction.label

    def _notify(self) -> None:
        if self.on_change is not None:
            self.on_change()


# ----------------------------------------------------------------------
def _capture(analysis: SourceFileAnalysis) -> _Snapshot:
    """Copy the state a correction can change.

    Groups and packets are copied in a single ``deepcopy`` so the packets keep
    referring to the same group objects afterwards -- copying them separately
    would silently break that link and leave packets pointing at documents
    nobody else can see.

    Pages are captured field by field rather than copied: a page carries its
    extracted text, and duplicating that per correction would make the stack
    enormous on a large file for no benefit. Only the fields a correction can
    touch are kept.
    """
    groups, packets = copy.deepcopy((analysis.groups, analysis.packets))
    pages = {page.page_index: _capture_page(page) for page in analysis.pages}
    return _Snapshot(groups=groups, packets=packets, pages=pages)


#: Every :class:`~app.models.page.PageAnalysis` field a correction can change.
#: Named explicitly rather than copied wholesale: a page also carries its
#: extracted text, and duplicating that per correction would make the stack
#: enormous on a large file while restoring nothing a correction ever touched.
_PAGE_FIELDS: tuple[str, ...] = (
    "predicted_type",
    "classification_confidence",
    "classification_source",
    "starts_new_document",
    "boundary_confidence",
    "reasoning_summary",
    "separator_label",
    "separator_state",
    "excluded",
    "requires_review",
)

#: Fields holding mutable containers, which must be copied rather than
#: referenced -- otherwise the snapshot and the live page share one list and
#: "restoring" it puts back whatever the correction did to it.
_PAGE_LIST_FIELDS: tuple[str, ...] = ("boundary_reasons", "review_reasons")


def _capture_page(page) -> dict[str, Any]:
    state: dict[str, Any] = {name: getattr(page, name) for name in _PAGE_FIELDS}
    for name in _PAGE_LIST_FIELDS:
        state[name] = list(getattr(page, name) or [])
    # The candidate is a small mutable record; retyping and reassignment both
    # rewrite it, so it needs a copy of its own.
    state["candidate"] = copy.deepcopy(page.candidate)
    return state


def _restore(analysis: SourceFileAnalysis, snapshot: _Snapshot) -> None:
    """Put a captured state back.

    The snapshot is copied again on the way in, so the same correction can be
    undone and redone repeatedly without the stack's own copy being mutated by
    whatever happens next.
    """
    groups, packets = copy.deepcopy((snapshot.groups, snapshot.packets))
    analysis.groups = groups
    analysis.packets = packets

    for page in analysis.pages:
        state = snapshot.pages.get(page.page_index)
        if state is None:
            continue
        for name in _PAGE_FIELDS:
            setattr(page, name, state[name])
        for name in _PAGE_LIST_FIELDS:
            setattr(page, name, list(state[name]))
        page.candidate = copy.deepcopy(state["candidate"])

    analysis.refresh_status()


def _same(left: _Snapshot, right: _Snapshot) -> bool:
    """Whether a correction actually changed anything."""
    return _shape(left) == _shape(right)


def _shape(snapshot: _Snapshot) -> Any:
    """A comparable summary of everything a correction can alter."""
    return (
        [
            (
                group.document_type,
                tuple(group.page_indexes),
                tuple(group.excluded_separator_pages),
                group.excluded,
                group.candidate.name,
                group.packet_id,
                group.type_manually_set,
                group.requires_review,
                group.association_review,
            )
            for group in snapshot.groups
        ],
        [
            (packet.candidate.name, packet.is_unknown, tuple(d.id for d in packet.documents))
            for packet in snapshot.packets
        ],
        {
            index: (
                state["separator_state"],
                state["excluded"],
                state["predicted_type"],
                state["starts_new_document"],
                state["classification_source"],
                state["candidate"].name,
            )
            for index, state in snapshot.pages.items()
        },
    )


__all__ = ["CorrectionHistory", "Correction", "DEFAULT_DEPTH"]
