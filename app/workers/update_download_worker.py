"""Background worker for downloading an update.

The installer is around 90 MB. On the UI thread that is not a pause, it is a
frozen window for as long as the connection takes -- and unlike the version
check, there is no short timeout to bound it. So it runs here, reports progress
as it streams, and can be cancelled part-way.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from app.services.update_installer import DownloadOutcome, download_installer
from app.utils.logging_setup import get_logger

logger = get_logger("worker.updates")


class UpdateDownloadWorker(QThread):
    """Downloads and verifies one release installer."""

    #: (bytes received, total bytes expected -- 0 when the server did not say)
    progressed = Signal(int, int)
    #: The finished :class:`DownloadOutcome`, whether it succeeded or not.
    completed = Signal(object)

    def __init__(self, check, parent=None) -> None:
        super().__init__(parent)
        self._check = check
        self._cancelled = False

    def cancel(self) -> None:
        """Ask the download to stop at the next chunk boundary."""
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def run(self) -> None:  # noqa: D102 - QThread entry point
        try:
            outcome = download_installer(
                self._check,
                on_progress=lambda done, total: self.progressed.emit(done, total),
                should_cancel=lambda: self._cancelled,
            )
        except Exception as exc:  # pragma: no cover - the service never raises
            logger.exception("Update download worker crashed")
            outcome = DownloadOutcome(
                error=f"The download stopped unexpectedly ({type(exc).__name__})."
            )
        self.completed.emit(outcome)


__all__ = ["UpdateDownloadWorker"]
