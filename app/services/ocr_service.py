"""OCR abstraction and the built-in Tesseract implementation.

OCR is used to *understand* scanned pages, never to rebuild them: the exported
PDF always contains the original page, not a re-rendered image. Additional
engines can be added by implementing :class:`OCRProvider`.

Installed builds ship a pinned Tesseract runtime beside the application, so a
user on a clean Windows machine can process a scanned PDF immediately without
installing anything or configuring a path. Discovery order is:

1. the path the user configured in Settings (explicit wins);
2. the OCR runtime bundled with this installation;
3. ``tesseract`` on ``PATH``;
4. the usual per-platform install locations.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from app import APP_NAME
from app.utils.external_process import run_hidden
from app.utils.logging_setup import get_logger
from app.utils.paths import resource_path

logger = get_logger("ocr")

_VERSION_TIMEOUT = 10
_OCR_TIMEOUT = 120

#: Directory name holding the bundled OCR runtime, next to the application.
BUNDLED_OCR_DIRNAME = "ocr"
#: Trained-data directory inside the bundled runtime.
BUNDLED_TESSDATA_DIRNAME = "tessdata"


def _tesseract_executable_name() -> str:
    return "tesseract.exe" if sys.platform.startswith("win") else "tesseract"


def bundled_ocr_dir() -> Path | None:
    """Locate the OCR runtime shipped with this installation, if present.

    Handles all three layouts the application runs in: a PyInstaller onedir
    install (next to the executable), a PyInstaller onefile build (extracted
    under ``_MEIPASS``), and a plain source checkout.
    """
    candidates: list[Path] = []

    # Alongside the executable -- the installed (onedir) layout.
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / BUNDLED_OCR_DIRNAME)

    # Bundled as data -- the onefile layout unpacks here, and this also resolves
    # to <repo>/ocr when running from source.
    candidates.append(Path(resource_path(BUNDLED_OCR_DIRNAME)))

    for candidate in candidates:
        try:
            if (candidate / _tesseract_executable_name()).is_file():
                return candidate
        except OSError:  # pragma: no cover - defensive
            continue
    return None


def resolve_bundled_tesseract() -> Path | None:
    """Full path to the bundled ``tesseract`` executable, or ``None``."""
    directory = bundled_ocr_dir()
    if directory is None:
        return None
    executable = directory / _tesseract_executable_name()
    return executable if executable.is_file() else None


def bundled_tessdata_dir() -> Path | None:
    """Trained-data directory of the bundled runtime, if present."""
    directory = bundled_ocr_dir()
    if directory is None:
        return None
    tessdata = directory / BUNDLED_TESSDATA_DIRNAME
    return tessdata if tessdata.is_dir() else None


@dataclass(frozen=True)
class OCRAvailability:
    """Whether OCR can run, and a message the UI can show verbatim."""

    available: bool
    message: str
    version: str | None = None
    languages: tuple[str, ...] = ()

    def __bool__(self) -> bool:  # pragma: no cover - trivial
        return self.available


class OCRProvider(ABC):
    """Interface every OCR engine implements."""

    name: str = "ocr"

    @abstractmethod
    def is_available(self) -> OCRAvailability:
        """Report whether the engine can be used right now."""

    @abstractmethod
    def extract_text(self, page_image: bytes, *, language: str = "eng") -> str:
        """Return the text found in a rendered page image (PNG bytes)."""


class NullOCRProvider(OCRProvider):
    """Used when OCR is disabled. Always unavailable, never raises."""

    name = "disabled"

    def is_available(self) -> OCRAvailability:
        return OCRAvailability(False, "OCR fallback is turned off in Settings.")

    def extract_text(self, page_image: bytes, *, language: str = "eng") -> str:
        return ""


class TesseractOCRProvider(OCRProvider):
    """Local OCR through the Tesseract executable.

    Invoked as a subprocess rather than through a Python binding so the engine
    stays optional, the executable path stays configurable, and a crashing OCR
    run can never take the application down with it.
    """

    name = "tesseract"

    def __init__(self, executable: str | None = None) -> None:
        self.executable = (executable or "").strip() or None
        self._availability: OCRAvailability | None = None
        #: True when the resolved engine is the one shipped with this build.
        self.using_bundled = False

    # ------------------------------------------------------------------
    def resolve_executable(self) -> str | None:
        """Locate the Tesseract binary.

        An explicitly configured path always wins, so a user can point at their
        own build. Otherwise the runtime bundled with this installation is
        preferred over anything on the machine, which keeps behaviour identical
        across every client PC regardless of what else is installed.
        """
        if self.executable:
            candidate = Path(self.executable)
            if candidate.is_file() and os.access(candidate, os.X_OK):
                self.using_bundled = False
                return str(candidate)
            found = shutil.which(self.executable)
            if found:
                self.using_bundled = False
                return found
            return None

        bundled = resolve_bundled_tesseract()
        if bundled is not None:
            self.using_bundled = True
            return str(bundled)

        self.using_bundled = False
        found = shutil.which("tesseract")
        if found:
            return found

        for guess in (
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            "/usr/bin/tesseract",
            "/usr/local/bin/tesseract",
            "/opt/homebrew/bin/tesseract",
        ):
            if Path(guess).is_file():
                return guess
        return None

    def _run(
        self,
        arguments: list[str],
        *,
        timeout: float,
        purpose: str = "ocr",
        detail: str = "",
    ) -> subprocess.CompletedProcess:
        """Run Tesseract through the shared hidden-process helper.

        Every Tesseract invocation -- ``--version``, ``--list-langs`` and page
        recognition alike -- goes through here, so none of them can start a
        visible console window. See :mod:`app.utils.external_process` for why
        both Windows mechanisms are applied.
        """
        return run_hidden(
            arguments,
            purpose=purpose,
            timeout=timeout,
            env=self._subprocess_env(),
            detail=detail,
        )

    def _subprocess_env(self) -> dict[str, str]:
        """Environment for Tesseract, pointing it at the bundled trained data.

        Without ``TESSDATA_PREFIX`` a relocated Tesseract cannot find its
        language files and fails with "Error opening data file".
        """
        env = os.environ.copy()
        if self.using_bundled:
            tessdata = bundled_tessdata_dir()
            if tessdata is not None:
                env["TESSDATA_PREFIX"] = str(tessdata)
        return env

    def is_available(self, *, refresh: bool = False) -> OCRAvailability:
        if self._availability is not None and not refresh:
            return self._availability

        executable = self.resolve_executable()
        if not executable:
            self._availability = OCRAvailability(
                False,
                "No OCR engine was found. This installation should include one; "
                f"reinstall {APP_NAME}, or set the Tesseract path in Settings, "
                "to read scanned pages.",
            )
            return self._availability

        try:
            result = self._run(
                [executable, "--version"],
                timeout=_VERSION_TIMEOUT,
                purpose="ocr.version",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self._availability = OCRAvailability(
                False, f"Tesseract could not be started: {exc}"
            )
            return self._availability

        if result.returncode != 0:
            self._availability = OCRAvailability(
                False, "Tesseract is installed but did not run successfully."
            )
            return self._availability

        version = (result.stdout or result.stderr or "").splitlines()
        version_text = version[0].strip() if version else "tesseract"
        origin = (
            f"included with {APP_NAME}" if self.using_bundled else "found on this computer"
        )
        self._availability = OCRAvailability(
            True,
            f"{version_text} is ready ({origin}).",
            version=version_text,
            languages=self._list_languages(executable),
        )
        return self._availability

    def _list_languages(self, executable: str) -> tuple[str, ...]:
        try:
            result = self._run(
                [executable, "--list-langs"],
                timeout=_VERSION_TIMEOUT,
                purpose="ocr.languages",
            )
        except (OSError, subprocess.SubprocessError):
            return ()
        lines = [line.strip() for line in (result.stdout or "").splitlines()[1:]]
        return tuple(line for line in lines if line and " " not in line)

    def supports_language(self, language: str) -> bool:
        availability = self.is_available()
        if not availability.available or not availability.languages:
            # Unknown language list: let Tesseract decide at run time.
            return True
        wanted = {part.strip() for part in (language or "eng").split("+") if part.strip()}
        return wanted.issubset(set(availability.languages))

    # ------------------------------------------------------------------
    def extract_text(self, page_image: bytes, *, language: str = "eng") -> str:
        """Run OCR over a rendered page image. Returns "" on any failure."""
        availability = self.is_available()
        if not availability.available or not page_image:
            return ""

        executable = self.resolve_executable()
        if not executable:
            return ""

        temp_dir = tempfile.mkdtemp(prefix="sps-ocr-")
        image_path = Path(temp_dir) / "page.png"
        try:
            image_path.write_bytes(page_image)
            command = [executable, str(image_path), "stdout", "-l", language or "eng"]
            result = self._run(command, timeout=_OCR_TIMEOUT, purpose="ocr.page")
            if result.returncode != 0:
                logger.warning(
                    "Tesseract exited with code %s (language=%s)", result.returncode, language
                )
                return ""
            return result.stdout or ""
        except subprocess.TimeoutExpired:
            logger.warning("Tesseract timed out after %ss", _OCR_TIMEOUT)
            return ""
        except OSError as exc:
            logger.warning("OCR failed: %s", exc)
            return ""
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class OCRService:
    """Policy layer: decides *whether* to OCR and delegates to a provider."""

    def __init__(
        self,
        provider: OCRProvider | None = None,
        *,
        enabled: bool = True,
        language: str = "eng",
        text_threshold: int = 24,
    ) -> None:
        self.provider = provider or NullOCRProvider()
        self.enabled = enabled
        self.language = language or "eng"
        self.text_threshold = text_threshold
        self._warned = False

    @property
    def is_enabled(self) -> bool:
        return self.enabled and not isinstance(self.provider, NullOCRProvider)

    def availability(self) -> OCRAvailability:
        if not self.enabled:
            return OCRAvailability(False, "OCR fallback is turned off in Settings.")
        return self.provider.is_available()

    def should_ocr(self, native_text: str) -> bool:
        """OCR only when the page has too little native text to be useful."""
        return len((native_text or "").strip()) < self.text_threshold

    def recognize(self, page_image: bytes) -> str:
        """OCR a rendered page; returns "" when OCR is unavailable or fails."""
        if not self.is_enabled or not page_image:
            return ""
        availability = self.provider.is_available()
        if not availability.available:
            if not self._warned:
                logger.warning("OCR requested but unavailable: %s", availability.message)
                self._warned = True
            return ""
        try:
            return self.provider.extract_text(page_image, language=self.language)
        except Exception as exc:  # pragma: no cover - provider must never crash a batch
            logger.warning("OCR provider raised: %s", exc)
            return ""


def build_ocr_service(
    *,
    enabled: bool,
    executable: str | None,
    language: str = "eng",
    text_threshold: int = 24,
) -> OCRService:
    """Construct the OCR service from user settings."""
    provider: OCRProvider = (
        TesseractOCRProvider(executable) if enabled else NullOCRProvider()
    )
    return OCRService(
        provider, enabled=enabled, language=language, text_threshold=text_threshold
    )


def describe_ocr_runtime(configured_path: str | None = None) -> str:
    """One-line description of the OCR engine this installation will use.

    Used by ``--ocr-info``, the smoke test and the About dialog.
    """
    provider = TesseractOCRProvider(configured_path)
    availability = provider.is_available(refresh=True)
    if not availability.available:
        return availability.message

    location = provider.resolve_executable() or "unknown location"
    languages = ", ".join(availability.languages) if availability.languages else "unknown"
    origin = "bundled" if provider.using_bundled else "system"
    return f"{availability.version} [{origin}] at {location}; languages: {languages}"


__all__ = [
    "OCRProvider",
    "OCRAvailability",
    "NullOCRProvider",
    "TesseractOCRProvider",
    "OCRService",
    "build_ocr_service",
    "describe_ocr_runtime",
    "bundled_ocr_dir",
    "bundled_tessdata_dir",
    "resolve_bundled_tesseract",
    "BUNDLED_OCR_DIRNAME",
]
