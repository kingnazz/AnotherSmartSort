"""Application entry point.

Run from source::

    python -m app.main

The packaged Windows build calls :func:`main` through ``SmartPDFSorter.spec``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from app import APP_NAME, APP_VERSION, ORG_NAME
from app.utils.logging_setup import configure_logging, get_logger, log_file_path


def _excepthook(exc_type, exc_value, exc_traceback) -> None:
    """Log unexpected errors and show a plain message instead of a traceback."""
    logger = get_logger("main")
    logger.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_traceback))

    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        if QApplication.instance() is not None:
            QMessageBox.critical(
                None,
                APP_NAME,
                f"{APP_NAME} ran into an unexpected problem and may not work correctly.\n\n"
                f"Technical details were written to:\n{log_file_path()}",
            )
    except Exception:  # pragma: no cover - never fail inside the error handler
        pass


def main(argv: list[str] | None = None) -> int:
    """Start the application and run the Qt event loop.

    Command-line-only invocations (``--version``, ``--smoke-test``,
    ``--ocr-info``) are handled first and never reach the user interface.
    """
    configure_logging()
    logger = get_logger("main")

    from app.cli import handle_cli

    exit_code = handle_cli(argv[1:] if argv is not None else None)
    if exit_code is not None:
        return exit_code

    logger.info("%s %s starting", APP_NAME, APP_VERSION)

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from app.ui.theme import apply_theme, high_dpi_setup
    from app.storage.history_store import HistoryStore
    from app.storage.settings_store import SettingsStore
    from app.ui.main_window import MainWindow
    from app.utils.paths import resource_path

    high_dpi_setup()

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationVersion(APP_VERSION)

    icon_path = resource_path("assets", "icon.png")
    if Path(icon_path).exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    sys.excepthook = _excepthook

    store = SettingsStore()
    settings = store.load()
    apply_theme(app, settings.theme)

    window = MainWindow(settings, store, HistoryStore())
    window.show()

    logger.info("Ready (provider=%s, theme=%s)", settings.provider, settings.theme)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
