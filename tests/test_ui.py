"""UI tests driven through the real widgets on Qt's offscreen platform.

These exercise the actual application objects -- the same MainWindow, workers
and review workspace the user gets -- rather than stand-ins.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="PySide6 is required for the UI tests")

from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402

from app.models.enums import FileStatus  # noqa: E402
from app.profiles.recruiting import COVER_LETTER, RESUME  # noqa: E402
from app.storage.history_store import HistoryStore  # noqa: E402
from app.storage.settings_store import AppSettings, SettingsStore  # noqa: E402
from app.ui.theme import DARK, LIGHT, apply_theme, build_stylesheet, palette_for  # noqa: E402
from scripts import sample_data  # noqa: E402

pytestmark = pytest.mark.gui


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qapp, tmp_path: Path, samples_dir: Path, monkeypatch):
    """A real MainWindow wired to temporary settings, history and output."""
    from app.ui.main_window import MainWindow

    # Never block on a modal dialog during tests. Every entry point matters:
    # a single unpatched static call blocks the whole run forever.
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )
    for name in ("information", "warning", "critical", "about"):
        monkeypatch.setattr(
            QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
        )
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Ok)

    # Candidate rename and merge prompt through QInputDialog. Unpatched, these
    # block exactly as fatally as an unpatched QMessageBox.
    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False))
    )
    monkeypatch.setattr(
        QInputDialog, "getItem", staticmethod(lambda *a, **k: ("", False))
    )

    store = SettingsStore(tmp_path / "settings.json")
    settings = AppSettings()
    settings.output_directory = str(tmp_path / "output")
    settings.open_output_when_complete = False
    settings.ocr_enabled = False
    store.save(settings)

    apply_theme(qapp, settings.theme)
    window = MainWindow(settings, store, HistoryStore(tmp_path / "history.sqlite3"))
    yield window
    _drain_workers(qapp, window)
    window.review_view.shutdown()
    window.close()
    # A worker's finished/failed signal is queued cross-thread and can still
    # be in flight even once isRunning() has gone False; delivered late, it
    # lands on whichever *next* test's window happens to be pumping the
    # (session-scoped) event loop at the time, exporting to that test's
    # directory instead of its own and leaving this test's own assertions
    # looking at an empty one. Draining here, after the worker is confirmed
    # stopped but before the next test's window exists, is what prevents
    # that leak.
    qapp.processEvents()
    qapp.processEvents()


def _drain_workers(qapp, window) -> None:
    """Block until any worker this window started has fully finished."""
    for worker in (window._analysis_worker, window._export_worker):
        if worker is not None and worker.isRunning():
            worker.wait(5000)
    qapp.processEvents()


def export_root(window) -> Path:
    """Where the run that just finished put its documents.

    Sort & Save gives every run its own timestamped folder inside the chosen
    output directory, so a test looking for ``Resumes/`` has to look inside
    the run rather than at the folder the user picked once. Asserting there is
    exactly one keeps a test from quietly passing against the wrong batch.
    """
    base = Path(window.settings.output_directory)
    runs = sorted(p for p in base.iterdir() if p.is_dir())
    assert len(runs) == 1, f"expected exactly one run folder in {base}, found {runs}"
    return runs[0]


def run_until_idle(qapp, window, limit: int = 600) -> None:
    """Pump the event loop until the window is genuinely idle.

    "Idle" deliberately does not mean "the worker stopped running". A
    worker's completion signal is emitted on its own thread and delivered to
    the window on the *next* ``processEvents()``, and handling it can start
    the next stage -- Sort & Save chains an export straight onto the end of
    analysis. Returning the moment ``isRunning()`` goes false would hand the
    caller a window that is about to become busy again, so idleness is only
    believed once it survives a drain.

    Bounded by wall-clock time rather than an iteration count, since one
    call may now have to carry the caller through two workers.
    """
    deadline = time.monotonic() + limit * 0.05
    while time.monotonic() < deadline:
        worker = window._analysis_worker or window._export_worker
        if worker is not None and worker.isRunning():
            worker.wait(30)
            qapp.processEvents()
            continue
        # Nothing is running: drain what is queued -- which may start the
        # next stage -- and only then trust that there is nothing left.
        for _ in range(3):
            qapp.processEvents()
        if not window._busy:
            return
    qapp.processEvents()


class TestTheme:
    def test_both_palettes_are_complete(self) -> None:
        for palette in (LIGHT, DARK):
            for value in vars(palette).values():
                assert value is not None

    def test_stylesheets_build(self) -> None:
        for palette in (LIGHT, DARK):
            sheet = build_stylesheet(palette)
            assert "QPushButton" in sheet
            assert palette.accent in sheet

    def test_explicit_modes_resolve(self) -> None:
        assert palette_for("dark").is_dark is True
        assert palette_for("light").is_dark is False

    def test_unknown_mode_falls_back(self) -> None:
        assert palette_for("nonsense") in (LIGHT, DARK)


class TestQueue:
    def test_adding_a_folder_populates_the_queue(self, window, samples_dir: Path) -> None:
        window.add_paths([samples_dir])
        assert window.queue_table.rowCount() == len(sample_data.ALL_SAMPLES)
        assert window.analyze_button.isEnabled()

    def test_analyze_is_disabled_with_an_empty_queue(self, window) -> None:
        assert not window.analyze_button.isEnabled()

    def test_duplicate_paths_are_not_added_twice(self, window, samples_dir: Path) -> None:
        window.add_paths([samples_dir])
        first = window.queue_table.rowCount()
        window.add_paths([samples_dir])
        assert window.queue_table.rowCount() == first

    def test_non_pdf_input_is_reported_not_crashed(self, window, tmp_path: Path) -> None:
        (tmp_path / "notes.txt").write_text("hello")
        window.add_paths([tmp_path])
        assert window.queue_table.rowCount() == 0

    def test_clear_empties_the_queue(self, window, samples_dir: Path) -> None:
        window.add_paths([samples_dir])
        window._clear_queue()
        assert window.queue_table.rowCount() == 0
        assert not window.analyze_button.isEnabled()


class TestAnalysisFlow:
    def test_analysis_produces_documents(
        self, qapp, window, samples_dir: Path
    ) -> None:
        window.add_paths([samples_dir / sample_data.sample_a().filename])
        window._start_analysis()
        run_until_idle(qapp, window)

        analysis = next(iter(window._files.values()))
        assert analysis.status in (FileStatus.READY, FileStatus.REVIEW_NEEDED)
        assert [g.document_type for g in analysis.groups] == [
            "Application Report", RESUME, COVER_LETTER, "References"
        ]

    def test_analysis_stays_on_the_home_screen(
        self, qapp, window, samples_dir: Path
    ) -> None:
        """Upload, separate, export is the path -- not upload, separate, and
        then a screen change nobody asked for."""
        window.add_paths([samples_dir / sample_data.sample_a().filename])
        window._start_analysis()
        run_until_idle(qapp, window)
        assert window._stack.currentIndex() == 0  # home page

    def test_split_and_save_is_reachable_without_opening_review(
        self, qapp, window, samples_dir: Path
    ) -> None:
        window.add_paths([samples_dir / sample_data.sample_a().filename])
        window._start_analysis()
        run_until_idle(qapp, window)

        assert window._stack.currentIndex() == 0
        assert window.export_button.isEnabled()

        window._start_export()
        run_until_idle(qapp, window)

        output = Path(window.settings.output_directory)
        assert list(output.rglob("*.pdf")), "Split & Save from the home screen exported nothing"

    def test_the_export_button_is_disabled_until_something_is_analysed(
        self, window
    ) -> None:
        assert not window.export_button.isEnabled()

    def test_sort_and_save_is_always_the_highlighted_action(
        self, qapp, window, samples_dir: Path
    ) -> None:
        """The one-click action is the thing to click, before and after analysis."""
        assert window.sort_save_button.property("variant") == "accent"
        assert window.analyze_button.property("variant") != "accent"
        assert window.export_button.property("variant") != "accent"

        window.add_paths([samples_dir / sample_data.sample_a().filename])
        window._start_analysis()
        run_until_idle(qapp, window)

        assert window.sort_save_button.property("variant") == "accent"
        assert window.analyze_button.property("variant") != "accent"
        assert window.export_button.property("variant") != "accent"

    def test_ui_stays_responsive_during_analysis(
        self, qapp, window, samples_dir: Path
    ) -> None:
        """Work must run off the UI thread."""
        window.add_paths([samples_dir])
        window._start_analysis()
        assert window._analysis_worker is not None
        assert window._analysis_worker.isRunning() or not window._busy
        qapp.processEvents()  # would block if analysis ran on this thread
        run_until_idle(qapp, window)
        assert not window._busy

    def test_a_broken_file_does_not_stop_the_batch(
        self, qapp, window, samples_dir: Path, tmp_path: Path
    ) -> None:
        broken = tmp_path / "broken.pdf"
        broken.write_bytes(b"not a pdf")
        window.add_paths([broken, samples_dir / sample_data.sample_b().filename])
        window._start_analysis()
        run_until_idle(qapp, window)

        statuses = {f.name: f.status for f in window._files.values()}
        assert statuses["broken.pdf"] is FileStatus.ERROR
        assert statuses[sample_data.sample_b().filename] is not FileStatus.ERROR

    def test_cancelling_stops_cleanly(self, qapp, window, samples_dir: Path) -> None:
        window.add_paths([samples_dir])
        window._start_analysis()
        window._cancel_current_job()
        run_until_idle(qapp, window)
        assert not window._busy


class TestSortAndSave:
    """The one-click primary action: analyze, split, classify, name, save."""

    def test_one_click_analyzes_and_exports(
        self, qapp, window, samples_dir: Path
    ) -> None:
        window.add_paths([samples_dir / sample_data.sample_a().filename])
        window._start_sort_and_save()
        run_until_idle(qapp, window)

        output = Path(window.settings.output_directory)
        assert list(output.rglob("*.pdf")), "Sort & Save produced no files"
        assert not window._busy

    def test_does_nothing_with_an_empty_queue(self, qapp, window) -> None:
        window._start_sort_and_save()
        qapp.processEvents()
        assert not window._busy
        assert window._analysis_worker is None

    def test_skips_the_review_confirmation_prompt(
        self, qapp, window, samples_dir: Path, monkeypatch
    ) -> None:
        """The whole point of one click is that it does not stop to ask."""
        calls: list[int] = []
        monkeypatch.setattr(
            QMessageBox,
            "question",
            staticmethod(
                lambda *a, **k: calls.append(1) or QMessageBox.StandardButton.Yes
            ),
        )
        window.add_paths([samples_dir / sample_data.sample_e().filename])
        window._start_sort_and_save()
        run_until_idle(qapp, window)

        assert not calls, "Sort & Save should not prompt about review items"
        output = Path(window.settings.output_directory)
        assert list(output.rglob("*.pdf")), "Sort & Save produced no files"

    def test_a_manual_export_still_asks_about_review_items(
        self, qapp, window, samples_dir: Path, monkeypatch
    ) -> None:
        """Only the automatic export after Sort & Save skips the prompt."""
        calls: list[int] = []
        monkeypatch.setattr(
            QMessageBox,
            "question",
            staticmethod(
                lambda *a, **k: calls.append(1) or QMessageBox.StandardButton.Yes
            ),
        )
        window.add_paths([samples_dir / sample_data.sample_e().filename])
        window._start_analysis()
        run_until_idle(qapp, window)

        window._start_export()
        run_until_idle(qapp, window)

        assert calls, "a manual export with review items pending should still ask"

    def test_cancelling_analysis_does_not_leave_an_export_pending(
        self, qapp, window, samples_dir: Path
    ) -> None:
        """A cancelled Sort & Save must not silently export afterwards."""
        window.add_paths([samples_dir])
        window._start_sort_and_save()
        window._cancel_current_job()
        run_until_idle(qapp, window)

        assert not window._busy
        assert window._auto_export_pending is False


class TestReviewWorkspace:
    @pytest.fixture
    def reviewed(self, qapp, window, samples_dir: Path):
        window.add_paths([samples_dir])
        window._start_analysis()
        run_until_idle(qapp, window)
        return window

    def test_all_analysed_files_are_listed(self, reviewed) -> None:
        assert reviewed.review_view.file_list.count() >= 7

    def test_groups_are_rendered_for_the_selected_file(self, reviewed) -> None:
        assert len(reviewed.review_view._sections) >= 1

    def test_review_filter_shows_only_files_needing_review(self, qapp, reviewed) -> None:
        view = reviewed.review_view
        total_files = view.file_list.count()
        expected = sum(1 for f in view.files() if f.review_group_count)

        view.review_filter_button.setChecked(True)
        qapp.processEvents()
        assert view.file_list.count() == expected
        assert view.file_list.count() < total_files

        view.review_filter_button.setChecked(False)
        qapp.processEvents()
        assert view.file_list.count() == total_files

    def test_review_filter_lands_on_a_file_with_work_to_do(self, qapp, reviewed) -> None:
        """The filter must never leave the user staring at an empty panel."""
        view = reviewed.review_view
        view.review_filter_button.setChecked(True)
        qapp.processEvents()
        assert view._current is not None
        assert view._current.review_group_count > 0
        assert len(view._sections) > 0

    def test_selecting_a_group_populates_the_inspector(self, qapp, reviewed) -> None:
        view = reviewed.review_view
        group = view._current.groups[0]
        view._select_group(group.id)
        qapp.processEvents()
        assert view.inspector._group is group


class TestCorrections:
    @pytest.fixture
    def sample_a_view(self, qapp, window, samples_dir: Path):
        window.add_paths([samples_dir / sample_data.sample_a().filename])
        window._start_analysis()
        run_until_idle(qapp, window)
        return window.review_view

    def shape(self, view):
        return [(g.document_type, g.start_page, g.end_page) for g in view._current.groups]

    def test_change_type(self, qapp, sample_a_view) -> None:
        view = sample_a_view
        group_id = view._current.groups[1].id
        view._change_type(group_id, "Writing Sample")
        qapp.processEvents()
        assert view._current.groups[1].document_type == "Writing Sample"

    def test_split_then_merge_restores_the_original(self, qapp, sample_a_view) -> None:
        view = sample_a_view
        before = self.shape(view)

        view._split_before(view._current.groups[1].id, 5)
        qapp.processEvents()
        assert len(view._current.groups) == len(before) + 1

        tail = next(g for g in view._current.groups if g.start_page_index == 5)
        view._merge_previous(tail.id)
        qapp.processEvents()
        assert self.shape(view) == before

    def test_exclude_toggles(self, qapp, sample_a_view) -> None:
        view = sample_a_view
        group = view._current.groups[0]
        view._set_excluded(group.id, True)
        qapp.processEvents()
        assert group.excluded
        view._set_excluded(group.id, False)
        qapp.processEvents()
        assert not group.excluded

    def test_accepting_a_group_clears_review(self, qapp, window, samples_dir: Path) -> None:
        window.add_paths([samples_dir / sample_data.sample_e().filename])
        window._start_analysis()
        run_until_idle(qapp, window)

        view = window.review_view
        group = view._current.groups[0]
        assert group.requires_review
        view._accept_group(group.id)
        qapp.processEvents()
        assert not group.requires_review

    def test_corrections_propagate_to_the_queue(self, qapp, sample_a_view, window) -> None:
        view = sample_a_view
        view._change_type(view._current.groups[1].id, "Portfolio")
        qapp.processEvents()
        analysis = window._files[str(view._current.path)]
        assert analysis.groups[1].document_type == "Portfolio"


class TestPacketReview:
    """The review panel is candidate-first, and packets can be corrected."""

    @pytest.fixture
    def mixed_view(self, qapp, window, tmp_path: Path):
        """A real multi-applicant PDF, analysed through the real window."""
        from scripts.mixed_batch import build_ambiguity_batch

        batch = build_ambiguity_batch()
        source = batch.write(tmp_path)
        window.add_paths([source])
        window._start_analysis()
        run_until_idle(qapp, window)
        return window.review_view

    def test_documents_are_shown_under_their_candidate(self, mixed_view) -> None:
        """Eighty pages as a flat document list is unreadable; people are not."""
        assert len(mixed_view._packet_sections) >= 2
        analysis = mixed_view._current
        for packet in analysis.identified_packets:
            assert packet.id in mixed_view._packet_sections

    def test_each_packet_header_names_the_candidate(self, mixed_view) -> None:
        analysis = mixed_view._current
        for packet in analysis.identified_packets:
            section = mixed_view._packet_sections[packet.id]
            assert section.name_label.text() == packet.display_name
            assert "document" in section.summary_label.text()

    def test_every_document_is_visible_somewhere(self, mixed_view) -> None:
        """A document belonging to no packet must not vanish from the panel."""
        analysis = mixed_view._current
        assert set(mixed_view._sections) == {g.id for g in analysis.groups}

    def test_moving_a_document_between_candidates(self, qapp, mixed_view) -> None:
        analysis = mixed_view._current
        source_packet = analysis.identified_packets[0]
        target_packet = analysis.identified_packets[1]
        document = source_packet.documents[-1]

        mixed_view.move_document_to_candidate(document.id, target_packet.id)
        qapp.processEvents()

        assert document.packet_id == target_packet.id
        assert document in target_packet.documents
        assert document not in source_packet.documents
        assert document.association_manually_set

    def test_renaming_a_candidate(self, qapp, mixed_view) -> None:
        packet = mixed_view._current.identified_packets[0]
        mixed_view.rename_candidate_to(packet.id, "Corrected Name")
        qapp.processEvents()

        assert packet.candidate.name == "Corrected Name"
        assert mixed_view._packet_sections[packet.id].name_label.text() == "Corrected Name"

    def test_merging_two_candidates(self, qapp, mixed_view) -> None:
        analysis = mixed_view._current
        keep, absorb = analysis.identified_packets[0], analysis.identified_packets[1]
        expected = len(keep.documents) + len(absorb.documents)

        mixed_view.merge_candidates(keep.id, absorb.id)
        qapp.processEvents()

        assert len(keep.documents) == expected
        assert absorb not in analysis.packets

    def test_splitting_a_candidate(self, qapp, mixed_view) -> None:
        analysis = mixed_view._current
        packet = next(p for p in analysis.identified_packets if len(p.documents) > 1)
        moving = packet.documents[-1]

        mixed_view.split_candidate(packet.id, [moving.id], "Someone Else")
        qapp.processEvents()

        assert moving not in packet.documents
        new_packet = analysis.packet_for_document(moving)
        assert new_packet is not None
        assert new_packet.candidate.name == "Someone Else"

    def test_creating_a_new_candidate_from_a_document(self, qapp, mixed_view) -> None:
        analysis = mixed_view._current
        document = analysis.groups[-1]
        before = len(analysis.packets)

        mixed_view.create_candidate_for(document.id, "Brand New")
        qapp.processEvents()

        packet = analysis.packet_for_document(document)
        assert packet is not None and packet.candidate.name == "Brand New"
        assert len(analysis.packets) >= before

    def test_the_inspector_shows_who_a_document_belongs_to(self, qapp, mixed_view) -> None:
        analysis = mixed_view._current
        document = analysis.groups[0]
        mixed_view._select_group(document.id)
        qapp.processEvents()

        packet = analysis.packet_for_document(document)
        assert packet is not None
        assert mixed_view.inspector._packet_label.text() == packet.display_name

    def test_candidate_choices_cover_every_packet(self, mixed_view) -> None:
        choices = dict(mixed_view.candidate_choices())
        for packet in mixed_view._current.packets:
            assert choices[packet.id] == packet.display_name

    def test_a_split_reassociates_without_losing_manual_choices(
        self, qapp, mixed_view
    ) -> None:
        """Re-deriving packets after a structural edit must respect the reviewer."""
        analysis = mixed_view._current
        target = analysis.identified_packets[-1]
        document = analysis.identified_packets[0].documents[-1]
        mixed_view.move_document_to_candidate(document.id, target.id)
        qapp.processEvents()

        multi = next(
            (g for g in analysis.groups if g.page_count > 1 and g is not document), None
        )
        assert multi is not None
        mixed_view._split_before(multi.id, multi.page_indexes[1])
        qapp.processEvents()

        assert document.packet_id == target.id, "a manual assignment was overwritten"


class TestExportFlow:
    def test_export_writes_pdfs_and_records_history(
        self, qapp, window, samples_dir: Path, tmp_path: Path
    ) -> None:
        window.add_paths([samples_dir / sample_data.sample_a().filename])
        window._start_analysis()
        run_until_idle(qapp, window)

        window._start_export()
        run_until_idle(qapp, window)

        run = export_root(window)
        pdfs = sorted(run.rglob("*.pdf"))
        # Four documents, one per type folder. Combined packets and the Excel
        # index are both opt-in now, so neither is produced by default.
        assert len(pdfs) == 4
        assert (run / "Application Reports" / "Benjamin Perez.pdf").exists()
        assert (run / "Resumes" / "Benjamin Perez.pdf").exists()
        assert (run / "Cover Letters" / "Benjamin Perez.pdf").exists()
        assert (run / "References" / "Benjamin Perez.pdf").exists()
        assert not (run / "DocumentIndex.xlsx").exists()
        assert window.history.recent_jobs()

    def test_excluded_documents_are_not_exported(
        self, qapp, window, samples_dir: Path
    ) -> None:
        window.add_paths([samples_dir / sample_data.sample_a().filename])
        window._start_analysis()
        run_until_idle(qapp, window)

        view = window.review_view
        view._set_excluded(view._current.groups[0].id, True)
        qapp.processEvents()

        window._start_export()
        run_until_idle(qapp, window)

        output = Path(window.settings.output_directory)
        # Three remaining documents, one per type folder; no combined packet
        # by default, so excluding one document is simply one fewer file.
        assert len(sorted(output.rglob("*.pdf"))) == 3

    def test_export_with_nothing_analysed_is_handled(self, qapp, window) -> None:
        window._start_export()
        qapp.processEvents()
        assert not window._busy


class TestExtractSelector:
    """Choosing what you need before analysing, on the home screen."""

    def test_everything_is_the_default(self, window) -> None:
        assert window.extract_selector.selection() == []
        assert "every document" in window.extract_selector._summary.text().lower()

    def test_choosing_one_type_narrows_the_output(self, qapp, window) -> None:
        window.extract_selector._buttons["Resume"].setChecked(True)
        qapp.processEvents()

        assert window.extract_selector.selection() == ["Resume"]
        assert window.settings.export_document_types == ["Resume"]

    def test_the_choice_is_remembered_for_next_time(self, qapp, window) -> None:
        window.extract_selector._buttons["Cover Letter"].setChecked(True)
        qapp.processEvents()
        assert window.settings_store.load().export_document_types == ["Cover Letter"]

    def test_everything_button_clears_the_choice(self, qapp, window) -> None:
        window.extract_selector._buttons["Resume"].setChecked(True)
        qapp.processEvents()
        window.extract_selector._select_everything()
        qapp.processEvents()

        assert window.extract_selector.selection() == []
        assert window.settings.export_document_types == []

    def test_selecting_every_type_individually_means_everything(
        self, qapp, window
    ) -> None:
        for button in window.extract_selector._buttons.values():
            button.setChecked(True)
        qapp.processEvents()
        assert window.extract_selector.selection() == []

    def test_the_choice_reaches_the_export(self, qapp, window, samples_dir: Path) -> None:
        """The point of the control: fewer files out, without extra work."""
        window.extract_selector._buttons["Resume"].setChecked(True)
        qapp.processEvents()

        window.add_paths([samples_dir / sample_data.sample_a().filename])
        window._start_analysis()
        run_until_idle(qapp, window)
        window._start_export()
        run_until_idle(qapp, window)

        run = export_root(window)
        written = sorted(str(p.relative_to(run)) for p in run.rglob("*.pdf"))
        assert written, "nothing was exported"
        assert all(
            name.startswith("Resumes" + os.sep) or "Complete_Packet" in name
            for name in written
        ), (
            f"documents other than resumes were saved: {written}"
        )

    def test_settings_and_the_home_screen_stay_in_step(self, qapp, window) -> None:
        """Two controls editing one setting must not disagree."""
        from app.ui.settings_dialog import SettingsDialog

        window.extract_selector._buttons["Resume"].setChecked(True)
        qapp.processEvents()

        dialog = SettingsDialog(
            window.settings, window.settings_store, window._tokens, window
        )
        assert dialog.document_type_boxes["Resume"].isChecked()
        assert not dialog.document_type_boxes["Cover Letter"].isChecked()

    def test_less_common_types_are_reachable_but_not_selected_by_default(
        self, window
    ) -> None:
        """References, Transcript etc. still work -- just behind "more types"."""
        selector = window.extract_selector
        for document_type in ("References", "Transcript", "Writing Sample", "Portfolio"):
            assert document_type in selector._buttons

    def test_choosing_a_less_common_type_still_reaches_settings(
        self, qapp, window
    ) -> None:
        window.extract_selector._buttons["Transcript"].setChecked(True)
        qapp.processEvents()

        assert window.extract_selector.selection() == ["Transcript"]
        assert window.settings.export_document_types == ["Transcript"]


class TestExtractSelectorPrimaryTypes:
    """The widget in isolation: the everyday-type row versus "more types"."""

    def test_primary_types_are_visible_and_secondary_types_start_collapsed(
        self, qapp
    ) -> None:
        from app.ui.theme import palette_for
        from app.ui.widgets.extract_selector import ExtractSelector

        all_types = ["Resume", "Cover Letter", "Application Report", "References", "Transcript"]
        selector = ExtractSelector(
            all_types, palette_for("light"), primary_types=["Resume", "Cover Letter", "Application Report"]
        )
        selector.show()
        qapp.processEvents()
        try:
            for document_type in ("Resume", "Cover Letter", "Application Report"):
                assert selector._buttons[document_type].isVisible()
            for document_type in ("References", "Transcript"):
                assert not selector._buttons[document_type].isVisible()
        finally:
            selector.deleteLater()

    def test_more_types_toggle_reveals_the_rest(self, qapp) -> None:
        from app.ui.theme import palette_for
        from app.ui.widgets.extract_selector import ExtractSelector

        all_types = ["Resume", "Cover Letter", "Application Report", "References"]
        selector = ExtractSelector(
            all_types, palette_for("light"), primary_types=["Resume", "Cover Letter", "Application Report"]
        )
        selector.show()
        qapp.processEvents()
        try:
            toggle = next(
                child
                for child in selector.findChildren(type(selector._buttons["Resume"]))
                if child.text().startswith("More types")
            )
            toggle.setChecked(True)
            qapp.processEvents()
            assert selector._buttons["References"].isVisible()
        finally:
            selector.deleteLater()

    def test_no_primary_types_means_no_split_at_all(self, qapp) -> None:
        """Without ``primary_types`` every type is in the one visible row,
        exactly as before this feature existed."""
        from app.ui.theme import palette_for
        from app.ui.widgets.extract_selector import ExtractSelector

        all_types = ["Resume", "Cover Letter", "References"]
        selector = ExtractSelector(all_types, palette_for("light"))
        selector.show()
        qapp.processEvents()
        try:
            for document_type in all_types:
                assert selector._buttons[document_type].isVisible()
        finally:
            selector.deleteLater()


class TestReviewFollowsTheChoice:
    """Choosing Resume must focus the whole screen, not just the export."""

    @pytest.fixture
    def resumes_only(self, qapp, window, samples_dir: Path):
        window.extract_selector._buttons["Resume"].setChecked(True)
        qapp.processEvents()
        window.add_paths([samples_dir / sample_data.sample_a().filename])
        window._start_analysis()
        run_until_idle(qapp, window)
        return window

    def test_only_the_chosen_type_is_shown(self, resumes_only) -> None:
        view = resumes_only.review_view
        shown = {
            g.document_type
            for g in view._current.groups
            if g.id in view._sections
        }
        assert shown == {"Resume"}, f"the screen still shows {sorted(shown)}"

    def test_the_other_documents_are_still_analysed(self, resumes_only) -> None:
        """Filtering the screen must not throw work away."""
        analysis = next(iter(resumes_only._files.values()))
        assert len(analysis.groups) == 4
        assert {g.document_type for g in analysis.groups} == {
            "Application Report", "Resume", "Cover Letter", "References",
        }

    def test_clearing_the_choice_brings_everything_back(
        self, qapp, resumes_only
    ) -> None:
        resumes_only.extract_selector._select_everything()
        qapp.processEvents()

        view = resumes_only.review_view
        assert len(view._sections) == 4

    def test_the_summary_counts_only_what_is_shown(self, resumes_only) -> None:
        text = resumes_only.review_view._summary_label.text()
        assert "1 resumes" in text or "1 resume" in text, text

    def test_review_count_ignores_documents_that_are_filtered_out(
        self, resumes_only
    ) -> None:
        """A flagged cover letter is not work when the user asked for resumes."""
        view = resumes_only.review_view
        assert view.visible_review_count() <= 1


class TestApproveAll:
    """Clicking through fifty correct documents is data entry, not review."""

    @pytest.fixture
    def reviewed(self, qapp, window, samples_dir: Path):
        window.add_paths([samples_dir])
        window._start_analysis()
        run_until_idle(qapp, window)
        return window

    def test_it_clears_every_flagged_document(self, qapp, reviewed) -> None:
        view = reviewed.review_view
        assert view.visible_review_count() > 0, "nothing was flagged to begin with"

        approved = view.approve_all()
        qapp.processEvents()

        assert approved > 0
        assert view.visible_review_count() == 0

    def test_files_come_out_of_the_review_state(self, qapp, reviewed) -> None:
        reviewed.review_view.approve_all()
        qapp.processEvents()
        assert all(
            f.status is not FileStatus.REVIEW_NEEDED
            for f in reviewed._files.values()
            if f.status is not FileStatus.ERROR
        )

    def test_it_only_approves_what_the_user_can_see(
        self, qapp, reviewed
    ) -> None:
        """Approving while filtered must not sign off unseen documents."""
        view = reviewed.review_view
        view.set_type_filter(["Resume"])
        qapp.processEvents()

        view.approve_all()
        qapp.processEvents()

        hidden_flagged = [
            g
            for f in view._files
            for g in f.groups
            if g.needs_attention and g.document_type != "Resume"
        ]
        assert hidden_flagged, (
            "nothing was hidden, so this test proves nothing about scope"
        )

    def test_approving_nothing_is_harmless(self, qapp, reviewed) -> None:
        view = reviewed.review_view
        view.approve_all()
        qapp.processEvents()
        assert view.approve_all() == 0

    def test_the_button_reports_how_much_is_waiting(self, reviewed) -> None:
        view = reviewed.review_view
        assert view.approve_all_button.isEnabled()
        assert "(" in view.approve_all_button.text()

    def test_the_button_switches_off_once_everything_is_approved(
        self, qapp, reviewed
    ) -> None:
        view = reviewed.review_view
        view.approve_all()
        qapp.processEvents()
        assert not view.approve_all_button.isEnabled()


class TestWindowFitsTheScreen:
    """The window must never place a panel where it cannot be reached."""

    def test_the_window_is_not_larger_than_the_display(self, qapp, window) -> None:
        screen = window.screen() or qapp.primaryScreen()
        available = screen.availableGeometry()
        assert window.width() <= available.width()
        assert window.height() <= available.height()

    def test_the_minimum_size_also_fits(self, qapp, window) -> None:
        """A minimum larger than the screen cannot be satisfied at all."""
        screen = window.screen() or qapp.primaryScreen()
        available = screen.availableGeometry()
        assert window.minimumWidth() <= available.width()
        assert window.minimumHeight() <= available.height()

    def test_restored_geometry_off_screen_is_pulled_back(self, qapp, window) -> None:
        """Geometry saved on another monitor must not hide the window."""
        screen = window.screen() or qapp.primaryScreen()
        available = screen.availableGeometry()

        window.move(available.right() + 4000, available.bottom() + 4000)
        window._pull_onto_screen()

        assert available.intersects(window.frameGeometry())

    def test_all_three_review_panels_stay_visible_when_narrow(
        self, qapp, window
    ) -> None:
        """The inspector was being pushed past the right edge."""
        view = window.review_view
        view.resize(900, 600)
        qapp.processEvents()
        view._apply_splitter_proportions()

        sizes = view._splitter.sizes()
        assert len(sizes) == 3
        assert all(size > 0 for size in sizes), f"a panel was squeezed away: {sizes}"
        assert sum(sizes) <= view._splitter.width() + 2, (
            f"panels total {sum(sizes)} in a {view._splitter.width()}px splitter"
        )

    def test_the_inspector_keeps_a_usable_width(self, qapp, window) -> None:
        view = window.review_view
        view.resize(900, 600)
        qapp.processEvents()
        view._apply_splitter_proportions()
        assert view._splitter.sizes()[2] >= 240


class TestSettingsDialog:
    def test_dialog_builds_and_saves(self, qapp, window, tmp_path: Path) -> None:
        from app.ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(
            window.settings, window.settings_store, window._tokens, window
        )
        dialog.template_edit.setText("{document_type}_{candidate}")
        dialog.high_slider.setValue(85)
        dialog._save()

        reloaded = window.settings_store.load()
        assert reloaded.filename_template == "{document_type}_{candidate}"
        assert reloaded.confidence_high == pytest.approx(0.85)

    def test_output_modes_round_trip(self, qapp, window) -> None:
        from app.ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(
            window.settings, window.settings_store, window._tokens, window
        )
        dialog.export_combined_packets.setChecked(False)
        dialog.export_separate_documents.setChecked(True)
        dialog._save()

        reloaded = window.settings_store.load()
        assert reloaded.export_separate_documents is True
        assert reloaded.export_combined_packets is False

    def test_turning_both_output_modes_off_still_exports_something(
        self, qapp, window
    ) -> None:
        """Saving a configuration that produces no files at all is never intended."""
        from app.ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(
            window.settings, window.settings_store, window._tokens, window
        )
        dialog.export_separate_documents.setChecked(False)
        dialog.export_combined_packets.setChecked(False)
        dialog._save()

        reloaded = window.settings_store.load()
        assert reloaded.export_separate_documents or reloaded.export_combined_packets

    def test_document_type_filter_round_trips(self, qapp, window) -> None:
        from app.ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(
            window.settings, window.settings_store, window._tokens, window
        )
        for document_type, box in dialog.document_type_boxes.items():
            box.setChecked(document_type == "Resume")
        dialog._save()

        assert window.settings_store.load().export_document_types == ["Resume"]

    def test_selecting_every_type_is_stored_as_no_restriction(
        self, qapp, window
    ) -> None:
        """Otherwise a type added to the profile later would be silently dropped."""
        from app.ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(
            window.settings, window.settings_store, window._tokens, window
        )
        for box in dialog.document_type_boxes.values():
            box.setChecked(True)
        dialog._save()

        assert window.settings_store.load().export_document_types == []

    def test_selecting_no_types_saves_everything_rather_than_nothing(
        self, qapp, window
    ) -> None:
        from app.ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(
            window.settings, window.settings_store, window._tokens, window
        )
        for box in dialog.document_type_boxes.values():
            box.setChecked(False)
        dialog._save()

        assert window.settings_store.load().export_document_types == []

    def test_review_threshold_cannot_exceed_the_high_threshold(
        self, qapp, window
    ) -> None:
        from app.ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(
            window.settings, window.settings_store, window._tokens, window
        )
        dialog.high_slider.setValue(60)
        dialog.review_slider.setValue(95)
        assert dialog.review_slider.value() < dialog.high_slider.value()

    def test_filename_preview_updates(self, qapp, window) -> None:
        from app.ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(
            window.settings, window.settings_store, window._tokens, window
        )
        dialog.template_edit.setText("{candidate}-{document_type}")
        assert dialog.preview_label.text().endswith(".pdf")
        assert "Benjamin_Perez" in dialog.preview_label.text()

    def test_provider_privacy_message_changes(self, qapp, window) -> None:
        from app.models.enums import ProviderKind
        from app.ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(
            window.settings, window.settings_store, window._tokens, window
        )
        index = dialog.provider_combo.findData(ProviderKind.OPENAI.value)
        dialog.provider_combo.setCurrentIndex(index)
        assert "openai" in dialog.privacy_label.text().lower()
        # The dialog is never shown in tests, so check the explicit hidden flag
        # rather than on-screen visibility.
        assert not dialog.openai_group.isHidden()
        assert dialog.ollama_group.isHidden()

        index = dialog.provider_combo.findData(ProviderKind.OLLAMA.value)
        dialog.provider_combo.setCurrentIndex(index)
        assert dialog.openai_group.isHidden()
        assert not dialog.ollama_group.isHidden()

        index = dialog.provider_combo.findData(ProviderKind.RULES.value)
        dialog.provider_combo.setCurrentIndex(index)
        assert "offline" in dialog.privacy_label.text().lower()
        assert dialog.openai_group.isHidden()
        assert dialog.ollama_group.isHidden()


class TestHistoryDialog:
    def test_dialog_builds_with_no_history(self, qapp, window) -> None:
        from app.ui.history_dialog import HistoryDialog

        dialog = HistoryDialog(window.history, window._tokens, window)
        assert dialog._table.rowCount() == 0

    def test_dialog_lists_a_recorded_job(
        self, qapp, window, samples_dir: Path
    ) -> None:
        from app.ui.history_dialog import HistoryDialog

        window.add_paths([samples_dir / sample_data.sample_b().filename])
        window._start_analysis()
        run_until_idle(qapp, window)
        window._start_export()
        run_until_idle(qapp, window)

        dialog = HistoryDialog(window.history, window._tokens, window)
        assert dialog._table.rowCount() == 1


class TestAboutDialog:
    def test_dialog_shows_version_and_locations(self, qapp, window) -> None:
        from app.ui.about_dialog import AboutDialog
        from app.version import __version__

        dialog = AboutDialog(
            window.settings,
            window._tokens,
            provider_description="Rules Only - nothing leaves this computer.",
            ocr_description="tesseract 5.4.0 [bundled]",
        )
        details = dialog.details_text()
        assert __version__ in details
        assert "Provider" in details
        assert "OCR" in details
        assert "Log file" in details

    def test_dialog_never_shows_a_secret(self, qapp, window) -> None:
        from app.ui.about_dialog import AboutDialog

        window.settings.openai_model = "gpt-4o-mini"
        dialog = AboutDialog(window.settings, window._tokens)
        details = dialog.details_text().lower()
        for forbidden in ("sk-", "api key", "password", "secret"):
            assert forbidden not in details

    def test_privacy_text_reflects_the_provider(self, qapp, window) -> None:
        from app.models.enums import ProviderKind
        from app.ui.about_dialog import AboutDialog

        window.settings.provider = ProviderKind.RULES.value
        assert "entirely on this computer" in AboutDialog._privacy_text(window.settings)

        window.settings.provider = ProviderKind.OPENAI.value
        assert "sent to that provider" in AboutDialog._privacy_text(window.settings)

    def test_main_window_opens_it(self, qapp, window, monkeypatch) -> None:
        from app.ui import about_dialog

        opened = {}
        monkeypatch.setattr(about_dialog.AboutDialog, "exec", lambda self: opened.setdefault("yes", True))
        window._open_about()
        assert opened


class TestSortAndSaveRunFolder:
    """Every Sort & Save lands in its own timestamped folder, and everything
    that reports a destination reports *that* folder rather than the base
    directory the user picked once and reuses forever."""

    def run_folders(self, base: Path) -> list[Path]:
        return sorted(p for p in base.iterdir() if p.is_dir())

    def sort_and_save(self, qapp, window, source: Path) -> Path:
        window.add_paths([source])
        window._start_sort_and_save()
        run_until_idle(qapp, window)

        base = Path(window.settings.output_directory)
        folders = self.run_folders(base)
        assert len(folders) == 1, folders
        return folders[0]

    def test_output_goes_into_a_timestamped_folder(
        self, qapp, window, samples_dir: Path
    ) -> None:
        run = self.sort_and_save(qapp, window, samples_dir / sample_data.sample_a().filename)

        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-(AM|PM)", run.name), run.name
        assert list(run.rglob("*.pdf")), "the run folder holds no documents"
        base = Path(window.settings.output_directory)
        assert not list(base.glob("*.pdf")), "documents were written beside the run folder"

    def test_two_runs_do_not_share_a_folder(
        self, qapp, window, samples_dir: Path
    ) -> None:
        base = Path(window.settings.output_directory)
        self.sort_and_save(qapp, window, samples_dir / sample_data.sample_a().filename)

        window.add_paths([samples_dir / sample_data.sample_b().filename])
        window._start_sort_and_save()
        run_until_idle(qapp, window)

        assert len(self.run_folders(base)) == 2

    def test_the_completion_status_names_the_run_folder(
        self, qapp, window, samples_dir: Path
    ) -> None:
        run = self.sort_and_save(qapp, window, samples_dir / sample_data.sample_a().filename)
        assert run.name in window._status_label.text()

    def test_open_folder_targets_the_run_folder(
        self, qapp, window, samples_dir: Path, monkeypatch
    ) -> None:
        opened: list[Path] = []
        monkeypatch.setattr(
            "app.ui.main_window.open_in_file_manager", lambda path: opened.append(Path(path))
        )
        window.settings.open_output_when_complete = True

        run = self.sort_and_save(qapp, window, samples_dir / sample_data.sample_a().filename)

        assert opened == [run], opened

    def test_history_records_the_run_folder(
        self, qapp, window, samples_dir: Path
    ) -> None:
        run = self.sort_and_save(qapp, window, samples_dir / sample_data.sample_a().filename)

        recent = window.history.recent_jobs(limit=1)
        assert recent, "the run was not recorded in history"
        assert Path(recent[0].output_directory) == run

    def test_the_excel_index_is_written_into_the_run_folder(
        self, qapp, window, samples_dir: Path
    ) -> None:
        window.settings.create_excel_index = True
        run = self.sort_and_save(qapp, window, samples_dir / sample_data.sample_a().filename)

        base = Path(window.settings.output_directory)
        assert list(run.glob("*.xlsx")), "the index did not land beside its own batch"
        assert not list(base.glob("*.xlsx"))


class TestCheckForUpdates:
    """The Settings row that tells you a newer version exists.

    The check itself is covered in tests/test_updates.py; what matters here is
    that the dialog reflects each outcome correctly and never offers a download
    for an update that is not there.
    """

    def dialog(self, window):
        from app.ui.settings_dialog import SettingsDialog

        return SettingsDialog(
            window.settings, window.settings_store, window._tokens, window
        )

    def test_the_row_exists_and_starts_on_the_current_version(self, qapp, window) -> None:
        from app import APP_VERSION

        dialog = self.dialog(window)
        try:
            assert dialog.update_button.text() == "Check for updates"
            assert APP_VERSION in dialog.update_status.text()
            assert not dialog.update_download_button.isVisible(), (
                "a download was offered before anything had been checked"
            )
        finally:
            dialog.deleteLater()

    def test_a_newer_version_offers_the_download(self, qapp, window) -> None:
        from app.services.update_service import UpdateCheck

        dialog = self.dialog(window)
        try:
            dialog.show()
            dialog._on_update_checked(
                UpdateCheck(
                    current_version="1.0.0",
                    latest_version="1.4.0",
                    release_url="https://example.com/r/1.4.0",
                )
            )
            qapp.processEvents()

            assert "1.4.0" in dialog.update_status.text()
            assert dialog.update_download_button.isVisible()
            assert dialog._release_url == "https://example.com/r/1.4.0"
        finally:
            dialog.hide()
            dialog.deleteLater()

    def test_being_up_to_date_offers_nothing(self, qapp, window) -> None:
        from app.services.update_service import UpdateCheck

        dialog = self.dialog(window)
        try:
            dialog.show()
            dialog._on_update_checked(
                UpdateCheck(current_version="1.0.0", latest_version="1.0.0")
            )
            qapp.processEvents()

            assert "up to date" in dialog.update_status.text()
            assert not dialog.update_download_button.isVisible()
        finally:
            dialog.hide()
            dialog.deleteLater()

    def test_a_failed_check_shows_why_and_offers_nothing(self, qapp, window) -> None:
        from app.services.update_service import UpdateCheck

        dialog = self.dialog(window)
        try:
            dialog.show()
            dialog._on_update_checked(
                UpdateCheck(current_version="1.0.0", error="Could not reach the update server.")
            )
            qapp.processEvents()

            assert "Could not reach" in dialog.update_status.text()
            assert not dialog.update_download_button.isVisible(), (
                "a failed check offered a download anyway"
            )
        finally:
            dialog.hide()
            dialog.deleteLater()

    def test_the_button_re_enables_after_a_check(self, qapp, window) -> None:
        """A check that failed must not leave the button dead."""
        from app.services.update_service import UpdateCheck

        dialog = self.dialog(window)
        try:
            dialog.update_button.setEnabled(False)
            dialog._on_update_checked(UpdateCheck(current_version="1.0.0", error="offline"))
            assert dialog.update_button.isEnabled()
        finally:
            dialog.deleteLater()

    def test_the_download_button_opens_the_release_page(self, qapp, window, monkeypatch) -> None:
        opened: list[str] = []
        monkeypatch.setattr(
            "app.ui.settings_dialog.QDesktopServices.openUrl",
            staticmethod(lambda url: opened.append(url.toString())),
        )
        dialog = self.dialog(window)
        try:
            dialog._release_url = "https://example.com/r/2.0.0"
            dialog._open_release_page()
            assert opened == ["https://example.com/r/2.0.0"]
        finally:
            dialog.deleteLater()


class TestBranding:
    """The product name on every surface a user actually looks at.

    The rest of the rename -- installer, Windows metadata, published artefacts,
    and the parts deliberately left alone -- is covered in
    ``tests/test_branding.py``. These are the ones that need a live widget.
    """

    PRODUCT = "AS Resume Sorter"
    FORMER = "Smart PDF Sorter"

    def labels(self, widget) -> list[str]:
        from PySide6.QtWidgets import QLabel

        return [label.text() for label in widget.findChildren(QLabel)]

    def test_the_window_title_is_the_product_name(self, qapp, window) -> None:
        assert window.windowTitle() == self.PRODUCT

    def test_the_header_carries_the_product_name(self, qapp, window) -> None:
        assert self.PRODUCT in self.labels(window)

    def test_the_first_screen_invites_you_by_name(self, qapp, window) -> None:
        joined = " ".join(self.labels(window))
        assert f"{self.PRODUCT} will identify" in joined

    def test_no_visible_text_still_shows_the_old_name(self, qapp, window) -> None:
        for text in self.labels(window):
            assert self.FORMER not in text, text

    def test_the_about_dialog_is_titled_for_the_product(self, qapp, window) -> None:
        from app.ui.about_dialog import AboutDialog

        dialog = AboutDialog(window.settings, window._tokens)
        try:
            assert dialog.windowTitle() == f"About {self.PRODUCT}"
            assert self.PRODUCT in self.labels(dialog)
            assert not any(self.FORMER in text for text in self.labels(dialog))
        finally:
            dialog.deleteLater()

    def test_the_about_details_lead_with_the_product(self, qapp, window) -> None:
        """This block gets copied into support emails; it has to say what it is."""
        from app.ui.about_dialog import AboutDialog

        dialog = AboutDialog(window.settings, window._tokens)
        try:
            assert dialog.details_text().startswith(self.PRODUCT)
        finally:
            dialog.deleteLater()

    def test_the_update_row_reports_under_the_product_name(self, qapp, window) -> None:
        from app.services.update_service import UpdateCheck
        from app.ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(
            window.settings, window.settings_store, window._tokens, window
        )
        try:
            dialog.show()
            dialog._on_update_checked(
                UpdateCheck(current_version="1.0.0", latest_version="1.0.0")
            )
            qapp.processEvents()
            assert dialog.update_status.text().startswith(self.PRODUCT)
        finally:
            dialog.hide()
            dialog.deleteLater()
