"""Application settings persistence.

Ordinary settings live in a JSON file under the per-user application data
directory. The OpenAI API key never goes there: it is stored through
:mod:`keyring` (Windows Credential Manager on Windows), with a restricted-
permission local file only as a last-resort fallback, and it is never logged.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from app.models.enums import ProviderKind, SeparatorPolicy
from app.profiles import DEFAULT_PROFILE_NAME
from app.services.confidence import (
    DEFAULT_HIGH_THRESHOLD,
    DEFAULT_REVIEW_THRESHOLD,
    ConfidenceThresholds,
)
from app.utils.filenames import DEFAULT_TEMPLATE
from app.utils.logging_setup import get_logger
from app.utils.paths import app_data_dir, default_output_dir, settings_path

logger = get_logger("settings")

#: Increment when a changed default should reach installs that already exist.
CURRENT_SETTINGS_VERSION = 3

KEYRING_SERVICE = "SmartPDFSorter"
KEYRING_USERNAME = "openai_api_key"
_FALLBACK_SECRET_FILE = "credentials.json"


@dataclass
class AppSettings:
    """Every user-configurable option in one typed record."""

    # -- General -----------------------------------------------------------
    output_directory: str = ""
    include_subfolders: bool = True
    #: Off by default: the client workflow is "give me the resumes", not a
    #: spreadsheet to cross-reference against the folders it describes.
    create_excel_index: bool = False
    open_output_when_complete: bool = True
    theme: str = "system"  # system | light | dark
    confidence_high: float = DEFAULT_HIGH_THRESHOLD
    confidence_review: float = DEFAULT_REVIEW_THRESHOLD
    warn_on_duplicates: bool = True

    # -- Classification ----------------------------------------------------
    profile_name: str = DEFAULT_PROFILE_NAME
    provider: str = ProviderKind.RULES.value
    #: Local confidence at or above which the AI provider is not consulted.
    ai_escalation_threshold: float = 0.85

    # -- OpenAI ------------------------------------------------------------
    openai_model: str = "gpt-4o-mini"
    openai_timeout_seconds: int = 45
    openai_acknowledged_privacy: bool = False

    # -- Ollama ------------------------------------------------------------
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    ollama_timeout_seconds: int = 90

    # -- OCR ---------------------------------------------------------------
    ocr_enabled: bool = True
    tesseract_path: str = ""
    ocr_language: str = "eng"

    #: Bumped when a default changes in a way an existing install should adopt.
    #: Without this, a stored settings file keeps the old default forever and the
    #: fix only reaches new users.
    settings_version: int = CURRENT_SETTINGS_VERSION

    # -- Output naming -----------------------------------------------------
    filename_template: str = DEFAULT_TEMPLATE
    #: Off by default: real ATS exports are large multi-applicant batches,
    #: where a folder per candidate means opening dozens of near-empty
    #: folders to find one resume. See ``group_by_document_type`` below.
    folder_per_candidate: bool = False
    #: On by default: ``Resumes/``, ``Cover Letters/``, ``Application
    #: Reports/`` and so on, one PDF per candidate named ``<Candidate>.pdf``.
    #: This is the primary output shape the product now targets -- "take
    #: these applicant PDFs and put the resumes in one pile, the cover
    #: letters in another". Takes priority over ``folder_per_candidate`` when
    #: both are set.
    group_by_document_type: bool = True

    # -- Output modes ------------------------------------------------------
    #: Write each logical document as its own PDF.
    export_separate_documents: bool = True
    #: Also write one combined PDF per candidate, in packet order. Off by
    #: default: a combined packet is a secondary, advanced output -- the
    #: everyday ask is the documents themselves, sorted by type.
    export_combined_packets: bool = False
    #: Document types to export. Empty means every type, which is the default;
    #: naming types narrows the output to just those (for example, resumes only
    #: when a hiring manager wants a reading pile rather than full packets).
    #: Analysis is unaffected -- everything is still detected and reviewable,
    #: this only decides what gets written to disk.
    export_document_types: list[str] = field(default_factory=list)

    # -- Separator pages ---------------------------------------------------
    #: Divider pages carrying only a label ("RESUME") are dropped from output by
    #: default. Real ATS exports insert one before every attachment, and leading
    #: each extracted resume with a near-blank page is never what anyone wants.
    separator_policy: str = SeparatorPolicy.EXCLUDE.value

    # -- Window state (not user facing) ------------------------------------
    window_geometry: str = ""

    def __post_init__(self) -> None:
        if not self.output_directory:
            self.output_directory = str(default_output_dir())

    # ------------------------------------------------------------------
    @property
    def thresholds(self) -> ConfidenceThresholds:
        return ConfidenceThresholds(high=self.confidence_high, review=self.confidence_review)

    @property
    def provider_kind(self) -> ProviderKind:
        try:
            return ProviderKind(self.provider)
        except ValueError:
            return ProviderKind.RULES

    @property
    def separator_policy_enum(self) -> SeparatorPolicy:
        try:
            return SeparatorPolicy(self.separator_policy)
        except ValueError:
            return SeparatorPolicy.INCLUDE

    @property
    def uses_external_provider(self) -> bool:
        return self.provider_kind.is_external

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AppSettings":
        """Build settings from stored JSON, ignoring unknown or invalid values."""
        settings = cls()
        if not data:
            return settings
        # Read the version before applying anything: a file written before
        # versioning existed has no version key at all, and defaulting to the
        # current one would skip every migration for exactly the installs that
        # need them most.
        stored_version = 0
        raw_version = data.get("settings_version")
        if isinstance(raw_version, (int, float, str)):
            try:
                stored_version = int(raw_version)
            except (TypeError, ValueError):
                stored_version = 0

        known = {f.name: f.type for f in fields(cls)}
        for key, value in data.items():
            if key not in known or value is None:
                continue
            current = getattr(settings, key)
            try:
                if isinstance(current, bool):
                    setattr(settings, key, bool(value))
                elif isinstance(current, int) and not isinstance(current, bool):
                    setattr(settings, key, int(value))
                elif isinstance(current, float):
                    setattr(settings, key, float(value))
                elif isinstance(current, list):
                    # Without this a stored list round-trips as the string
                    # "['Resume']", which then matches no document type at all.
                    if not isinstance(value, list):
                        raise TypeError(f"{key} must be a list")
                    setattr(settings, key, [str(item) for item in value if str(item).strip()])
                else:
                    setattr(settings, key, str(value))
            except (TypeError, ValueError):
                logger.warning("Ignoring invalid stored setting %s=%r", key, value)
        settings.settings_version = stored_version
        settings._migrate()
        settings.__post_init__()
        return settings

    def _migrate(self) -> None:
        """Adopt changed defaults on a settings file written by an older build.

        Only values still sitting on the *old* default are touched: anything the
        user chose for themselves is left exactly as they left it.
        """
        if self.settings_version < 2:
            # Separator pages used to be kept. Every real applicant tracking
            # export puts one before each attachment, so keeping them meant
            # every exported resume opened on a page reading "Resume".
            if self.separator_policy == SeparatorPolicy.INCLUDE.value:
                self.separator_policy = SeparatorPolicy.EXCLUDE.value
                logger.info("Settings migrated: separator pages are now excluded")
        if self.settings_version < 3:
            # The default output shape changed from "one folder per
            # candidate holding everything" to "one folder per document
            # type". A real ATS export is a large multi-applicant batch,
            # where a hiring manager wants a pile of resumes, not fifty
            # candidate folders to open one at a time.
            if self.folder_per_candidate is True:
                self.folder_per_candidate = False
                logger.info(
                    "Settings migrated: exporting by document type, not by candidate folder"
                )
            if self.export_combined_packets is True:
                self.export_combined_packets = False
                logger.info("Settings migrated: combined candidate packets are now opt-in")
            if self.create_excel_index is True:
                self.create_excel_index = False
                logger.info("Settings migrated: the Excel index is now opt-in")
        self.settings_version = CURRENT_SETTINGS_VERSION


class SecretStore:
    """Stores the OpenAI API key outside the settings file.

    Uses the OS credential store when available. Where it is not (some Linux
    desktops without a keyring daemon), it falls back to an owner-only file so
    the feature still works, and reports which backend is in use so the UI can
    tell the truth about it.
    """

    def __init__(self, service: str = KEYRING_SERVICE) -> None:
        self.service = service
        self._backend_message = ""

    @property
    def backend_description(self) -> str:
        return self._backend_message or "Operating system credential store"

    def get_secret(self, username: str = KEYRING_USERNAME) -> str:
        value = self._get_from_keyring(username)
        if value:
            return value
        return self._get_from_file(username)

    def set_secret(self, value: str, username: str = KEYRING_USERNAME) -> bool:
        """Persist (or clear, when ``value`` is empty) a secret. Never logged."""
        cleaned = (value or "").strip()
        if not cleaned:
            self._delete_everywhere(username)
            return True

        try:
            import keyring

            keyring.set_password(self.service, username, cleaned)
            self._backend_message = "Operating system credential store"
            self._delete_from_file(username)
            return True
        except Exception as exc:
            logger.warning("Credential store unavailable (%s); using local fallback", type(exc).__name__)

        return self._set_in_file(cleaned, username)

    # ------------------------------------------------------------------
    def _get_from_keyring(self, username: str) -> str:
        try:
            import keyring

            return keyring.get_password(self.service, username) or ""
        except Exception:
            return ""

    def _fallback_path(self) -> Path:
        return app_data_dir() / _FALLBACK_SECRET_FILE

    def _get_from_file(self, username: str) -> str:
        path = self._fallback_path()
        if not path.exists():
            return ""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            value = str(data.get(username, ""))
            if value:
                self._backend_message = (
                    "Local file (the operating system credential store was unavailable)"
                )
            return value
        except (OSError, ValueError):
            return ""

    def _set_in_file(self, value: str, username: str) -> bool:
        path = self._fallback_path()
        try:
            data: dict[str, str] = {}
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except ValueError:
                    data = {}
            data[username] = value
            path.write_text(json.dumps(data), encoding="utf-8")
            try:
                os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
            self._backend_message = (
                "Local file (the operating system credential store was unavailable)"
            )
            return True
        except OSError as exc:
            logger.error("Could not save credential: %s", exc)
            return False

    def _delete_from_file(self, username: str) -> None:
        path = self._fallback_path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data.pop(username, None)
            path.write_text(json.dumps(data), encoding="utf-8")
        except (OSError, ValueError):
            pass

    def _delete_everywhere(self, username: str) -> None:
        try:
            import keyring

            keyring.delete_password(self.service, username)
        except Exception:
            pass
        self._delete_from_file(username)


class SettingsStore:
    """Loads and saves :class:`AppSettings`."""

    def __init__(self, path: Path | None = None, secrets: SecretStore | None = None) -> None:
        self.path = Path(path) if path else settings_path()
        self.secrets = secrets or SecretStore()

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("Could not read settings (%s); using defaults", exc)
            return AppSettings()
        return AppSettings.from_dict(data)

    def save(self, settings: AppSettings) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = settings.to_dict()
            temp = self.path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(temp, self.path)
            return True
        except OSError as exc:
            logger.error("Could not save settings: %s", exc)
            return False

    # -- secrets ---------------------------------------------------------
    def get_openai_key(self) -> str:
        return self.secrets.get_secret(KEYRING_USERNAME)

    def set_openai_key(self, value: str) -> bool:
        return self.secrets.set_secret(value, KEYRING_USERNAME)


__all__ = ["AppSettings", "SettingsStore", "SecretStore", "KEYRING_SERVICE", "KEYRING_USERNAME"]
