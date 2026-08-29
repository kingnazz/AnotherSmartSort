"""The product name, everywhere a person can read it.

The application was renamed from "Smart PDF Sorter" to "AS Resume Sorter". A
rename is easy to do halfway: the window title changes, and then months later a
client sees the old name in Programs and Features, on a Start Menu shortcut, or
in the Details tab of the EXE. These tests pin every surface a user can reach.

They also pin the three things that deliberately did *not* change, because each
of them looks like an oversight and would be "fixed" by the next person to read
the code:

* ``ORG_NAME`` -- the folder holding settings and history. Renaming it abandons
  every existing installation's data rather than migrating it.
* ``SMART_PDF_SORTER_HOME`` -- a documented deployment knob.
* The MSI ``UpgradeCode`` -- what makes the rebranded installer upgrade an old
  Smart PDF Sorter in place instead of installing a second product beside it.

The widget-level half of this -- window title, header, About dialog, the first
screen -- lives in ``tests/test_ui.py``, next to the fixture that builds a real
MainWindow.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app import APP_NAME, ORG_NAME, PUBLISHER

ROOT = Path(__file__).resolve().parent.parent

PRODUCT = "AS Resume Sorter"
FORMER_PRODUCT = "Smart PDF Sorter"
RETIRED_REPO = "github.com/kingnazz/AnotherSort"
CURRENT_REPO = "github.com/kingnazz/AnotherSmartSort"


def read(*parts: str) -> str:
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


class TestTheNameItself:
    def test_the_product_name_is_the_new_one(self) -> None:
        assert APP_NAME == PRODUCT

    def test_the_publisher_does_not_still_say_the_old_name(self) -> None:
        """Publisher is a column in Programs and Features, and a line in About."""
        assert FORMER_PRODUCT not in PUBLISHER
        assert PUBLISHER == PRODUCT


class TestMessagesToTheUser:
    def test_the_update_check_reports_under_the_new_name(self) -> None:
        """Settings -> Check for updates answers with the product's name."""
        from app.services.update_service import UpdateCheck

        message = UpdateCheck("1.0.0", latest_version="1.0.0").message
        assert message.startswith(PRODUCT), message


class TestTheDefaultOutputFolder:
    def test_it_is_named_for_the_product(self) -> None:
        from app.utils.paths import default_output_dir

        assert default_output_dir().name == PRODUCT


class TestTheInstaller:
    @pytest.fixture(scope="class")
    def wxs(self) -> str:
        return read("installer", "Package.wxs")

    def test_the_installed_product_is_named_for_the_new_brand(self, wxs: str) -> None:
        assert f'<?define ProductName = "{PRODUCT}" ?>' in wxs

    def test_the_manufacturer_is_not_the_old_product_name(self, wxs: str) -> None:
        """Manufacturer becomes the Publisher column in Programs and Features."""
        assert f'<?define Manufacturer = "{FORMER_PRODUCT}" ?>' not in wxs
        assert f'<?define Manufacturer = "{PRODUCT}" ?>' in wxs

    def test_install_folder_start_menu_and_shortcuts_follow_the_product_name(
        self, wxs: str
    ) -> None:
        """Each of these is a place a user reads the name on their own PC."""
        assert '<Directory Id="INSTALLFOLDER" Name="$(ProductName)" />' in wxs
        assert '<Directory Id="ApplicationProgramsFolder" Name="$(ProductName)" />' in wxs
        assert wxs.count('Name="$(ProductName)"') >= 4, (
            "the Start Menu and desktop shortcuts must be named from ProductName, "
            "not spelled out separately where they can drift"
        )

    def test_the_upgrade_code_survived_the_rename(self, wxs: str) -> None:
        """The rebrand must upgrade an existing install, not sit beside it.

        Windows Installer identifies a product family by UpgradeCode alone.
        Regenerating it here would leave every client with two entries in
        Programs and Features and two copies on disk.
        """
        assert '"7B3F2E64-9A21-4C0D-9E2B-5F1A6D8C4E30"' in wxs

    def test_the_support_links_point_at_the_live_repository(self, wxs: str) -> None:
        assert f'<Property Id="ARPHELPLINK" Value="https://{CURRENT_REPO}" />' in wxs
        assert f'<Property Id="ARPURLINFOABOUT" Value="https://{CURRENT_REPO}" />' in wxs

    def test_the_old_name_survives_only_as_an_explanatory_comment(self, wxs: str) -> None:
        """One mention is deliberate: it explains why the UpgradeCode is frozen."""
        for number, line in enumerate(wxs.splitlines(), 1):
            if FORMER_PRODUCT not in line:
                continue
            assert "<?define" not in line and "Value=" not in line and "Name=" not in line, (
                f"Package.wxs line {number} still ships the old product name"
            )

    def test_the_licence_shown_during_install_carries_the_new_name(self) -> None:
        licence = read("installer", "License.rtf")
        assert PRODUCT in licence
        assert FORMER_PRODUCT not in licence


class TestWindowsFileMetadata:
    """What Explorer's Details tab and deployment tooling read off the EXE."""

    @pytest.fixture(scope="class")
    def common(self):
        sys.path.insert(0, str(ROOT / "packaging"))
        try:
            import pyinstaller_common

            return pyinstaller_common
        finally:
            sys.path.pop(0)

    def test_product_name_metadata_is_the_new_brand(self, common) -> None:
        assert common.DISPLAY_NAME == PRODUCT
        assert common.PUBLISHER == PRODUCT

    def test_the_generated_version_resource_carries_the_new_brand(
        self, common, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Generate the resource itself rather than trusting the constants.

        The resource is only written on Windows, so pretend to be Windows; the
        template is platform-independent and this is what actually gets
        compiled into the EXE.
        """
        monkeypatch.setattr(common.sys, "platform", "win32")
        written = common.write_version_resource(tmp_path, "1.0.0", portable=False)
        assert written is not None
        text = Path(written).read_text(encoding="utf-8")

        assert f"StringStruct('ProductName', '{PRODUCT}')" in text
        assert f"StringStruct('CompanyName', '{PRODUCT}')" in text
        assert f"StringStruct('FileDescription', '{PRODUCT}')" in text
        assert FORMER_PRODUCT not in text

    def test_the_portable_build_is_described_as_the_same_product(
        self, common, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(common.sys, "platform", "win32")
        written = common.write_version_resource(tmp_path, "1.0.0", portable=True)
        text = Path(written).read_text(encoding="utf-8")

        assert f"StringStruct('FileDescription', '{PRODUCT} (portable)')" in text
        assert FORMER_PRODUCT not in text


class TestTheArtwork:
    """The icon files, and the one property that is easy to get wrong.

    The product's logo is a wordmark: the mark plus the name set over three
    lines, about 1.4:1. Shipping *that* as ``icon.png`` is a natural mistake and
    an invisible one on the machine that makes it -- it looks fine in a file
    browser at 512px. Windows then draws it at 16, 24, 32 and 48 px for the
    taskbar, Start Menu and Programs and Features, and the About dialog scales
    it into a 56x56 box, where the type is a few pixels tall and the whole thing
    is a smudge. A square mark is the requirement, not a preference.

    The second half is the ``.ico``. One 256px image inside it is valid and
    loads without complaint; Windows just downscales it itself, badly, and the
    result is a blurred taskbar icon next to a crisp one in Explorer.
    """

    #: What Windows actually asks for. 16 is the taskbar and the title bar,
    #: 32 the Start Menu, 48 Explorer's medium icons, 256 its extra-large view.
    REQUIRED_ICO_SIZES = {(16, 16), (32, 32), (48, 48), (256, 256)}

    @pytest.fixture(scope="class")
    def pillow(self):
        return pytest.importorskip("PIL.Image", reason="Pillow is needed to read the artwork")

    def test_both_icon_files_are_present(self) -> None:
        assert (ROOT / "assets" / "icon.png").is_file()
        assert (ROOT / "assets" / "icon.ico").is_file()

    def test_the_icon_is_square(self, pillow) -> None:
        """A wordmark here would be letterboxed into every icon slot."""
        image = pillow.open(ROOT / "assets" / "icon.png")
        width, height = image.size
        assert width == height, (
            f"icon.png is {width}x{height}. The icon must be the square mark; "
            "the wordmark belongs in the README, not in a 16px taskbar slot"
        )

    def test_the_icon_is_big_enough_to_scale_down_from(self, pillow) -> None:
        image = pillow.open(ROOT / "assets" / "icon.png")
        assert image.size[0] >= 256, f"icon.png is only {image.size[0]}px"

    def test_the_icon_has_a_transparent_background(self, pillow) -> None:
        """An opaque rectangle shows as a white block on the dark theme and as
        a card behind the icon in Programs and Features."""
        image = pillow.open(ROOT / "assets" / "icon.png").convert("RGBA")
        corners = [
            image.getpixel((0, 0)),
            image.getpixel((image.width - 1, 0)),
            image.getpixel((0, image.height - 1)),
            image.getpixel((image.width - 1, image.height - 1)),
        ]
        assert all(pixel[3] == 0 for pixel in corners), f"opaque corners: {corners}"

    def test_the_ico_carries_the_sizes_windows_asks_for(self, pillow) -> None:
        image = pillow.open(ROOT / "assets" / "icon.ico")
        sizes = set(image.info.get("sizes", ()))
        missing = self.REQUIRED_ICO_SIZES - sizes
        assert not missing, (
            f"icon.ico is missing {sorted(missing)}; it has {sorted(sizes)}. "
            "Windows will downscale from the nearest size itself, badly"
        )

    def test_the_wordmark_is_present_for_the_documentation(self, pillow) -> None:
        logo = ROOT / "assets" / "logo.png"
        assert logo.is_file(), "the README's header image is missing"
        assert pillow.open(logo).size[0] >= 512

    def test_the_readme_leads_with_the_wordmark(self) -> None:
        assert 'src="assets/logo.png"' in read("README.md")

    def test_only_the_icon_is_bundled_into_the_build(self) -> None:
        """The wordmark is documentation. Bundling it would put 200KB of
        unused PNG inside every installer."""
        common = read("packaging", "pyinstaller_common.py")
        assert '"icon.png", "icon.ico"' in common
        assert "logo.png" not in common


class TestPublishedArtefacts:
    def test_the_release_is_titled_with_the_new_name(self) -> None:
        workflow = read(".github", "workflows", "release.yml")
        assert "name: AS Resume Sorter ${{ needs.build.outputs.version }}" in workflow
        assert f"## {PRODUCT} $version" in workflow, "release notes still use the old heading"

    def test_the_python_distribution_is_named_for_the_product(self) -> None:
        """Nothing depends on this name, but it is the first line of
        pyproject.toml and reading the old one there invites somebody to
        "finish" the rename by changing the things that must not change."""
        pyproject = read("pyproject.toml")
        assert 'name = "as-resume-sorter"' in pyproject
        assert 'as-resume-sorter = "app.main:main"' in pyproject

    def test_the_readme_leads_with_the_new_name(self) -> None:
        """The first heading names the product. The wordmark image may sit
        above it -- that is the same name, drawn."""
        readme = read("README.md")
        headings = [line for line in readme.splitlines() if line.startswith("# ")]
        assert headings and headings[0] == f"# {PRODUCT}", headings[:1]

    def test_the_windows_ci_checks_the_new_name(self) -> None:
        """The build's own verification must assert the shipped name, or the
        rename could regress without any check going red."""
        workflow = read(".github", "workflows", "windows-build.yml")
        assert f'$productName -ne "{PRODUCT}"' in workflow
        assert f'$_.DisplayName -eq "{PRODUCT}"' in workflow


class TestNothingStillShowsTheOldName:
    """A repository-wide sweep, so no surface is missed by being forgotten."""

    SUFFIXES = {".py", ".md", ".toml", ".txt", ".yml", ".yaml", ".wxs", ".rtf", ".ps1", ".spec"}
    SKIP_DIRS = {".git", ".venv", "__pycache__", "qa", "build", "dist", "artifacts", "node_modules"}
    #: Four files legitimately contain the old name, each because it is writing
    #: *about* the rename rather than shipping the old brand: the installer
    #: comment explaining why the UpgradeCode predates it, the status document's
    #: Naming section, and the two test files that have to spell out what they
    #: are searching for. None of them is a surface a user of the application
    #: reads, which is what this sweep is protecting.
    ALLOWED = {
        Path("installer/Package.wxs"),
        Path("IMPLEMENTATION_STATUS.md"),
        Path("tests/test_branding.py"),
        Path("tests/test_ui.py"),
    }

    def tracked_text_files(self):
        for path in ROOT.rglob("*"):
            if not path.is_file() or path.suffix not in self.SUFFIXES:
                continue
            if any(part in self.SKIP_DIRS for part in path.relative_to(ROOT).parts):
                continue
            yield path

    def test_the_old_product_name_is_gone(self) -> None:
        offenders = [
            str(path.relative_to(ROOT))
            for path in self.tracked_text_files()
            if path.relative_to(ROOT) not in self.ALLOWED
            and FORMER_PRODUCT in path.read_text(encoding="utf-8", errors="ignore")
        ]
        assert not offenders, f"still branded with the old name: {offenders}"

    def test_nothing_links_to_the_retired_repository(self) -> None:
        """The old repository is private and stays that way. A link to it is a
        404 for every client who clicks Help in Programs and Features."""
        offenders = []
        for path in self.tracked_text_files():
            if path.relative_to(ROOT) in self.ALLOWED:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                if RETIRED_REPO in line and CURRENT_REPO not in line:
                    offenders.append(f"{path.relative_to(ROOT)}: {line.strip()[:80]}")
        assert not offenders, offenders


class TestWhatDeliberatelyDidNotChange:
    def test_saved_settings_and_history_keep_their_home(self) -> None:
        """Renaming this folder abandons every existing installation's data."""
        assert ORG_NAME == "SmartPDFSorter"

    def test_the_data_directory_still_resolves_to_that_folder(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.utils import paths

        monkeypatch.delenv("SMART_PDF_SORTER_HOME", raising=False)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        monkeypatch.setattr(paths.sys, "platform", "linux")
        assert paths._base_data_dir() == tmp_path / "SmartPDFSorter"

    def test_the_documented_home_override_still_works(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Deployment scripts already set this variable by name."""
        from app.utils import paths

        monkeypatch.setenv("SMART_PDF_SORTER_HOME", str(tmp_path / "elsewhere"))
        assert paths._base_data_dir() == tmp_path / "elsewhere"

    def test_the_update_feed_points_at_the_live_repository(self) -> None:
        from app.services import update_service

        assert update_service.RELEASE_OWNER == "kingnazz"
        assert update_service.RELEASE_REPO == "AnotherSmartSort"
        assert update_service.RELEASES_PAGE_URL == f"https://{CURRENT_REPO}/releases"

    def test_build_artefact_filenames_are_unchanged(self, tmp_path: Path) -> None:
        """The EXE and MSI filenames stay SmartPDFSorter-*.

        Nobody reads a product name off them -- the Release page, the installer,
        and the installed application all say AS Resume Sorter -- while the two
        .spec files are named after this constant and invoked by name from CI,
        and the release workflow's artifact cleanup matches on the prefix.
        """
        sys.path.insert(0, str(ROOT / "packaging"))
        try:
            import pyinstaller_common
        finally:
            sys.path.pop(0)

        assert pyinstaller_common.APP_NAME == "SmartPDFSorter"
        assert (ROOT / "SmartPDFSorter.spec").is_file()
        assert (ROOT / "SmartPDFSorter-Portable.spec").is_file()
