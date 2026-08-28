"""Versioning, command-line entry points, bundled-OCR discovery and packaging.

These guard the deployment surface: a version that drifts between files, a
smoke test that stops proving anything, or OCR discovery that silently prefers
the wrong engine would all ship broken installers.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app import APP_NAME, APP_VERSION
from app.services import ocr_service
from app.version import (
    __version__,
    is_prerelease,
    version_tuple,
    windows_version,
    windows_version_tuple,
)

ROOT = Path(__file__).resolve().parent.parent


class TestVersionSourceOfTruth:
    def test_semantic_version_format(self) -> None:
        assert re.match(r"^\d+\.\d+\.\d+", __version__), __version__

    def test_version_tuple(self) -> None:
        assert version_tuple() == (1, 0, 0)

    def test_windows_four_part_version(self) -> None:
        assert windows_version() == "1.0.0.0"
        assert windows_version(7) == "1.0.0.7"
        assert windows_version_tuple() == (1, 0, 0, 0)

    def test_package_exports_the_same_version(self) -> None:
        assert APP_VERSION == __version__

    def test_pyproject_does_not_hard_code_a_version(self) -> None:
        """A second literal version is exactly how these drift apart."""
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert 'version = "1.0' not in text, "pyproject.toml pins a literal version"
        assert 'dynamic = ["version"]' in text
        assert "app.version.__version__" in text

    def test_no_module_hard_codes_a_version_string(self) -> None:
        offenders = []
        for path in (ROOT / "app").rglob("*.py"):
            if path.name == "version.py":
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r'"\d+\.\d+\.\d+"', line) and "version" in line.lower():
                    offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
        assert not offenders, "hard-coded versions found:\n" + "\n".join(offenders)

    def test_module_prints_the_version(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "app.version"],
            capture_output=True, text=True, cwd=ROOT, check=True,
        )
        assert result.stdout.strip() == __version__

    def test_module_prints_the_windows_version(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "app.version", "--windows"],
            capture_output=True, text=True, cwd=ROOT, check=True,
        )
        assert result.stdout.strip() == windows_version()

    def test_release_is_not_a_prerelease(self) -> None:
        assert not is_prerelease()


class TestCommandLine:
    def _run(self, *arguments: str) -> subprocess.CompletedProcess:
        import os

        environment = dict(os.environ)
        environment["QT_QPA_PLATFORM"] = "offscreen"
        return subprocess.run(
            [sys.executable, "-m", "app.main", *arguments],
            capture_output=True, text=True, cwd=ROOT, env=environment, timeout=300,
        )

    def test_version_flag(self) -> None:
        result = self._run("--version")
        assert result.returncode == 0
        assert APP_NAME in result.stdout
        assert __version__ in result.stdout

    def test_smoke_test_succeeds(self) -> None:
        result = self._run("--smoke-test")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "RESULT: PASS" in result.stdout

    def test_smoke_test_reports_each_check(self) -> None:
        result = self._run("--smoke-test")
        for check in (
            "Version metadata",
            "Settings load",
            "Document profile",
            "PDF analysis",
            "OCR runtime",
            "Qt runtime",
        ):
            assert check in result.stdout, f"{check} missing from the smoke test"

    def test_smoke_test_actually_analyses_a_pdf(self) -> None:
        """It must exercise the real pipeline, not just import modules."""
        result = self._run("--smoke-test")
        assert re.search(r"PDF analysis - \d+ pages -> \d+ document", result.stdout)

    def test_ocr_info(self) -> None:
        result = self._run("--ocr-info")
        assert result.returncode == 0
        assert "OCR runtime" in result.stdout

    def test_help_does_not_start_the_gui(self) -> None:
        result = self._run("--help")
        assert result.returncode == 0
        assert "smoke-test" in result.stdout

    def test_unknown_arguments_do_not_hijack_startup(self) -> None:
        """Qt's own switches must fall through to the GUI, not be swallowed."""
        from app.cli import handle_cli

        assert handle_cli(["-platform", "offscreen"]) is None
        assert handle_cli([]) is None

    def test_recognised_flags_are_handled(self) -> None:
        from app.cli import handle_cli

        assert handle_cli(["--version"]) == 0


class TestConsoleAttachment:
    """A windowed build has no console, so CLI output needs the caller's.

    Without this, `SmartPDFSorter.exe --version` prints into the void and the
    documented verification commands appear to do nothing at all.
    """

    def test_it_does_nothing_off_windows(self) -> None:
        from app.cli import attach_parent_console

        # The real assertion is that this is safe to call unconditionally.
        attach_parent_console()

    def test_a_redirected_stream_is_left_alone(self, tmp_path: Path) -> None:
        """A caller who redirected output must keep receiving it there."""
        from app.cli import _reopen_on_console

        target = tmp_path / "captured.txt"
        with open(target, "w", encoding="utf-8") as handle:
            original = sys.stdout
            sys.stdout = handle
            try:
                _reopen_on_console("stdout")
                assert sys.stdout is handle, "a real redirect was overwritten"
            finally:
                sys.stdout = original

    def test_a_dead_stream_is_replaced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A windowed build starts with no usable stdout; it must be rebuilt."""
        from app import cli

        monkeypatch.setattr(cli.sys, "stdout", None, raising=False)
        opened: list[str] = []

        def fake_open(name: str, *args: object, **kwargs: object) -> io.StringIO:
            opened.append(name)
            return io.StringIO()

        monkeypatch.setattr(cli, "open", fake_open, raising=False)
        cli._reopen_on_console("stdout")
        assert opened == ["CONOUT$"]

    def test_the_console_is_only_touched_for_command_line_runs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A normal GUI launch must not go looking for a console."""
        from app import cli

        calls: list[int] = []
        monkeypatch.setattr(cli, "attach_parent_console", lambda: calls.append(1))

        assert cli.handle_cli([]) is None
        assert cli.handle_cli(["-platform", "offscreen"]) is None
        assert calls == [], "a GUI launch reached for the console"

        cli.handle_cli(["--version"])
        assert calls == [1]


class TestSmokeTestReport:
    def test_all_passing(self) -> None:
        from app.cli import SmokeTestReport

        report = SmokeTestReport()
        report.record("a", True)
        report.record("b", True, "detail")
        assert report.passed
        assert "RESULT: PASS" in report.render()

    def test_one_failure_fails_the_report(self) -> None:
        from app.cli import SmokeTestReport

        report = SmokeTestReport()
        report.record("a", True)
        report.record("b", False, "broken")
        assert not report.passed
        assert "RESULT: FAIL" in report.render()
        assert "broken" in report.render()


class TestBundledOCRDiscovery:
    def _make_runtime(self, directory: Path) -> Path:
        """Create a fake bundled runtime with the platform's executable name."""
        ocr_dir = directory / "ocr"
        (ocr_dir / "tessdata").mkdir(parents=True)
        executable = ocr_dir / ocr_service._tesseract_executable_name()
        executable.write_text("#!/bin/sh\necho fake\n")
        executable.chmod(0o755)
        (ocr_dir / "tessdata" / "eng.traineddata").write_bytes(b"fake")
        return ocr_dir

    def test_bundled_runtime_is_found_next_to_the_executable(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        ocr_dir = self._make_runtime(tmp_path)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(tmp_path / "SmartPDFSorter.exe"))

        assert ocr_service.bundled_ocr_dir() == ocr_dir
        assert ocr_service.resolve_bundled_tesseract() == (
            ocr_dir / ocr_service._tesseract_executable_name()
        )
        assert ocr_service.bundled_tessdata_dir() == ocr_dir / "tessdata"

    def test_bundled_runtime_is_found_via_resource_path(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The onefile layout unpacks data under _MEIPASS."""
        ocr_dir = self._make_runtime(tmp_path)
        monkeypatch.setattr(
            ocr_service, "resource_path", lambda *parts: tmp_path.joinpath(*parts)
        )
        assert ocr_service.bundled_ocr_dir() == ocr_dir

    def test_no_bundle_returns_none(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(
            ocr_service, "resource_path", lambda *parts: tmp_path.joinpath(*parts)
        )
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        assert ocr_service.bundled_ocr_dir() is None
        assert ocr_service.resolve_bundled_tesseract() is None

    def test_bundled_engine_is_preferred_over_the_system_one(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Behaviour must be identical on every client PC."""
        ocr_dir = self._make_runtime(tmp_path)
        monkeypatch.setattr(
            ocr_service, "resource_path", lambda *parts: tmp_path.joinpath(*parts)
        )
        monkeypatch.setattr(ocr_service.shutil, "which", lambda name: "/usr/bin/tesseract")

        provider = ocr_service.TesseractOCRProvider()
        resolved = provider.resolve_executable()
        assert resolved == str(ocr_dir / ocr_service._tesseract_executable_name())
        assert provider.using_bundled

    def test_an_explicit_setting_overrides_the_bundle(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        self._make_runtime(tmp_path)
        monkeypatch.setattr(
            ocr_service, "resource_path", lambda *parts: tmp_path.joinpath(*parts)
        )
        custom = tmp_path / "custom-tesseract"
        custom.write_text("#!/bin/sh\n")
        custom.chmod(0o755)

        provider = ocr_service.TesseractOCRProvider(str(custom))
        assert provider.resolve_executable() == str(custom)
        assert not provider.using_bundled

    def test_tessdata_prefix_is_set_for_the_bundled_engine(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Without this a relocated Tesseract cannot find its language files."""
        ocr_dir = self._make_runtime(tmp_path)
        monkeypatch.setattr(
            ocr_service, "resource_path", lambda *parts: tmp_path.joinpath(*parts)
        )
        provider = ocr_service.TesseractOCRProvider()
        provider.resolve_executable()

        environment = provider._subprocess_env()
        assert environment["TESSDATA_PREFIX"] == str(ocr_dir / "tessdata")

    def test_tessdata_prefix_is_untouched_for_a_system_engine(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            ocr_service, "resource_path", lambda *parts: tmp_path.joinpath(*parts)
        )
        monkeypatch.setattr(ocr_service.shutil, "which", lambda name: "/usr/bin/tesseract")
        monkeypatch.delenv("TESSDATA_PREFIX", raising=False)

        provider = ocr_service.TesseractOCRProvider()
        provider.resolve_executable()
        assert "TESSDATA_PREFIX" not in provider._subprocess_env()

    def test_describe_runtime_never_raises(self) -> None:
        assert isinstance(ocr_service.describe_ocr_runtime("/nonexistent/tesseract"), str)


class TestPackagingConfiguration:
    def test_installed_spec_is_onedir(self) -> None:
        text = (ROOT / "SmartPDFSorter.spec").read_text(encoding="utf-8")
        assert "COLLECT(" in text, "the installed build must be onedir"
        assert "exclude_binaries=True" in text

    def test_portable_spec_is_onefile(self) -> None:
        text = (ROOT / "SmartPDFSorter-Portable.spec").read_text(encoding="utf-8")
        assert "COLLECT(" not in text, "the portable build must be onefile"
        assert "a.binaries" in text

    def test_both_specs_read_the_shared_version(self) -> None:
        for name in ("SmartPDFSorter.spec", "SmartPDFSorter-Portable.spec"):
            text = (ROOT / name).read_text(encoding="utf-8")
            assert "read_version(ROOT)" in text, name

    def test_neither_spec_opens_a_console_window(self) -> None:
        for name in ("SmartPDFSorter.spec", "SmartPDFSorter-Portable.spec"):
            text = (ROOT / name).read_text(encoding="utf-8")
            assert "console=False" in text, name

    def test_common_module_excludes_the_web_engine(self) -> None:
        sys.path.insert(0, str(ROOT / "packaging"))
        try:
            import pyinstaller_common
        finally:
            sys.path.pop(0)
        assert "PySide6.QtWebEngineCore" in pyinstaller_common.excludes()

    def test_keyring_backends_are_hidden_imports(self) -> None:
        """keyring resolves its backend at run time; without these the packaged
        build silently loses the ability to store an API key."""
        text = (ROOT / "packaging" / "pyinstaller_common.py").read_text(encoding="utf-8")
        assert "keyring.backends.Windows" in text


class TestInstallerSources:
    @pytest.fixture(scope="class")
    def wxs(self) -> str:
        return (ROOT / "installer" / "Package.wxs").read_text(encoding="utf-8")

    def test_installer_source_is_well_formed_xml(self) -> None:
        import xml.etree.ElementTree as ET

        ET.parse(ROOT / "installer" / "Package.wxs")

    def test_upgrade_code_is_fixed(self, wxs: str) -> None:
        """Changing this GUID would orphan every already-installed client."""
        assert "7B3F2E64-9A21-4C0D-9E2B-5F1A6D8C4E30" in wxs

    def test_major_upgrade_is_configured(self, wxs: str) -> None:
        assert "<MajorUpgrade" in wxs
        assert "DowngradeErrorMessage" in wxs
        assert 'AllowSameVersionUpgrades="yes"' in wxs

    def test_installs_per_machine_for_deployment(self, wxs: str) -> None:
        assert 'Scope="perMachine"' in wxs

    def test_version_is_injected_not_hard_coded(self, wxs: str) -> None:
        assert 'Version="$(Version)"' in wxs

    def test_start_menu_shortcut_is_present(self, wxs: str) -> None:
        assert "StartMenuShortcut" in wxs
        assert "ApplicationProgramsFolder" in wxs

    def test_desktop_shortcut_is_opt_in(self, wxs: str) -> None:
        assert 'Property Id="INSTALLDESKTOPSHORTCUT" Value="0"' in wxs
        assert 'INSTALLDESKTOPSHORTCUT = "1"' in wxs

    def test_installs_to_program_files_64(self, wxs: str) -> None:
        assert "ProgramFiles64Folder" in wxs

    def test_harvests_the_onedir_output(self, wxs: str) -> None:
        assert "$(HarvestPath)" in wxs

    def test_declares_no_user_data_directories(self, wxs: str) -> None:
        """Mutable user data must never be installed under Program Files."""
        for forbidden in ("settings.json", "history.sqlite3", "AppDataFolder"):
            assert forbidden not in wxs, f"{forbidden} must not be in the installer"

    def test_no_property_collides_with_the_wix_ui_library(self) -> None:
        """WixUI_InstallDir defines some ARP properties itself.

        Declaring one of them again is a duplicate-symbol error that only
        surfaces when WiX actually links on Windows.
        """
        wxs = (ROOT / "installer" / "Package.wxs").read_text(encoding="utf-8")
        for owned_by_wixui in ("ARPNOMODIFY",):
            declared = f'<Property Id="{owned_by_wixui}"'
            assert declared not in wxs, (
                f"{owned_by_wixui} is defined by WixUI_InstallDir; declaring it "
                "here breaks the MSI build"
            )

    def test_license_file_exists(self) -> None:
        assert (ROOT / "installer" / "License.rtf").is_file()


class TestOCRRuntimeFetcher:
    def test_the_pin_is_complete(self) -> None:
        from scripts.fetch_ocr_runtime import PINNED

        assert PINNED.version
        assert PINNED.url.startswith("https://")
        assert re.fullmatch(r"[0-9a-f]{64}", PINNED.sha256), "a full SHA-256 pin is required"

    def test_english_is_staged(self) -> None:
        from scripts.fetch_ocr_runtime import LANGUAGES

        assert "eng" in LANGUAGES

    def test_user_agent_avoids_the_blocked_default(self) -> None:
        """The mirror returns 403 for any agent containing "python-urllib".

        urllib's default agent is exactly that, which broke the Windows build
        while passing locally because a cached installer hid the network path.
        """
        from scripts.fetch_ocr_runtime import _USER_AGENT

        assert "python-urllib" not in _USER_AGENT.lower()
        assert _USER_AGENT.strip(), "a User-Agent must be sent"

    def test_download_failures_are_actionable(self) -> None:
        """A build that cannot download must say how to proceed by hand."""
        source = (ROOT / "scripts" / "fetch_ocr_runtime.py").read_text(encoding="utf-8")
        assert "download the installer by hand" in source
        assert "_DOWNLOAD_ATTEMPTS" in source

    def test_permanent_http_errors_are_not_retried(self) -> None:
        source = (ROOT / "scripts" / "fetch_ocr_runtime.py").read_text(encoding="utf-8")
        assert "(401, 403, 404)" in source

    def test_system_dlls_are_not_redistributed(self) -> None:
        from scripts.fetch_ocr_runtime import _SYSTEM_DLLS

        assert "kernel32.dll" in _SYSTEM_DLLS
        assert "user32.dll" in _SYSTEM_DLLS

    def test_dependency_closure_skips_system_libraries(self, tmp_path: Path, monkeypatch) -> None:
        from scripts import fetch_ocr_runtime

        root = tmp_path / "tesseract.exe"
        root.write_bytes(b"stub")
        (tmp_path / "libleptonica-6.dll").write_bytes(b"stub")
        (tmp_path / "unused.dll").write_bytes(b"stub")

        imports = {
            "tesseract.exe": {"libleptonica-6.dll", "kernel32.dll"},
            "libleptonica-6.dll": {"msvcrt.dll"},
        }
        monkeypatch.setattr(
            fetch_ocr_runtime, "imported_dlls", lambda path: imports.get(path.name, set())
        )

        closure = fetch_ocr_runtime.dependency_closure(root, tmp_path)
        names = [path.name for path in closure]
        assert names == ["libleptonica-6.dll"]

    def test_dependency_closure_is_transitive(self, tmp_path: Path, monkeypatch) -> None:
        """A DLL needed only by another DLL must still be collected."""
        from scripts import fetch_ocr_runtime

        root = tmp_path / "tesseract.exe"
        root.write_bytes(b"stub")
        for name in ("a.dll", "b.dll", "c.dll"):
            (tmp_path / name).write_bytes(b"stub")

        imports = {
            "tesseract.exe": {"a.dll"},
            "a.dll": {"b.dll"},
            "b.dll": {"c.dll"},
        }
        monkeypatch.setattr(
            fetch_ocr_runtime, "imported_dlls", lambda path: imports.get(path.name, set())
        )

        closure = fetch_ocr_runtime.dependency_closure(root, tmp_path)
        assert [p.name for p in closure] == ["a.dll", "b.dll", "c.dll"]

    def test_closure_tolerates_import_cycles(self, tmp_path: Path, monkeypatch) -> None:
        from scripts import fetch_ocr_runtime

        root = tmp_path / "tesseract.exe"
        root.write_bytes(b"stub")
        for name in ("a.dll", "b.dll"):
            (tmp_path / name).write_bytes(b"stub")

        imports = {"tesseract.exe": {"a.dll"}, "a.dll": {"b.dll"}, "b.dll": {"a.dll"}}
        monkeypatch.setattr(
            fetch_ocr_runtime, "imported_dlls", lambda path: imports.get(path.name, set())
        )

        closure = fetch_ocr_runtime.dependency_closure(root, tmp_path)
        assert sorted(p.name for p in closure) == ["a.dll", "b.dll"]


class TestBuildAutomation:
    @pytest.fixture(scope="class")
    def build_script(self) -> str:
        return (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")

    def test_script_exists_and_is_documented(self, build_script: str) -> None:
        assert ".SYNOPSIS" in build_script
        assert "-Clean" in build_script

    def test_build_fails_when_tests_fail(self, build_script: str) -> None:
        assert "Tests failed" in build_script

    def test_builds_both_flavours(self, build_script: str) -> None:
        assert "SmartPDFSorter.spec" in build_script
        assert "SmartPDFSorter-Portable.spec" in build_script

    def test_stages_ocr_next_to_the_executable(self, build_script: str) -> None:
        assert "fetch_ocr_runtime.py" in build_script
        assert "InstalledOcr" in build_script

    def test_validates_builds_before_packaging(self, build_script: str) -> None:
        assert "--smoke-test" in build_script
        assert "--ocr-info" in build_script

    def test_produces_checksums(self, build_script: str) -> None:
        assert "SHA256SUMS.txt" in build_script
        assert "Get-FileHash" in build_script

    def test_signing_is_optional_and_secret_free(self, build_script: str) -> None:
        assert "-Sign" in build_script
        assert "SPS_SIGN_THUMBPRINT" in build_script
        # Nothing secret may be committed.
        assert ".pfx'" not in build_script.replace("'C:\\secure\\codesign.pfx'", "")

    def test_documents_silent_install_and_uninstall(self, build_script: str) -> None:
        assert "msiexec /i" in build_script
        assert "msiexec /x" in build_script


class TestGuiSubsystemInvocation:
    """A windowed EXE is not awaited by cmd/PowerShell.

    Checking $LASTEXITCODE straight after `& app.exe --smoke-test` reads a stale
    value, so such a check can pass while the application is still starting.
    Every script that verifies a build must go through the waiting helper.
    """

    HELPER = "Invoke-AppCli.ps1"

    def test_helper_exists_and_waits(self) -> None:
        helper = ROOT / "scripts" / self.HELPER
        assert helper.is_file()
        text = helper.read_text(encoding="utf-8")
        assert "Start-Process" in text
        assert "WaitForExit" in text
        assert "exit $process.ExitCode" in text

    def test_helper_explains_why_it_exists(self) -> None:
        text = (ROOT / "scripts" / self.HELPER).read_text(encoding="utf-8")
        assert "GUI subsystem" in text or "windowed" in text

    def _powershell_sources(self) -> list[Path]:
        return [
            ROOT / "scripts" / "build_windows.ps1",
            ROOT / ".github" / "workflows" / "windows-build.yml",
            ROOT / ".github" / "workflows" / "release.yml",
        ]

    #: Ways the application executable is referred to in the scripts.
    APP_REFERENCES = (
        "SmartPDFSorter",
        "$exe",
        "$InstalledExe",
        "$PortableExe",
    )
    CLI_FLAGS = re.compile(r"--(?:version|smoke-test|ocr-info)")

    def test_no_script_invokes_the_app_directly(self) -> None:
        """Catch a regression back to the unreliable `& $exe --flag` form."""
        offenders: list[str] = []
        for path in self._powershell_sources():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if not stripped.startswith("&"):
                    continue
                # Going through the helper is the correct form.
                if "AppCli" in stripped:
                    continue
                if not self.CLI_FLAGS.search(stripped):
                    continue
                # Only our own windowed executable matters; console tools such
                # as tesseract.exe are awaited normally and are fine.
                if any(reference in stripped for reference in self.APP_REFERENCES):
                    offenders.append(f"{path.relative_to(ROOT)}:{number}: {stripped}")
        assert not offenders, (
            "these call a windowed executable without waiting for it:\n"
            + "\n".join(offenders)
        )

    def test_verification_scripts_use_the_helper(self) -> None:
        for path in self._powershell_sources():
            text = path.read_text(encoding="utf-8")
            if "--smoke-test" not in text:
                continue
            assert self.HELPER in text, f"{path.name} runs a smoke test without the helper"

    def test_readme_warns_deployment_scripters(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "Scripting the command line" in readme
        assert "does not" in readme and "wait" in readme


class TestWorkflow:
    @pytest.fixture(scope="class")
    def workflow(self) -> dict:
        import yaml  # PyYAML ships with the CI tooling

        return yaml.safe_load(
            (ROOT / ".github" / "workflows" / "windows-build.yml").read_text(encoding="utf-8")
        )

    def test_workflow_is_valid_yaml(self, workflow: dict) -> None:
        assert "jobs" in workflow

    def test_windows_job_runs_on_windows(self, workflow: dict) -> None:
        assert workflow["jobs"]["windows"]["runs-on"] == "windows-latest"

    def test_workflow_installs_and_uninstalls_the_msi(self, workflow: dict) -> None:
        steps = " ".join(
            str(step.get("run", "")) for step in workflow["jobs"]["windows"]["steps"]
        )
        assert "/i" in steps and "msiexec" in steps
        assert "/x" in steps
        assert "/qn" in steps

    def test_workflow_smoke_tests_the_installed_app(self, workflow: dict) -> None:
        steps = " ".join(
            str(step.get("run", "")) for step in workflow["jobs"]["windows"]["steps"]
        )
        assert "--smoke-test" in steps
        assert "Uninstall" in steps or "uninstall" in steps

    def test_workflow_uploads_both_artifacts(self, workflow: dict) -> None:
        uploads = [
            step
            for step in workflow["jobs"]["windows"]["steps"]
            if str(step.get("uses", "")).startswith("actions/upload-artifact")
        ]
        paths = " ".join(str(step.get("with", {}).get("path", "")) for step in uploads)
        assert "Setup-*.msi" in paths
        assert "Portable-*.exe" in paths
        assert "SHA256SUMS" in paths


class TestThirdPartyNotices:
    @pytest.fixture(scope="class")
    def notices(self) -> str:
        return (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    def test_notices_exist(self, notices: str) -> None:
        assert len(notices) > 500

    def test_redistributed_ocr_is_attributed(self, notices: str) -> None:
        assert "Tesseract" in notices
        assert "Apache License" in notices
        assert "Leptonica" in notices

    def test_copyleft_obligations_are_recorded(self, notices: str) -> None:
        """PyMuPDF is AGPL; that has to be visible, not buried."""
        assert "PyMuPDF" in notices
        assert "AGPL" in notices
        assert "LGPL" in notices  # Qt

    def test_pinned_ocr_version_matches_the_fetcher(self, notices: str) -> None:
        from scripts.fetch_ocr_runtime import PINNED

        assert PINNED.version in notices, "the notices must describe the version actually shipped"
