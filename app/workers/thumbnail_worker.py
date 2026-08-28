"""Lazy page-thumbnail rendering.

Large batches must never load thousands of full-resolution previews. Thumbnails
are rendered on demand, at low DPI, on a background thread, and cached with a
bounded LRU so memory stays flat however many PDFs are queued.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QImage, QPixmap

from app.services.pdf_service import PREVIEW_DPI, THUMBNAIL_DPI, open_pdf, render_page_png
from app.utils.logging_setup import get_logger

logger = get_logger("worker.thumbnails")

#: Bounded so a 2,000-PDF batch cannot exhaust memory through previews.
_CACHE_LIMIT = 240

#: Bounded so a 300-page file cannot queue 300 renders ahead of the few pages
#: actually on screen. Comfortably more than one screenful.
_QUEUE_LIMIT = 64


class ThumbnailCache:
    """Bounded LRU cache of rendered page images."""

    def __init__(self, limit: int = _CACHE_LIMIT) -> None:
        self._items: OrderedDict[tuple[str, int, int], QPixmap] = OrderedDict()
        self._limit = limit

    @staticmethod
    def key(pdf_path: str, page_index: int, dpi: int) -> tuple[str, int, int]:
        return (str(pdf_path), int(page_index), int(dpi))

    def get(self, pdf_path: str, page_index: int, dpi: int) -> QPixmap | None:
        key = self.key(pdf_path, page_index, dpi)
        pixmap = self._items.get(key)
        if pixmap is not None:
            self._items.move_to_end(key)
        return pixmap

    def put(self, pdf_path: str, page_index: int, dpi: int, pixmap: QPixmap) -> None:
        key = self.key(pdf_path, page_index, dpi)
        self._items[key] = pixmap
        self._items.move_to_end(key)
        while len(self._items) > self._limit:
            self._items.popitem(last=False)

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self._items)


class ThumbnailWorker(QThread):
    """Renders a queue of page thumbnails without blocking the UI."""

    #: (pdf path, page index, dpi, QPixmap)
    rendered = Signal(str, int, int, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._queue: list[tuple[str, int, int]] = []
        self._running = True

    def request(self, pdf_path: str, page_index: int, dpi: int = THUMBNAIL_DPI) -> None:
        """Queue a page for rendering (most recent requests are served first).

        The queue is bounded. Opening a 300-page file used to enqueue every
        page at once, so the thread spent minutes rendering pages nobody had
        scrolled to yet while the handful on screen waited behind them. When
        the queue is full the *oldest* request is dropped: it is the one
        furthest from what the user is looking at now, and a dropped request
        costs nothing -- the card asks again when it next needs an image.
        """
        item = (str(pdf_path), int(page_index), int(dpi))
        if item in self._queue:
            return
        self._queue.append(item)
        while len(self._queue) > _QUEUE_LIMIT:
            self._queue.pop(0)

    def cancel(self, pdf_path: str, page_index: int, dpi: int = THUMBNAIL_DPI) -> None:
        """Withdraw a request whose card has scrolled out of view."""
        item = (str(pdf_path), int(page_index), int(dpi))
        try:
            self._queue.remove(item)
        except ValueError:
            pass

    def cancel_all_except(self, keep: set[tuple[str, int, int]]) -> None:
        """Drop every queued request except the ones still wanted.

        Called when the viewport moves: whatever was queued for the pages the
        user has scrolled past is now work that would finish into nothing.
        """
        self._queue = [item for item in self._queue if item in keep]

    @property
    def pending(self) -> int:
        return len(self._queue)

    def clear_queue(self) -> None:
        self._queue.clear()

    def stop(self) -> None:
        self._running = False
        self._queue.clear()

    def run(self) -> None:  # noqa: D102 - QThread entry point
        while self._running:
            if not self._queue:
                self.msleep(40)
                continue

            # Serve the newest request first: it is what the user is looking at.
            pdf_path, page_index, dpi = self._queue.pop()
            pixmap = render_thumbnail(pdf_path, page_index, dpi)
            if pixmap is not None and self._running:
                self.rendered.emit(pdf_path, page_index, dpi, pixmap)


def render_thumbnail(pdf_path: str | Path, page_index: int, dpi: int = THUMBNAIL_DPI):
    """Render one page to a :class:`QPixmap`, or ``None`` if it cannot be read."""
    try:
        with open_pdf(pdf_path) as document:
            if page_index < 0 or page_index >= document.page_count:
                return None
            data = render_page_png(document, page_index, dpi=dpi)
    except Exception as exc:
        logger.debug("Thumbnail failed for %s page %s: %s", pdf_path, page_index + 1, exc)
        return None

    if not data:
        return None

    image = QImage()
    if not image.loadFromData(data, "PNG"):
        return None
    return QPixmap.fromImage(image)


__all__ = ["ThumbnailWorker", "ThumbnailCache", "render_thumbnail", "THUMBNAIL_DPI", "PREVIEW_DPI"]
