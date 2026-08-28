"""Cross-cutting helpers: filenames, hashing, logging, paths, OS integration."""

from .filenames import (
    DEFAULT_TEMPLATE,
    SUPPORTED_VARIABLES,
    render_filename_template,
    sanitize_filename,
    sanitize_folder_name,
    template_preview,
    unique_path,
)
from .hashing import hash_file, hash_text
from .logging_setup import configure_logging, get_logger, log_event
from .paths import (
    app_data_dir,
    cache_dir,
    default_output_dir,
    history_db_path,
    log_file_path,
    logs_dir,
    resource_path,
    settings_path,
)
from .system import format_duration, open_in_file_manager, plural

__all__ = [
    "DEFAULT_TEMPLATE",
    "SUPPORTED_VARIABLES",
    "render_filename_template",
    "sanitize_filename",
    "sanitize_folder_name",
    "template_preview",
    "unique_path",
    "hash_file",
    "hash_text",
    "configure_logging",
    "get_logger",
    "log_event",
    "app_data_dir",
    "cache_dir",
    "default_output_dir",
    "history_db_path",
    "log_file_path",
    "logs_dir",
    "resource_path",
    "settings_path",
    "format_duration",
    "open_in_file_manager",
    "plural",
]
