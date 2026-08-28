"""Low-level PDF operations built on PyMuPDF.

Quality rules enforced here:

* Splitting **copies original pages** with ``insert_pdf`` -- vector content,
  embedded fonts, searchable text, page size and rotation all survive.
* Pages are never rasterised in order to split them. Rendering exists only for
  thumbnails and for feeding OCR.
* Output is written to a temporary file and atomically moved into place, so a
  cancelled or failed export never leaves a corrupt half-written PDF.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

import pymupdf

from app.utils.logging_setup import get_logger

logger = get_logger("pdf")

#: Below this many extracted characters a page is treated as needing OCR.
DEFAULT_OCR_TEXT_THRESHOLD = 24

#: DPI used for review thumbnails (small, cheap, cached).
THUMBNAIL_DPI = 48
#: DPI used for the larger review preview.
PREVIEW_DPI = 110
#: DPI used when rendering a page for OCR (accuracy matters more than speed).
OCR_DPI = 300


class PdfError(Exception):
    """Base class for user-facing PDF problems."""

    def __init__(self, message: str, *, path: str | Path | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.path = str(path) if path else None


class PdfCorruptError(PdfError):
    """The file could not be parsed as a PDF."""


class PdfEncryptedError(PdfError):
    """The file is password protected and no valid password was supplied."""


class PdfPermissionError(PdfError):
    """The file could not be read or written because of OS permissions."""


@dataclass(frozen=True)
class PdfInfo:
    """Cheap summary of a PDF, obtained without reading every page."""

    path: Path
    page_count: int
    encrypted: bool
    title: str | None = None


@contextmanager
def open_pdf(path: str | Path, password: str | None = None) -> Iterator[pymupdf.Document]:
    """Open a PDF, translating library failures into actionable errors."""
    resolved = Path(path)
    document: pymupdf.Document | None = None
    try:
        document = pymupdf.open(resolved)
    except FileNotFoundError as exc:
        raise PdfError(f"The file could not be found: {resolved.name}", path=resolved) from exc
    except PermissionError as exc:
        raise PdfPermissionError(
            f"Permission denied when opening {resolved.name}.", path=resolved
        ) from exc
    except (pymupdf.FileDataError, RuntimeError, ValueError) as exc:
        raise PdfCorruptError(
            f"{resolved.name} could not be read. The file may be damaged or not a PDF.",
            path=resolved,
        ) from exc

    try:
        if document.needs_pass:
            if not password or not document.authenticate(password):
                raise PdfEncryptedError(
                    f"{resolved.name} is password protected and was skipped.", path=resolved
                )
        yield document
    finally:
        try:
            document.close()
        except Exception:  # pragma: no cover - closing must never mask the real error
            logger.debug("Ignoring error while closing %s", resolved.name)


def read_info(path: str | Path, password: str | None = None) -> PdfInfo:
    """Return page count and encryption state for a PDF."""
    resolved = Path(path)
    with open_pdf(resolved, password) as document:
        title = (document.metadata or {}).get("title") or None
        return PdfInfo(
            path=resolved,
            page_count=document.page_count,
            encrypted=bool(document.needs_pass),
            title=title,
        )


def extract_page_text(document: pymupdf.Document, page_index: int) -> str:
    """Extract native (already-digital) text from one page.

    Returns an empty string when the page has no text layer, which is the
    signal the pipeline uses to decide whether OCR is required.
    """
    try:
        page = document.load_page(page_index)
    except Exception as exc:
        logger.warning("Could not load page %s: %s", page_index + 1, exc)
        return ""
    try:
        return page.get_text("text") or ""
    except Exception as exc:
        logger.warning("Could not extract text from page %s: %s", page_index + 1, exc)
        return ""


def page_needs_ocr(text: str, threshold: int = DEFAULT_OCR_TEXT_THRESHOLD) -> bool:
    """True when a page has too little native text to classify.

    Keeping this a pure function makes the "OCR only when necessary" rule
    directly testable.
    """
    return len((text or "").strip()) < threshold


def page_has_visual_content(document: pymupdf.Document, page_index: int) -> bool:
    """Whether a page holds anything OCR could possibly read.

    OCR exists to recover text from a *picture* of text. A page with no text
    layer and no images or drawings is simply blank -- a separator sheet, the
    back of a duplex scan, a spacer inside a generated form -- and running OCR
    on it costs a process launch (and, on Windows, historically a flashing
    console window) to be told what the page already said: nothing.

    Errs towards ``True``: if the page cannot be inspected, OCR still gets its
    chance rather than text being silently skipped.
    """
    try:
        page = document.load_page(page_index)
    except Exception as exc:
        logger.warning("Could not load page %s: %s", page_index + 1, exc)
        return True

    try:
        if page.get_images(full=False):
            return True
    except Exception:  # pragma: no cover - defensive
        return True

    try:
        return bool(page.get_drawings())
    except Exception:  # pragma: no cover - defensive
        return True


def render_page_png(
    document: pymupdf.Document,
    page_index: int,
    *,
    dpi: int = THUMBNAIL_DPI,
    grayscale: bool = False,
) -> bytes | None:
    """Render a page to PNG bytes for previews or OCR. Never used for export."""
    try:
        page = document.load_page(page_index)
        colorspace = pymupdf.csGRAY if grayscale else pymupdf.csRGB
        pixmap = page.get_pixmap(dpi=dpi, colorspace=colorspace, alpha=False)
        return pixmap.tobytes("png")
    except Exception as exc:
        logger.warning("Could not render page %s: %s", page_index + 1, exc)
        return None


def contiguous_runs(page_indexes: Sequence[int]) -> list[tuple[int, int]]:
    """Collapse sorted page indexes into inclusive ``(start, end)`` runs."""
    ordered = sorted(set(int(i) for i in page_indexes))
    if not ordered:
        return []
    runs: list[tuple[int, int]] = []
    start = previous = ordered[0]
    for index in ordered[1:]:
        if index == previous + 1:
            previous = index
            continue
        runs.append((start, previous))
        start = previous = index
    runs.append((start, previous))
    return runs


def split_pdf(
    source_path: str | Path,
    page_indexes: Sequence[int],
    output_path: str | Path,
    *,
    password: str | None = None,
) -> Path:
    """Copy ``page_indexes`` from ``source_path`` into a new PDF at ``output_path``.

    Original page content is copied verbatim -- no rasterisation, no re-encoding
    of images, no loss of the text layer. Written atomically.
    """
    source = Path(source_path)
    destination = Path(output_path)
    indexes = sorted(set(int(i) for i in page_indexes))
    if not indexes:
        raise PdfError("No pages were selected for export.", path=source)

    destination.parent.mkdir(parents=True, exist_ok=True)

    with open_pdf(source, password) as document:
        invalid = [i for i in indexes if i < 0 or i >= document.page_count]
        if invalid:
            raise PdfError(
                f"Pages {[i + 1 for i in invalid]} are outside {source.name}.", path=source
            )

        output = pymupdf.open()
        try:
            for start, end in contiguous_runs(indexes):
                output.insert_pdf(document, from_page=start, to_page=end)

            handle, temp_name = tempfile.mkstemp(
                suffix=".pdf.part", dir=str(destination.parent)
            )
            os.close(handle)
            temp_path = Path(temp_name)
            try:
                output.save(str(temp_path), garbage=3, deflate=True)
                os.replace(temp_path, destination)
            except PermissionError as exc:
                temp_path.unlink(missing_ok=True)
                raise PdfPermissionError(
                    f"Could not write {destination.name}. The file may be open in another "
                    "program.",
                    path=destination,
                ) from exc
            except OSError as exc:
                temp_path.unlink(missing_ok=True)
                raise PdfError(
                    f"Could not write {destination.name}: {exc.strerror or exc}",
                    path=destination,
                ) from exc
        finally:
            output.close()

    return destination


def combine_pdf(
    sections: Sequence[tuple[str | Path, Sequence[int]]],
    output_path: str | Path,
    *,
    password: str | None = None,
) -> Path:
    """Assemble one PDF from page ranges of one or more source PDFs.

    Used to build a candidate's complete packet. Pages are copied exactly as
    :func:`split_pdf` copies them -- no rasterisation, no re-encoding -- so the
    combined file keeps its searchable text, fonts, vectors, page size and
    orientation. Sections are emitted in the order given, which is what lets the
    packet follow a configured document order rather than source order.
    """
    destination = Path(output_path)
    wanted = [
        (Path(source), sorted(set(int(i) for i in indexes)))
        for source, indexes in sections
        if indexes
    ]
    if not wanted:
        raise PdfError("No pages were selected for the combined packet.", path=destination)

    destination.parent.mkdir(parents=True, exist_ok=True)
    output = pymupdf.open()
    try:
        for source, indexes in wanted:
            with open_pdf(source, password) as document:
                invalid = [i for i in indexes if i < 0 or i >= document.page_count]
                if invalid:
                    raise PdfError(
                        f"Pages {[i + 1 for i in invalid]} are outside {source.name}.",
                        path=source,
                    )
                for start, end in contiguous_runs(indexes):
                    output.insert_pdf(document, from_page=start, to_page=end)

        handle, temp_name = tempfile.mkstemp(suffix=".pdf.part", dir=str(destination.parent))
        os.close(handle)
        temp_path = Path(temp_name)
        try:
            output.save(str(temp_path), garbage=3, deflate=True)
            os.replace(temp_path, destination)
        except PermissionError as exc:
            temp_path.unlink(missing_ok=True)
            raise PdfPermissionError(
                f"Could not write {destination.name}. The file may be open in another program.",
                path=destination,
            ) from exc
        except OSError as exc:
            temp_path.unlink(missing_ok=True)
            raise PdfError(
                f"Could not write {destination.name}: {exc.strerror or exc}", path=destination
            ) from exc
    finally:
        output.close()

    return destination


__all__ = [
    "PdfError",
    "PdfCorruptError",
    "PdfEncryptedError",
    "PdfPermissionError",
    "PdfInfo",
    "open_pdf",
    "read_info",
    "extract_page_text",
    "page_needs_ocr",
    "render_page_png",
    "split_pdf",
    "combine_pdf",
    "contiguous_runs",
    "DEFAULT_OCR_TEXT_THRESHOLD",
    "THUMBNAIL_DPI",
    "PREVIEW_DPI",
    "OCR_DPI",
]
