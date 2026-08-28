"""Command-line entry points used by installers, deployment tooling and CI.

Deliberately separate from the UI: ``--smoke-test`` exists so an automated
Windows installer check can prove the installed application actually works,
without any test-only branches leaking into the normal application flow.

    SmartPDFSorter.exe --version
    SmartPDFSorter.exe --smoke-test
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

from app import APP_NAME, APP_VERSION

#: Exit code returned when a smoke test fails.
SMOKE_TEST_FAILURE = 1


@dataclass
class SmokeTestReport:
    """Outcome of ``--smoke-test``."""

    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((name, ok, detail))

    @property
    def passed(self) -> bool:
        return all(ok for _name, ok, _detail in self.checks)

    def render(self) -> str:
        lines = [f"{APP_NAME} {APP_VERSION} smoke test", ""]
        for name, ok, detail in self.checks:
            mark = "PASS" if ok else "FAIL"
            lines.append(f"  [{mark}] {name}" + (f" - {detail}" if detail else ""))
        lines.append("")
        lines.append("RESULT: PASS" if self.passed else "RESULT: FAIL")
        return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="SmartPDFSorter",
        description=f"{APP_NAME} - group combined PDFs into logical documents.",
        add_help=True,
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the application version and exit.",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help=(
            "Initialise the runtime, confirm it is healthy, and exit. "
            "Processes no user documents and leaves no window open."
        ),
    )
    parser.add_argument(
        "--ocr-info",
        action="store_true",
        help="Report which OCR engine this installation will use, and exit.",
    )
    return parser


def run_smoke_test(verbose: bool = True) -> int:
    """Prove the installed application can start and do real work.

    Exercises the pieces that actually break when packaging goes wrong: bundled
    data files, the Qt platform plugins, PDF handling, the classifier, and OCR
    discovery. No user data is touched -- the PDF it analyses is generated in
    memory-backed temporary storage and deleted immediately.
    """
    report = SmokeTestReport()

    # -- version ------------------------------------------------------------
    try:
        from app.version import __version__, windows_version

        report.record("Version metadata", True, f"{__version__} ({windows_version()})")
    except Exception as exc:
        report.record("Version metadata", False, str(exc))

    # -- settings and history (user data locations) --------------------------
    try:
        from app.storage.history_store import HistoryStore
        from app.storage.settings_store import SettingsStore
        from app.utils.paths import app_data_dir

        settings = SettingsStore().load()
        HistoryStore()
        report.record("User data location", True, str(app_data_dir()))
        report.record("Settings load", True, f"provider={settings.provider}")
    except Exception as exc:
        report.record("Settings load", False, str(exc))
        settings = None

    # -- profile and classifier ---------------------------------------------
    try:
        from app.profiles import get_profile

        profile = get_profile(settings.profile_name if settings else None)
        report.record(
            "Document profile", True, f"{profile.name} ({len(profile.document_types)} types)"
        )
    except Exception as exc:
        report.record("Document profile", False, str(exc))
        profile = None

    # -- PDF engine + end-to-end analysis -----------------------------------
    try:
        import tempfile
        from pathlib import Path

        from app.services.app_services import build_analysis_services
        from app.storage.settings_store import AppSettings

        with tempfile.TemporaryDirectory(prefix="sps-smoke-") as tmp:
            sample = _write_smoke_pdf(Path(tmp) / "smoke.pdf")
            services = build_analysis_services(settings or AppSettings())
            analysis = services.pipeline.analyze_file(sample)
            services.close()

        ok = bool(analysis.groups) and analysis.error is None
        report.record(
            "PDF analysis",
            ok,
            f"{analysis.page_count} pages -> {len(analysis.groups)} document(s)"
            if ok
            else (analysis.error or "no documents detected"),
        )
    except Exception as exc:
        report.record("PDF analysis", False, f"{type(exc).__name__}: {exc}")

    # -- OCR ----------------------------------------------------------------
    try:
        from app.services.ocr_service import describe_ocr_runtime

        description = describe_ocr_runtime(settings.tesseract_path if settings else "")
        # OCR being unavailable is a warning, not a smoke-test failure: the
        # application is designed to work without it.
        report.record("OCR runtime", True, description)
    except Exception as exc:
        report.record("OCR runtime", False, str(exc))

    # -- Qt -----------------------------------------------------------------
    try:
        ok, detail = _check_qt()
        report.record("Qt runtime", ok, detail)
    except Exception as exc:
        report.record("Qt runtime", False, f"{type(exc).__name__}: {exc}")

    if verbose:
        print(report.render())
    return 0 if report.passed else SMOKE_TEST_FAILURE


def _write_smoke_pdf(path):
    """Create a tiny single-page PDF with recognisable resume text."""
    import pymupdf

    lines = [
        "Alex Smoke",
        "Austin, TX  |  (555) 010-2030  |  alex.smoke@example.com",
        "",
        "PROFESSIONAL EXPERIENCE",
        "Operations Analyst",
        "Example Logistics",
        "June 2020 - Present",
        "- Built the weekly capacity forecast used by regional directors.",
        "",
        "EDUCATION",
        "Bachelor of Science, Supply Chain Management",
        "",
        "SKILLS",
        "SQL, Python, Power BI",
    ]
    document = pymupdf.open()
    try:
        page = document.new_page(width=612, height=792)
        y = 72.0
        for line in lines:
            if line:
                page.insert_text((62, y), line, fontsize=10, fontname="helv")
            y += 14
        document.save(str(path), garbage=3, deflate=True)
    finally:
        document.close()
    return path


def _check_qt() -> tuple[bool, str]:
    """Create a real widget offscreen, then tear it down.

    This is what catches a packaging failure where the Qt platform plugins were
    not bundled -- the application would otherwise start and immediately die in
    front of the user.
    """
    import os

    # Never pop a window during an unattended install check.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6 import __version__ as pyside_version
    from PySide6.QtWidgets import QApplication, QLabel

    app = QApplication.instance() or QApplication([])
    label = QLabel("smoke")
    label.show()
    app.processEvents()
    label.close()
    label.deleteLater()
    app.processEvents()
    return True, f"PySide6 {pyside_version} ({os.environ['QT_QPA_PLATFORM']})"


def run_ocr_info() -> int:
    """Print which OCR engine this installation will use."""
    from app.services.ocr_service import describe_ocr_runtime, resolve_bundled_tesseract
    from app.storage.settings_store import SettingsStore

    settings = SettingsStore().load()
    bundled = resolve_bundled_tesseract()

    print(f"{APP_NAME} {APP_VERSION} - OCR runtime")
    print(f"  Bundled engine : {bundled or 'not present in this build'}")
    print(f"  Configured path: {settings.tesseract_path or '(auto-detect)'}")
    print(f"  Status         : {describe_ocr_runtime(settings.tesseract_path)}")
    return 0


def _reopen_on_console(stream_name: str) -> None:
    """Point one standard stream at the console, unless it already has a home."""
    stream = getattr(sys, stream_name, None)
    if stream is not None:
        try:
            stream.fileno()
        except (OSError, ValueError, AttributeError):
            pass
        else:
            # Already attached to a real file or pipe — a caller redirected us
            # deliberately and must keep getting the output where it asked.
            return
    try:
        setattr(
            sys,
            stream_name,
            open("CONOUT$", "w", buffering=1, encoding="utf-8", errors="replace"),
        )
    except OSError:
        pass


def attach_parent_console() -> None:
    """Make command-line output visible when a windowed build is run manually.

    ``SmartPDFSorter.exe`` is a GUI-subsystem executable, so Windows starts it
    with no console at all and everything it prints goes nowhere the user can
    see. Windows does let such a process borrow the console of whoever launched
    it, which is what makes ``--version`` behave the way the documentation says
    it does when someone types it into PowerShell.

    Redirected streams are left alone, so scripted callers that asked for the
    output in a file still get it there.
    """
    if sys.platform != "win32":
        return
    import ctypes

    ATTACH_PARENT_PROCESS = -1
    try:
        attached = ctypes.windll.kernel32.AttachConsole(ATTACH_PARENT_PROCESS)
    except (AttributeError, OSError):  # pragma: no cover - non-Windows guard
        return
    if not attached:
        # No parent console (launched from Explorer, or we already have one).
        return
    _reopen_on_console("stdout")
    _reopen_on_console("stderr")


def handle_cli(argv: list[str] | None = None) -> int | None:
    """Handle a command-line-only invocation.

    Returns an exit code when the arguments were fully handled, or ``None`` when
    the application should start its user interface normally.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        return None

    # Unknown arguments (or Qt's own switches) must not stop the GUI starting.
    known = {"--version", "-V", "--smoke-test", "--ocr-info", "--help", "-h"}
    if not any(argument in known for argument in arguments):
        return None

    # Only now, once we know this is a command-line run rather than a GUI
    # launch, is it right to reach for the caller's console.
    attach_parent_console()

    parser = build_parser()
    if "-V" in arguments:
        arguments = ["--version" if a == "-V" else a for a in arguments]

    namespace, _unknown = parser.parse_known_args(arguments)

    if namespace.version:
        print(f"{APP_NAME} {APP_VERSION}")
        return 0
    if namespace.smoke_test:
        return run_smoke_test()
    if namespace.ocr_info:
        return run_ocr_info()
    return None


__all__ = [
    "attach_parent_console",
    "handle_cli",
    "run_smoke_test",
    "run_ocr_info",
    "build_parser",
    "SmokeTestReport",
    "SMOKE_TEST_FAILURE",
]
