"""The release workflow stays deliberate, tag-bound, and fully tested."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def workflow_text() -> str:
    return (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def workflow(workflow_text: str) -> dict:
    return yaml.safe_load(workflow_text)


def triggers(workflow: dict) -> dict:
    # PyYAML 1.1 treats the unquoted YAML key ``on`` as boolean true.
    return workflow.get("on", workflow.get(True, {}))


def release_action(workflow: dict) -> dict:
    return next(
        step
        for step in workflow["jobs"]["release"]["steps"]
        if str(step.get("uses", "")).startswith("softprops/action-gh-release")
    )


def test_manual_runs_require_an_explicit_existing_version_tag(workflow: dict) -> None:
    inputs = triggers(workflow)["workflow_dispatch"]["inputs"]
    assert inputs["tag"]["required"] is True
    assert inputs["tag"]["type"] == "string"
    assert inputs["draft"]["type"] == "boolean"
    assert inputs["draft"]["default"] is True


def test_ordinary_tag_pushes_still_default_to_draft(workflow_text: str) -> None:
    assert '$draft = "true"' in workflow_text
    assert 'EVENT_NAME: ${{ github.event_name }}' in workflow_text
    assert 'MANUAL_DRAFT: ${{ inputs.draft }}' in workflow_text


def test_manual_build_checks_out_and_verifies_the_requested_tag(workflow: dict) -> None:
    build = workflow["jobs"]["build"]
    checkout = next(
        step for step in build["steps"] if step.get("name") == "Check out the repository"
    )
    assert "inputs.tag" in checkout["with"]["ref"]

    verifier = next(
        step for step in build["steps"] if step.get("name") == "Resolve and verify the release tag"
    )
    script = verifier["run"]
    assert "show-ref --verify" in script
    assert "rev-list -n 1" in script
    assert "rev-parse HEAD" in script
    assert "app/version.py" in script


def test_manual_publication_rejects_prerelease_tags(workflow_text: str) -> None:
    assert '$draft -eq "false" -and $tag.Contains("-")' in workflow_text
    assert "published stable release requires a stable vX.Y.Z tag" in workflow_text


def test_release_job_is_bound_to_the_verified_tag_and_draft_choice(workflow: dict) -> None:
    release = workflow["jobs"]["release"]
    assert release["needs"] == "build"
    checkout = next(
        step for step in release["steps"] if step.get("name") == "Check out the repository"
    )
    assert checkout["with"]["ref"] == "${{ needs.build.outputs.release_tag }}"

    settings = release_action(workflow)["with"]
    assert settings["tag_name"] == "${{ needs.build.outputs.release_tag }}"
    assert settings["draft"] == "${{ needs.build.outputs.release_draft }}"
    assert settings["prerelease"] == "${{ contains(needs.build.outputs.release_tag, '-') }}"


def test_release_creation_waits_for_tests_build_and_msi_verification(workflow: dict) -> None:
    names = [step.get("name", "") for step in workflow["jobs"]["build"]["steps"]]
    assert names.index("Run the test suite") < names.index("Build the production artifacts")
    assert names.index("Build the production artifacts") < names.index("Verify the artifacts")
    assert names.index("Verify the artifacts") < names.index(
        "Install and verify the MSI before releasing it"
    )
    assert names.index("Install and verify the MSI before releasing it") < names.index(
        "Upload the artifacts"
    )
