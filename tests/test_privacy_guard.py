"""Repository-level protections for confidential QA files."""

from scripts.check_private_qa import (
    is_forbidden_qa_path,
    main,
)


def test_forbidden_qa_paths_are_recognized() -> None:
    assert is_forbidden_qa_path("qa/example.pdf")
    assert is_forbidden_qa_path("qa/nested/example.PDF")
    assert is_forbidden_qa_path("qa/input/notes.txt")
    assert is_forbidden_qa_path("qa/output/result.json")


def test_synthetic_qa_metadata_is_allowed() -> None:
    assert not is_forbidden_qa_path("qa/expected.example.json")
    assert not is_forbidden_qa_path("tests/fixtures/example.pdf")


def test_repository_tracks_no_confidential_qa_files() -> None:
    assert main() == 0
