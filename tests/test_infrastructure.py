"""File discovery, settings persistence, history, logging and error handling."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pymupdf
import pytest

from app.models.enums import FileStatus, JobStatus, ProviderKind, SeparatorPolicy
from app.models.processing_job import ProcessingJob
from app.services.file_discovery import discover_pdfs, is_pdf
from app.services.pdf_service import (
    PdfCorruptError,
    PdfEncryptedError,
    open_pdf,
    read_info,
)
from app.services.processing_service import CancellationToken, mark_duplicates
from app.storage.history_store import HistoryStore
from app.storage.settings_store import AppSettings, SettingsStore
from app.utils.hashing import hash_file, hash_text
from app.utils.logging_setup import RedactingFilter
from scripts import sample_data


class TestFileDiscovery:
    def test_finds_pdfs_in_a_folder(self, samples_dir: Path) -> None:
        found = discover_pdfs([samples_dir])
        assert len(found) == len(sample_data.ALL_SAMPLES)

    def test_single_file_input(self, samples_dir: Path) -> None:
        target = samples_dir / sample_data.sample_b().filename
        assert discover_pdfs([target]) == [target]

    def test_non_pdf_files_are_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "notes.txt").write_text("hello")
        (tmp_path / "sheet.xlsx").write_bytes(b"x")
        assert discover_pdfs([tmp_path]) == []

    def test_subfolders_can_be_included_or_excluded(self, tmp_path: Path) -> None:
        nested = tmp_path / "nested"
        nested.mkdir()
        sample_data.build_pdf(sample_data.sample_b(), tmp_path / "top.pdf")
        sample_data.build_pdf(sample_data.sample_b(), nested / "inner.pdf")

        assert len(discover_pdfs([tmp_path], include_subfolders=True)) == 2
        assert len(discover_pdfs([tmp_path], include_subfolders=False)) == 1

    def test_output_folder_is_not_re_ingested(self, tmp_path: Path) -> None:
        """A previous run's output must never become the next run's input."""
        output = tmp_path / "Output"
        output.mkdir()
        sample_data.build_pdf(sample_data.sample_b(), tmp_path / "source.pdf")
        sample_data.build_pdf(sample_data.sample_b(), output / "exported.pdf")

        found = discover_pdfs([tmp_path], exclude_dirs=[output])
        assert [p.name for p in found] == ["source.pdf"]

    def test_duplicate_inputs_are_collapsed(self, samples_dir: Path) -> None:
        target = samples_dir / sample_data.sample_b().filename
        assert discover_pdfs([target, target, samples_dir.parent / samples_dir.name]) is not None
        assert len(discover_pdfs([target, target])) == 1

    def test_missing_paths_are_ignored(self, tmp_path: Path) -> None:
        assert discover_pdfs([tmp_path / "nope.pdf"]) == []

    def test_is_pdf_is_case_insensitive(self) -> None:
        assert is_pdf("A.PDF") and is_pdf("a.pdf") and not is_pdf("a.pdfx")


class TestBrokenInput:
    def test_corrupt_file_raises_a_clear_error(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.pdf"
        broken.write_bytes(b"this is not a pdf at all")
        with pytest.raises(PdfCorruptError) as excinfo:
            with open_pdf(broken):
                pass
        assert "broken.pdf" in str(excinfo.value)

    def test_encrypted_file_raises_a_clear_error(self, tmp_path: Path) -> None:
        locked = tmp_path / "locked.pdf"
        document = pymupdf.open()
        document.new_page()
        document.save(
            str(locked),
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            owner_pw="owner",
            user_pw="secret",
        )
        document.close()

        with pytest.raises(PdfEncryptedError):
            with open_pdf(locked):
                pass

    def test_encrypted_file_opens_with_the_right_password(self, tmp_path: Path) -> None:
        locked = tmp_path / "locked.pdf"
        document = pymupdf.open()
        document.new_page()
        document.save(
            str(locked),
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            owner_pw="owner",
            user_pw="secret",
        )
        document.close()

        with open_pdf(locked, "secret") as opened:
            assert opened.page_count == 1

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(Exception):
            read_info(tmp_path / "nope.pdf")

    def test_pipeline_records_the_error_instead_of_raising(
        self, pipeline, tmp_path: Path
    ) -> None:
        broken = tmp_path / "broken.pdf"
        broken.write_bytes(b"not a pdf")
        analysis = pipeline.analyze_file(broken)

        assert analysis.status is FileStatus.ERROR
        assert analysis.error and "broken.pdf" in analysis.error
        assert "Traceback" not in analysis.error

    def test_one_bad_file_does_not_stop_the_batch(
        self, pipeline, samples_dir: Path, tmp_path: Path
    ) -> None:
        broken = tmp_path / "broken.pdf"
        broken.write_bytes(b"not a pdf")
        good = samples_dir / sample_data.sample_b().filename

        job = ProcessingJob()
        results = pipeline.analyze_files([broken, good, broken], job=job)

        assert len(results) == 3
        assert results[1].status is not FileStatus.ERROR
        assert len(job.errors) == 2
        assert job.pdfs_processed == 1

    def test_pdf_with_no_pages_is_reported(self, pipeline, tmp_path: Path) -> None:
        """PyMuPDF refuses to *write* a zero-page PDF, but one can arrive."""
        empty = tmp_path / "empty.pdf"
        empty.write_bytes(
            b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
            b"trailer<</Root 1 0 R>>\n"
            b"%%EOF\n"
        )

        analysis = pipeline.analyze_file(empty)
        assert analysis.status is FileStatus.ERROR
        assert analysis.error and "Traceback" not in analysis.error


class TestCancellation:
    def test_cancelling_stops_the_batch(self, pipeline, samples_dir: Path) -> None:
        token = CancellationToken()
        token.cancel()
        results = pipeline.analyze_files(
            [samples_dir / sample_data.sample_a().filename], token=token
        )
        assert results == []

    def test_cancelling_mid_file_leaves_no_groups(self, pipeline, samples_dir: Path) -> None:
        token = CancellationToken()

        class OneShotToken(CancellationToken):
            def __init__(self) -> None:
                super().__init__()
                self.checks = 0

            @property
            def is_cancelled(self) -> bool:
                self.checks += 1
                return self.checks > 3

        analysis = pipeline.analyze_file(
            samples_dir / sample_data.sample_a().filename, token=OneShotToken()
        )
        assert analysis.status is FileStatus.WAITING
        assert analysis.groups == []


class TestDuplicateDetection:
    def test_identical_content_is_detected(self, tmp_path: Path) -> None:
        first = tmp_path / "one.pdf"
        second = tmp_path / "different_name.pdf"
        sample_data.build_pdf(sample_data.sample_b(), first)
        second.write_bytes(first.read_bytes())

        assert hash_file(first) == hash_file(second)

    def test_different_content_with_the_same_name_is_not_a_duplicate(
        self, tmp_path: Path
    ) -> None:
        a = tmp_path / "a" / "packet.pdf"
        b = tmp_path / "b" / "packet.pdf"
        a.parent.mkdir()
        b.parent.mkdir()
        sample_data.build_pdf(sample_data.sample_b(), a)
        sample_data.build_pdf(sample_data.sample_c(), b)

        assert hash_file(a) != hash_file(b)

    def test_mark_duplicates_flags_but_does_not_block(
        self, pipeline, samples_dir: Path
    ) -> None:
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_b().filename)
        flagged = mark_duplicates([analysis], {analysis.content_hash: "EarlierName.pdf"})

        assert flagged == [analysis]
        assert analysis.duplicate_of == "EarlierName.pdf"
        assert analysis.status is not FileStatus.ERROR

    def test_text_hash_is_whitespace_insensitive(self) -> None:
        assert hash_text("hello   world") == hash_text("hello world\n")


class TestSettingsPersistence:
    def test_defaults_are_usable(self) -> None:
        settings = AppSettings()
        assert settings.output_directory
        assert settings.provider_kind is ProviderKind.RULES
        # Divider pages are dropped by default: every real ATS export puts one
        # before each attachment, and keeping them led each exported resume with
        # a near-blank page.
        assert settings.separator_policy_enum is SeparatorPolicy.EXCLUDE

    def test_round_trip(self, tmp_path: Path) -> None:
        store = SettingsStore(tmp_path / "settings.json")
        settings = AppSettings(
            include_subfolders=False,
            create_excel_index=False,
            theme="dark",
            confidence_high=0.8,
            provider=ProviderKind.OLLAMA.value,
            filename_template="{document_type}_{candidate}",
        )
        assert store.save(settings)

        loaded = store.load()
        assert loaded.include_subfolders is False
        assert loaded.theme == "dark"
        assert loaded.confidence_high == pytest.approx(0.8)
        assert loaded.provider_kind is ProviderKind.OLLAMA
        assert loaded.filename_template == "{document_type}_{candidate}"

    def test_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        assert SettingsStore(tmp_path / "nothing.json").load().theme == "system"

    def test_corrupt_file_returns_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.json"
        path.write_text("{not valid json")
        assert SettingsStore(path).load().theme == "system"

    def test_unknown_and_invalid_keys_are_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.json"
        path.write_text('{"theme": "dark", "bogus_key": 1, "confidence_high": "abc"}')
        loaded = SettingsStore(path).load()
        assert loaded.theme == "dark"
        assert not hasattr(loaded, "bogus_key")

    def test_api_key_is_never_written_to_the_settings_file(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.json"
        store = SettingsStore(path)
        store.save(AppSettings())
        assert "api_key" not in path.read_text().lower()
        assert "sk-" not in path.read_text()


class TestHistoryStore:
    def test_records_and_reads_back_a_job(self, pipeline, samples_dir: Path, tmp_path: Path) -> None:
        store = HistoryStore(tmp_path / "history.sqlite3")
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_a().filename)

        job = ProcessingJob(inputs=[str(analysis.path)], output_directory=str(tmp_path))
        job.pdfs_processed = 1
        job.pages_processed = analysis.page_count
        job.documents_found = len(analysis.groups)
        job.finish(JobStatus.COMPLETED)

        assert store.record_job(job, [analysis])
        entries = store.recent_jobs()
        assert len(entries) == 1
        assert entries[0].pdfs_processed == 1
        assert entries[0].documents_found == 4
        assert analysis.name in entries[0].sources

    def test_duplicate_index_uses_content_hashes(
        self, pipeline, samples_dir: Path, tmp_path: Path
    ) -> None:
        store = HistoryStore(tmp_path / "history.sqlite3")
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_b().filename)
        job = ProcessingJob()
        store.record_job(job, [analysis])

        known = store.known_hashes()
        assert analysis.content_hash in known
        assert store.find_duplicates([analysis.content_hash])

    def test_jobs_are_returned_newest_first(self, tmp_path: Path) -> None:
        from datetime import datetime, timedelta

        store = HistoryStore(tmp_path / "history.sqlite3")
        older = ProcessingJob(id="old")
        older.started_at = datetime.now() - timedelta(hours=2)
        newer = ProcessingJob(id="new")
        store.record_job(older, [])
        store.record_job(newer, [])

        assert [entry.id for entry in store.recent_jobs()] == ["new", "old"]

    def test_clear_removes_everything(self, tmp_path: Path) -> None:
        store = HistoryStore(tmp_path / "history.sqlite3")
        store.record_job(ProcessingJob(), [])
        assert store.clear()
        assert store.recent_jobs() == []

    def test_unwritable_location_degrades_gracefully(self, tmp_path: Path) -> None:
        blocker = tmp_path / "blocked"
        blocker.write_text("i am a file, not a directory")
        store = HistoryStore(blocker / "history.sqlite3")
        assert not store.is_available
        assert store.recent_jobs() == []


class TestLoggingPrivacy:
    def _record(self, message: str) -> logging.LogRecord:
        return logging.LogRecord("t", logging.INFO, "f", 1, message, None, None)

    def test_openai_keys_are_redacted(self) -> None:
        record = self._record("using key sk-abcdef1234567890abcdef")
        RedactingFilter().filter(record)
        assert "sk-abcdef1234567890abcdef" not in record.getMessage()
        assert "REDACTED" in record.getMessage()

    def test_labelled_secrets_are_redacted(self) -> None:
        for message in (
            "api_key=supersecretvalue",
            "password: hunter2hunter2",
            "Authorization: Bearer abcdef123456",
        ):
            record = self._record(message)
            RedactingFilter().filter(record)
            assert "REDACTED" in record.getMessage(), message

    def test_ordinary_messages_are_untouched(self) -> None:
        record = self._record("pdf.analyzed file='Packet.pdf' pages=10")
        RedactingFilter().filter(record)
        assert record.getMessage() == "pdf.analyzed file='Packet.pdf' pages=10"


class TestFixtureDataIsInvented:
    """The generated fixtures must never carry real applicant or client data.

    This repository is public. Everything under ``scripts/`` that generates
    sample documents is written to *look* like the client's files, and that is
    exactly what makes a real value pasted in there hard to spot: it belongs, it
    reads correctly, and nobody greps a file called ``fixtures`` for somebody's
    phone number. A comment claiming the data is invented has already failed to
    keep it invented once, so this checks instead of trusting.

    It catches the two classes a machine can recognise. Names, job titles and
    requisition numbers still need a person to look -- keep using obviously
    invented ones.
    """

    #: Reserved by RFC 2606 and RFC 6761 -- these can never belong to anybody.
    SAFE_DOMAINS = ("example.com", "example.net", "example.org", "example.edu")
    #: 555 is the North American fiction convention, and 555 is not an assigned
    #: area code either, so a number carrying it in either position -- the
    #: repository uses both -- cannot reach a real subscriber.
    SAFE_PHONE = re.compile(r"\b555\b")

    FIXTURE_MODULES = (
        "sample_data.py",
        "ats_fixtures.py",
        "pageup_fixtures.py",
        "packet_fixtures.py",
        "mixed_batch.py",
    )

    def sources(self) -> list[tuple[str, str]]:
        root = Path(__file__).resolve().parent.parent / "scripts"
        found = [
            (name, (root / name).read_text(encoding="utf-8"))
            for name in self.FIXTURE_MODULES
            if (root / name).is_file()
        ]
        assert found, "no fixture modules found -- this test is checking nothing"
        return found

    def test_every_email_address_uses_a_reserved_domain(self) -> None:
        pattern = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
        offenders = [
            f"{name}: {address}"
            for name, text in self.sources()
            for address in pattern.findall(text)
            if not address.lower().endswith(self.SAFE_DOMAINS)
        ]
        assert not offenders, f"fixture email addresses outside example.*: {offenders}"

    def test_every_phone_number_is_a_reserved_one(self) -> None:
        pattern = re.compile(r"\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}")
        offenders = [
            f"{name}: {number}"
            for name, text in self.sources()
            for number in pattern.findall(text)
            if not self.SAFE_PHONE.search(number)
        ]
        assert not offenders, f"fixture phone numbers that could reach a real person: {offenders}"


class TestProcessingJob:
    def test_errors_are_collected(self) -> None:
        job = ProcessingJob()
        job.add_error("a.pdf", "could not read")
        assert job.has_errors
        assert job.errors[0].source == "a.pdf"

    def test_finishing_sets_status_and_time(self) -> None:
        job = ProcessingJob()
        job.finish(JobStatus.COMPLETED)
        assert job.status is JobStatus.COMPLETED
        assert job.finished_at is not None
        assert job.duration_seconds >= 0

    def test_serialisation_contains_the_summary_counters(self) -> None:
        job = ProcessingJob()
        job.pages_processed = 10
        data = job.to_dict()
        assert data["pages_processed"] == 10
        assert "errors" in data


class TestSettingsMigration:
    """A changed default has to reach installs that already exist.

    Otherwise the fix only ever helps new users, and the person who reported
    the problem still has it after upgrading.
    """

    def test_an_old_install_adopts_the_new_separator_default(self) -> None:
        settings = AppSettings.from_dict(
            {"settings_version": 1, "separator_policy": "include"}
        )
        assert settings.separator_policy_enum is SeparatorPolicy.EXCLUDE

    def test_a_deliberate_choice_is_left_alone(self) -> None:
        """Migration adopts defaults; it does not overrule the user."""
        settings = AppSettings.from_dict(
            {"settings_version": 1, "separator_policy": "ask"}
        )
        assert settings.separator_policy_enum is SeparatorPolicy.ASK

    def test_a_current_install_is_not_touched(self) -> None:
        """Someone who chose to keep separators after the change keeps them."""
        settings = AppSettings.from_dict(
            {"settings_version": 2, "separator_policy": "include"}
        )
        assert settings.separator_policy_enum is SeparatorPolicy.INCLUDE

    def test_migration_stamps_the_current_version(self) -> None:
        from app.storage.settings_store import CURRENT_SETTINGS_VERSION

        settings = AppSettings.from_dict({"settings_version": 1})
        assert settings.settings_version == CURRENT_SETTINGS_VERSION

    def test_a_settings_file_with_no_version_is_migrated(self) -> None:
        """Files written before versioning existed carry no version at all."""
        settings = AppSettings.from_dict({"separator_policy": "include"})
        assert settings.separator_policy_enum is SeparatorPolicy.EXCLUDE

    def test_an_old_install_adopts_the_document_type_export_layout(self) -> None:
        """The default output shape moved from candidate folders (with a
        combined packet and an Excel index) to document-type folders."""
        settings = AppSettings.from_dict(
            {
                "settings_version": 2,
                "folder_per_candidate": True,
                "export_combined_packets": True,
                "create_excel_index": True,
            }
        )
        assert settings.folder_per_candidate is False
        assert settings.export_combined_packets is False
        assert settings.create_excel_index is False

    def test_a_deliberate_export_layout_choice_survives_migration(self) -> None:
        """A user who had already turned these off keeps them off -- nothing
        to migrate, and nothing to overrule either."""
        settings = AppSettings.from_dict(
            {
                "settings_version": 2,
                "folder_per_candidate": False,
                "export_combined_packets": False,
                "create_excel_index": False,
            }
        )
        assert settings.folder_per_candidate is False
        assert settings.export_combined_packets is False
        assert settings.create_excel_index is False

    def test_a_current_install_export_layout_is_not_touched(self) -> None:
        """Someone who chose the old candidate-folder layout after upgrading
        keeps it -- migration only ever runs once, on the version jump."""
        settings = AppSettings.from_dict(
            {
                "settings_version": 3,
                "folder_per_candidate": True,
                "export_combined_packets": True,
                "create_excel_index": True,
            }
        )
        assert settings.folder_per_candidate is True
        assert settings.export_combined_packets is True
        assert settings.create_excel_index is True

    def test_group_by_document_type_defaults_on_for_every_install(self) -> None:
        """A brand new field: old settings files simply never mention it, so
        the dataclass default (on) applies uniformly -- no migration needed."""
        settings = AppSettings.from_dict({"settings_version": 2})
        assert settings.group_by_document_type is True
