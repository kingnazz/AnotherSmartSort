"""The one place this application starts an external program.

Smart PDF Sorter is a windowed application: it has no console of its own. On
Windows, starting a *console* executable from a windowed process makes Windows
create a console for it, which appears as a black window that flashes on
screen and can steal focus. Tesseract is a console executable and OCR runs once
per page, so a 100-page scan used to produce a hundred flashes.

Two mechanisms suppress that, and both are applied together:

``CREATE_NO_WINDOW``
    Tells Windows not to give the new process a console at all.

``STARTF_USESHOWWINDOW`` + ``SW_HIDE``
    Tells it that any window the process does create starts hidden.

They overlap deliberately. ``CREATE_NO_WINDOW`` is sufficient in most
environments, but not reliably so across every Windows version and shell
context, and a single flash in a packaged build is a visible defect. Belt and
braces costs nothing.

Everything else here follows from the same goal: never route through
``cmd.exe`` or ``powershell.exe`` to launch a program (that starts a console
*and* is a shell-injection hazard), never pass ``shell=True``, and always call
the target executable directly.

Application code should not call :mod:`subprocess` itself. Route through
:func:`run_hidden` so the behaviour, and the diagnostics, stay in one place.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Mapping, Sequence

from app.utils.logging_setup import get_logger, log_event

logger = get_logger("process")


def is_windows() -> bool:
    return sys.platform.startswith("win")


def hidden_process_options() -> dict[str, object]:
    """Keyword arguments that keep a child process off the screen.

    Empty on non-Windows platforms, where a console executable started from a
    GUI process does not create a window in the first place.
    """
    if not is_windows():  # pragma: no cover - exercised on Windows CI
        return {}

    options: dict[str, object] = {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    }

    # The STARTUPINFO half is additive. Where it is unavailable -- a
    # non-Windows build of Python reporting a Windows platform, which happens
    # under test -- the creation flag still stands on its own rather than the
    # whole call failing and taking OCR down with it.
    startupinfo_type = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_type is not None:
        startupinfo = startupinfo_type()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        options["startupinfo"] = startupinfo

    return options


def run_hidden(
    command: Sequence[str],
    *,
    purpose: str,
    timeout: float | None = None,
    env: Mapping[str, str] | None = None,
    capture_output: bool = True,
    text: bool = True,
    detail: str = "",
) -> subprocess.CompletedProcess:
    """Run an external program without any window appearing.

    ``purpose`` and ``detail`` are for the log only. Nothing from the document
    being processed is ever logged -- only which executable ran, why, and how
    it went -- so a support log can identify a stray popup's cause without
    carrying an applicant's details with it.
    """
    if not command:
        raise ValueError("run_hidden needs a command to run")

    executable = Path(str(command[0])).name
    started = time.monotonic()
    log_event(
        logger,
        "external_process.start",
        executable=executable,
        purpose=purpose,
        hidden=is_windows(),
        detail=detail or None,
    )

    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, never a shell
            list(command),
            capture_output=capture_output,
            text=text,
            timeout=timeout,
            check=False,
            env=dict(env) if env is not None else None,
            **hidden_process_options(),  # type: ignore[arg-type]
        )
    except Exception as exc:
        log_event(
            logger,
            "external_process.failed",
            executable=executable,
            purpose=purpose,
            error=type(exc).__name__,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        raise

    log_event(
        logger,
        "external_process.complete",
        executable=executable,
        purpose=purpose,
        exit_code=result.returncode,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    return result


def open_path_in_file_manager(path: str | Path) -> bool:
    """Reveal a folder in the OS file manager.

    Deliberately not routed through :func:`run_hidden`: this is a *visible*
    user action, and on Windows it uses ``os.startfile``, which asks the shell
    to open the folder rather than starting a console program at all.
    """
    target = Path(path)
    if not target.exists():
        return False
    folder = target if target.is_dir() else target.parent

    try:
        if is_windows():
            import os

            os.startfile(str(folder))  # type: ignore[attr-defined]  # Windows only
            return True
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen(  # noqa: S603 - fixed argv, never a shell
            [opener, str(folder)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **hidden_process_options(),  # type: ignore[arg-type]
        )
        return True
    except (OSError, AttributeError) as exc:
        logger.warning("Could not open %s: %s", folder, exc)
        return False


__all__ = [
    "run_hidden",
    "hidden_process_options",
    "open_path_in_file_manager",
    "is_windows",
]
