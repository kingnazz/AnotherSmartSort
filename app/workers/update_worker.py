"""Background worker for the update check.

The check makes a network request with a timeout measured in seconds. Running
it on the UI thread would freeze the Settings dialog for that long on a slow
or filtered connection -- an application that appears to hang while asking
about updates is worse than one that never asks.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from app.services.update_service import UpdateCheck, check_for_updates
from app.utils.logging_setup import get_logger

logger = get_logger("worker.updates")


class UpdateCheckWorker(QThread):
    """Runs one update check and reports the result."""

    #: The finished :class:`UpdateCheck`, whether it succeeded or not.
    completed = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

    def run(self) -> None:  # noqa: D102 - QThread entry point
        try:
            result = check_for_updates()
        except Exception as exc:  # pragma: no cover - the service never raises
            logger.exception("Update check worker crashed")
            result = UpdateCheck(
                current_version="",
                error=f"The update check stopped unexpectedly ({type(exc).__name__}).",
            )
        self.completed.emit(result)


__all__ = ["UpdateCheckWorker"]
