"""Filename sanitisation, templating and de-duplication."""

from __future__ import annotations

from datetime import date

import pytest

from app.utils.filenames import (
    DEFAULT_TEMPLATE,
    render_filename_template,
    sanitize_filename,
    sanitize_folder_name,
    template_preview,
    unique_path,
)


class TestSanitizeFilename:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Benjamin Perez", "Benjamin_Perez"),
            ("Benjamin/Perez", "Benjamin_Perez"),
            (r"a<b>c:d\e|f?g*h", "a_b_c_d_e_f_g_h"),
            ("  spaced  out  ", "spaced_out"),
            ("trailing dots...", "trailing_dots"),
            ("__multiple___underscores__", "multiple_underscores"),
        ],
    )
    def test_removes_illegal_characters(self, raw: str, expected: str) -> None:
        assert sanitize_filename(raw) == expected

    def test_empty_input_uses_fallback(self) -> None:
        assert sanitize_filename("", fallback="Untitled") == "Untitled"
        assert sanitize_filename("   ///   ", fallback="Doc") == "Doc"

    def test_control_characters_are_stripped(self) -> None:
        assert "\n" not in sanitize_filename("line\nbreak")
        assert "\t" not in sanitize_filename("tab\there")

    @pytest.mark.parametrize("reserved", ["CON", "PRN", "AUX", "NUL", "COM1", "LPT9"])
    def test_windows_reserved_device_names_are_escaped(self, reserved: str) -> None:
        result = sanitize_filename(reserved)
        assert result.upper() != reserved
        assert result.startswith("_")

    def test_very_long_names_are_truncated(self) -> None:
        result = sanitize_filename("x" * 500)
        assert 0 < len(result) <= 120

    def test_folder_names_keep_spaces(self) -> None:
        assert sanitize_folder_name("Benjamin Perez") == "Benjamin Perez"
        assert sanitize_folder_name("Bad/Name") == "Bad Name"
        assert sanitize_folder_name("") == "Unknown"


class TestFilenameTemplate:
    def test_default_template(self) -> None:
        stem = render_filename_template(
            DEFAULT_TEMPLATE, candidate="Benjamin Perez", document_type="Resume"
        )
        assert stem == "Benjamin_Perez_Resume"

    def test_all_supported_variables(self) -> None:
        stem = render_filename_template(
            "{candidate}-{document_type}-{source_file}-{applicant_id}-{sequence}-{date}",
            candidate="Jane Smith",
            document_type="Cover Letter",
            source_file="/tmp/Packet.pdf",
            applicant_id="A-1",
            sequence=7,
            when=date(2024, 3, 12),
        )
        assert "Jane_Smith" in stem
        assert "Cover_Letter" in stem
        assert "Packet" in stem
        assert "007" in stem
        assert "2024-03-12" in stem

    def test_missing_values_do_not_leave_gaps(self) -> None:
        stem = render_filename_template(
            "{candidate}_{document_type}", candidate=None, document_type="Resume"
        )
        assert stem == "Resume"

    def test_unknown_placeholder_is_left_alone_but_sanitized(self) -> None:
        stem = render_filename_template("{candidate}_{nonsense}", candidate="Ann Lee")
        assert stem.startswith("Ann_Lee")

    def test_empty_template_falls_back_to_something_useful(self) -> None:
        stem = render_filename_template("", candidate="Ann Lee", document_type="Resume")
        assert stem == "Ann_Lee_Resume"

    def test_template_with_no_resolvable_values_still_produces_a_name(self) -> None:
        stem = render_filename_template("{candidate}", candidate=None, source_file="Packet.pdf")
        assert stem  # never empty
        assert "/" not in stem

    def test_preview_is_a_pdf_name(self) -> None:
        assert template_preview(DEFAULT_TEMPLATE).endswith(".pdf")
        assert "Benjamin_Perez_Resume" in template_preview(DEFAULT_TEMPLATE)


class TestUniquePath:
    def test_first_name_is_used_when_free(self, tmp_path) -> None:
        assert unique_path(tmp_path, "Report").name == "Report.pdf"

    def test_existing_file_gets_a_numeric_suffix(self, tmp_path) -> None:
        (tmp_path / "Report.pdf").write_bytes(b"%PDF-1.4")
        assert unique_path(tmp_path, "Report").name == "Report_2.pdf"

    def test_suffixes_keep_incrementing(self, tmp_path) -> None:
        (tmp_path / "Report.pdf").write_bytes(b"x")
        (tmp_path / "Report_2.pdf").write_bytes(b"x")
        (tmp_path / "Report_3.pdf").write_bytes(b"x")
        assert unique_path(tmp_path, "Report").name == "Report_4.pdf"

    def test_reserved_names_avoid_collisions_before_files_exist(self, tmp_path) -> None:
        taken: set[str] = set()
        first = unique_path(tmp_path, "Report", taken=taken)
        second = unique_path(tmp_path, "Report", taken=taken)
        third = unique_path(tmp_path, "Report", taken=taken)
        assert [p.name for p in (first, second, third)] == [
            "Report.pdf",
            "Report_2.pdf",
            "Report_3.pdf",
        ]
