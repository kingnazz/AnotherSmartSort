"""Fetch and stage the Tesseract OCR runtime bundled with installed builds.

A client should be able to install Smart PDF Sorter on a clean Windows PC and
immediately process a scanned PDF. That means shipping an OCR engine, not
telling the user to install one.

This script downloads a **pinned** Tesseract build, verifies its SHA-256,
extracts it, and stages only what is needed to *run* recognition:

* ``tesseract.exe``
* the transitive closure of DLLs it actually imports
* ``tessdata/eng.traineddata`` (plus ``osd`` for page-orientation detection)

The training tools and the other ~100 language packs are left out, which takes
the payload from ~239 MB to a fraction of that.

Usage::

    python scripts/fetch_ocr_runtime.py                 # stage into ./ocr
    python scripts/fetch_ocr_runtime.py --output build/ocr
    python scripts/fetch_ocr_runtime.py --verify-only    # check the pin
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class TesseractRelease:
    """A pinned, redistributable Tesseract build."""

    version: str
    url: str
    sha256: str
    #: Licences that must be reproduced in THIRD_PARTY_NOTICES.md.
    licences: tuple[str, ...] = ("Apache-2.0 (Tesseract)", "BSD-2-Clause (Leptonica)")


#: The pinned runtime. Changing this is a deliberate, reviewable act: update the
#: version, URL and hash together, then re-run with --verify-only.
PINNED = TesseractRelease(
    version="5.4.0.20240606",
    url=(
        "https://digi.bib.uni-mannheim.de/tesseract/"
        "tesseract-ocr-w64-setup-5.4.0.20240606.exe"
    ),
    sha256="c885fff6998e0608ba4bb8ab51436e1c6775c2bafc2559a19b423e18678b60c9",
)

#: Language data staged with the runtime. English is the product requirement;
#: `osd` lets Tesseract detect rotated scans, which is cheap and worth having.
LANGUAGES = ("eng", "osd")

#: System DLLs that are always present on Windows and must not be redistributed.
_SYSTEM_DLLS = {
    "kernel32.dll", "user32.dll", "gdi32.dll", "advapi32.dll", "shell32.dll",
    "ole32.dll", "oleaut32.dll", "ws2_32.dll", "comdlg32.dll", "comctl32.dll",
    "msvcrt.dll", "ntdll.dll", "rpcrt4.dll", "shlwapi.dll", "version.dll",
    "winspool.drv", "crypt32.dll", "wldap32.dll", "normaliz.dll", "bcrypt.dll",
    "userenv.dll", "secur32.dll", "iphlpapi.dll", "dnsapi.dll", "setupapi.dll",
    "imm32.dll", "winmm.dll", "dwmapi.dll", "uxtheme.dll", "psapi.dll",
    "api-ms-win-crt-runtime-l1-1-0.dll", "vcruntime140.dll", "msvcp140.dll",
    "concrt140.dll", "vcruntime140_1.dll", "ucrtbase.dll",
}


@dataclass
class StagedRuntime:
    """What ended up in the output directory."""

    directory: Path
    executable: Path
    dll_count: int = 0
    languages: tuple[str, ...] = ()
    total_bytes: int = 0
    files: list[str] = field(default_factory=list)

    @property
    def total_mb(self) -> float:
        return round(self.total_bytes / (1024 * 1024), 1)


# ----------------------------------------------------------------------
# Download
# ----------------------------------------------------------------------

def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


#: The mirror returns HTTP 403 for any User-Agent containing "python-urllib",
#: which is urllib's default. Identify the build tool honestly instead -- and
#: keep the substring "python-urllib" out of this string entirely.
_USER_AGENT = "SmartPDFSorter-build/1.0 (+https://github.com/kingnazz/AnotherSort)"
_DOWNLOAD_ATTEMPTS = 3


def _open_url(url: str, timeout: int = 300):
    request = urllib.request.Request(
        url, headers={"User-Agent": _USER_AGENT, "Accept": "*/*"}
    )
    return urllib.request.urlopen(request, timeout=timeout)


def download(release: TesseractRelease, cache_dir: Path) -> Path:
    """Download the pinned installer, reusing a cached copy when it matches."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / f"tesseract-{release.version}.exe"

    if destination.exists():
        actual = sha256_of(destination)
        if actual == release.sha256:
            print(f"  Using cached installer ({destination.name})")
            return destination
        print("  Cached installer failed its checksum; re-downloading")
        destination.unlink()

    print(f"  Downloading {release.url}")
    temporary = destination.with_suffix(".part")
    last_error: Exception | None = None

    for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
        try:
            with _open_url(release.url) as response:
                with open(temporary, "wb") as handle:
                    shutil.copyfileobj(response, handle, length=1024 * 256)
            last_error = None
            break
        except urllib.error.HTTPError as exc:
            last_error = exc
            print(f"    attempt {attempt}: HTTP {exc.code} {exc.reason}")
            if exc.code in (401, 403, 404):
                # Not transient: retrying will not help.
                break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            print(f"    attempt {attempt}: {exc}")
        if attempt < _DOWNLOAD_ATTEMPTS:
            time.sleep(2 ** attempt)

    if last_error is not None:
        temporary.unlink(missing_ok=True)
        raise SystemExit(
            f"Could not download the Tesseract runtime: {last_error}\n"
            f"  URL: {release.url}\n"
            "If the mirror is unreachable, download the installer by hand and place it at:\n"
            f"  {destination}\n"
            f"It must have SHA-256 {release.sha256}"
        )

    actual = sha256_of(temporary)
    if actual != release.sha256:
        temporary.unlink(missing_ok=True)
        raise SystemExit(
            "SHA-256 mismatch for the Tesseract installer.\n"
            f"  expected: {release.sha256}\n"
            f"  actual:   {actual}\n"
            "Refusing to bundle an unverified binary."
        )

    temporary.replace(destination)
    print(f"  Verified SHA-256 {actual[:16]}…")
    return destination


def extract(installer: Path, work_dir: Path) -> Path:
    """Extract the NSIS installer without running it."""
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    seven_zip = _find_7zip()
    if seven_zip is None:
        raise SystemExit(
            "7-Zip is required to extract the Tesseract installer but was not found.\n"
            "  Windows: winget install 7zip.7zip   (or choco install 7zip)\n"
            "  Linux:   sudo apt-get install p7zip-full"
        )

    result = subprocess.run(
        [seven_zip, "x", f"-o{work_dir}", str(installer), "-y"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"Extraction failed:\n{result.stdout}\n{result.stderr}")
    return work_dir


def _find_7zip() -> str | None:
    for candidate in ("7z", "7za", "7zz"):
        found = shutil.which(candidate)
        if found:
            return found
    for guess in (
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
    ):
        if Path(guess).is_file():
            return guess
    return None


# ----------------------------------------------------------------------
# Dependency closure
# ----------------------------------------------------------------------

def imported_dlls(pe_path: Path) -> set[str]:
    """DLL names a PE file imports, lowercased."""
    try:
        import pefile
    except ImportError:  # pragma: no cover - dev dependency
        raise SystemExit(
            "pefile is required to compute the OCR dependency closure.\n"
            "  pip install pefile   (it is in requirements-dev.txt)"
        )

    try:
        binary = pefile.PE(str(pe_path), fast_load=True)
    except Exception as exc:
        print(f"    warning: could not read imports from {pe_path.name}: {exc}")
        return set()

    try:
        binary.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]]
        )
        names: set[str] = set()
        for entry in getattr(binary, "DIRECTORY_ENTRY_IMPORT", []) or []:
            if entry.dll:
                names.add(entry.dll.decode("ascii", "ignore").lower())
        return names
    finally:
        binary.close()


def dependency_closure(root_exe: Path, search_dir: Path) -> list[Path]:
    """Every redistributable DLL reachable from ``root_exe``.

    Walks imports breadth-first so a DLL needed only by another DLL is still
    collected -- missing one produces a runtime failure the user sees as
    "OCR does not work on this PC" and nothing more helpful.
    """
    available = {path.name.lower(): path for path in search_dir.glob("*.dll")}
    needed: dict[str, Path] = {}
    queue = [root_exe]
    seen: set[str] = {root_exe.name.lower()}

    while queue:
        current = queue.pop(0)
        for name in sorted(imported_dlls(current)):
            if name in _SYSTEM_DLLS or name in seen:
                continue
            seen.add(name)
            match = available.get(name)
            if match is None:
                # Not shipped in the installer: it is a Windows-provided library.
                continue
            needed[name] = match
            queue.append(match)

    return sorted(needed.values(), key=lambda p: p.name.lower())


# ----------------------------------------------------------------------
# Staging
# ----------------------------------------------------------------------

def stage(extracted: Path, output: Path, release: TesseractRelease) -> StagedRuntime:
    """Copy the minimal runnable runtime into ``output``."""
    executable = extracted / "tesseract.exe"
    if not executable.is_file():
        raise SystemExit(f"tesseract.exe was not found in {extracted}")

    if output.exists():
        shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True, exist_ok=True)

    print("  Computing the DLL dependency closure…")
    dlls = dependency_closure(executable, extracted)
    print(f"  {len(dlls)} DLLs required (of {len(list(extracted.glob('*.dll')))} shipped)")

    staged = StagedRuntime(directory=output, executable=output / executable.name)
    shutil.copy2(executable, output / executable.name)
    staged.files.append(executable.name)

    for dll in dlls:
        shutil.copy2(dll, output / dll.name)
        staged.files.append(dll.name)
    staged.dll_count = len(dlls)

    tessdata_out = output / "tessdata"
    tessdata_out.mkdir(parents=True, exist_ok=True)
    found_languages: list[str] = []
    for language in LANGUAGES:
        source = extracted / "tessdata" / f"{language}.traineddata"
        if source.is_file():
            shutil.copy2(source, tessdata_out / source.name)
            staged.files.append(f"tessdata/{source.name}")
            found_languages.append(language)
        else:
            print(f"    warning: {language}.traineddata not found in the installer")
    staged.languages = tuple(found_languages)

    if "eng" not in found_languages:
        raise SystemExit("eng.traineddata is required but was not found.")

    staged.total_bytes = sum(
        path.stat().st_size for path in output.rglob("*") if path.is_file()
    )

    manifest = {
        "engine": "tesseract",
        "version": release.version,
        "source_url": release.url,
        "source_sha256": release.sha256,
        "languages": list(staged.languages),
        "licences": list(release.licences),
        "files": sorted(staged.files),
        "total_bytes": staged.total_bytes,
    }
    (output / "runtime.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return staged


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default=str(ROOT / "ocr"),
        help="Where to stage the runtime (default: ./ocr)",
    )
    parser.add_argument(
        "--cache",
        default=str(ROOT / "build" / "ocr-cache"),
        help="Where to cache the downloaded installer",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Download and verify the pinned installer, then stop.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-stage even if the output directory already looks complete.",
    )
    arguments = parser.parse_args(argv)

    output = Path(arguments.output)
    cache = Path(arguments.cache)

    print(f"Tesseract OCR runtime {PINNED.version}")

    if not arguments.force and (output / "runtime.json").is_file():
        try:
            existing = json.loads((output / "runtime.json").read_text(encoding="utf-8"))
        except ValueError:
            existing = {}
        if existing.get("version") == PINNED.version:
            print(f"  Already staged in {output} (use --force to redo)")
            return 0

    installer = download(PINNED, cache)
    if arguments.verify_only:
        print("  Pin verified.")
        return 0

    work = cache / "extracted"
    print("  Extracting…")
    extracted = extract(installer, work)

    staged = stage(extracted, output, PINNED)

    shutil.rmtree(work, ignore_errors=True)

    print()
    print(f"  Staged {len(staged.files)} files ({staged.total_mb} MB) into {output}")
    print(f"    engine    : {staged.executable.name}")
    print(f"    libraries : {staged.dll_count}")
    print(f"    languages : {', '.join(staged.languages)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
