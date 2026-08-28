"""Real OCR integration tests.

These run a genuine Tesseract binary — the one bundled with an installed build
when present, otherwise whatever is on the machine — rather than a stub. They
prove the whole scanned-document path: an image-only PDF goes in, real text
comes out, the classifier uses it, and the exported PDF still contains the
original page rather than a re-rendered copy.

Run just these:

    pytest -m ocr_real

Skip them (they are slower than the unit tests):

    pytest -m "not ocr_real"
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from app.models.enums import TextSource
from app.profiles.recruiting import RESUME
from app.services.export_service import ExportService
from app.services.ocr_service import (
    OCRService,
    TesseractOCRProvider,
    describe_ocr_runtime,
    resolve_bundled_tesseract,
)
from app.services.pdf_service import open_pdf, page_needs_ocr
from app.services.text_features import extract_features

from tests.helpers import build_pipeline

pytestmark = pytest.mark.ocr_real

#: Rendered large enough that OCR of a synthetic page is reliable.
_SCAN_DPI = 200


def _ocr_provider() -> TesseractOCRProvider:
    return TesseractOCRProvider()


def _ocr_available() -> bool:
    return bool(_ocr_provider().is_available(refresh=True).available)


requires_ocr = pytest.mark.skipif(
    not _ocr_available(),
    reason="No Tesseract runtime available (bundled or system).",
)


# The text the synthetic scan contains. Deliberately resume-shaped so the
# classifier has something real to work with once OCR recovers it.
SCANNED_LINES = [
    "BENJAMIN PEREZ",
    "Austin, TX 78704   (555) 214-8890   benjamin.perez@example.com",
    "",
    "PROFESSIONAL SUMMARY",
    "Operations analyst with nine years of experience in capacity planning",
    "and logistics reporting for regional distribution networks.",
    "",
    "PROFESSIONAL EXPERIENCE",
    "",
    "Senior Operations Analyst",
    "Rivermark Logistics, Austin, TX",
    "June 2020 to Present",
    "Rebuilt the weekly capacity forecast used by regional directors.",
    "Consolidated regional spreadsheets into one governed reporting model.",
    "",
    "EDUCATION",
    "Bachelor of Science, Supply Chain Management",
    "The University of Texas at Austin",
    "",
    "SKILLS",
    "SQL, Python, Power BI, Tableau, SAP ERP",
]


@pytest.fixture(scope="module")
def scanned_pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """An image-only PDF: real pixels, no text layer at all."""
    directory = tmp_path_factory.mktemp("ocr_real")
    destination = directory / "scanned_resume.pdf"

    # Render the text to a page, rasterise it, then place only the raster in a
    # fresh document. The result is exactly what a flatbed scanner produces.
    source = pymupdf.open()
    try:
        page = source.new_page(width=612, height=792)
        y = 72.0
        for line in SCANNED_LINES:
            if line.strip():
                page.insert_text((62, y), line, fontsize=11, fontname="helv")
            y += 20
        pixmap = page.get_pixmap(dpi=_SCAN_DPI, colorspace=pymupdf.csGRAY, alpha=False)
    finally:
        source.close()

    output = pymupdf.open()
    try:
        image_page = output.new_page(width=612, height=792)
        image_page.insert_image(pymupdf.Rect(0, 0, 612, 792), pixmap=pixmap)
        output.save(str(destination), garbage=3, deflate=True)
    finally:
        output.close()

    return destination


@requires_ocr
class TestOCRRuntimeDiscovery:
    """Assertions about a runtime that is actually present.

    Skipped when no engine is installed -- for example on a fresh Windows CI
    runner before the bundled runtime has been staged.
    """

    def test_a_runtime_is_available(self) -> None:
        availability = _ocr_provider().is_available(refresh=True)
        assert availability.available, availability.message

    def test_english_is_installed(self) -> None:
        provider = _ocr_provider()
        provider.is_available(refresh=True)
        assert provider.supports_language("eng")

    def test_runtime_description_is_informative(self) -> None:
        description = describe_ocr_runtime()
        assert "tesseract" in description.lower()
        assert "languages" in description.lower()

    def test_bundled_runtime_is_preferred_when_present(self) -> None:
        """In an installed build the shipped engine must win over any other."""
        bundled = resolve_bundled_tesseract()
        if bundled is None:
            pytest.skip("This build has no bundled OCR runtime (source checkout).")
        provider = _ocr_provider()
        assert provider.resolve_executable() == str(bundled)
        assert provider.using_bundled


@requires_ocr
class TestScannedPdfPipeline:
    def test_the_page_really_has_no_text_layer(self, scanned_pdf: Path) -> None:
        """Step 3: prove native extraction is insufficient before OCR runs."""
        with open_pdf(scanned_pdf) as document:
            native = document.load_page(0).get_text("text")
        assert native.strip() == ""
        assert page_needs_ocr(native)

    def test_real_ocr_recovers_the_text(self, scanned_pdf: Path) -> None:
        """Steps 4-5: run the actual binary and check what comes back."""
        with open_pdf(scanned_pdf) as document:
            image = document.load_page(0).get_pixmap(dpi=300, alpha=False).tobytes("png")

        text = OCRService(_ocr_provider(), enabled=True).recognize(image)
        assert text.strip(), "OCR returned nothing at all"

        lowered = text.lower()
        # Don't demand a perfect transcript — demand the content is usable.
        for expected in ("perez", "experience", "education", "analyst"):
            assert expected in lowered, f"OCR did not recover {expected!r}:\n{text[:400]}"

    def test_ocr_text_is_good_enough_to_classify(self, scanned_pdf: Path) -> None:
        """Step 6: the recovered text must actually drive classification."""
        with open_pdf(scanned_pdf) as document:
            image = document.load_page(0).get_pixmap(dpi=300, alpha=False).tobytes("png")

        text = OCRService(_ocr_provider(), enabled=True).recognize(image)
        features = extract_features(text)
        assert features.word_count > 40

        from app.intelligence.base import PageContext
        from app.intelligence.rules_provider import RulesProvider
        from app.profiles import get_profile

        classification = RulesProvider(get_profile()).classify_page(
            PageContext(
                source_pdf=str(scanned_pdf),
                page_index=0,
                page_count=1,
                text=text,
                features=features,
            )
        )
        assert classification.document_type == RESUME
        assert classification.confidence >= 0.7

    def test_full_pipeline_analyses_a_scan_end_to_end(
        self, profile, thresholds, scanned_pdf: Path
    ) -> None:
        pipeline = build_pipeline(
            profile, thresholds, ocr=OCRService(_ocr_provider(), enabled=True)
        )
        analysis = pipeline.analyze_file(scanned_pdf)

        assert analysis.error is None
        assert analysis.ocr_pages == 1
        assert analysis.pages[0].text_source is TextSource.OCR
        assert analysis.pages[0].ocr_used
        assert not analysis.pages[0].ocr_failed
        assert len(analysis.groups) == 1
        assert analysis.groups[0].document_type == RESUME

    def test_identity_is_recovered_from_the_scan(
        self, profile, thresholds, scanned_pdf: Path
    ) -> None:
        pipeline = build_pipeline(
            profile, thresholds, ocr=OCRService(_ocr_provider(), enabled=True)
        )
        analysis = pipeline.analyze_file(scanned_pdf)
        candidate = analysis.groups[0].candidate
        # OCR of a synthetic scan is good but not perfect; require the email,
        # which is the most reliable machine-readable identifier on the page.
        assert candidate.email == "benjamin.perez@example.com"

    def test_export_keeps_the_original_scanned_page(
        self, profile, thresholds, scanned_pdf: Path, tmp_path: Path
    ) -> None:
        """Step 7: OCR informs understanding; it must never replace the page.

        The exported PDF has to be the original scan — same image, still no text
        layer — not a re-rendering of what OCR thought it saw.
        """
        pipeline = build_pipeline(
            profile, thresholds, ocr=OCRService(_ocr_provider(), enabled=True)
        )
        analysis = pipeline.analyze_file(scanned_pdf)
        result = ExportService().export([analysis], tmp_path)

        assert result.document_count == 1
        exported = result.exported[0].output_path

        def read(path: Path) -> tuple[pymupdf.Rect, str, bytes]:
            """Page geometry, text layer and embedded image, read while open."""
            with open_pdf(path) as document:
                assert document.page_count == 1
                page = document.load_page(0)
                images = page.get_images(full=True)
                assert images, f"{path.name} has no embedded image"
                return (
                    pymupdf.Rect(page.rect),
                    page.get_text("text"),
                    document.extract_image(images[0][0])["image"],
                )

        original_rect, original_text, original_image = read(scanned_pdf)
        exported_rect, exported_text, exported_image = read(exported)

        assert not exported_text.strip(), "OCR text was burned into the output"
        assert original_text.strip() == ""
        assert exported_rect == original_rect
        # Byte-identical image stream: the page was copied, not re-encoded.
        assert exported_image == original_image


@requires_ocr
class TestLargeNativeTextFileDoesNoOCR:
    """The other half of the OCR contract, and the one that costs money.

    OCR must fire on a scan and must *not* fire on anything else. A hundred
    pages of native text sent through Tesseract would turn a two-second
    analysis into a several-minute one and produce worse text than the page
    already had. This runs with real OCR switched on precisely so a regression
    that starts OCR-ing readable pages fails here rather than on a client's
    hundred-page file.

    The counters are printed as well as asserted: Windows CI has no other way
    to record what the packaged runtime actually did.
    """

    def test_a_hundred_page_native_file_never_reaches_tesseract(
        self, profile, thresholds, tmp_path: Path
    ) -> None:
        from app.models.enums import SeparatorPolicy
        from app.services.parsers.registry import build_default_registry
        from scripts.pageup_fixtures import build_bulk_compile

        batch = build_bulk_compile(filename="PageUp_OCR_Guard.pdf")
        path = batch.write(tmp_path)

        pipeline = build_pipeline(
            profile,
            thresholds,
            ocr=OCRService(_ocr_provider(), enabled=True),
            separator_policy=SeparatorPolicy.EXCLUDE,
            parser_registry=build_default_registry(profile),
        )
        analysis = pipeline.analyze_file(path)

        print(
            f"\n[measured] {analysis.page_count} native-text pages: "
            f"native_text_pages={analysis.native_text_pages} "
            f"ocr_pages={analysis.ocr_pages} "
            f"ocr_failures={analysis.ocr_failures}"
        )

        assert analysis.error is None
        assert analysis.page_count == 104
        assert analysis.native_text_pages == 104
        assert analysis.ocr_pages == 0, "readable pages were sent to OCR"
        assert analysis.ocr_failures == 0
        assert all(page.text_source is TextSource.NATIVE for page in analysis.pages)

    def test_the_runtime_reports_its_languages(self) -> None:
        """``--list-langs`` reaches the app, not just the command line."""
        availability = _ocr_provider().is_available(refresh=True)
        print(f"[measured] tesseract: {availability.version}")
        print(f"[measured] languages: {', '.join(availability.languages) or '(none reported)'}")
        assert availability.languages, "the runtime reported no languages at all"
        assert "eng" in availability.languages
