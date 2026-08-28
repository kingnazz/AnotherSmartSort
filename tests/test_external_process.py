"""No process this application starts may put a window on screen.

Two kinds of check, because one alone would not hold. The behavioural tests
prove every Tesseract path goes through the shared helper with the Windows
hide flags applied. The source audit proves nobody has quietly added a fourth
path that bypasses it -- which is how the original defect would come back.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.ocr_service import TesseractOCRProvider
from app.utils import external_process
from app.utils.external_process import hidden_process_options, run_hidden

APP_ROOT = Path(__file__).resolve().parent.parent / "app"

#: The one module allowed to call subprocess directly. Everything else must
#: route through it.
SANCTIONED = {"external_process.py"}

#: Ways a child process can be started. Any of these outside the sanctioned
#: module is a potential popup window.
LAUNCHERS = {"run", "Popen", "call", "check_call", "check_output"}


class _Recorder:
    """Captures what would have been launched, without launching it."""

    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.calls: list[tuple[tuple, dict]] = []
        self.stdout = stdout
        self.returncode = returncode

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args=args[0] if args else [],
            returncode=self.returncode,
            stdout=self.stdout,
            stderr="",
        )


@pytest.fixture
def recorder(monkeypatch):
    record = _Recorder(stdout="tesseract 5.3.0\n")
    monkeypatch.setattr(subprocess, "run", record)
    return record


class TestHiddenProcessOptions:
    def test_windows_gets_both_suppression_mechanisms(self, monkeypatch) -> None:
        """CREATE_NO_WINDOW alone has not proven sufficient in packaged builds."""
        monkeypatch.setattr(external_process.sys, "platform", "win32")
        # The flags only exist on Windows builds of Python; skip where absent
        # rather than asserting against a shim that would prove nothing.
        if not hasattr(subprocess, "STARTUPINFO"):
            pytest.skip("Windows-only subprocess attributes are unavailable here")

        options = hidden_process_options()
        assert options["creationflags"] == subprocess.CREATE_NO_WINDOW
        startupinfo = options["startupinfo"]
        assert startupinfo.dwFlags & subprocess.STARTF_USESHOWWINDOW
        assert startupinfo.wShowWindow == subprocess.SW_HIDE

    def test_other_platforms_need_no_flags(self, monkeypatch) -> None:
        monkeypatch.setattr(external_process.sys, "platform", "linux")
        assert hidden_process_options() == {}

    def test_a_shell_is_never_used(self, recorder) -> None:
        """Routing through cmd.exe would create a console and invite injection."""
        run_hidden(["tesseract", "--version"], purpose="test")
        _args, kwargs = recorder.calls[0]
        assert kwargs.get("shell") in (None, False)

    def test_the_command_is_a_list_not_a_string(self, recorder) -> None:
        run_hidden(["tesseract", "--version"], purpose="test")
        args, _kwargs = recorder.calls[0]
        assert isinstance(args[0], list)


class TestEveryTesseractPathIsHidden:
    """--version, --list-langs and page OCR must all go through the helper."""

    @pytest.fixture
    def provider(self, tmp_path: Path) -> TesseractOCRProvider:
        executable = tmp_path / (
            "tesseract.exe" if sys.platform.startswith("win") else "tesseract"
        )
        executable.write_text("#!/bin/sh\n")
        executable.chmod(0o755)
        return TesseractOCRProvider(str(executable))

    def test_version_check_is_hidden(self, provider, recorder, monkeypatch) -> None:
        monkeypatch.setattr(external_process, "is_windows", lambda: True)
        monkeypatch.setattr(
            external_process, "hidden_process_options", lambda: {"creationflags": 0x08000000}
        )
        provider.is_available(refresh=True)

        assert recorder.calls, "the version check did not run"
        for _args, kwargs in recorder.calls:
            assert kwargs.get("creationflags") == 0x08000000

    def test_language_list_is_hidden(self, provider, recorder, monkeypatch) -> None:
        monkeypatch.setattr(
            external_process, "hidden_process_options", lambda: {"creationflags": 0x08000000}
        )
        provider.is_available(refresh=True)

        commands = [args[0] for args, _ in recorder.calls]
        assert any("--list-langs" in command for command in commands)
        for _args, kwargs in recorder.calls:
            assert kwargs.get("creationflags") == 0x08000000

    def test_page_recognition_is_hidden(self, provider, recorder, monkeypatch) -> None:
        monkeypatch.setattr(
            external_process, "hidden_process_options", lambda: {"creationflags": 0x08000000}
        )
        provider._availability = None
        provider.extract_text(b"fake-png-bytes", language="eng")

        assert recorder.calls, "page OCR did not run"
        for _args, kwargs in recorder.calls:
            assert kwargs.get("creationflags") == 0x08000000

    def test_a_configured_external_tesseract_uses_the_same_path(
        self, tmp_path: Path, recorder, monkeypatch
    ) -> None:
        """A user-supplied path must not bypass the hiding either."""
        monkeypatch.setattr(
            external_process, "hidden_process_options", lambda: {"creationflags": 0x08000000}
        )
        external = tmp_path / "custom-tesseract"
        external.write_text("#!/bin/sh\n")
        external.chmod(0o755)

        provider = TesseractOCRProvider(str(external))
        provider.is_available(refresh=True)

        assert recorder.calls
        assert str(external) in recorder.calls[0][0][0][0]
        for _args, kwargs in recorder.calls:
            assert kwargs.get("creationflags") == 0x08000000


class TestDiagnostics:
    def test_launches_are_logged_without_document_content(self, recorder, caplog) -> None:
        """Support needs to identify a stray popup; nobody needs the resume."""
        import logging

        with caplog.at_level(logging.INFO, logger="smartpdfsorter.process"):
            run_hidden(
                ["tesseract", "page.png", "stdout"],
                purpose="ocr.page",
                detail="page=14",
            )

        text = "\n".join(record.getMessage() for record in caplog.records)
        assert "external_process.start" in text
        assert "external_process.complete" in text
        assert "purpose=" in text and "exit_code=" in text
        assert "duration_ms=" in text

    def test_a_failure_is_logged_too(self, monkeypatch, caplog) -> None:
        import logging

        def explode(*_args, **_kwargs):
            raise OSError("no such executable")

        monkeypatch.setattr(subprocess, "run", explode)
        with caplog.at_level(logging.INFO, logger="smartpdfsorter.process"):
            with pytest.raises(OSError):
                run_hidden(["missing-program"], purpose="test")

        text = "\n".join(record.getMessage() for record in caplog.records)
        assert "external_process.failed" in text


def _launch_sites(path: Path) -> list[str]:
    """Every child-process launch in one source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func

        # subprocess.run(...) / subprocess.Popen(...)
        if isinstance(func, ast.Attribute):
            if (
                isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"
                and func.attr in LAUNCHERS
            ):
                found.append(f"subprocess.{func.attr} (line {node.lineno})")
            if (
                isinstance(func.value, ast.Name)
                and func.value.id == "os"
                and func.attr in {"system", "popen", "spawnv", "spawnl"}
            ):
                found.append(f"os.{func.attr} (line {node.lineno})")

        # Bare run(...)/Popen(...) from a `from subprocess import run` style import.
        if isinstance(func, ast.Name) and func.id in {"Popen", "check_output"}:
            found.append(f"{func.id} (line {node.lineno})")

    return found


class TestNoUnsanctionedLaunchSites:
    """A source audit, so a future direct launch fails here rather than on a
    client's screen."""

    def test_only_the_helper_starts_processes(self) -> None:
        offenders: dict[str, list[str]] = {}

        for path in sorted(APP_ROOT.rglob("*.py")):
            if path.name in SANCTIONED:
                continue
            sites = _launch_sites(path)
            if sites:
                offenders[str(path.relative_to(APP_ROOT.parent))] = sites

        assert not offenders, (
            "these files start a child process directly instead of using "
            "app.utils.external_process.run_hidden, which risks a console "
            f"window appearing: {offenders}"
        )

    def test_nothing_invokes_a_shell_interpreter(self) -> None:
        """Launching cmd.exe/powershell to run a program creates a console.

        The helper module is exempt because it *names* these in prose, to
        record why they are avoided -- the point of the rule is that no code
        calls them.
        """
        offenders: list[str] = []
        for path in sorted(APP_ROOT.rglob("*.py")):
            if path.name in SANCTIONED:
                continue
            text = path.read_text(encoding="utf-8").lower()
            for needle in ("cmd.exe", "powershell.exe", "shell=true"):
                if needle in text:
                    offenders.append(f"{path.name}: {needle}")
        assert not offenders, offenders

    def test_the_audit_would_catch_a_regression(self, tmp_path: Path) -> None:
        """Mutation check: the audit must actually fail on a direct launch."""
        offender = tmp_path / "sneaky.py"
        offender.write_text(
            "import subprocess\n"
            "def go():\n"
            "    subprocess.run(['tesseract', '--version'])\n",
            encoding="utf-8",
        )
        assert _launch_sites(offender), "the audit would not notice a direct launch"
