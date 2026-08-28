"""OCR policy: only when necessary, and never fatal when unavailable."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.models.enums import TextSource
from app.services.ocr_service import (
    NullOCRProvider,
    OCRAvailability,
    OCRProvider,
    OCRService,
    TesseractOCRProvider,
    build_ocr_service,
)
from app.services.pdf_service import page_needs_ocr
from scripts import sample_data

from tests.helpers import build_pipeline


class RecordingOCRProvider(OCRProvider):
    """Available OCR engine that records every page it was asked to read."""

    name = "recording"

    def __init__(self, text: str = "RECOVERED TEXT", available: bool = True) -> None:
        self.calls = 0
        self._text = text
        self._available = available

    def is_available(self) -> OCRAvailability:
        return OCRAvailability(self._available, "recording provider")

    def extract_text(self, page_image: bytes, *, language: str = "eng") -> str:
        self.calls += 1
        return self._text


class SequencedOCRProvider(OCRProvider):
    """Returns a different result per call, like a real engine reading real pages."""

    name = "sequenced"

    def __init__(self, texts: list[str]) -> None:
        self._texts = list(texts)
        self.calls = 0

    def is_available(self) -> OCRAvailability:
        return OCRAvailability(True, "sequenced provider")

    def extract_text(self, page_image: bytes, *, language: str = "eng") -> str:
        text = self._texts[min(self.calls, len(self._texts) - 1)] if self._texts else ""
        self.calls += 1
        return text


class ExplodingOCRProvider(OCRProvider):
    name = "exploding"

    def is_available(self) -> OCRAvailability:
        return OCRAvailability(True, "will fail")

    def extract_text(self, page_image: bytes, *, language: str = "eng") -> str:
        raise RuntimeError("OCR engine crashed")


class TestOCRNecessity:
    def test_a_page_with_plenty_of_text_does_not_need_ocr(self) -> None:
        assert not page_needs_ocr("A" * 200)

    def test_an_empty_page_needs_ocr(self) -> None:
        assert page_needs_ocr("")
        assert page_needs_ocr("   \n  ")

    def test_a_nearly_empty_page_needs_ocr(self) -> None:
        assert page_needs_ocr("Page 1")

    def test_threshold_is_configurable(self) -> None:
        assert page_needs_ocr("A" * 30, threshold=50)
        assert not page_needs_ocr("A" * 30, threshold=10)


class TestOCRService:
    def test_disabled_service_never_calls_the_provider(self) -> None:
        provider = RecordingOCRProvider()
        service = OCRService(provider, enabled=False)
        assert service.recognize(b"image") == ""
        assert provider.calls == 0

    def test_unavailable_provider_returns_empty_text(self) -> None:
        service = OCRService(RecordingOCRProvider(available=False), enabled=True)
        assert service.recognize(b"image") == ""
        assert not service.availability().available

    def test_a_crashing_provider_does_not_propagate(self) -> None:
        service = OCRService(ExplodingOCRProvider(), enabled=True)
        assert service.recognize(b"image") == ""

    def test_null_provider_is_never_available(self) -> None:
        assert not NullOCRProvider().is_available().available

    def test_build_ocr_service_respects_the_enabled_flag(self) -> None:
        assert not build_ocr_service(enabled=False, executable=None).is_enabled
        assert build_ocr_service(enabled=True, executable=None).enabled


class TestTesseractProvider:
    def test_missing_executable_is_reported_actionably(self) -> None:
        provider = TesseractOCRProvider("/definitely/not/tesseract")
        availability = provider.is_available()
        assert not availability.available
        assert "tesseract" in availability.message.lower()
        assert "settings" in availability.message.lower()

    def test_extract_text_is_safe_when_unavailable(self) -> None:
        provider = TesseractOCRProvider("/definitely/not/tesseract")
        assert provider.extract_text(b"fake image") == ""

    def test_empty_image_is_ignored(self) -> None:
        assert TesseractOCRProvider().extract_text(b"") == ""


class TestPipelineOCRIntegration:
    def test_ocr_is_only_invoked_for_pages_without_text(
        self, profile, thresholds, samples_dir: Path
    ) -> None:
        provider = RecordingOCRProvider()
        pipeline = build_pipeline(
            profile, thresholds, ocr=OCRService(provider, enabled=True)
        )
        pipeline.analyze_file(samples_dir / sample_data.sample_b().filename)
        assert provider.calls == 0, "digital pages must not be sent to OCR"

    def test_scanned_pages_are_sent_to_ocr(
        self, profile, thresholds, samples_dir: Path
    ) -> None:
        provider = RecordingOCRProvider(text="\n".join(sample_data.resume_pages(total=1)[0].lines))
        pipeline = build_pipeline(
            profile, thresholds, ocr=OCRService(provider, enabled=True)
        )
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_d().filename)

        assert provider.calls == analysis.page_count
        assert analysis.ocr_pages == analysis.page_count
        assert all(page.text_source is TextSource.OCR for page in analysis.pages)

    def test_ocr_text_is_actually_classified(
        self, profile, thresholds, samples_dir: Path
    ) -> None:
        """OCR output must feed classification, not just be collected."""
        provider = RecordingOCRProvider(
            text="\n".join(sample_data.resume_pages(total=1)[0].lines)
        )
        pipeline = build_pipeline(
            profile, thresholds, ocr=OCRService(provider, enabled=True)
        )
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_d().filename)
        assert analysis.groups[0].document_type == "Resume"

    def test_scanned_file_without_ocr_is_flagged_not_crashed(
        self, pipeline, samples_dir: Path
    ) -> None:
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_d().filename)
        assert analysis.error is None
        assert analysis.review_group_count >= 1
        assert all(page.text_source is TextSource.NONE for page in analysis.pages)

    def test_failed_ocr_marks_the_page(self, profile, thresholds, samples_dir: Path) -> None:
        provider = RecordingOCRProvider(text="")  # available but recovers nothing
        pipeline = build_pipeline(
            profile, thresholds, ocr=OCRService(provider, enabled=True)
        )
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_d().filename)
        assert all(page.ocr_failed for page in analysis.pages)

    def test_the_exported_pdf_still_contains_the_original_page(
        self, profile, thresholds, samples_dir: Path, tmp_path: Path
    ) -> None:
        """OCR informs classification; it must never replace the original page."""
        from app.services.export_service import ExportService
        from app.services.pdf_service import open_pdf

        provider = RecordingOCRProvider(
            text="\n".join(sample_data.resume_pages(total=1)[0].lines)
        )
        pipeline = build_pipeline(
            profile, thresholds, ocr=OCRService(provider, enabled=True)
        )
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_d().filename)
        result = ExportService().export([analysis], tmp_path)

        assert result.document_count == 1
        with open_pdf(result.exported[0].output_path) as document:
            page = document.load_page(0)
            # Still an image-only scan, exactly as it arrived.
            assert page.get_images(full=True)
            assert not page.get_text("text").strip()


class TestOCRTextMerging:
    """OCR renders the whole page, so it already contains any native text.

    Concatenating the two duplicated every word on pages that carry a small
    text layer -- a separator page, a stamp, a page number -- which broke
    separator detection and doubled keyword counts.
    """

    def test_ocr_output_supersedes_a_covered_native_fragment(self) -> None:
        from app.services.processing_service import _ocr_supersedes_native

        assert _ocr_supersedes_native("RESUME", "RESUME")
        assert _ocr_supersedes_native("RESUME", "RESUME\nsome scanned body text")

    def test_punctuation_and_case_differences_still_count_as_covered(self) -> None:
        from app.services.processing_service import _ocr_supersedes_native

        assert _ocr_supersedes_native("Page 2 of 3", "page 2 of 3 — scanned content")

    def test_genuinely_new_native_text_is_kept(self) -> None:
        from app.services.processing_service import _ocr_supersedes_native

        assert not _ocr_supersedes_native(
            "Confidential watermark reference ABC12345",
            "completely different scanned content here",
        )

    def test_empty_native_text_is_trivially_covered(self) -> None:
        from app.services.processing_service import _ocr_supersedes_native

        assert _ocr_supersedes_native("", "anything")

    def test_separator_page_survives_ocr(
        self, profile, thresholds, samples_dir: Path
    ) -> None:
        """The regression: separator pages with OCR enabled.

        A real engine reads what is actually on each page, so the stub returns
        each separator's own label in turn -- only the two label pages are
        sparse enough to be sent to OCR at all.
        """
        from app.models.enums import SeparatorState

        provider = SequencedOCRProvider(["RESUME", "COVER LETTER"])
        pipeline = build_pipeline(
            profile, thresholds, ocr=OCRService(provider, enabled=True)
        )
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_f().filename)

        first = analysis.pages[0]
        assert first.extracted_text.strip() == "RESUME", first.extracted_text
        assert first.separator_state is not SeparatorState.NOT_SEPARATOR
        assert [(g.document_type, g.start_page, g.end_page) for g in analysis.groups] == [
            ("Resume", 1, 3),
            ("Cover Letter", 4, 5),
        ]


class TestNoConsoleWindow:
    """A windowed application must never flash a console at the user.

    Tesseract is a console executable run once per page needing OCR. Started
    naively from a GUI process on Windows, each call pops a black window --
    which is what a client actually saw during their first real analysis.
    """

    def test_every_tesseract_call_goes_through_the_helper(self) -> None:
        """No direct subprocess.run may creep back into the OCR provider."""
        source = Path("app/services/ocr_service.py").read_text(encoding="utf-8")
        body = source.split("def _run(", 1)[1]
        # The helper itself contains the one legitimate subprocess.run; any
        # other occurrence after it is a call site that skipped the helper.
        remainder = body.split("def _subprocess_env", 1)[1]
        assert "subprocess.run(" not in remainder, (
            "a Tesseract call bypasses _run() and will flash a console window"
        )

    def test_the_helper_suppresses_the_window_on_windows(self, monkeypatch) -> None:
        from app.services import ocr_service

        captured: dict[str, object] = {}

        def fake_run(arguments, **kwargs):
            captured.update(kwargs)
            captured["arguments"] = arguments
            return subprocess.CompletedProcess(arguments, 0, "", "")

        monkeypatch.setattr(ocr_service.sys, "platform", "win32")
        monkeypatch.setattr(ocr_service.subprocess, "run", fake_run)
        monkeypatch.setattr(
            ocr_service.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False
        )

        provider = ocr_service.TesseractOCRProvider()
        provider._run(["tesseract", "--version"], timeout=5)

        assert captured.get("creationflags") == 0x08000000, (
            "CREATE_NO_WINDOW was not applied; a console window will appear"
        )

    def test_the_flag_is_not_used_off_windows(self, monkeypatch) -> None:
        """CREATE_NO_WINDOW does not exist elsewhere and would raise."""
        from app.services import ocr_service

        captured: dict[str, object] = {}

        def fake_run(arguments, **kwargs):
            captured.update(kwargs)
            return subprocess.CompletedProcess(arguments, 0, "", "")

        monkeypatch.setattr(ocr_service.sys, "platform", "linux")
        monkeypatch.setattr(ocr_service.subprocess, "run", fake_run)

        ocr_service.TesseractOCRProvider()._run(["tesseract", "--version"], timeout=5)
        assert "creationflags" not in captured


class TestBlankPagesDoNotStartOCR:
    """OCR reads pictures of text. A blank page has no picture and no text.

    Running it anyway costs a process launch per page -- which on Windows is
    exactly what used to flash a console window -- to be told the page is
    empty, which the text layer already said.
    """

    def _document(self, tmp_path, *, blank: bool):
        import pymupdf

        path = tmp_path / ("blank.pdf" if blank else "drawn.pdf")
        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)
        if not blank:
            page.draw_rect(pymupdf.Rect(50, 50, 200, 200), fill=(0, 0, 0))
        doc.save(str(path))
        doc.close()
        return path

    def test_a_truly_blank_page_is_not_sent_to_ocr(self, tmp_path, profile, thresholds):
        from app.services import pdf_service
        from app.services.pdf_service import open_pdf

        path = self._document(tmp_path, blank=True)
        with open_pdf(path) as document:
            assert not pdf_service.page_has_visual_content(document, 0)

    def test_a_page_with_drawn_content_still_reaches_ocr(self, tmp_path):
        from app.services import pdf_service
        from app.services.pdf_service import open_pdf

        path = self._document(tmp_path, blank=False)
        with open_pdf(path) as document:
            assert pdf_service.page_has_visual_content(document, 0)

    def test_an_unreadable_page_still_gets_its_chance(self, tmp_path):
        """Erring towards OCR: never silently skip text because of an error."""
        from app.services import pdf_service

        class Broken:
            def load_page(self, _index):
                raise RuntimeError("unreadable")

        assert pdf_service.page_has_visual_content(Broken(), 0)

    def test_a_blank_page_records_no_ocr_and_no_failure(
        self, tmp_path, profile, thresholds
    ):
        """The diagnostics must show the page was handled, not OCR'd."""
        from tests.helpers import build_pipeline

        path = self._document(tmp_path, blank=True)
        analysis = build_pipeline(profile, thresholds).analyze_file(path)
        assert analysis.ocr_pages == 0
        assert analysis.ocr_failures == 0
